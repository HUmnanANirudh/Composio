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
    composio_api_key: str | None = field(default_factory=lambda: _env("COMPOSIO_API_KEY"))
    groq_api_key: str | None = field(default_factory=lambda: _env("GROQ_API_KEY"))
    extract_model: str = field(default_factory=lambda: _env("EXTRACT_MODEL", "openai/gpt-oss-120b"))
    critic_model: str = field(default_factory=lambda: _env("CRITIC_MODEL", "qwen/qwen3.8-27b"))
    # Spread across models: each has its own 200K tokens/day on Groq's free tier.
    reresearch_model: str = field(
        default_factory=lambda: _env("RERESEARCH_MODEL", "openai/gpt-oss-20b")
    )
    critic_rounds: int = field(default_factory=lambda: int(_env("CRITIC_ROUNDS", "1")))
    search_model: str = field(default_factory=lambda: _env("SEARCH_MODEL", "openai/gpt-oss-20b"))
    search_backend: str = field(
        default_factory=lambda: _env("SEARCH_BACKEND", "auto")
    )  # auto | composio | groq | none
    search_toolkit: str = field(default_factory=lambda: _env("SEARCH_TOOLKIT", "exa"))
    # wait out a daily-quota 429 if Groq says tokens free up within this many seconds
    max_daily_wait: int = field(default_factory=lambda: int(_env("MAX_DAILY_WAIT", "1800")))
    concurrency: int = field(default_factory=lambda: int(_env("CONCURRENCY", "3")))
    composio_user_id: str = field(
        default_factory=lambda: _env("COMPOSIO_USER_ID", "research-agent")
    )
    # Sized for Groq's free tier (8K tokens/min/model): ~6 pages x 2.2K chars ~= 3.5K tokens in.
    max_pages: int = field(default_factory=lambda: int(_env("MAX_PAGES", "6")))
    page_chars: int = field(default_factory=lambda: int(_env("PAGE_CHARS", "2000")))
    max_output_tokens: int = field(default_factory=lambda: int(_env("MAX_OUTPUT_TOKENS", "2500")))
    tpm_limit: int = field(default_factory=lambda: int(_env("TPM_LIMIT", "8000")))

    def resolved_search_backend(self) -> str:
        """auto -> Composio (Exa connected through a Composio session) when a key is set.

        If no Composio search tool is connected, web.search() degrades to 'none' and the
        agent relies on the docs crawler. 'groq' (browser_search) is opt-in only: each
        query pulls ~35K tokens of results into context, too much for the free tier.
        """
        if self.search_backend != "auto":
            return self.search_backend
        return "composio" if self.composio_api_key else "none"


settings = Settings()
