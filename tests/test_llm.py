import time

from app_research_agent.llm import TokenBudget, parse_json


def test_parse_json_fenced():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('noise {"a": 2} trailing') == {"a": 2}


def test_budget_allows_within_cap_and_settles():
    b = TokenBudget()
    b.limit["m"] = 1000
    r1 = b.acquire("m", 400)
    r2 = b.acquire("m", 400)  # 800 <= 900 cap, no wait
    b.settle("m", r1, 100, {"x-ratelimit-limit-tokens": "2000"})
    assert b._used("m", time.time()) == 500 and b.limit["m"] == 2000
    assert r2 in b.spent["m"]
