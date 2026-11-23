"""Verification loops that turn a pass-1 record into a final record.

Loop 1  grounding      deterministic: is each quote really on the cited page?
Loop 2  rules          deterministic: internal consistency + Composio catalog cross-check
Loop 3  critic         a *different* LLM audits each field against the sources
Loop 4  re-research    flagged fields get targeted searches and are re-extracted,
                       then loops 1-3 run again (up to MAX_ROUNDS)

Every flag and every change is logged in record.verification so the case
study can show exactly what each loop caught.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from . import web
from .apps import App
from .catalog import composio_auth_as_enum
from .config import settings
from .llm import DailyLimitError, LLMError, chat_json
from .research import RUBRIC, Source, claims_from_json, format_sources, gather
from .schema import SCORED_FIELDS, AppRecord, Claim

MAX_ROUNDS = 2
GROUND_THRESHOLD = 88  # rapidfuzz partial_ratio needed to count a quote as present


# --------------------------------------------------------------------------- loop 1


def _norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", re.sub(r"[`*_#>|]", " ", s)).strip()


def quote_score(quote: str, text: str) -> float:
    q, t = _norm(quote), _norm(text)
    if not q:
        return 0.0
    if q in t:
        return 100.0
    if len(q) < 12:
        return 0.0  # too short to fuzzy match meaningfully
    # partial_ratio over the full page is slow for big pages; anchor on a rare word
    words = sorted(set(re.findall(r"[a-z0-9]{5,}", q)), key=len, reverse=True)[:3]
    best = 0.0
    for w in words or [q[:10]]:
        for m in re.finditer(re.escape(w), t):
            window = t[max(0, m.start() - len(q)) : m.end() + len(q)]
            best = max(best, fuzz.partial_ratio(q, window))
            if best >= 99:
                return best
    return best


def page_text(url: str, sources: list[Source]) -> str | None:
    for s in sources:
        if s.url == url:
            return s.text
    p = web.fetch(url)  # cached
    return p.text if p.ok else None


def ground(rec: AppRecord, sources: list[Source]) -> dict[str, list[str]]:
    flags: dict[str, list[str]] = {}
    for f, c in rec.claims().items():
        for e in c.evidence:
            text = page_text(e.url, sources) if e.url.startswith("http") else None
            e.match_score = quote_score(e.quote, text) if text else 0.0
            e.grounded = e.match_score >= GROUND_THRESHOLD
        if f == "main_blocker" and c.value is None:
            continue
        if not any(e.grounded for e in c.evidence):
            flags.setdefault(f, []).append("ungrounded")
    return flags


# --------------------------------------------------------------------------- loop 2

EXPECTED_BUILD = {
    "PARTNERSHIP": {"NEEDS_OUTREACH"},
    "NO_PUBLIC_API": {"BLOCKED"},
    "APPROVAL": {"READY_WITH_CAVEATS", "NEEDS_OUTREACH"},
    "PAID_PLAN": {"READY_WITH_CAVEATS"},
    "SELF_SERVE_TRIAL": {"READY", "READY_WITH_CAVEATS"},
    "SELF_SERVE_FREE": {"READY", "READY_WITH_CAVEATS"},
}


def _equiv(methods: set[str]) -> set[str]:
    """API keys and static bearer tokens are the same thing under different names."""
    if methods & {"API_KEY", "BEARER_TOKEN"}:
        return methods | {"API_KEY", "BEARER_TOKEN"}
    return methods


def rule_checks(rec: AppRecord, sources: list[Source]) -> dict[str, list[str]]:
    flags: dict[str, list[str]] = {}

    def flag(f: str, why: str) -> None:
        flags.setdefault(f, []).append(why)

    cat = rec.composio
    auth = _equiv(set(rec.auth_methods.value or []))
    if cat.found and cat.auth and not (cat.slug or "").endswith("_MCP"):
        theirs = _equiv(composio_auth_as_enum(cat)) - {"NONE"}
        if auth and theirs and not auth & theirs:
            flag("auth_methods", f"disagrees_with_composio:{sorted(theirs)}")
        elif theirs - auth:
            flag("auth_methods", f"maybe_incomplete_vs_composio:{sorted(theirs - auth)}")
    if rec.official_mcp.value is False and cat.mcp_variant and "DCR_OAUTH" in cat.mcp_variant_auth:
        flag("official_mcp", f"composio_proxies_vendor_mcp:{cat.mcp_variant}")

    access, build = rec.access.value, rec.buildability.value
    if access in EXPECTED_BUILD and build not in EXPECTED_BUILD[access]:
        flag("buildability", f"inconsistent_with_access:{access}->{build}")
    if access == "NO_PUBLIC_API" and cat.found and (cat.tools or 0) > 0:
        flag("access", f"composio_has_{cat.tools}_tools_so_api_exists")
    if rec.api_breadth.value == "NONE" and access != "NO_PUBLIC_API":
        flag("api_breadth", "none_but_api_exists")

    if not any(s.official for s in sources):
        for f in SCORED_FIELDS:
            flag(f, "no_official_source")
    for f in SCORED_FIELDS:
        if getattr(rec, f).confidence < 0.5:
            flag(f, "low_confidence")
    return flags


# --------------------------------------------------------------------------- loop 3

CRITIC = """\
You are a skeptical reviewer auditing another analyst's research on a SaaS API.
For each field, decide using ONLY the sources (and the field definitions below):
  "ok"            the value is supported by the sources
  "wrong"         the sources contradict it (give the corrected value)
  "unsupported"   the sources neither confirm nor contradict it
Be strict about access: a free developer/sandbox account is SELF_SERVE_FREE even if production
needs app review; "contact sales for API access" is PARTNERSHIP.

Field definitions:
{rubric}

Return JSON: {{"fields": {{"<field>": {{"verdict": "ok|wrong|unsupported", "corrected": <value or null>,
"reason": "<= 25 words"}}, ...}}}}
"""


def critic(rec: AppRecord, sources: list[Source]) -> tuple[dict[str, list[str]], dict]:
    record = {f: getattr(rec, f).value for f in SCORED_FIELDS}
    user = (
        f"App: {rec.app} ({rec.category})\nAnalyst's answers: {record}\n\n"
        f"SOURCES:\n{format_sources(sources)}"
    )
    data = chat_json(
        settings.critic_model,
        CRITIC.format(rubric=RUBRIC.split("Field definitions")[1]),
        user,
        purpose="critic",
    )
    flags: dict[str, list[str]] = {}
    for f, v in (data.get("fields") or {}).items():
        if (
            f in SCORED_FIELDS
            and isinstance(v, dict)
            and v.get("verdict") in ("wrong", "unsupported")
        ):
            note = f"critic_{v['verdict']}"
            if v.get("corrected") is not None:
                note += f":{v['corrected']}"
            flags.setdefault(f, []).append(
                note + (f" ({v.get('reason')})" if v.get("reason") else "")
            )
    return flags, data


# --------------------------------------------------------------------------- loop 4

FIELD_QUERIES = {
    "auth_methods": [
        "{app} API authentication documentation",
        "{app} API OAuth app create client id",
    ],
    "access": [
        "{app} API access which plan pricing",
        "{app} developer account sandbox free API key",
    ],
    "api_style": ["{app} API reference REST GraphQL"],
    "api_breadth": ["{app} API reference endpoints list"],
    "official_mcp": [
        "{app} MCP server modelcontextprotocol",
        "{app} remote MCP server docs",
    ],
    "buildability": ["{app} API partner program developer access"],
    "webhooks": ["{app} API webhooks"],
    "sandbox": ["{app} API sandbox test mode developer account"],
    "one_liner": ["{app} what is"],
    "main_blocker": ["{app} API limitations requirements"],
}

REEXTRACT = """\
{rubric}

You are re-checking ONLY these fields: {fields}.
A verifier flagged the previous answers for these reasons (treat them as leads to check, NOT as facts):
{notes}
Previous answers: {previous}
Answer only those fields, same JSON shape, each with verbatim quotes from the sources.
"""


def merge_flags(*dicts: dict[str, list[str]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for d in dicts:
        for k, v in d.items():
            out.setdefault(k, []).extend(v)
    return out


def _actionable(flags: dict[str, list[str]]) -> dict[str, list[str]]:
    """Flags that justify spending a re-research round on the field."""
    soft = ("maybe_incomplete", "low_confidence")
    return {f: v for f, v in flags.items() if len(v) > 1 or not v[0].startswith(soft)}


def reresearch(
    app: App, rec: AppRecord, flags: dict[str, list[str]], sources: list[Source]
) -> list[Source]:
    queries = sorted({q.format(app=app.app) for f in flags for q in FIELD_QUERIES.get(f, [])})
    keep = [s.url for s in sources if s.official]
    new_sources, errors = gather(
        app, queries=queries, extra_urls=keep, max_pages=settings.max_pages + 2
    )
    rec.errors += errors
    user = (
        f"App: {app.app}\nCategory: {app.category}\nHint: {app.hint}\n\n"
        f"SOURCES:\n{format_sources(new_sources)}"
    )
    system = REEXTRACT.format(
        rubric=RUBRIC,
        fields=", ".join(flags),
        notes="\n".join(f"- {f}: {'; '.join(v)}" for f, v in flags.items()),
        previous={f: getattr(rec, f).value for f in flags},
    )
    data = chat_json(settings.extract_model, system, user, purpose="reresearch")
    new = claims_from_json(data, new_sources, fields=tuple(flags))
    changes = []
    for f, c in new.items():
        old: Claim = getattr(rec, f)
        if c.value in (None, []) and old.value not in (None, []):
            continue  # model dropped the field; keep the old answer
        if c.value != old.value:
            changes.append({"field": f, "from": old.value, "to": c.value, "why": flags[f]})
        setattr(rec, f, c)
    rec.verification.setdefault("changes", []).extend(changes)
    seen = {s.url for s in sources}
    return sources + [s for s in new_sources if s.url not in seen]


def finalize_confidence(rec: AppRecord, flags: dict[str, list[str]]) -> None:
    for f, c in rec.claims().items():
        grounded_official = any(e.grounded for e in c.evidence) and any(
            e.grounded and not web.is_low_trust(e.url) for e in c.evidence
        )
        conf = c.confidence
        conf = min(1.0, conf + 0.1) if grounded_official else min(conf, 0.4)
        conf -= 0.15 * len([x for x in flags.get(f, []) if not x.startswith("maybe_")])
        c.confidence = round(max(0.05, conf), 2)
        c.flags = flags.get(f, [])


def verify(app: App, rec: AppRecord, sources: list[Source], use_critic: bool = True) -> AppRecord:
    rec = rec.model_copy(deep=True)
    rec.pass_name = "final"
    rounds = []
    for rnd in range(MAX_ROUNDS + 1):
        g = ground(rec, sources)
        r = rule_checks(rec, sources)
        c: dict[str, list[str]] = {}
        if use_critic:
            try:
                c, _ = critic(rec, sources)
            except DailyLimitError:
                raise
            except LLMError as e:
                rec.errors.append(f"critic round {rnd}: {e}")
        flags = merge_flags(g, r, c)
        rounds.append({"round": rnd, "grounding": g, "rules": r, "critic": c})
        todo = _actionable(flags)
        if not todo or rnd == MAX_ROUNDS:
            break
        sources = reresearch(app, rec, todo, sources)
    rec.sources = [s.url for s in sources]
    finalize_confidence(rec, flags)
    rec.verification["rounds"] = rounds
    rec.verification["open_flags"] = flags
    return rec
