from app_research_agent.manual import _to_llm_shape
from app_research_agent.research import Source, claims_from_json


def test_compact_answers_become_cited_claims():
    src = [
        Source("S1", "https://dev.acme.com/auth", "Auth", True, "Use OAuth 2.0 or an API key.", "")
    ]
    data = _to_llm_shape(
        {
            "one_liner": "Acme makes widgets",
            "auth_methods": [
                ["OAuth 2.0", "api key"],
                0.9,
                [["S1", "Use OAuth 2.0 or an API key."]],
            ],
            "official_mcp": [False, 0.6, []],
            "docs_url": "https://dev.acme.com",
        }
    )
    claims = claims_from_json(data, src)
    assert claims["auth_methods"].value == ["API_KEY", "OAUTH2"]
    assert claims["auth_methods"].evidence[0].url == "https://dev.acme.com/auth"
    assert claims["one_liner"].value == "Acme makes widgets"
    assert claims["official_mcp"].value is False
    assert data["docs_url"] == "https://dev.acme.com"
