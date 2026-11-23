from app_research_agent.research import Source
from app_research_agent.schema import AppRecord, Claim, ComposioCatalogEntry, Evidence
from app_research_agent.verify import ground, quote_score, rule_checks

PAGE = (
    "Klaviyo provides 3 methods of authentication including private key authentication and "
    "OAuth (used to call server-side APIs) and public key authentication."
)
SRC = [Source("S1", "https://developers.klaviyo.com/a", "", True, PAGE, PAGE)]


def test_quote_score_exact_fuzzy_and_fabricated():
    assert quote_score("private key authentication and OAuth", PAGE) == 100
    # small paraphrase / whitespace / smart quotes still count
    assert (
        quote_score("provides 3 methods of authentication, including private key auth", PAGE) >= 88
    )
    assert quote_score("Klaviyo requires a paid plan to access the API", PAGE) < 88


def _rec(**kw):
    r = AppRecord(id=36, app="Klaviyo", category="m", hint="")
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def test_ground_flags_fabricated_quote():
    rec = _rec(
        auth_methods=Claim(
            value=["OAUTH2"],
            evidence=[Evidence(url=SRC[0].url, quote="OAuth (used to call server-side APIs)")],
        ),
        access=Claim(
            value="PAID_PLAN",
            evidence=[Evidence(url=SRC[0].url, quote="API access requires the Pro plan")],
        ),
    )
    flags = ground(rec, SRC)
    assert "auth_methods" not in flags
    assert flags["access"] == ["ungrounded"]
    assert rec.auth_methods.evidence[0].grounded is True


def test_rules_catch_composio_mcp_and_inconsistency():
    rec = _rec(
        auth_methods=Claim(value=["BASIC"], confidence=0.9),
        access=Claim(value="PARTNERSHIP", confidence=0.9),
        buildability=Claim(value="READY", confidence=0.9),
        official_mcp=Claim(value=False, confidence=0.9),
        composio=ComposioCatalogEntry(
            found=True,
            slug="KLAVIYO",
            auth=["API_KEY", "OAUTH2"],
            tools=233,
            mcp_variant="KLAVIYO_MCP",
            mcp_variant_auth=["DCR_OAUTH"],
        ),
    )
    for f in ("api_style", "api_breadth"):
        setattr(rec, f, Claim(value="X", confidence=0.9))
    flags = rule_checks(rec, SRC)
    assert flags["auth_methods"][0].startswith("disagrees_with_composio")
    assert flags["official_mcp"][0].startswith("composio_proxies_vendor_mcp")
    assert flags["buildability"][0].startswith("inconsistent_with_access")
