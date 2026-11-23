"""In-session extraction: the agent's reading step done by Claude in a Claude Code session.

The pipeline is unchanged around it: the same catalog match, crawler and Composio/Exa
search gather the sources; the same deterministic loops verify the answers. Only the
LLM call is replaced by Claude reading a source pack and writing answers to disk:

  research pack     --apps ..   gather sources -> data/packs/NNN.json + a readable .md
  (Claude writes data/manual/pass1-*.json: answers citing S-numbered sources)
  research ingest   --apps ..   pass-1 records; grounding + rules; for flagged fields,
                                targeted re-research -> fix pack (R-numbered sources)
  (Claude writes data/manual/fix-*.json for the flagged fields)
  research finalize --apps ..   apply fixes, re-run grounding + rules, write final records

Compact answer format, per field: [value, confidence, [[source_id, verbatim quote], ...]]
one_liner may be a plain string; docs_url is a plain string.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from . import web
from .apps import App
from .catalog import lookup
from .config import DATA, atomic_write
from .pipeline import FINAL, PASS1, load, save
from .research import Source, claims_from_json, gather
from .schema import SCORED_FIELDS, AppRecord
from .verify import (
    FIELD_QUERIES,
    _actionable,
    finalize_confidence,
    ground,
    merge_flags,
    rule_checks,
)

PACKS = DATA / "packs"
MANUAL = DATA / "manual"
EXTRACTOR = "Claude (claude-opus-5-5), reading source packs in a Claude Code session"
PACK_CHARS = 2400  # per page: the paragraphs most about auth / access / API / MCP


def _sources_json(sources: list[Source]) -> list[dict]:
    return [
        {"sid": s.sid, "url": s.url, "official": s.official, "excerpt": s.excerpt} for s in sources
    ]


def _load_sources(items: list[dict]) -> list[Source]:
    out = []
    for s in items:
        p = web.fetch(s["url"])  # cached: the full text the grounding check uses
        out.append(Source(s["sid"], s["url"], p.title, s["official"], p.text, s["excerpt"]))
    return out


def _render(app: App, rec_composio: dict, sources: list[dict], title: str) -> str:
    head = f"## {app.id:03d} {app.app} ({app.category}) hint={app.hint} {title}\n"
    parts = [head]
    for s in sources:
        tag = "official" if s["official"] else "third-party"
        parts.append(f"[{s['sid']}] {s['url']} ({tag})\n{s['excerpt']}\n")
    return "\n".join(parts)


def pack_one(app: App, refresh: bool = False) -> str:
    sources, errors = gather(app, refresh=refresh)
    for s in sources:
        s.excerpt = web.relevant_excerpt(s.text, PACK_CHARS)
    cat = lookup(app)
    data = {
        "id": app.id,
        "app": app.app,
        "category": app.category,
        "hint": app.hint,
        "composio": cat.model_dump(),
        "sources": _sources_json(sources),
        "errors": errors,
    }
    atomic_write(PACKS / f"{app.id:03d}.json", json.dumps(data, indent=1))
    atomic_write(PACKS / f"{app.id:03d}.md", _render(app, data["composio"], data["sources"], ""))
    return f"{app.app}: {len(sources)} sources" + (f", {len(errors)} errors" if errors else "")


def pack(apps: list[App], workers: int = 6) -> None:
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for line in ex.map(pack_one, apps):
            print(line, flush=True)


def _answers(kind: str) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for p in sorted(MANUAL.glob(f"{kind}-*.json")):
        for k, v in json.loads(p.read_text()).items():
            out[int(k)] = v
    return out


def _to_llm_shape(ans: dict) -> dict:
    """Compact [value, conf, [[sid, quote], ...]] -> the extractor's JSON shape."""
    data: dict = {}
    for f, v in ans.items():
        if f == "docs_url":
            data[f] = v
        elif isinstance(v, list) and len(v) == 3 and isinstance(v[2], list):
            data[f] = {
                "value": v[0],
                "confidence": v[1],
                "evidence": [{"source": sid, "quote": q} for sid, q in v[2]],
            }
        else:  # a bare value (e.g. one_liner as a string)
            data[f] = {"value": v, "confidence": 0.9, "evidence": []}
    return data


def ingest(apps: list[App]) -> None:
    answers = _answers("pass1")
    for app in apps:
        if app.id not in answers:
            continue
        pk = json.loads((PACKS / f"{app.id:03d}.json").read_text())
        sources = _load_sources(pk["sources"])
        rec = AppRecord(id=app.id, app=app.app, category=app.category, hint=app.hint)
        rec.composio = rec.composio.model_validate(pk["composio"])
        rec.errors = pk["errors"]
        rec.sources = [s.url for s in sources]
        data = _to_llm_shape(answers[app.id])
        for f, c in claims_from_json(data, sources).items():
            setattr(rec, f, c)
        rec.docs_url = data.get("docs_url")
        rec.pass_name = "pass1"
        rec.verification = {"extractor": EXTRACTOR}
        g, r = ground(rec, sources), rule_checks(rec, sources)
        flags = merge_flags(g, r)
        save(rec, PASS1)
        # re-research is spent on the scored fields; unscored ones keep a low confidence
        todo = {f: v for f, v in _actionable(flags).items() if f in SCORED_FIELDS}
        state = {"round0": {"grounding": g, "rules": r}, "todo": todo}
        atomic_write(PACKS / f"{app.id:03d}.flags.json", json.dumps(state, indent=1))
        if not todo:
            print(f"{app.app}: clean")
            continue
        queries = sorted({q.format(app=app.app) for f in todo for q in FIELD_QUERIES.get(f, [])})
        new, errors = gather(
            app, queries=queries, extra_urls=[s.url for s in sources if s.official]
        )
        seen = {s.url for s in sources}
        fresh = [s for s in new if s.url not in seen]
        for i, s in enumerate(fresh, 1):
            s.sid = f"R{i}"
            s.excerpt = web.relevant_excerpt(s.text, PACK_CHARS)
        fix = {"sources": _sources_json(fresh), "errors": errors, "todo": todo}
        atomic_write(PACKS / f"{app.id:03d}.fix.json", json.dumps(fix, indent=1))
        notes = "\n".join(f"- {f}: {'; '.join(v)}" for f, v in todo.items())
        prev = {f: getattr(rec, f).value for f in todo}
        atomic_write(
            PACKS / f"{app.id:03d}.fix.md",
            _render(app, pk["composio"], fix["sources"], f"FIX {list(todo)}")
            + f"\nFLAGS:\n{notes}\nPREVIOUS: {json.dumps(prev)}\n",
        )
        print(f"{app.app}: flagged {sorted(todo)} -> {len(fresh)} new sources")


def finalize(apps: list[App]) -> None:
    fixes = _answers("fix")
    for app in apps:
        rec = load(PASS1, app)
        if rec is None:
            continue
        pk = json.loads((PACKS / f"{app.id:03d}.json").read_text())
        sources = _load_sources(pk["sources"])
        state = json.loads((PACKS / f"{app.id:03d}.flags.json").read_text())
        fin = rec.model_copy(deep=True)
        fin.pass_name = "final"
        rounds = [{"round": 0, **state["round0"]}]
        changes = []
        fixp = PACKS / f"{app.id:03d}.fix.json"
        if state["todo"] and fixp.exists():
            fx = json.loads(fixp.read_text())
            sources += _load_sources(fx["sources"])
            ans = fixes.get(app.id)
            if ans is None:
                print(f"{app.app}: flagged but no fix answer yet; skipped")
                continue
            data = _to_llm_shape(ans)
            for f, c in claims_from_json(data, sources, fields=tuple(ans)).items():
                old = getattr(fin, f)
                if c.value in (None, []) and old.value not in (None, []) and f != "main_blocker":
                    continue
                if c.value != old.value:
                    changes.append(
                        {"field": f, "from": old.value, "to": c.value, "why": state["todo"].get(f)}
                    )
                setattr(fin, f, c)
            fin.sources = [s.url for s in sources]
        g, r = ground(fin, sources), rule_checks(fin, sources)
        flags = merge_flags(g, r)
        if state["todo"]:
            rounds.append({"round": 1, "grounding": g, "rules": r})
        finalize_confidence(fin, flags)
        fin.verification = {
            "extractor": EXTRACTOR,
            "rounds": rounds,
            "changes": changes,
            "open_flags": flags,
        }
        save(fin, FINAL)
        print(f"{app.app}: final ({len(changes)} field(s) changed by verification)")
