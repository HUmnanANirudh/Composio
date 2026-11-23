"""Composio toolkit catalog lookup.

Source of truth: Composio's public catalog at docs.composio.dev/toolkits.md
(no API key needed). When COMPOSIO_API_KEY is set we additionally pull the
toolkit detail from the SDK. The catalog gives us two things per app:

* does Composio already ship a toolkit for it (and how many tools)?
* which auth schemes Composio implemented -- an independent cross-check on
  the agent's own auth finding.

Name -> slug matching is fuzzy, so a hand-reviewed override file
(data/composio_overrides.json) pins the ambiguous ones. That review is one
of the documented human-in-the-loop steps.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

import httpx

from .apps import App
from .config import CACHE, DATA
from .schema import ComposioCatalogEntry

CATALOG_URL = "https://docs.composio.dev/toolkits.md"
DOCS_BASE = "https://docs.composio.dev"
OVERRIDES = DATA / "composio_overrides.json"

_ROW = re.compile(
    r"^\|\s*\[(?P<name>[^\]]+)\]\((?P<path>[^)]+)\)\s*\|\s*`(?P<slug>[^`]+)`\s*\|"
    r"\s*(?P<tools>\d+)\s*\|\s*(?P<triggers>\d+)\s*\|\s*(?P<auth>[^|]*)\|\s*(?P<managed>[^|]*)\|"
)


def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\b(mcp|api|app|inc|the|io|ai|com)\b", " ", s)
    return re.sub(r"[^a-z0-9]", "", s)


def fetch_catalog_text(refresh: bool = False) -> str:
    path = CACHE / "composio_toolkits.md"
    if path.exists() and not refresh:
        return path.read_text()
    CACHE.mkdir(parents=True, exist_ok=True)
    text = httpx.get(CATALOG_URL, timeout=60, follow_redirects=True).text
    path.write_text(text)
    return text


def parse_catalog(text: str) -> list[ComposioCatalogEntry]:
    rows = []
    for line in text.splitlines():
        m = _ROW.match(line)
        if not m:
            continue
        auth = [a.strip() for a in m["auth"].split(",") if a.strip() and a.strip() != "—"]
        managed = m["managed"].strip()
        rows.append(
            ComposioCatalogEntry(
                found=True,
                slug=m["slug"],
                name=m["name"],
                tools=int(m["tools"]),
                triggers=int(m["triggers"]),
                auth=auth,
                managed_app=None if managed in ("", "—") else managed,
                url=DOCS_BASE + m["path"],
            )
        )
    return rows


@lru_cache(maxsize=1)
def catalog() -> tuple[ComposioCatalogEntry, ...]:
    return tuple(parse_catalog(fetch_catalog_text()))


def _overrides() -> dict[str, dict]:
    if OVERRIDES.exists():
        return {k: v for k, v in json.loads(OVERRIDES.read_text()).items() if not k.startswith("_")}
    return {}


def candidates(
    app: App, rows: tuple[ComposioCatalogEntry, ...] | None = None
) -> list[ComposioCatalogEntry]:
    """Rank catalog rows that plausibly correspond to this app (best first)."""
    rows = rows if rows is not None else catalog()
    names = {_norm(app.app)}
    paren = re.findall(r"\((.*?)\)", app.app)
    names |= {_norm(p) for p in paren}
    names.discard("")
    scored: list[tuple[int, ComposioCatalogEntry]] = []
    for r in rows:
        rn, rs = _norm(r.name or ""), _norm(r.slug or "")
        score = 0
        for n in names:
            if n in (rn, rs):
                score = max(score, 100)
            elif rn.startswith(n) or rs.startswith(n):
                score = max(score, 60 - abs(len(rn) - len(n)))
            elif len(n) >= 5 and (n in rn or n in rs):
                score = max(score, 40 - abs(len(rn) - len(n)))
        if score:
            # prefer the native toolkit over its *_MCP proxy, bigger toolkits on ties
            if (r.slug or "").endswith("_MCP"):
                score -= 8
            score += min((r.tools or 0) // 50, 5)
            scored.append((score, r))
    scored.sort(key=lambda t: -t[0])
    return [r for _, r in scored]


def _by_slug(slug: str | None) -> ComposioCatalogEntry | None:
    return next((r for r in catalog() if r.slug == slug), None) if slug else None


def lookup(app: App) -> ComposioCatalogEntry:
    """Primary toolkit for the app, plus its *_MCP proxy if Composio has one."""
    ov = _overrides().get(app.app, {})
    cands = candidates(app)
    if "toolkit" in ov:
        base = _by_slug(ov["toolkit"])
    else:
        base = cands[0] if cands else None
    if "mcp" in ov:
        mcp = _by_slug(ov["mcp"])
    else:
        mcp = next((r for r in cands if (r.slug or "").endswith("_MCP")), None)
    entry = base.model_copy() if base else ComposioCatalogEntry(found=False)
    if mcp:
        entry.mcp_variant, entry.mcp_variant_auth = mcp.slug, list(mcp.auth)
    return entry


# Composio auth label -> our Auth enum value
COMPOSIO_AUTH_MAP = {
    "OAUTH2": "OAUTH2",
    "OAUTH1": "OTHER",
    "S2S_OAUTH2": "OAUTH2",
    "DCR_OAUTH": "OAUTH2",
    "API_KEY": "API_KEY",
    "BEARER_TOKEN": "BEARER_TOKEN",
    "BASIC": "BASIC",
    "BASIC_WITH_JWT": "BASIC",
    "NO_AUTH": "NONE",
    "GOOGLE_SERVICE_ACCOUNT": "JWT",
    "SERVICE_ACCOUNT": "JWT",
}


def composio_auth_as_enum(entry: ComposioCatalogEntry) -> set[str]:
    return {COMPOSIO_AUTH_MAP.get(a, "OTHER") for a in entry.auth}
