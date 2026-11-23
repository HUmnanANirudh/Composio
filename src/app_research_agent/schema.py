"""Data model for one researched app.

Every researched field is a `Claim`: a value plus the evidence behind it
(source URL + a verbatim quote from that page). The verifier checks the quote
really appears on the page; a claim without grounded evidence is downgraded.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Auth(StrEnum):
    OAUTH2 = "OAUTH2"
    API_KEY = "API_KEY"
    BEARER_TOKEN = "BEARER_TOKEN"  # personal access / static bearer token
    BASIC = "BASIC"
    JWT = "JWT"  # signed JWT / service account / client-credentials style
    OTHER = "OTHER"  # HMAC signing, session tokens, mTLS, ...
    NONE = "NONE"  # no auth (public API, local CLI)


class Access(StrEnum):
    SELF_SERVE_FREE = "SELF_SERVE_FREE"  # free account/plan or free dev account gives credentials
    SELF_SERVE_TRIAL = "SELF_SERVE_TRIAL"  # credentials only during a free trial / sandbox
    PAID_PLAN = "PAID_PLAN"  # API needs a paid plan or paid add-on
    APPROVAL = "APPROVAL"  # app review, admin approval, developer-access application
    PARTNERSHIP = "PARTNERSHIP"  # partner program / contact sales / enterprise-only
    NO_PUBLIC_API = "NO_PUBLIC_API"


class Breadth(StrEnum):
    BROAD = "BROAD"  # most of the product is reachable (dozens+ resources)
    MODERATE = "MODERATE"  # core objects only
    NARROW = "NARROW"  # a handful of endpoints
    NONE = "NONE"


class Buildability(StrEnum):
    READY = "READY"  # a toolkit can be built today, self-serve
    READY_WITH_CAVEATS = "READY_WITH_CAVEATS"  # buildable but paid tier / review / limits
    NEEDS_OUTREACH = "NEEDS_OUTREACH"  # requires partnership / sales to get access
    BLOCKED = "BLOCKED"  # no usable public API


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
    auth_methods: Claim = Field(default_factory=Claim)  # list[Auth]
    access: Claim = Field(default_factory=Claim)  # Access
    api_style: Claim = Field(default_factory=Claim)  # list[REST|GRAPHQL|SOAP|SDK|CLI|NONE]
    api_breadth: Claim = Field(default_factory=Claim)  # Breadth
    official_mcp: Claim = Field(default_factory=Claim)  # bool
    webhooks: Claim = Field(default_factory=Claim)  # bool
    sandbox: Claim = Field(default_factory=Claim)  # bool (free sandbox / dev store / test mode)
    buildability: Claim = Field(default_factory=Claim)  # Buildability
    main_blocker: Claim = Field(default_factory=Claim)  # str | None

    composio: ComposioCatalogEntry = Field(default_factory=ComposioCatalogEntry)
    sources: list[str] = Field(default_factory=list)
    pass_name: str = "pass1"
    verification: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)

    def claims(self) -> dict[str, Claim]:
        return {f: getattr(self, f) for f in CLAIM_FIELDS}
