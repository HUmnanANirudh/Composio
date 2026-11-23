"""Field-level audit of the 20-app answer key: one cell per (app, field).

Each cell records the truth, the agent's pass-1 and final values, whether each was
correct, the agent's cited source, and a human-review verdict. Regenerating the
file keeps every review already written, so reviewing is incremental.

Verdicts (data/audit.json -> cells[].review.verdict):
  agent_correct  the agent's final value was read against its source and holds up
  agent_wrong    the agent's final value is wrong; the note says why
  key_wrong      the answer key was wrong; the key was corrected (see its revisions)
  ambiguous      the docs support more than one reading; the field is left unscored
"""

from __future__ import annotations

import json
from collections import Counter

from .apps import load_apps
from .config import DATA, atomic_write
from .evaluate import GROUND_TRUTH, field_correct
from .pipeline import FINAL, PASS1, load
from .schema import SCORED_FIELDS

AUDIT = DATA / "audit.json"
VERDICTS = ("agent_correct", "agent_wrong", "key_wrong", "ambiguous")


def _status(rec, field: str, truth, accept) -> tuple[object, str]:
    if truth is None:
        return (getattr(rec, field).value if rec else None), "unscored"
    if rec is None:
        return None, "missing"
    got = getattr(rec, field).value
    return got, "hit" if field_correct(field, got, truth, accept) else "miss"


def build() -> dict:
    gt = json.loads(GROUND_TRUTH.read_text())
    apps = {a.id: a for a in load_apps()}
    old = json.loads(AUDIT.read_text()) if AUDIT.exists() else {"cells": []}
    reviews = {(c["id"], c["field"]): c.get("review") for c in old.get("cells", [])}
    cells = []
    for item in gt["apps"]:
        app = apps[item["id"]]
        p1, fin = load(PASS1, app), load(FINAL, app)
        for f in SCORED_FIELDS:
            truth = item["truth"].get(f)
            accept = item.get("accept", {}).get(f)
            v1, s1 = _status(p1, f, truth, accept)
            vf, sf = _status(fin, f, truth, accept)
            claim = getattr(fin or p1, f, None) if (fin or p1) else None
            ev = claim.evidence[0] if claim and claim.evidence else None
            review = reviews.get((item["id"], f))
            cells.append(
                {
                    "id": item["id"],
                    "app": item["app"],
                    "field": f,
                    "truth": truth,
                    "accept": accept,
                    "pass1": v1,
                    "pass1_status": s1,
                    "final": vf,
                    "final_status": sf,
                    "fixed_by_verification": s1 == "miss" and sf == "hit",
                    "broken_by_verification": s1 == "hit" and sf == "miss",
                    "confidence": claim.confidence if claim else None,
                    "agent_source": {"url": ev.url, "quote": ev.quote, "grounded": ev.grounded}
                    if ev
                    else None,
                    "key_source": item.get("evidence", {}).get(f),
                    # a stale review (made against an older final value) is dropped
                    "review": review
                    if review and review.get("final_value") == vf and sf != "missing"
                    else None,
                }
            )
    st = Counter(c["final_status"] for c in cells)
    misses = [c for c in cells if c["final_status"] == "miss"]
    out = {
        "_about": {
            "what": "Field-level audit of the 20-app answer key (20 apps x 6 scored fields).",
            "verdicts": {
                "agent_correct": "final value read against its source and holds up",
                "agent_wrong": "final value is wrong; note says why",
                "key_wrong": "answer key was wrong and was corrected (ground_truth.json revisions)",
                "ambiguous": "docs support more than one reading; field left unscored",
            },
            "regenerate": "uv run research audit (keeps existing reviews)",
        },
        "summary": {
            "cells": len(cells),
            "final_status": dict(st),
            "misses_reviewed": sum(1 for c in misses if c["review"]),
            "misses": len(misses),
            "hits_reviewed": sum(1 for c in cells if c["final_status"] == "hit" and c["review"]),
            "fixed_by_verification": sum(c["fixed_by_verification"] for c in cells),
            "broken_by_verification": sum(c["broken_by_verification"] for c in cells),
            "verdicts": dict(Counter(c["review"]["verdict"] for c in cells if c["review"])),
        },
        "cells": cells,
    }
    atomic_write(AUDIT, json.dumps(out, indent=1, default=str))
    return out


def review(app_id: int, field: str, verdict: str, note: str, by: str) -> None:
    """Record a verdict on one cell (pinned to the final value it was made against)."""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    data = build()
    for c in data["cells"]:
        if c["id"] == app_id and c["field"] == field:
            c["review"] = {"verdict": verdict, "note": note, "by": by, "final_value": c["final"]}
            atomic_write(AUDIT, json.dumps(data, indent=1, default=str))
            build()  # refresh the summary
            return
    raise KeyError(f"no audit cell for app {app_id} / {field}")
