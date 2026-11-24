from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
CACHE = DATA / "cache"
RESULTS = DATA / "results"

load_dotenv(ROOT / ".env")


def atomic_write(path: Path, text: str) -> None:
    """Write via a temp file + rename, so a killed run never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    return v if v not in (None, "") else default


@dataclass
class Settings:
    composio_api_key: str | None = field(default_factory=lambda: _env("COMPOSIO_API_KEY"))
    gemini_api_key: str | None = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    # Each role is a fallback chain (comma-separated), newest first. A model whose quota is
    # used up (or that is capped at 0 on this key's tier) is retired for the run and the
    # next one takes over; an overloaded model (503) is skipped for a few minutes.
    extract_model: str = field(
        default_factory=lambda: _env(
            "EXTRACT_MODEL",
            "gemini-4-hydrogen,gemini-3.8-flash,gemini-3.6-flash,gemini-flash-latest,"
            "gemini-3.5-flash-lite",
        )
    )
    reresearch_model: str = field(
        default_factory=lambda: _env(
            "RERESEARCH_MODEL",
            "gemini-3.6-flash,gemini-4-hydrogen,gemini-3.8-flash,gemini-flash-latest,"
            "gemini-3.5-flash-lite",
        )
    )
    thinking_level: str = field(default_factory=lambda: _env("THINKING_LEVEL", "medium"))
    search_backend: str = field(
        default_factory=lambda: _env("SEARCH_BACKEND", "auto")
    )  # auto | composio | none
    search_toolkit: str = field(default_factory=lambda: _env("SEARCH_TOOLKIT", "exa"))
    concurrency: int = field(default_factory=lambda: int(_env("CONCURRENCY", "3")))
    composio_user_id: str = field(
        default_factory=lambda: _env("COMPOSIO_USER_ID", "research-agent")
    )
    # evidence budget per app: 8 pages x 5K chars of the most relevant paragraphs
    max_pages: int = field(default_factory=lambda: int(_env("MAX_PAGES", "8")))
    page_chars: int = field(default_factory=lambda: int(_env("PAGE_CHARS", "5000")))
    # Gemini counts thinking tokens against the output limit
    max_output_tokens: int = field(default_factory=lambda: int(_env("MAX_OUTPUT_TOKENS", "16384")))

    def resolved_search_backend(self) -> str:
        """auto -> Composio (Exa connected through a Composio session) when a key is set.

        If no Composio search tool is connected, web.search() degrades to 'none' and the
        agent relies on the docs crawler.
        """
        if self.search_backend != "auto":
            return self.search_backend
        return "composio" if self.composio_api_key else "none"


settings = Settings()
