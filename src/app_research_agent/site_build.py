"""Bundle results into site/: data.json (page + agents), results.csv, llms.txt."""

from __future__ import annotations

import csv
import io
import json
import time
from collections import Counter, defaultdict

from .apps import load_apps
from .audit import AUDIT
from .audit import build as audit_build
from .config import RESULTS, ROOT, settings
from .evaluate import GROUND_TRUTH
from .evaluate import main as eval_main
from .llm import chain
from .patterns import compute
from .pipeline import FINAL, PASS1, load
from .schema import CLAIM_FIELDS, SCORED_FIELDS

SITE = ROOT / "site"
REPO = "https://github.com/HUmnanANirudh/Composio"
PAGES = "https://humnananirudh.github.io/Composio/"


def _slim(rec, p1) -> dict:
    fields = {}
    for f in CLAIM_FIELDS:
        c = getattr(rec, f)
        ev = sorted(c.evidence, key=lambda e: not e.grounded)[:2]
        fields[f] = {
            "value": c.value,
            "confidence": c.confidence,
            "flags": c.flags,
            "evidence": [{"url": e.url, "quote": e.quote, "grounded": e.grounded} for e in ev],
        }
    return {
        "id": rec.id,
        "app": rec.app,
        "category": rec.category,
        "hint": rec.hint,
        "docs_url": rec.docs_url,
        "composio": {
            "slug": rec.composio.slug if rec.composio.found else None,
            "tools": rec.composio.tools,
            "auth": rec.composio.auth,
            "mcp_variant": rec.composio.mcp_variant,
            "url": rec.composio.url,
        },
        "fields": fields,
        "pass1": {f: getattr(p1, f).value for f in CLAIM_FIELDS} if p1 else None,
        "changes": rec.verification.get("changes", []),
        "sources": rec.sources,
        "errors": len(rec.errors),
    }


def loop_stats(recs) -> dict:
    """What each verification loop flagged, and how many fields re-research changed."""
    flagged = Counter()
    by_field = defaultdict(Counter)
    for r in recs:
        for rnd in r.verification.get("rounds", [])[:1]:  # round 0 = verdict on pass 1
            for loop in ("grounding", "rules"):
                for f in rnd.get(loop) or {}:
                    flagged[loop] += 1
                    by_field[loop][f] += 1
    changes = [c for r in recs for c in r.verification.get("changes", [])]
    return {
        "flags_round0": dict(flagged),
        "flags_by_field": {k: dict(v) for k, v in by_field.items()},
        "fields_changed": len(changes),
        "apps_changed": sum(1 for r in recs if r.verification.get("changes")),
        "rounds_used": dict(Counter(len(r.verification.get("rounds", [])) for r in recs)),
    }


def usage_summary() -> dict:
    path = RESULTS / "usage.jsonl"
    if not path.exists():
        return {}
    agg = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0})
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:  # a torn last line from a killed run
            continue
        a = agg[f"{r['model']}|{r['purpose']}"]
        a["calls"] += 1
        a["in"] += r["in"]
        a["out"] += r["out"]
    return dict(agg)


def models_used() -> dict[str, list[str]]:
    calls: dict[str, Counter] = defaultdict(Counter)
    for key, v in usage_summary().items():
        model, purpose = key.split("|")
        calls[purpose][model] += v["calls"]
    return {p: [m for m, _ in c.most_common()] for p, c in calls.items()}


def _extraction_summary(recs) -> dict:
    """Who extracted the fields and how much reading the loops did (for the page and llms.txt)."""
    from .manual import EXTRACTOR, PACKS

    by = Counter(r.verification.get("extractor", "LLM (automated pipeline)") for r in recs)
    packs = sorted(PACKS.glob("[0-9][0-9][0-9].json"))
    fixes = sorted(PACKS.glob("[0-9][0-9][0-9].fix.json"))
    n_sources = sum(len(json.loads(p.read_text())["sources"]) for p in packs)
    n_fix_sources = sum(len(json.loads(p.read_text())["sources"]) for p in fixes)
    quotes = [e for r in recs for c in r.claims().values() for e in c.evidence]
    return {
        "extractor": by.most_common(1)[0][0] if by else EXTRACTOR,
        "apps": len(recs),
        "sources_read": n_sources,
        "reresearch_apps": len(fixes),
        "reresearch_sources": n_fix_sources,
        "quotes": len(quotes),
        "quotes_grounded": sum(1 for e in quotes if e.grounded),
        "search_queries": len(list((RESULTS.parent / "cache" / "search").glob("*.json"))),
    }


def _audit_for_site() -> dict | None:
    if not AUDIT.exists():
        return None
    a = json.loads(AUDIT.read_text())
    reviews = [
        {
            "id": c["id"],
            "field": c["field"],
            **{k: c["review"][k] for k in ("verdict", "note", "by")},
        }
        for c in a["cells"]
        if c.get("review")
    ]
    return {"summary": a["summary"], "reviews": reviews}


def build() -> dict:
    apps = load_apps()
    pairs = [(load(FINAL, a), load(PASS1, a)) for a in apps]
    recs = [f for f, _ in pairs if f]
    eval_main()  # refresh eval.json
    audit_build()  # refresh the field-level audit (keeps reviews)
    ev = json.loads((RESULTS / "eval.json").read_text())
    gt = json.loads(GROUND_TRUTH.read_text())
    bundle = {
        "generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "repo": REPO,
        "page": PAGES,
        # how this dataset's extraction was done (the in-session path, see manual.py)
        "extraction": _extraction_summary(recs),
        "models": {
            "provider": "Gemini API (automated path; not used for this dataset)",
            "chains": {
                "extract": chain(settings.extract_model),
                "reresearch": chain(settings.reresearch_model),
            },
            # the models that actually answered, most calls first (from usage.jsonl)
            "used": models_used(),
            "thinking_level": settings.thinking_level,
            "search": f"composio session -> {settings.search_toolkit.upper()}_SEARCH",
        },
        "audit": _audit_for_site(),
        "counts": {"apps": len(apps), "researched": len(recs)},
        "patterns": compute(recs) if recs else {},
        "eval": ev,
        "ground_truth": {
            "about": gt["_about"],
            "apps": [
                {
                    "id": a["id"],
                    "app": a["app"],
                    "notes": a["notes"],
                    "truth": a["truth"],
                    "accept": a.get("accept", {}),
                    "evidence": a["evidence"],
                }
                for a in gt["apps"]
            ],
        },
        "loops": loop_stats(recs),
        "usage": usage_summary(),
        "roster": [{"id": a.id, "app": a.app, "category": a.category} for a in apps],
        "apps": [_slim(f, p) for f, p in pairs if f],
        "pending": [a.app for (f, _), a in zip(pairs, apps) if not f],
    }
    SITE.mkdir(exist_ok=True)
    (SITE / "data.json").write_text(json.dumps(bundle, indent=1, default=str))
    (SITE / "results.csv").write_text(_csv(bundle["apps"]))
    (SITE / "llms.txt").write_text(_llms(bundle))
    (ROOT / "submission.html").write_text(standalone((SITE / "index.html").read_text(), bundle))
    return bundle


FETCH = 'fetch("data.json").then(r => r.json())'


def standalone(html: str, bundle: dict) -> str:
    """The case study as one file: data embedded, data-file links pointing at the live site."""
    assert FETCH in html, "index.html no longer loads data.json the expected way"
    data = json.dumps(bundle, separators=(",", ":"), default=str).replace("</", "<\\/")
    html = html.replace(
        FETCH, 'Promise.resolve(JSON.parse(document.getElementById("bundle").textContent))'
    )
    html = html.replace(
        "<script>", f'<script id="bundle" type="application/json">{data}</script>\n<script>', 1
    )
    for f in ("llms.txt", "data.json", "results.csv"):
        html = html.replace(f'href="{f}"', f'href="{PAGES}{f}"')
    return html.replace(
        '<a href="#run">Run it</a>', f'<a href="#run">Run it</a><a href="{PAGES}">Live site</a>', 1
    )


def _csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        ["id", "app", "category", *CLAIM_FIELDS, "composio_toolkit", "composio_tools", "docs_url"]
    )
    for r in rows:
        vals = []
        for f in CLAIM_FIELDS:
            v = r["fields"][f]["value"]
            vals.append(";".join(v) if isinstance(v, list) else v)
        w.writerow(
            [
                r["id"],
                r["app"],
                r["category"],
                *vals,
                r["composio"]["slug"],
                r["composio"]["tools"],
                r["docs_url"],
            ]
        )
    return buf.getvalue()


def _llms(b: dict) -> str:
    p, ev = b["patterns"], b["eval"]
    acc = lambda k: (ev.get(k, {}).get("overall") or {}).get("accuracy")
    lines = [
        "# Entry Requirements for 100 APIs",
        "",
        (
            "> An agent researched 100 SaaS apps for AI-agent-toolkit buildability: auth methods, "
            "self-serve vs gated access, API surface, official MCP, blockers. Every field cites a "
            "source URL and a verbatim quote that was re-checked on the page."
        ),
        "",
        (
            f"Accuracy on a 20-app hand-checked random sample: pass 1 {acc('pass1')}, after "
            f"verification loops {acc('final')} (fields: {', '.join(SCORED_FIELDS)})."
        ),
        "",
        (
            f"Extraction: {b['extraction']['extractor']}. The same author wrote the answer key, "
            "so the sample is not an independent grader; every miss and key revision is logged "
            "in data/audit.json and data/ground_truth.json."
        ),
        "",
        "## Findings",
        *[f"- {h['fig']} {h['text']}" for h in p.get("headlines", [])],
        "",
        "## Data",
        (
            f"- [data.json]({b['page']}data.json): every record with values, confidence, verifier "
            "flags, evidence (url + quote + grounded), pass-1 values, plus patterns and eval"
        ),
        f"- [results.csv]({b['page']}results.csv): one row per app, final values",
        f"- [Source and agent]({b['repo']}): README explains how to run it",
        "",
        "## Enums",
        "- auth_methods: OAUTH2, API_KEY, BEARER_TOKEN, BASIC, JWT, OTHER, NONE",
        "- access: SELF_SERVE_FREE, SELF_SERVE_TRIAL, PAID_PLAN, APPROVAL, PARTNERSHIP, NO_PUBLIC_API",
        "- buildability: READY, READY_WITH_CAVEATS, NEEDS_OUTREACH, BLOCKED",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    b = build()
    print(
        f"site/data.json: {b['counts']['researched']}/{b['counts']['apps']} apps; pending: {len(b['pending'])}"
    )
