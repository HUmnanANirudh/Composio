"""Gemini wrapper: JSON-only generateContent calls over a per-role model fallback chain.

* Each role (extract / re-research) names a chain of models, newest first
  (see config.py). A call tries the first model that is still available.
* 429 with a quota of 0, or a used-up daily quota: that model is retired for the run and
  the next one in the chain takes over.
* 429 per-minute limit: wait the server's retryDelay (if short), then try again.
* 503 / 500 (model overloaded): back off briefly, then skip that model for a few minutes.
* Only when every model in the chain is retired does DailyLimitError pause the run
  (every finished app is saved, so the next run resumes).
* Every call's token usage, and the model that actually answered, is appended to
  data/results/usage.jsonl.
"""

from __future__ import annotations

import json
import random
import re
import sys
import threading
import time
from typing import Any

import httpx

from .config import RESULTS, settings

API = "https://generativelanguage.googleapis.com/v1beta/models"


class LLMError(RuntimeError):
    pass


class DailyLimitError(LLMError):
    """Every model in the chain has used up its quota."""


_usage_lock = threading.Lock()
_state_lock = threading.Lock()
_retired: dict[str, str] = {}  # model -> reason (quota used up / capped at 0 on this tier)
_cooldown: dict[str, float] = {}  # model -> time until which it is skipped (overloaded)
_client: httpx.Client | None = None


def record_usage(model: str, purpose: str, usage: dict | None) -> None:
    """Append token usage to data/results/usage.jsonl (cost and quota accounting)."""
    if not usage:
        return
    row = {
        "t": round(time.time()),
        "model": model,
        "purpose": purpose,
        "in": usage.get("promptTokenCount", 0),
        "out": usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0),
    }
    with _usage_lock:
        RESULTS.mkdir(parents=True, exist_ok=True)
        with (RESULTS / "usage.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")


def chain(spec: str) -> list[str]:
    return [m.strip() for m in spec.split(",") if m.strip()]


def first_model(spec: str) -> str:
    """The model a role would use right now (for display)."""
    live = [m for m in chain(spec) if m not in _retired]
    return (live or chain(spec))[0]


def client() -> httpx.Client:
    global _client
    if _client is None:
        if not settings.gemini_api_key:
            raise LLMError("GEMINI_API_KEY is not set (see .env.example)")
        _client = httpx.Client(
            headers={"x-goog-api-key": settings.gemini_api_key},
            timeout=httpx.Timeout(300, connect=20),
        )
    return _client


def parse_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def _body(model: str, system: str, messages: list[str]) -> dict:
    gen: dict[str, Any] = {
        "responseMimeType": "application/json",
        "maxOutputTokens": settings.max_output_tokens,
    }
    if model.startswith("gemini-"):  # Gemma takes no thinking config
        gen["thinkingConfig"] = {"thinkingLevel": settings.thinking_level}
    contents = [{"role": "user", "parts": [{"text": m}]} for m in messages]
    if model.startswith("gemma-"):  # Gemma has no system instruction: prepend it
        contents[0]["parts"][0]["text"] = f"{system}\n\n{contents[0]['parts'][0]['text']}"
        return {"contents": contents, "generationConfig": gen}
    return {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": gen,
    }


def _retry_delay(err: dict) -> float | None:
    """google.rpc.RetryInfo retryDelay ('23s') from a 429 body, if present."""
    for d in err.get("details", []) or []:
        if str(d.get("@type", "")).endswith("RetryInfo") and d.get("retryDelay"):
            m = re.match(r"([\d.]+)s", d["retryDelay"])
            if m:
                return float(m.group(1))
    return None


def _quota_exhausted(err: dict) -> bool:
    """A quota that will not free up within this run: capped at 0, or a per-day limit."""
    msg = (err.get("message") or "").lower()
    if "limit: 0" in msg or "per day" in msg or "perday" in msg:
        return True
    for d in err.get("details", []) or []:
        for v in d.get("violations", []) or []:
            if "PerDay" in str(v.get("quotaId", "")) or str(v.get("quotaValue")) == "0":
                return True
    return False


def _text(resp: dict) -> str:
    cands = resp.get("candidates") or []
    if not cands:
        fb = resp.get("promptFeedback", {})
        raise ValueError(f"no candidates (blocked: {fb.get('blockReason')})")
    parts = cands[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text:
        raise ValueError(f"empty response (finishReason {cands[0].get('finishReason')})")
    return text


def _call_model(
    model: str, system: str, user: str, purpose: str, last: bool = False
) -> dict[str, Any]:
    """Try one model. Returns parsed JSON, or raises _Skip / LLMError.

    `last`: no other model in the chain is live, so an overloaded model is retried with
    backoff instead of being skipped.
    """
    messages = [user]
    delay = 4.0
    for attempt in range(5):
        try:
            r = client().post(f"{API}/{model}:generateContent", json=_body(model, system, messages))
        except httpx.HTTPError as e:
            time.sleep(delay)
            delay = min(delay * 2, 60)
            if attempt == 4:
                raise _Skip(model, f"network: {e}", cool=120) from e
            continue
        if r.status_code == 200:
            resp = r.json()
            record_usage(model, purpose, resp.get("usageMetadata"))
            try:
                return parse_json(_text(resp))
            except (json.JSONDecodeError, ValueError) as e:
                messages = [
                    user,
                    "Your last reply was not valid JSON. Return ONLY one JSON object.",
                ]
                if attempt == 4:
                    raise _Skip(model, f"bad JSON: {e}", cool=0) from e
                continue
        try:
            err = r.json().get("error", {})
        except ValueError:
            err = {"message": r.text[:300]}
        if r.status_code == 429:
            if _quota_exhausted(err):
                raise _Skip(model, f"quota: {err.get('message', '')[:160]}", retire=True)
            wait = _retry_delay(err) or delay
            if wait > 120:
                raise _Skip(model, f"rate limited for {wait:.0f}s", cool=wait)
            time.sleep(wait + random.uniform(0, 2))
            delay = min(delay * 2, 60)
            continue
        if r.status_code in (500, 502, 503, 504):
            if not last or attempt == 4:  # overloaded: let the next model take this call
                raise _Skip(model, f"{r.status_code} {err.get('message', '')[:80]}", cool=600)
            time.sleep(delay + random.uniform(0, 2))
            delay = min(delay * 2, 60)
            continue
        if r.status_code in (400, 404) and "model" in (err.get("message") or "").lower():
            raise _Skip(model, f"{r.status_code} {err.get('message', '')[:160]}", retire=True)
        raise LLMError(f"{model}: {r.status_code} {err.get('message', '')[:300]}")
    raise _Skip(model, "no usable reply after retries", cool=60)


class _Skip(Exception):
    def __init__(self, model: str, why: str, retire: bool = False, cool: float = 0):
        super().__init__(f"{model}: {why}")
        self.model, self.why, self.retire, self.cool = model, why, retire, cool


def chat_json(spec: str, system: str, user: str, purpose: str = "extract") -> dict[str, Any]:
    """Call the first available model in `spec` (a comma-separated chain); fall back on quota."""
    models = chain(spec)
    tried: list[str] = []
    for _ in range(3):  # passes over the chain (cooled-down models come back)
        for model in models:
            with _state_lock:
                now = time.time()
                if model in _retired or _cooldown.get(model, 0) > now:
                    continue
                live = [m for m in models if m not in _retired and _cooldown.get(m, 0) <= now]
            try:
                return _call_model(model, system, user, purpose, last=live == [model])
            except _Skip as s:
                tried.append(str(s))
                print(f"[llm] {purpose}: {s} -> next model", file=sys.stderr, flush=True)
                with _state_lock:
                    if s.retire:
                        _retired[model] = s.why
                    elif s.cool:
                        _cooldown[model] = time.time() + s.cool
        with _state_lock:
            if all(m in _retired for m in models):
                raise DailyLimitError(
                    "every model in the chain is out of quota: "
                    + "; ".join(f"{m} ({_retired[m][:80]})" for m in models)
                )
            soonest = min((_cooldown.get(m, 0) for m in models if m not in _retired), default=0)
        time.sleep(max(5.0, min(soonest - time.time(), 300)))
    raise LLMError(f"no model in the chain answered: {' | '.join(tried[-5:])}")
