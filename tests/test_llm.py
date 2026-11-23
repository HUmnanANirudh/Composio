import json

import httpx
import pytest

from app_research_agent import llm
from app_research_agent.llm import DailyLimitError, chat_json, parse_json


def test_parse_json_fenced():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('noise {"a": 2} trailing') == {"a": 2}


def _ok(payload: dict) -> httpx.Response:
    body = {
        "candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
    }
    return httpx.Response(200, json=body)


def _err(code: int, msg: str, details=None) -> httpx.Response:
    return httpx.Response(
        code, json={"error": {"code": code, "message": msg, "details": details or []}}
    )


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """Route generateContent calls to a per-model handler; no sleeping, no usage file."""
    calls: list[str] = []
    handlers: dict = {}

    def transport(request: httpx.Request) -> httpx.Response:
        model = request.url.path.split("/")[-1].split(":")[0]
        calls.append(model)
        return handlers[model](request)

    monkeypatch.setattr(llm, "_client", httpx.Client(transport=httpx.MockTransport(transport)))
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm, "record_usage", lambda *a: None)
    monkeypatch.setattr(llm, "_retired", {})
    monkeypatch.setattr(llm, "_cooldown", {})
    return calls, handlers


def test_quota_capped_model_is_retired_and_next_answers(fake):
    calls, h = fake
    h["a"] = lambda r: _err(429, "Quota exceeded for metric: free_tier_input_token_count, limit: 0")
    h["b"] = lambda r: _ok({"ok": 1})
    assert chat_json("a,b", "sys json", "user") == {"ok": 1}
    assert chat_json("a,b", "sys json", "user") == {"ok": 1}
    assert calls == ["a", "b", "b"]  # 'a' is not retried once retired


def test_overloaded_model_falls_through(fake):
    calls, h = fake
    h["a"] = lambda r: _err(503, "This model is currently experiencing high demand.")
    h["b"] = lambda r: _ok({"ok": 2})
    assert chat_json("a,b", "s", "u") == {"ok": 2}
    assert calls == ["a", "b"]  # skipped on the first 503, not retried while 'b' is live


def test_per_minute_limit_waits_and_retries_same_model(fake):
    calls, h = fake
    seq = iter(
        [
            _err(
                429,
                "rate",
                [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}],
            ),
            _ok({"ok": 3}),
        ]
    )
    h["a"] = lambda r: next(seq)
    assert chat_json("a,b", "s", "u") == {"ok": 3}
    assert calls == ["a", "a"]


def test_all_models_out_of_quota_pauses_the_run(fake):
    _, h = fake
    h["a"] = h["b"] = lambda r: _err(429, "Quota exceeded ... limit: 0")
    with pytest.raises(DailyLimitError):
        chat_json("a,b", "s", "u")


def test_gemma_gets_system_prompt_inline_and_no_thinking_config():
    body = llm._body("gemma-4-31b-it", "SYSTEM", ["USER"])
    assert "systemInstruction" not in body and "thinkingConfig" not in body["generationConfig"]
    assert body["contents"][0]["parts"][0]["text"].startswith("SYSTEM")
    g = llm._body("gemini-4-hydrogen", "SYSTEM", ["USER"])
    assert g["systemInstruction"]["parts"][0]["text"] == "SYSTEM"
    assert "temperature" not in g["generationConfig"]  # ignored by Gemini 4; future 400
