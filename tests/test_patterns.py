from app_research_agent.patterns import blocker_theme, compute
from app_research_agent.schema import AppRecord, Claim, ComposioCatalogEntry


def rec(i, cat, auth, access, build, blocker=None, composio=False, mcp=False):
    r = AppRecord(id=i, app=f"A{i}", category=cat, hint="")
    r.auth_methods, r.access, r.buildability = (
        Claim(value=auth),
        Claim(value=access),
        Claim(value=build),
    )
    r.main_blocker, r.official_mcp = Claim(value=blocker), Claim(value=mcp)
    r.composio = ComposioCatalogEntry(
        found=composio, tools=10 if composio else None, slug="X" if composio else None
    )
    return r


def test_blocker_themes():
    assert blocker_theme("API requires a paid Growth plan") == "Paid plan required"
    assert blocker_theme("Contact sales for enterprise access") == "Partnership / sales gate"
    assert blocker_theme(None) is None


def test_compute_counts_and_easy_wins():
    recs = [
        rec(1, "CRM", ["OAUTH2"], "SELF_SERVE_FREE", "READY", composio=True, mcp=True),
        rec(2, "CRM", ["API_KEY"], "SELF_SERVE_FREE", "READY"),  # easy win
        rec(3, "Fin", ["OAUTH2", "API_KEY"], "PARTNERSHIP", "NEEDS_OUTREACH", "contact sales"),
    ]
    p = compute(recs)
    assert p["auth_any"]["OAUTH2"] == 2
    assert [r["id"] for r in p["easy_wins"]] == [2]
    assert [r["id"] for r in p["outreach"]] == [3]
    assert p["categories"][0]["category"] == "CRM" and p["categories"][0]["self_serve_pct"] == 100
    assert p["official_mcp"] == 1
