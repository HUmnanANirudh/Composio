from app_research_agent.apps import App
from app_research_agent.research import (
    Source,
    claims_from_json,
    is_official,
    normalize,
    rank_urls,
)

KLAVIYO = App(36, "Marketing", "Klaviyo", "developers.klaviyo.com")


def test_official_detection():
    assert is_official(KLAVIYO, "https://developers.klaviyo.com/en/docs")
    assert is_official(KLAVIYO, "https://help.klaviyo.com/x")
    assert not is_official(KLAVIYO, "https://zapier.com/apps/klaviyo")


def test_rank_prefers_official_docs():
    urls = [
        "https://medium.com/klaviyo-tips",
        "https://www.klaviyo.com/pricing",
        "https://developers.klaviyo.com/en/reference",
    ]
    assert rank_urls(KLAVIYO, urls)[0] == "https://developers.klaviyo.com/en/reference"


def test_normalize_aliases():
    assert normalize("auth_methods", ["OAuth 2.0", "api token", "OAUTH2"]) == [
        "API_KEY",
        "OAUTH2",
    ]
    assert normalize("access", "self-serve free") == "SELF_SERVE_FREE"
    assert normalize("official_mcp", "yes") is True
    assert normalize("main_blocker", "None") is None


def test_claims_map_source_ids_to_urls():
    srcs = [Source("S1", "https://developers.klaviyo.com/a", "", True, "t", "t")]
    data = {
        "auth_methods": {
            "value": ["OAUTH2"],
            "evidence": [{"source": "S1", "quote": "OAuth"}],
            "confidence": 0.9,
        }
    }
    c = claims_from_json(data, srcs, fields=("auth_methods", "access"))
    assert c["auth_methods"].evidence[0].url == "https://developers.klaviyo.com/a"
    assert c["access"].value is None and c["access"].confidence == 0.2
