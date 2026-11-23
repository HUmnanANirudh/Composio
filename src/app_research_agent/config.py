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


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    return v if v not in (None, "") else default


@dataclass
class Settings:
    composio_api_key: str | None = field(
        default_factory=lambda: _env("COMPOSIO_API_KEY")
    )
    groq_api_key: str | None = field(default_factory=lambda: _env("GROQ_API_KEY"))
    extract_model: str = field(
        default_factory=lambda: _env("EXTRACT_MODEL", "openai/gpt-oss-120b")
    )
    critic_model: str = field(
        default_factory=lambda: _env("CRITIC_MODEL", "moonshotai/kimi-k2-instruct")
    )
    search_model: str = field(
        default_factory=lambda: _env("SEARCH_MODEL", "groq/compound-mini")
    )
    search_backend: str = field(
        default_factory=lambda: _env("SEARCH_BACKEND", "auto")
    )  # auto | composio | groq | none
    concurrency: int = field(default_factory=lambda: int(_env("CONCURRENCY", "3")))
    composio_user_id: str = field(
        default_factory=lambda: _env("COMPOSIO_USER_ID", "research-agent")
    )
    max_pages: int = field(default_factory=lambda: int(_env("MAX_PAGES", "6")))
    page_chars: int = field(default_factory=lambda: int(_env("PAGE_CHARS", "3500")))

    def resolved_search_backend(self) -> str:
        if self.search_backend != "auto":
            return self.search_backend
        if self.composio_api_key:
            return "composio"
        if self.groq_api_key:
            return "groq"
        return "none"


settings = Settings()
