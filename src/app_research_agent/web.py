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
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
import trafilatura

from .config import CACHE, settings

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

    @property
    def ok(self) -> bool:
        return self.status == 200 and len(self.text) > 200


def _key(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:24]


def registrable(host: str) -> str:
    parts = host.lower().split(":")[0].split(".")
    if (
        len(parts) >= 3
        and parts[-2] in ("co", "com", "org", "net")
        and len(parts[-1]) == 2
    ):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_low_trust(url: str) -> bool:
    return any(d in url for d in LOW_TRUST)


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


def fetch(url: str, refresh: bool = False) -> Page:
    PAGES.mkdir(parents=True, exist_ok=True)
    path = PAGES / f"{_key(url)}.json"
    if path.exists() and not refresh:
        return Page(**json.loads(path.read_text()))
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
        elif ctype.startswith("text/") or "json" in ctype or "markdown" in ctype:
            page.text = r.text
        else:
            page.error = f"unsupported content-type {ctype}"
    except httpx.HTTPError as e:
        page.error = f"{type(e).__name__}: {e}"
    page.text = page.text[:200_000]
    path.write_text(json.dumps(asdict(page)))
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
                            o.get("snippet")
                            or o.get("description")
                            or o.get("content")
                            or ""
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
    """Web search via Composio's Search toolkit (no connected account needed)."""

    CANDIDATE_SLUGS = (
        "COMPOSIO_SEARCH_WEB",
        "COMPOSIO_SEARCH_SEARCH",
        "COMPOSIO_SEARCH_DUCK_DUCK_GO_SEARCH",
        "COMPOSIO_SEARCH_EXA_SEARCH",
        "COMPOSIO_SEARCH_TAVILY_SEARCH",
    )

    def __init__(self) -> None:
        from composio import Composio

        self.client = Composio(api_key=settings.composio_api_key)
        self.slug: str | None = None

    def _execute(self, slug: str, args: dict) -> Any:
        res = self.client.tools.execute(
            slug,
            args,
            user_id=settings.composio_user_id,
            dangerously_skip_version_check=True,
        )
        data = (
            res if isinstance(res, dict) else getattr(res, "model_dump", lambda: res)()
        )
        if isinstance(data, dict) and data.get("successful") is False:
            raise RuntimeError(data.get("error") or "tool failed")
        return data

    def search(self, query: str) -> list[SearchHit]:
        slugs = [self.slug] if self.slug else list(self.CANDIDATE_SLUGS)
        last: Exception | None = None
        for slug in slugs:
            try:
                data = self._execute(slug, {"query": query})
                self.slug = slug
                return _walk_hits(data, f"composio:{slug}")
            except Exception as e:  # noqa: BLE001 - try next slug
                last = e
        raise RuntimeError(f"no Composio search tool worked: {last}")


class GroqSearch:
    """Web search via a Groq compound model's built-in search tool."""

    def __init__(self) -> None:
        from groq import Groq

        self.client = Groq(api_key=settings.groq_api_key)

    def search(self, query: str) -> list[SearchHit]:
        r = self.client.chat.completions.create(
            model=settings.search_model,
            messages=[
                {
                    "role": "user",
                    "content": f"Search the web for: {query}\n"
                    "Reply only with a JSON list of the top 8 results as "
                    '[{"url":..., "title":..., "snippet":...}]. Prefer official vendor docs.',
                }
            ],
            temperature=0,
        )
        msg = r.choices[0].message
        hits = _walk_hits(getattr(msg, "executed_tools", None) or [], "groq")
        m = re.search(r"\[.*\]", msg.content or "", re.DOTALL)
        if m:
            hits += _walk_hits(m.group(0), "groq")
        seen: set[str] = set()
        return [h for h in hits if not (h.url in seen or seen.add(h.url))]


_backend_cache: dict[str, Any] = {}


def search(query: str, refresh: bool = False) -> list[SearchHit]:
    backend = settings.resolved_search_backend()
    if backend == "none":
        return []
    SEARCHES.mkdir(parents=True, exist_ok=True)
    path = SEARCHES / f"{backend}-{_key(query)}.json"
    if path.exists() and not refresh:
        return [SearchHit(**h) for h in json.loads(path.read_text())]
    if backend not in _backend_cache:
        _backend_cache[backend] = (
            ComposioSearch() if backend == "composio" else GroqSearch()
        )
    hits = _backend_cache[backend].search(query)
    path.write_text(json.dumps([asdict(h) for h in hits]))
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
