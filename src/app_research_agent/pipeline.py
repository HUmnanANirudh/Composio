"""Run the agent over a set of apps: pass 1 -> verification -> final, resumable."""

from __future__ import annotations

import json
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rich.console import Console

from .apps import App
from .config import RESULTS, settings
from .research import gather, research_app
from .schema import AppRecord
from .verify import verify

console = Console()
PASS1 = RESULTS / "pass1"
FINAL = RESULTS / "final"


def _path(d: Path, app: App) -> Path:
    return d / f"{app.id:03d}.json"


def save(rec: AppRecord, d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rec.id:03d}.json").write_text(rec.model_dump_json(indent=2))


def load(d: Path, app: App) -> AppRecord | None:
    p = _path(d, app)
    return AppRecord.model_validate_json(p.read_text()) if p.exists() else None


def run_one(
    app: App, force: bool = False, use_critic: bool = True, verify_only: bool = False
) -> str:
    try:
        rec = None if force else load(PASS1, app)
        if rec is None:
            if verify_only:
                return f"{app.app}: no pass1 yet"
            rec, sources = research_app(app, refresh=force)
            save(rec, PASS1)
        else:
            sources, _ = gather(app)  # served from cache: same sources pass 1 saw
        if force or verify_only or load(FINAL, app) is None:
            final = verify(app, rec, sources, use_critic=use_critic)
            save(final, FINAL)
            n = len(final.verification.get("changes", []))
            return f"{app.app}: done ({n} field(s) changed by verification)"
        return f"{app.app}: cached"
    except Exception as e:  # noqa: BLE001 - one app failing must not kill the run
        (RESULTS / "errors").mkdir(parents=True, exist_ok=True)
        (RESULTS / "errors" / f"{app.id:03d}.txt").write_text(traceback.format_exc())
        return f"[red]{app.app}: FAILED {type(e).__name__}: {e}[/red]"


def run(apps: list[App], **kw) -> None:
    console.print(
        f"Researching {len(apps)} app(s) | search={settings.resolved_search_backend()} "
        f"extract={settings.extract_model} critic={settings.critic_model}"
    )
    with ThreadPoolExecutor(max_workers=settings.concurrency) as ex:
        futs = {ex.submit(run_one, a, **kw): a for a in apps}
        for i, f in enumerate(as_completed(futs), 1):
            console.print(f"[{i}/{len(apps)}] {f.result()}")


def export(apps: list[App], out: Path) -> dict:
    """Bundle pass1 + final records into one JSON for the site and for other agents."""
    rows = []
    for a in apps:
        p1, fin = load(PASS1, a), load(FINAL, a)
        if fin or p1:
            rows.append(
                {
                    "pass1": p1.model_dump() if p1 else None,
                    "final": fin.model_dump() if fin else None,
                }
            )
    bundle = {"apps": rows}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, indent=1, default=str))
    return bundle
