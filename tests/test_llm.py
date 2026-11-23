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


def test_try_again_parse():
    from app_research_agent.llm import _try_again_seconds

    assert _try_again_seconds("please try again in 20m12.624s. need more") == 1212.624
    assert _try_again_seconds("try again in 1h2m3s") == 3723
    assert _try_again_seconds("try again in 7.5s") == 7.5
    assert _try_again_seconds("no hint here") is None
