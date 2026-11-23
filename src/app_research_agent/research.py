"""Pass 1: gather sources for an app and extract a structured, cited record."""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import web
from .apps import App
from .config import settings
from .llm import chat_json
from .schema import CLAIM_FIELDS, AppRecord, Claim, Evidence

QUERY_TEMPLATES = (
    "{app} API authentication OAuth API key",
    "{app} developer API access requirements plan pricing partner",
    "{app} official MCP server",
)


@dataclass
class Source:
    sid: str
    url: str
    title: str
    official: bool
    text: str  # full cached text (used by the verifier)
    excerpt: str  # what the model sees


def _app_tokens(app: App) -> list[str]:
    base = re.sub(r"\(.*?\)", "", app.app).lower()
    return [t for t in re.findall(r"[a-z0-9]+", base) if len(t) >= 4] or [
        re.sub(r"[^a-z0-9]", "", base)
    ]


def is_official(app: App, url: str) -> bool:
    h = web.host(url)
    if app.domain and web.registrable(h) == web.registrable(app.domain):
        return True
    reg = web.registrable(h).split(".")[0]
    return any(t in reg for t in _app_tokens(app))


def rank_urls(app: App, urls: list[str]) -> list[str]:
    def score(u: str) -> tuple:
        off = is_official(app, u)
        docsy = bool(
            re.search(r"developer|docs|api|dev\.|reference|auth", u, re.IGNORECASE)
        )
        return (not off, web.is_low_trust(u), not docsy)

    seen: set[str] = set()
    uniq = [
        u for u in urls if not (u.split("#")[0] in seen or seen.add(u.split("#")[0]))
    ]
    return sorted(uniq, key=score)


def gather(
    app: App,
    queries: list[str] | None = None,
    extra_urls: list[str] | None = None,
    max_pages: int | None = None,
    refresh: bool = False,
) -> tuple[list[Source], list[str]]:
    """Search + fetch. Returns (sources, errors)."""
    errors: list[str] = []
    urls: list[str] = list(extra_urls or [])
    if app.hint_url:
        urls.append(app.hint_url)
    for q in queries or [t.format(app=app.app) for t in QUERY_TEMPLATES]:
        try:
            urls += [h.url for h in web.search(q, refresh=refresh)]
        except Exception as e:  # noqa: BLE001
            errors.append(f"search '{q}': {e}")
    ranked = [u for u in rank_urls(app, urls) if not web.is_low_trust(u)]
    sources: list[Source] = []
    budget = settings.page_chars
    for u in ranked:
        if len(sources) >= (max_pages or settings.max_pages):
            break
        p = web.fetch(u, refresh=refresh)
        if not p.ok:
            errors.append(
                f"fetch {u}: {p.status} {p.error or 'too little text (JS-rendered?)'}"
            )
            continue
        sources.append(
            Source(
                sid=f"S{len(sources) + 1}",
                url=p.final_url or u,
                title=p.title,
                official=is_official(app, p.final_url or u),
                text=p.text,
                excerpt=web.relevant_excerpt(p.text, budget),
            )
        )
    return sources, errors


RUBRIC = """\
You research SaaS APIs for an integrations team deciding whether an AI-agent toolkit can be built.
Use ONLY the numbered sources provided. Every field needs evidence: the source id and a short
VERBATIM quote (copy-paste, max 200 chars) from that source that supports the value. If the sources
do not support a value, give your best estimate with confidence <= 0.3 and evidence [].

Field definitions (use these exact enum strings):
- one_liner: what the product does, <= 15 words.
- auth_methods: list from OAUTH2 | API_KEY | BEARER_TOKEN (personal access / static token) |
  BASIC (username+password or key as basic-auth) | JWT (signed JWT / service account / client-credentials) |
  OTHER (HMAC request signing, session login, mTLS) | NONE (no auth). Include every method the
  official API accepts for server-side calls.
- access: the FIRST gate a developer hits to make real API calls against their own account:
  SELF_SERVE_FREE (free plan or free developer/sandbox account yields credentials) |
  SELF_SERVE_TRIAL (only during a time-limited free trial) | PAID_PLAN (API needs a paid plan/add-on) |
  APPROVAL (must apply / pass app review / admin approval before any access) |
  PARTNERSHIP (partner program, contact sales, enterprise-only) | NO_PUBLIC_API.
- api_style: list from REST | GRAPHQL | SOAP | SDK_ONLY | CLI | NONE.
- api_breadth: BROAD (most product objects, dozens+ endpoints) | MODERATE (core objects) |
  NARROW (a handful of endpoints) | NONE.
- official_mcp: true if the vendor itself publishes an MCP server (local or remote), else false.
- webhooks: true if the API offers webhooks/event subscriptions.
- sandbox: true if there is a free sandbox / test mode / developer store or account.
- buildability: READY (public API + self-serve credentials) | READY_WITH_CAVEATS (buildable but paid
  plan, app review for production, or narrow API) | NEEDS_OUTREACH (access requires partnership/sales) |
  BLOCKED (no usable public API).
- main_blocker: one short sentence naming the main obstacle to shipping a toolkit, or null if none.
- docs_url: the best official API docs URL among the sources.

Return JSON:
{"one_liner": {"value": ..., "evidence": [{"source": "S1", "quote": "..."}], "confidence": 0.0-1.0},
 ... same shape for every field ...,
 "docs_url": "https://..."}
"""


def format_sources(sources: list[Source]) -> str:
    return "\n\n".join(
        f"[{s.sid}] {s.url} ({'official' if s.official else 'third-party'})\n{s.excerpt}"
        for s in sources
    )


def claims_from_json(
    data: dict, sources: list[Source], fields=CLAIM_FIELDS
) -> dict[str, Claim]:
    by_id = {s.sid: s for s in sources}
    out: dict[str, Claim] = {}
    for f in fields:
        raw = data.get(f)
        if not isinstance(raw, dict):
            raw = {"value": raw, "evidence": [], "confidence": 0.2}
        ev = []
        for e in raw.get("evidence") or []:
            if not isinstance(e, dict):
                continue
            src = by_id.get(str(e.get("source", "")).strip("[]"))
            ev.append(
                Evidence(
                    url=src.url if src else str(e.get("source", "")),
                    quote=str(e.get("quote", ""))[:400],
                )
            )
        try:
            conf = float(raw.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.5
        out[f] = Claim(
            value=normalize(f, raw.get("value")), evidence=ev, confidence=conf
        )
    return out


# keys are upper-cased with every non-alphanumeric stripped
_ENUM_ALIASES = {
    "OAUTH": "OAUTH2",
    "OAUTH2": "OAUTH2",
    "OAUTH20": "OAUTH2",
    "APIKEY": "API_KEY",
    "APITOKEN": "API_KEY",
    "APIKEYS": "API_KEY",
    "BEARERTOKEN": "BEARER_TOKEN",
    "TOKEN": "BEARER_TOKEN",
    "PAT": "BEARER_TOKEN",
    "PERSONALACCESSTOKEN": "BEARER_TOKEN",
    "ACCESSTOKEN": "BEARER_TOKEN",
    "BASIC": "BASIC",
    "BASICAUTH": "BASIC",
    "JWT": "JWT",
    "SERVICEACCOUNT": "JWT",
    "CLIENTCREDENTIALS": "JWT",
    "HMAC": "OTHER",
    "OTHER": "OTHER",
    "NONE": "NONE",
    "REST": "REST",
    "GRAPHQL": "GRAPHQL",
    "SOAP": "SOAP",
    "SDKONLY": "SDK_ONLY",
    "CLI": "CLI",
}


def normalize(field: str, v):
    if field in ("auth_methods", "api_style"):
        items = v if isinstance(v, list) else ([v] if v else [])
        out = []
        for i in items:
            raw = str(i).strip().upper()
            k = _ENUM_ALIASES.get(
                re.sub(r"[^A-Z0-9]", "", raw), re.sub(r"[\s-]+", "_", raw)
            )
            if k not in out:
                out.append(k)
        return sorted(out)
    if field in ("access", "api_breadth", "buildability") and isinstance(v, str):
        return re.sub(r"[\s-]+", "_", v.strip().upper())
    if field in ("official_mcp", "webhooks", "sandbox"):
        if isinstance(v, str):
            return v.strip().lower() in ("true", "yes", "1")
        return bool(v) if v is not None else None
    if (
        field == "main_blocker"
        and isinstance(v, str)
        and v.strip().lower() in ("", "none", "null", "n/a")
    ):
        return None
    return v


def extract(app: App, sources: list[Source]) -> tuple[dict[str, Claim], str | None]:
    user = (
        f"App: {app.app}\nCategory: {app.category}\nHint: {app.hint}\n\n"
        f"SOURCES:\n{format_sources(sources) if sources else '(no sources could be fetched)'}"
    )
    data = chat_json(settings.extract_model, RUBRIC, user)
    return claims_from_json(data, sources), data.get("docs_url")


def research_app(app: App, refresh: bool = False) -> tuple[AppRecord, list[Source]]:
    from .catalog import lookup

    rec = AppRecord(id=app.id, app=app.app, category=app.category, hint=app.hint)
    rec.composio = lookup(app)
    sources, errors = gather(app, refresh=refresh)
    rec.errors += errors
    rec.sources = [s.url for s in sources]
    claims, docs_url = extract(app, sources)
    for f, c in claims.items():
        setattr(rec, f, c)
    rec.docs_url = docs_url
    return rec, sources
