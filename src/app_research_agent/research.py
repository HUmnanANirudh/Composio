"""Pass 1: gather sources for an app and extract a structured, cited record."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse

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


CODE_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "npmjs.com", "pypi.org")


def _app_tokens(app: App) -> list[str]:
    base = re.sub(r"\(.*?\)", "", app.app).lower()
    return [t for t in re.findall(r"[a-z0-9]+", base) if len(t) >= 4] or [
        re.sub(r"[^a-z0-9]", "", base)
    ]


def _is_code_host(app: App) -> bool:
    """The hint is a repository on a code host (not, say, docs.github.com)."""
    h = web.host(app.hint_url) if app.hint_url else ""
    return h.removeprefix("www.") in CODE_HOSTS


@lru_cache(maxsize=256)
def official_domains(app: App) -> frozenset[str]:
    """Registrable domains that count as the vendor's own: the hint's, and where it redirects."""
    doms = set()
    if app.hint_url:
        doms.add(web.registrable(web.host(app.hint_url)))
        p = web.fetch(app.hint_url)
        if p.final_url:
            doms.add(web.registrable(web.host(p.final_url)))
    # a repo hint makes the code host a shared platform, not the vendor's domain
    return frozenset(doms - set(CODE_HOSTS)) if _is_code_host(app) else frozenset(doms)


def is_official(app: App, url: str) -> bool:
    if _is_code_host(app):  # an open-source project: only its own repo/org is official
        owner = "/".join(app.hint_url.split("/")[3:4])
        h = web.host(url)
        if web.registrable(h) in CODE_HOSTS or h.endswith("githubusercontent.com"):
            return f"/{owner}/" in url + "/"
        return False  # other sites sharing the project's name are not the project
    h = web.host(url)
    if web.registrable(h) in official_domains(app):
        return True
    if web.registrable(h) in ("github.com", "gitlab.com"):  # the vendor's own org, e.g. /gorgias/
        owner = (urlparse(url).path.strip("/").split("/") or [""])[0].lower()
        labels = {d.split(".")[0] for d in official_domains(app)}
        return bool(owner) and (owner in labels or any(t in owner for t in _app_tokens(app)))
    reg = web.registrable(h).split(".")[0]
    return any(t in reg for t in _app_tokens(app))


def _page_key(u: str) -> str:
    """X and X.md are the same docs page (many doc sites serve both)."""
    return re.sub(r"\.md$", "", web.canonical(u))


def rank_urls(app: App, urls: list[str]) -> list[str]:
    hint_path = urlparse(app.hint_url or "").path.rstrip("/")

    def score(u: str) -> tuple:
        off = is_official(app, u)
        in_scope = bool(hint_path) and urlparse(u).path.startswith(hint_path)
        # stable sort: within a tier, gather()'s budget order (hint, topic picks, search) holds
        return (not off, web.is_low_trust(u), not in_scope)

    seen: set[str] = set()
    uniq = [
        u for u in map(web.canonical, urls) if not (_page_key(u) in seen or seen.add(_page_key(u)))
    ]
    return sorted(uniq, key=score)


def interleave(app: App, lanes: list[list[str]], fallback: list[str]) -> list[str]:
    """Round-robin the official pages of each lane; then off-site pages, then index pages."""
    tiers = [rank_urls(app, [u for u in lane if not web.is_low_trust(u)]) for lane in lanes]
    official = [[u for u in t if is_official(app, u)] for t in tiers]
    out: list[str] = []
    for i in range(max((len(t) for t in official), default=0)):
        out += [t[i] for t in official if i < len(t)]
    out += [u for t in tiers for u in t if not is_official(app, u)]
    out += fallback
    seen: set[str] = set()
    return [u for u in out if not (_page_key(u) in seen or seen.add(_page_key(u)))]


# Link topics the crawler tries to cover, each with URL/anchor keywords.
TOPICS = {  # surface first: the search queries already cover auth, access and MCP
    "surface": r"api[-_ ]?reference|/reference|/api/?$|endpoints|rest[-_ ]api|graphql|webhook",
    "auth": r"auth|oauth|api[-_ ]?key|apikey|access[-_ ]token|credential|personal[-_ ]access",
    "access": r"pricing|plans?\b|billing|partner|developer[-_ ]?(program|account|portal)|sandbox|"
    r"get[-_ ]?started|getting[-_ ]started|quickstart|apply|request[-_ ]access|"
    r"dev[-_ ]?environment|test[-_ ]store|developer[-_ ]store|limits|free",
    "mcp": r"\bmcp\b|model[-_ ]context|/mcp|ai[-_ ]?(agents?|tools)",
}


def docs_seeds(app: App) -> list[str]:
    """The hint URL, plus the usual developer-docs hosts and their llms.txt."""
    seeds: list[str] = []
    if app.hint_url:
        seeds.append(app.hint_url)
        if _is_code_host(app):  # an open-source repo: the repo is the documentation
            return seeds
        reg = web.registrable(web.host(app.hint_url))
        hosts = [web.host(app.hint_url)]
        bare = urlparse(app.hint_url).path.strip("/") == ""
        if bare:  # a marketing domain: also probe the usual developer-docs hosts
            hosts += [f"{p}.{reg}" for p in ("developers", "developer", "docs")]
        for h in dict.fromkeys(hosts):
            seeds += [f"https://{h}/llms.txt"]
            if h != web.host(app.hint_url):
                seeds.append(f"https://{h}")
    return seeds


def crawl_candidates(app: App, pages: list[web.Page], per_topic: int = 2) -> list[str]:
    """Pick the most promising official links for each topic from already-fetched pages."""
    links: dict[str, str] = {}
    for p in pages:
        for u, text in p.links:
            if is_official(app, u) and not web.is_low_trust(u):
                links.setdefault(u, text)
    ranked: list[list[str]] = []
    for rx in TOPICS.values():
        scored = []
        for u, text in links.items():
            hay = f"{urlparse(u).path} {text}".lower()
            hits = len(re.findall(rx, hay))
            if re.search(r"changelog|release[-_ ]notes|/blog|/news|careers|/legal|/terms", u):
                continue
            if hits:
                docsy = bool(re.search(r"developer|docs|api", u))
                scored.append((hits + docsy, -len(u), u))
        ranked.append([u for *_, u in sorted(scored, reverse=True)])
    # round-robin: every topic's best link before any topic's second, so one topic
    # (e.g. two OAuth pages) can't use up the page budget
    picked: list[str] = []
    for rank in range(per_topic):
        for topic in ranked:
            fresh = [u for u in topic if u not in picked]
            if fresh:
                picked.append(fresh[0])
    return picked


def gather(
    app: App,
    queries: list[str] | None = None,
    extra_urls: list[str] | None = None,
    max_pages: int | None = None,
    refresh: bool = False,
) -> tuple[list[Source], list[str]]:
    """Discover + fetch sources. Returns (sources, errors).

    1. crawl: hint URL, developer-docs hosts and llms.txt, then their auth/pricing/MCP links
    2. search: supplementary web search (Exa through a Composio session) for what the docs don't link
    """
    errors: list[str] = []
    limit = max_pages or settings.max_pages
    seed_pages = [p for u in docs_seeds(app) if (p := web.fetch(u, refresh=refresh)).ok]
    seeds = [p.final_url or p.url for p in seed_pages if is_official(app, p.final_url or p.url)]
    # Lanes: one per search query (auth / access / MCP), plus the crawler's topic picks
    # (which also cover the API reference). The page budget is filled round-robin across
    # lanes, so every topic gets a page before any topic gets a second one.
    # for an open-source repo the repo page is the documentation: it goes first
    lanes: list[list[str]] = [list(extra_urls or []) + (seeds[:1] if _is_code_host(app) else [])]
    for q in queries or [t.format(app=app.app) for t in QUERY_TEMPLATES]:
        try:
            lanes.append([h.url for h in web.search(q, refresh=refresh)])
        except Exception as e:  # noqa: BLE001 - search is supplementary
            errors.append(f"search '{q}': {str(e)[:200]}")
    lanes.append(crawl_candidates(app, seed_pages, per_topic=3))
    ranked = interleave(app, lanes, fallback=seeds)
    sources: list[Source] = []
    seen_final: set[str] = set()
    for u in ranked:
        if len(sources) >= limit:
            break
        p = web.fetch(u, refresh=refresh)
        final = web.canonical(p.final_url or u)
        if not p.ok:
            errors.append(f"fetch {u}: {p.status} {p.error or 'too little text (JS-rendered?)'}")
            continue
        if _page_key(final) in seen_final:
            continue
        seen_final.add(_page_key(final))
        sources.append(
            Source(
                sid=f"S{len(sources) + 1}",
                url=final,
                title=p.title,
                official=is_official(app, final),
                text=p.text,
                excerpt=web.relevant_excerpt(p.text, settings.page_chars),
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


def claims_from_json(data: dict, sources: list[Source], fields=CLAIM_FIELDS) -> dict[str, Claim]:
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
        out[f] = Claim(value=normalize(f, raw.get("value")), evidence=ev, confidence=conf)
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
            k = _ENUM_ALIASES.get(re.sub(r"[^A-Z0-9]", "", raw), re.sub(r"[\s-]+", "_", raw))
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
