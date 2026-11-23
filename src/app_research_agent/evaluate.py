"""Score pass-1 and final records against the hand-checked sample.

Rules (also stated in data/ground_truth.json):
* enum fields: correct if agent value == truth or is in the field's `accept` list
* auth_methods / api_style: correct if core <= agent <= core | optional, with
  API_KEY and BEARER_TOKEN treated as the same thing
* truth == null: field is unscored (docs contested/unreadable) and reported as such
"""

from __future__ import annotations

import json
from collections import defaultdict

from .apps import load_apps
from .config import DATA, RESULTS
from .pipeline import FINAL, PASS1, load
from .schema import SCORED_FIELDS

GROUND_TRUTH = DATA / "ground_truth.json"


def _eq(s: set[str]) -> set[str]:
    return s | {"API_KEY", "BEARER_TOKEN"} if s & {"API_KEY", "BEARER_TOKEN"} else s


def field_correct(field: str, agent, truth, accept: list | None = None) -> bool:
    if isinstance(truth, dict):  # set-valued field
        got = _eq(set(agent or []))
        core, opt = _eq(set(truth["core"])), _eq(set(truth.get("optional", [])))
        return core <= got <= core | opt
    return agent == truth or agent in (accept or [])


def score(records: dict[int, object], gt: dict) -> dict:
    per_field = defaultdict(lambda: {"correct": 0, "total": 0})
    rows = []
    for item in gt["apps"]:
        rec = records.get(item["id"])
        for f in SCORED_FIELDS:
            truth = item["truth"].get(f)
            if truth is None:
                rows.append(
                    {"id": item["id"], "app": item["app"], "field": f, "status": "unscored"}
                )
                continue
            if rec is None:
                rows.append({"id": item["id"], "app": item["app"], "field": f, "status": "missing"})
                per_field[f]["total"] += 1
                continue
            got = getattr(rec, f).value
            ok = field_correct(f, got, truth, item.get("accept", {}).get(f))
            per_field[f]["total"] += 1
            per_field[f]["correct"] += ok
            rows.append(
                {
                    "id": item["id"],
                    "app": item["app"],
                    "field": f,
                    "status": "hit" if ok else "miss",
                    "agent": got,
                    "truth": truth,
                    "accept": item.get("accept", {}).get(f),
                    "confidence": getattr(rec, f).confidence,
                    "flags": getattr(rec, f).flags,
                }
            )
    tot_c = sum(v["correct"] for v in per_field.values())
    tot_n = sum(v["total"] for v in per_field.values())
    return {
        "overall": {
            "correct": tot_c,
            "total": tot_n,
            "accuracy": round(tot_c / tot_n, 3) if tot_n else None,
        },
        "per_field": {
            f: {**v, "accuracy": round(v["correct"] / v["total"], 3) if v["total"] else None}
            for f, v in per_field.items()
        },
        "rows": rows,
    }


def calibration(rows: list[dict]) -> list[dict]:
    """Accuracy by confidence bucket: does the agent know when it doesn't know?"""
    buckets = defaultdict(lambda: [0, 0])
    for r in rows:
        if r["status"] in ("hit", "miss"):
            b = (
                "high (>=0.75)"
                if r["confidence"] >= 0.75
                else "medium (0.5-0.75)"
                if r["confidence"] >= 0.5
                else "low (<0.5)"
            )
            buckets[b][0] += r["status"] == "hit"
            buckets[b][1] += 1
    return [
        {"bucket": b, "correct": c, "total": n, "accuracy": round(c / n, 3)}
        for b, (c, n) in buckets.items()
    ]


def main() -> None:
    gt = json.loads(GROUND_TRUTH.read_text())
    apps = {a.id: a for a in load_apps()}
    out = {}
    for name, d in (("pass1", PASS1), ("final", FINAL)):
        recs = {i["id"]: load(d, apps[i["id"]]) for i in gt["apps"]}
        recs = {k: v for k, v in recs.items() if v is not None}
        out[name] = score(recs, gt)
        out[name]["calibration"] = calibration(out[name]["rows"])
        o = out[name]["overall"]
        print(
            f"{name:>6}: {o['correct']}/{o['total']} = {o['accuracy']}  ({len(recs)} apps scored)"
        )
        for f, v in out[name]["per_field"].items():
            print(f"        {f:<14} {v['correct']}/{v['total']}")
    (RESULTS).mkdir(parents=True, exist_ok=True)
    (RESULTS / "eval.json").write_text(json.dumps(out, indent=1, default=str))
