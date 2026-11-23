from app_research_agent.apps import App
from app_research_agent.catalog import candidates, composio_auth_as_enum, parse_catalog

SAMPLE = """
| Toolkit | Slug | Tools | Triggers | Auth | Managed App |
|---------|------|-------|----------|------|-------------|
| [Close MCP](/toolkits/close_mcp.md) | `CLOSE_MCP` | 114 | 0 | DCR_OAUTH | No |
| [Close](/toolkits/close.md) | `CLOSE` | 75 | 0 | API_KEY | — |
| [Closeio Extra](/toolkits/closeio.md) | `CLOSEIO` | 3 | 0 | API_KEY | — |
| [HubSpot](/toolkits/hubspot.md) | `HUBSPOT` | 355 | 2 | OAUTH2, API_KEY | Yes |
"""


def test_parse_rows():
    rows = parse_catalog(SAMPLE)
    assert [r.slug for r in rows] == ["CLOSE_MCP", "CLOSE", "CLOSEIO", "HUBSPOT"]
    hub = rows[-1]
    assert (
        hub.auth == ["OAUTH2", "API_KEY"]
        and hub.tools == 355
        and hub.managed_app == "Yes"
    )
    assert rows[1].managed_app is None


def test_native_toolkit_preferred_over_mcp_proxy():
    rows = tuple(parse_catalog(SAMPLE))
    c = candidates(App(8, "CRM", "Close", "close.com"), rows)
    assert c[0].slug == "CLOSE"
    assert "CLOSE_MCP" in [r.slug for r in c]


def test_auth_mapping():
    rows = parse_catalog(SAMPLE)
    assert composio_auth_as_enum(rows[0]) == {"OAUTH2"}
    assert composio_auth_as_enum(rows[-1]) == {"OAUTH2", "API_KEY"}
