"""Thin Groq wrapper: JSON-only chat with retries on rate limits and bad JSON."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from .config import settings


class LLMError(RuntimeError):
    pass


_client = None


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


def chat_json(model: str, system: str, user: str, attempts: int = 5) -> dict[str, Any]:
    """Call `model` and return a parsed JSON object. Backs off on 429/5xx."""
    from groq import APIConnectionError, APIStatusError, RateLimitError

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    delay = 8.0
    last: Exception | None = None
    for _ in range(attempts):
        try:
            r = client().chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
                response_format={"type": "json_object"},
                max_completion_tokens=4096,
            )
            return parse_json(r.choices[0].message.content)
        except RateLimitError as e:
            last = e
            ra = (
                e.response.headers.get("retry-after")
                if e.response is not None
                else None
            )
            time.sleep(float(ra) + 1 if ra else delay)
            delay = min(delay * 2, 90)
        except (APIConnectionError, APIStatusError) as e:
            last = e
            if isinstance(e, APIStatusError) and e.status_code in (401, 403, 404):
                raise LLMError(f"{model}: {e.status_code} {e}") from e
            if (
                isinstance(e, APIStatusError)
                and e.status_code < 500
                and e.status_code != 413
            ):
                # 400 json_validate_failed etc: ask again, more firmly
                messages = messages[:2] + [
                    {"role": "user", "content": "Return ONLY one valid JSON object."}
                ]
            time.sleep(delay)
            delay = min(delay * 2, 90)
        except (json.JSONDecodeError, ValueError) as e:
            last = e
            messages = messages[:2] + [
                {
                    "role": "user",
                    "content": "Your last reply was not valid JSON. Return ONLY one valid JSON object.",
                }
            ]
    raise LLMError(f"{model} failed after {attempts} attempts: {last}")
