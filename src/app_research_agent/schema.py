"""Data model for one researched app.

Every researched field is a `Claim`: a value plus the evidence behind it
(source URL + a verbatim quote from that page). The verifier checks the quote
really appears on the page; a claim without grounded evidence is downgraded.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

CLAIM_FIELDS = (
    "one_liner",
    "auth_methods",
    "access",
    "api_style",
    "api_breadth",
    "official_mcp",
    "webhooks",
    "sandbox",
    "buildability",
    "main_blocker",
)

# Fields whose correctness we score in evaluation (exact/set comparison).
SCORED_FIELDS = (
    "auth_methods",
    "access",
    "api_style",
    "api_breadth",
    "official_mcp",
    "buildability",
)


class Evidence(BaseModel):
    url: str = ""
    quote: str = ""
    grounded: bool | None = None  # set by verifier: quote found on the page?
    match_score: float | None = None


class Claim(BaseModel):
    value: Any = None
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = 0.5
    flags: list[str] = Field(default_factory=list)  # verifier notes


class ComposioCatalogEntry(BaseModel):
    """Ground-truth-ish signal from Composio's public toolkit catalog."""

    found: bool = False
    slug: str | None = None
    name: str | None = None
    tools: int | None = None
    triggers: int | None = None
    auth: list[str] = Field(default_factory=list)  # raw Composio auth labels
    managed_app: str | None = None
    url: str | None = None
    mcp_variant: str | None = None  # e.g. PYLON_MCP: Composio proxy of the vendor's remote MCP
    mcp_variant_auth: list[str] = Field(default_factory=list)


class AppRecord(BaseModel):
    id: int
    app: str
    category: str
    hint: str
    docs_url: str | None = None

    one_liner: Claim = Field(default_factory=Claim)
    auth_methods: Claim = Field(default_factory=Claim)  # enum values: research.RUBRIC
    access: Claim = Field(default_factory=Claim)
    api_style: Claim = Field(default_factory=Claim)  # list[REST|GRAPHQL|SOAP|SDK|CLI|NONE]
    api_breadth: Claim = Field(default_factory=Claim)
    official_mcp: Claim = Field(default_factory=Claim)  # bool
    webhooks: Claim = Field(default_factory=Claim)  # bool
    sandbox: Claim = Field(default_factory=Claim)  # bool (free sandbox / dev store / test mode)
    buildability: Claim = Field(default_factory=Claim)
    main_blocker: Claim = Field(default_factory=Claim)  # str | None

    composio: ComposioCatalogEntry = Field(default_factory=ComposioCatalogEntry)
    sources: list[str] = Field(default_factory=list)
    pass_name: str = "pass1"
    verification: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)

    def claims(self) -> dict[str, Claim]:
        return {f: getattr(self, f) for f in CLAIM_FIELDS}
