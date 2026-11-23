"""Cross-app patterns, computed deterministically from final records.

No LLM here on purpose: every number on the case-study page is a count over
the records, so it can be traced back to rows and re-derived by anyone.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

from .apps import load_apps
from .config import RESULTS
from .pipeline import FINAL, load
from .schema import AppRecord

BLOCKER_THEMES = (
    ("Paid plan required", r"paid|plan|subscription|upgrade|tier|pricing|credit"),
    (
        "Partnership / sales gate",
        r"partner|sales|enterprise|contact|account (manager|team)|customer",
    ),
    ("App review / approval", r"review|approv|apply|application|verification|verified|register"),
    ("No public API", r"no (public |official |hosted )?api|cli|library|not an api|undocumented"),
    ("Narrow / limited API", r"narrow|limited|few endpoints|read-only|rate limit"),
    ("Auth complexity", r"oauth|token|signature|sigv4|jwt|scope"),
)


def blocker_theme(text: str | None) -> str | None:
    if not text:
        return None
    t = text.lower()
    for name, rx in BLOCKER_THEMES:
        if re.search(rx, t):
            return name
    return "Other"


def _pct(n: int, d: int) -> int:
    return round(100 * n / d) if d else 0


def compute(recs: list[AppRecord]) -> dict:
    n = len(recs)
    auth_any = Counter(a for r in recs for a in (r.auth_methods.value or []))
    auth_primary = Counter(
        "OAUTH2"
        if "OAUTH2" in (r.auth_methods.value or [])
        else "API_KEY/TOKEN"
        if set(r.auth_methods.value or []) & {"API_KEY", "BEARER_TOKEN"}
        else (r.auth_methods.value or ["UNKNOWN"])[0]
        for r in recs
    )
    access = Counter(r.access.value for r in recs)
    build = Counter(r.buildability.value for r in recs)
    by_cat: dict[str, dict] = defaultdict(
        lambda: {"n": 0, "access": Counter(), "build": Counter(), "auth": Counter()}
    )
    for r in recs:
        c = by_cat[r.category]
        c["n"] += 1
        c["access"][r.access.value] += 1
        c["build"][r.buildability.value] += 1
        for a in r.auth_methods.value or []:
            c["auth"][a] += 1
    cats = []
    for name, c in by_cat.items():
        self_serve = c["access"]["SELF_SERVE_FREE"] + c["access"]["SELF_SERVE_TRIAL"]
        gated = c["n"] - self_serve
        cats.append(
            {
                "category": name,
                "n": c["n"],
                "self_serve": self_serve,
                "gated": gated,
                "self_serve_pct": _pct(self_serve, c["n"]),
                "access": dict(c["access"]),
                "build": dict(c["build"]),
                "auth": dict(c["auth"]),
            }
        )
    cats.sort(key=lambda x: -x["self_serve_pct"])

    blockers = Counter(blocker_theme(r.main_blocker.value) for r in recs if r.main_blocker.value)
    mcp = sum(1 for r in recs if r.official_mcp.value)
    composio_has = [r for r in recs if r.composio.found and (r.composio.tools or 0) > 0]
    easy_wins = [  # buildable today, self-serve, and Composio has no toolkit yet
        r
        for r in recs
        if r.buildability.value in ("READY", "READY_WITH_CAVEATS")
        and r.access.value in ("SELF_SERVE_FREE", "SELF_SERVE_TRIAL")
        and not (r.composio.found and (r.composio.tools or 0) > 0)
    ]
    outreach = [
        r
        for r in recs
        if r.buildability.value == "NEEDS_OUTREACH" or r.access.value == "PARTNERSHIP"
    ]
    blocked = [r for r in recs if r.buildability.value == "BLOCKED"]
    mcp_only = [
        r for r in recs if r.official_mcp.value and r.access.value in ("PARTNERSHIP", "PAID_PLAN")
    ]

    self_serve_total = access["SELF_SERVE_FREE"] + access["SELF_SERVE_TRIAL"]
    oauth = auth_any["OAUTH2"]
    static = sum(1 for r in recs if set(r.auth_methods.value or []) & {"API_KEY", "BEARER_TOKEN"})
    gated = n - self_serve_total
    both = sum(
        1
        for r in recs
        if "OAUTH2" in (r.auth_methods.value or [])
        and set(r.auth_methods.value or []) & {"API_KEY", "BEARER_TOKEN"}
    )
    # Each headline: a figure the eye lands on, then the plain sentence it belongs to.
    # A headline is only emitted when the data supports it (no "0 apps name '-'").
    headlines = [
        {
            "fig": f"{_pct(self_serve_total, n)}%",
            "text": f"of apps ({self_serve_total} of {n}) let a developer get working credentials "
            f"alone; {gated} {'is' if gated == 1 else 'are'} gated by a paid plan, an approval "
            "step, or sales.",
        }
    ]
    lead, other = ("OAuth 2.0", "a static API key or token")
    lead_n, other_n = oauth, static
    if static > oauth:
        lead, other, lead_n, other_n = other, lead, static, oauth
    headlines.append(
        {
            "fig": f"{_pct(lead_n, n)}%",
            "text": f"accept {lead}, the most common scheme; {_pct(other_n, n)}% accept {other}, "
            f"and {both} {'offers' if both == 1 else 'offer'} both.",
        }
    )
    if len(cats) >= 2 and cats[0]["self_serve_pct"] > cats[-1]["self_serve_pct"]:
        headlines.append(
            {
                "fig": cats[0]["category"],
                "text": f"is the most open category ({cats[0]['self_serve_pct']}% self-serve); "
                f"{cats[-1]['category']} is the most gated "
                f"({cats[-1]['self_serve_pct']}% self-serve).",
            }
        )
    if blockers:
        theme, count = blockers.most_common(1)[0]
        headlines.append(
            {
                "fig": str(count),
                "text": f"{'app is' if count == 1 else 'apps are'} held back by the most common "
                f"blocker: {theme.lower()}.",
            }
        )
    headlines += [
        {
            "fig": str(len(easy_wins)),
            "text": f"{'easy win' if len(easy_wins) == 1 else 'easy wins'}: self-serve and "
            f"buildable today, with no Composio toolkit yet. {len(outreach)} "
            f"{'needs' if len(outreach) == 1 else 'need'} outreach or a partnership.",
        },
        {"fig": str(mcp), "text": f"of {n} vendors already ship an official MCP server."},
    ]

    def row(r: AppRecord) -> dict:
        return {
            "id": r.id,
            "app": r.app,
            "category": r.category,
            "access": r.access.value,
            "build": r.buildability.value,
            "blocker": r.main_blocker.value,
            "composio": r.composio.slug if r.composio.found else None,
        }

    return {
        "n": n,
        "headlines": headlines,
        "auth_any": dict(auth_any.most_common()),
        "auth_primary": dict(auth_primary.most_common()),
        "access": dict(access.most_common()),
        "buildability": dict(build.most_common()),
        "blockers": dict(blockers.most_common()),
        "categories": cats,
        "official_mcp": mcp,
        "composio_coverage": len(composio_has),
        "easy_wins": [row(r) for r in easy_wins],
        "outreach": [row(r) for r in outreach],
        "blocked": [row(r) for r in blocked],
        "mcp_but_api_gated": [row(r) for r in mcp_only],
    }


def main() -> None:
    recs = [r for a in load_apps() if (r := load(FINAL, a))]
    out = compute(recs)
    (RESULTS / "patterns.json").write_text(json.dumps(out, indent=1))
    for h in out["headlines"]:
        print("-", h["fig"], h["text"])
