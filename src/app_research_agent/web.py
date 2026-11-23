"""Web layer: search + fetch, with an on-disk cache so runs are reproducible.

Search backends (SEARCH_BACKEND):
  composio  -> Composio Search toolkit executed through the Composio SDK
  groq      -> Groq compound model with built-in web search
  none      -> no search; only seed URLs (the app's hint URL)

Fetching is plain HTTP + trafilatura text extraction. Every fetched page is
cached under data/cache/pages so the verifier re-checks quotes against the
exact text the extractor saw, and so a second run costs nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura

from .config import CACHE, atomic_write, settings

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36"
)
PAGES = CACHE / "pages"
SEARCHES = CACHE / "search"

# Third-party aggregators we never accept as primary evidence.
LOW_TRUST = (
    "g2.com",
    "capterra.com",
    "getapp.com",
    "softwareadvice.com",
    "trustradius.com",
    "zapier.com",
    "make.com",
    "pipedream.com",
    "n8n.io",
    "rapidapi.com",
    "medium.com",
    "reddit.com",
    "youtube.com",
    "linkedin.com/pulse",
    "stackshare.io",
    "apitracker.io",
)


@dataclass
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""
    backend: str = ""


@dataclass
class Page:
    url: str
    final_url: str = ""
    status: int = 0
    title: str = ""
    text: str = ""
    error: str | None = None
    fetched_at: float = field(default_factory=time.time)
    links: list[list[str]] = field(default_factory=list)  # [[absolute_url, anchor_text], ...]

    @property
    def ok(self) -> bool:
        return self.status == 200 and len(self.text) > 200


def _key(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:24]


def registrable(host: str) -> str:
    parts = host.lower().split(":")[0].split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def canonical(url: str) -> str:
    """Drop fragments and a trailing slash so one page is one URL."""
    u = url.split("#")[0]
    return (
        u[:-1]
        if u.endswith("/") and u.count("/") > 3
        else u.rstrip("/")
        if u.count("/") == 3
        else u
    )


_SKIP = re.compile(
    r"accounts\.|/signin|/sign-in|/login|/signup|/sign-up|/register\?|/oauth/authorize\?"
)


def is_low_trust(url: str) -> bool:
    """Aggregators, community posts and login walls: never used as evidence."""
    return any(d in url for d in LOW_TRUST) or bool(_SKIP.search(url))


# --------------------------------------------------------------------------- fetch


def _extract_text(html: str, url: str) -> tuple[str, str]:
    title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    title = re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else ""
    text = trafilatura.extract(
        html, url=url, include_tables=True, include_links=False, favor_recall=True
    )
    if not text:
        text = re.sub(
            r"<script.*?</script>|<style.*?</style>",
            " ",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        text = re.sub(r"<[^>]+>", " ", text)
    return title, re.sub(r"[ \t]+", " ", text).strip()


_HREF = re.compile(r"<a\s[^>]*?href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_MDLINK = re.compile(r"\[([^\]]{1,200})\]\((https?://[^)\s]+|/[^)\s]*)\)")


def extract_links(body: str, base: str, is_html: bool) -> list[list[str]]:
    """Absolute links (with anchor text) from an HTML page or a markdown/llms.txt file."""
    out: list[list[str]] = []
    if is_html:
        pairs = [(h, re.sub(r"<[^>]+>|\s+", " ", t).strip()) for h, t in _HREF.findall(body)]
    else:
        pairs = [(h, t) for t, h in _MDLINK.findall(body)]
    seen: set[str] = set()
    for href, text in pairs:
        if href.startswith(("mailto:", "javascript:", "tel:")):
            continue
        u = canonical(urljoin(base, href))
        if u.startswith("http") and u not in seen:
            seen.add(u)
            out.append([u, text[:120]])
    return out[:600]


def fetch(url: str, refresh: bool = False) -> Page:
    PAGES.mkdir(parents=True, exist_ok=True)
    path = PAGES / f"{_key(url)}.json"
    if path.exists() and not refresh:
        cached = json.loads(path.read_text())
        if "links" in cached:  # entries from before link capture are refetched once
            return Page(**cached)
    page = Page(url=url)
    try:
        r = httpx.get(
            url,
            headers={"User-Agent": UA, "Accept": "text/html,text/markdown,*/*"},
            timeout=25,
            follow_redirects=True,
        )
        page.status, page.final_url = r.status_code, str(r.url)
        ctype = r.headers.get("content-type", "")
        if "html" in ctype:
            page.title, page.text = _extract_text(r.text, page.final_url)
            page.links = extract_links(r.text, page.final_url, is_html=True)
        elif ctype.startswith("text/") or "json" in ctype or "markdown" in ctype:
            page.text = r.text
            page.links = extract_links(r.text, page.final_url, is_html=False)
        else:
            page.error = f"unsupported content-type {ctype}"
    except httpx.HTTPError as e:
        page.error = f"{type(e).__name__}: {e}"
        return page  # transient network errors are not cached: the next run retries
    page.text = page.text[:200_000]
    if page.status == 429 or page.status >= 500:
        return page  # rate-limited / server error: retry on the next run instead of caching
    atomic_write(path, json.dumps(asdict(page)))
    return page


# --------------------------------------------------------------------------- search


def _walk_hits(obj: Any, backend: str) -> list[SearchHit]:
    """Pull {url,title,snippet} out of an arbitrary tool response."""
    hits: list[SearchHit] = []

    def visit(o: Any) -> None:
        if isinstance(o, dict):
            url = o.get("url") or o.get("link") or o.get("href")
            if isinstance(url, str) and url.startswith("http"):
                hits.append(
                    SearchHit(
                        url=url,
                        title=str(o.get("title") or o.get("name") or ""),
                        snippet=str(
                            o.get("snippet") or o.get("description") or o.get("content") or ""
                        )[:400],
                        backend=backend,
                    )
                )
            for v in o.values():
                visit(v)
        elif isinstance(o, list):
            for v in o:
                visit(v)
        elif isinstance(o, str) and o.strip().startswith(("{", "[")):
            try:
                visit(json.loads(o))
            except ValueError:
                pass

    visit(obj)
    seen: set[str] = set()
    return [h for h in hits if not (h.url in seen or seen.add(h.url))]


class ComposioSearch:
    """Web search through a Composio session (Platform SDK), scoped to one search toolkit.

    Composio's own COMPOSIO_SEARCH toolkit is retired (410 ToolRouterV2_ToolkitRetired,
    observed 2026-11-23), so search runs on a toolkit the project connects (Exa by default,
    SEARCH_TOOLKIT to change). Per the Composio skill, the tool slug and its arguments are
    read from the live tool schema rather than hard-coded.
    """

    def __init__(self) -> None:
        from composio import Composio

        tk = settings.search_toolkit
        composio = Composio()
        self.session = composio.create(user_id=settings.composio_user_id, toolkits=[tk])
        state = self.session.toolkits(toolkits=[tk]).items
        if not state or not state[0].connection or not state[0].connection.is_active:
            link = self.session.authorize(tk).redirect_url
            raise RuntimeError(
                f"{tk} is not connected for this Composio user; connect it at {link}"
            )
        tools = composio.tools.get_raw_composio_tools(toolkits=[tk], limit=200)

        def required(t) -> set[str]:
            return set((t.input_parameters or {}).get("required") or [])

        # prefer <TK>_SEARCH; otherwise any *_SEARCH tool whose only required arg is `query`
        exact = [t for t in tools if t.slug == f"{tk.upper()}_SEARCH"]
        loose = [t for t in tools if t.slug.endswith("_SEARCH") and required(t) == {"query"}]
        cands = exact + loose
        if not cands:
            raise RuntimeError(f"no search tool found in toolkit {tk}")
        self.tool = cands[0]
        self.props = set((self.tool.input_parameters or {}).get("properties", {}))

    def search(self, query: str) -> list[SearchHit]:
        args: dict[str, Any] = {"query": query}
        if "numResults" in self.props:
            args["numResults"] = 8
        r = self.session.execute(self.tool.slug, arguments=args)
        if r.result_type != "completed":
            raise RuntimeError(f"{self.tool.slug}: {r.result_type} {r.error}")
        return _walk_hits(r.data, f"composio:{self.tool.slug}")


class GroqSearch:
    """Web search via Groq's built-in browser_search tool on a gpt-oss model."""

    def __init__(self) -> None:
        from groq import Groq

        self.client = Groq(api_key=settings.groq_api_key, max_retries=4)

    def search(self, query: str) -> list[SearchHit]:
        from .llm import record_usage

        r = self.client.chat.completions.create(
            model=settings.search_model,
            messages=[{"role": "user", "content": f"Search the web for: {query}"}],
            tools=[{"type": "browser_search"}],
            tool_choice="required",
            max_completion_tokens=600,
            temperature=0,
        )
        record_usage(settings.search_model, "search", r.usage)
        hits: list[SearchHit] = []
        for t in r.choices[0].message.executed_tools or []:
            res = getattr(t, "search_results", None)
            for item in getattr(res, "results", None) or []:
                if item.url:
                    hits.append(SearchHit(url=item.url, title=item.title or "", backend="groq"))
        seen: set[str] = set()
        return [h for h in hits if not (h.url in seen or seen.add(h.url))]


_backend_cache: dict[str, Any] = {}
_search_lock = threading.Lock()
_backend_errors: dict[str, str] = {}


def search(query: str, refresh: bool = False) -> list[SearchHit]:
    backend = settings.resolved_search_backend()
    if backend == "none":
        return []
    SEARCHES.mkdir(parents=True, exist_ok=True)
    path = SEARCHES / f"{backend}-{_key(query)}.json"
    if path.exists() and not refresh:
        return [SearchHit(**h) for h in json.loads(path.read_text())]
    if backend not in _backend_cache:
        try:
            _backend_cache[backend] = ComposioSearch() if backend == "composio" else GroqSearch()
        except Exception as e:  # noqa: BLE001 - no search tool connected: crawler-only mode
            _backend_cache[backend] = None
            _backend_errors[backend] = str(e)
    if _backend_cache[backend] is None:
        raise RuntimeError(f"search backend '{backend}' unavailable: {_backend_errors[backend]}")
    # one search at a time keeps us inside per-minute rate limits
    with _search_lock:
        hits = _backend_cache[backend].search(query)
    atomic_write(path, json.dumps([asdict(h) for h in hits]))
    return hits


# --------------------------------------------------------------------------- snippets

KEYWORDS = (
    "oauth",
    "api key",
    "api token",
    "access token",
    "personal access",
    "bearer",
    "basic auth",
    "authenticat",
    "authoriz",
    "client id",
    "client secret",
    "jwt",
    "service account",
    "pricing",
    "plan",
    "paid",
    "free",
    "trial",
    "sandbox",
    "developer account",
    "test mode",
    "partner",
    "approval",
    "approved",
    "review",
    "apply",
    "contact sales",
    "enterprise",
    "mcp",
    "model context protocol",
    "webhook",
    "graphql",
    "rest",
    "endpoint",
    "rate limit",
)


def relevant_excerpt(text: str, budget: int) -> str:
    """Keep the paragraphs that mention auth/access/API keywords, up to `budget` chars."""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=#)", text) if p.strip()]
    if sum(len(p) for p in paras) <= budget:
        return "\n\n".join(paras)
    scored = []
    for i, p in enumerate(paras):
        low = p.lower()
        score = sum(low.count(k) for k in KEYWORDS)
        scored.append((score, i, p[:1200]))
    keep = sorted(scored, key=lambda t: (-t[0], t[1]))
    out, used = [], 0
    for score, i, p in keep:
        if score == 0 and used > budget * 0.5:
            break
        if used + len(p) > budget:
            continue
        out.append((i, p))
        used += len(p) + 2
    if not out:  # every paragraph was larger than the budget
        return text[:budget]
    return "\n\n".join(p for _, p in sorted(out))


def host(url: str) -> str:
    return urlparse(url).netloc.lower()
