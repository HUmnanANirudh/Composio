"""Groq wrapper: JSON-only chat that lives inside free-tier rate limits.

* A per-model sliding-window token budget keeps each call under the
  tokens-per-minute limit (read from Groq's x-ratelimit-* headers).
* 429s back off using the server's retry-after.
* Hitting the tokens-per-DAY cap raises DailyLimitError so the pipeline can
  stop cleanly and resume on the next run (every finished app is saved).
* Every call's token usage is appended to data/results/usage.jsonl.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import defaultdict, deque
from typing import Any

from .config import RESULTS, settings


class LLMError(RuntimeError):
    pass


class DailyLimitError(LLMError):
    """The model's tokens/requests-per-day quota is exhausted."""


_client = None
_usage_lock = threading.Lock()


def record_usage(model: str, purpose: str, usage) -> None:
    """Append token usage to data/results/usage.jsonl (cost/rate-limit accounting)."""
    if usage is None:
        return
    row = {
        "t": round(time.time()),
        "model": model,
        "purpose": purpose,
        "in": getattr(usage, "prompt_tokens", 0),
        "out": getattr(usage, "completion_tokens", 0),
    }
    with _usage_lock:
        RESULTS.mkdir(parents=True, exist_ok=True)
        with (RESULTS / "usage.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")


class TokenBudget:
    """Sliding 60 s window of tokens spent per model."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.spent: dict[str, deque] = defaultdict(deque)
        self.limit: dict[str, int] = defaultdict(lambda: settings.tpm_limit)

    def _cap(self, model: str) -> int:
        return int(self.limit[model] * 0.9)

    def _used(self, model: str, now: float) -> int:
        q = self.spent[model]
        while q and now - q[0][0] > 60:
            q.popleft()
        return sum(t for _, t in q)

    def acquire(self, model: str, tokens: int) -> tuple[float, int]:
        """Block until `tokens` fit in this minute's budget; returns a reservation."""
        tokens = min(tokens, self._cap(model))
        while True:
            with self.lock:
                now = time.time()
                if self._used(model, now) + tokens <= self._cap(model):
                    self.spent[model].append((now, tokens))
                    return (now, tokens)
                oldest = self.spent[model][0][0] if self.spent[model] else now
            time.sleep(max(1.0, 60 - (now - oldest) + 0.5))

    def settle(self, model: str, reservation: tuple[float, int], actual: int, headers) -> None:
        """Replace the estimate with the real token count and learn the real limit."""
        with self.lock:
            q = self.spent[model]
            if reservation in q:
                q[q.index(reservation)] = (reservation[0], actual)
            lim = headers.get("x-ratelimit-limit-tokens") if headers else None
            if lim and lim.isdigit():
                self.limit[model] = int(lim)


budget = TokenBudget()


def client():
    global _client
    if _client is None:
        if not settings.groq_api_key:
            raise LLMError("GROQ_API_KEY is not set (see .env.example)")
        from groq import Groq

        _client = Groq(api_key=settings.groq_api_key, max_retries=0)
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


def _extra(model: str) -> dict:
    # hidden reasoning tokens count against the per-minute budget: keep them low
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low"}
    return {}


def _try_again_seconds(msg: str) -> float | None:
    """Parse Groq's 'try again in 1h2m3.4s' hint into seconds."""
    m = re.search(r"try again in ((?:\d+h)?(?:\d+m)?(?:[\d.]+s)?)", msg)
    if not m or not m.group(1):
        return None
    parts = {unit: val for val, unit in re.findall(r"([\d.]+)([hms])", m.group(1))}
    return (
        float(parts.get("h", 0)) * 3600 + float(parts.get("m", 0)) * 60 + float(parts.get("s", 0))
    )


def _retry_after(e) -> float | None:
    h = getattr(getattr(e, "response", None), "headers", None) or {}
    try:
        return float(h.get("retry-after")) if h.get("retry-after") else None
    except ValueError:
        return None


def chat_json(
    model: str, system: str, user: str, attempts: int = 8, purpose: str = "extract"
) -> dict[str, Any]:
    """Call `model` and return a parsed JSON object, waiting out per-minute limits."""
    from groq import APIConnectionError, APIStatusError, RateLimitError

    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    # ~4 chars/token for English + JSON; reserve a typical (not maximum) output; settle() corrects it
    est = (len(system) + len(user)) // 4 + min(settings.max_output_tokens, 1200)
    delay = 8.0
    last: Exception | None = None
    for _ in range(attempts):
        res = budget.acquire(model, est)
        try:
            raw = client().chat.completions.with_raw_response.create(
                model=model,
                messages=messages,
                temperature=0,
                response_format={"type": "json_object"},
                max_completion_tokens=settings.max_output_tokens,
                **_extra(model),
            )
            r = raw.parse()
            used = (r.usage.prompt_tokens + r.usage.completion_tokens) if r.usage else est
            budget.settle(model, res, used, raw.headers)
            record_usage(model, purpose, r.usage)
            return parse_json(r.choices[0].message.content)
        except RateLimitError as e:
            last = e
            msg = str(e).lower()
            if "per day" in msg or "(tpd)" in msg or "(rpd)" in msg:
                # Groq's daily window is rolling: if tokens free up soon, wait instead of pausing
                wait = _try_again_seconds(msg)
                if wait is not None and wait <= settings.max_daily_wait:
                    time.sleep(wait + 5)
                    continue
                raise DailyLimitError(f"{model}: daily quota exhausted: {e}") from e
            time.sleep((_retry_after(e) or delay) + 1)
            delay = min(delay * 2, 90)
        except APIStatusError as e:
            last = e
            if e.status_code in (401, 403, 404):
                raise LLMError(f"{model}: {e.status_code} {e}") from e
            if e.status_code == 413 or "reduce the length" in str(e).lower():
                raise LLMError(f"{model}: request too large for the rate limit: {e}") from e
            if e.status_code < 500:  # e.g. json_validate_failed: ask again, more firmly
                messages = messages[:2] + [
                    {"role": "user", "content": "Return ONLY one valid JSON object."}
                ]
            time.sleep(delay)
            delay = min(delay * 2, 90)
        except APIConnectionError as e:
            last = e
            time.sleep(delay)
            delay = min(delay * 2, 90)
        except (json.JSONDecodeError, ValueError) as e:
            last = e
            messages = messages[:2] + [
                {
                    "role": "user",
                    "content": "Your last reply was not valid JSON. Return ONLY one JSON object.",
                }
            ]
    raise LLMError(f"{model} failed after {attempts} attempts: {last}")
