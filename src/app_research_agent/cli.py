from __future__ import annotations

import argparse

from .apps import load_apps, select
from .config import RESULTS


def main() -> None:
    p = argparse.ArgumentParser(prog="research", description="App buildability research agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    def with_apps(sp):
        sp.add_argument("--apps", help="selection: '1-10', '3,7', 'Slack,Stripe' (default: all)")
        return sp

    with_apps(sub.add_parser("list", help="list apps in the research set"))
    r = with_apps(sub.add_parser("run", help="research apps: pass 1 + verification loops"))
    r.add_argument("--force", action="store_true", help="ignore caches and redo from scratch")
    r.add_argument("--no-critic", action="store_true", help="skip the critic-model loop")
    v = with_apps(sub.add_parser("verify", help="re-run verification on existing pass-1 records"))
    v.add_argument("--no-critic", action="store_true")
    with_apps(sub.add_parser("catalog", help="show Composio catalog match per app"))
    sh = with_apps(sub.add_parser("show", help="print final records"))
    sh.add_argument("--md", action="store_true", help="markdown table (for CI job summaries)")
    sub.add_parser("connect-search", help="connect the search toolkit (Exa) via Composio")
    sub.add_parser("export", help="bundle results into data/results/results.json")
    sub.add_parser("evaluate", help="score pass 1 and final against the hand-checked sample")
    sub.add_parser("patterns", help="compute cross-app patterns")
    args = p.parse_args()
    apps = select(load_apps(), getattr(args, "apps", None))

    if args.cmd == "list":
        for a in apps:
            print(f"{a.id:>3}  {a.app:<28} {a.category:<36} {a.hint_url}")
    elif args.cmd == "catalog":
        from .catalog import lookup

        for a in apps:
            e = lookup(a)
            print(
                f"{a.id:>3}  {a.app:<26} {e.slug or '-':<22} tools={e.tools} auth={','.join(e.auth)} mcp={e.mcp_variant}"
            )
    elif args.cmd == "run":
        from .pipeline import run

        run(apps, force=args.force, use_critic=not args.no_critic)
    elif args.cmd == "verify":
        from .pipeline import run

        run(apps, verify_only=True, use_critic=not args.no_critic)
    elif args.cmd == "show":
        show(apps, args.md)
    elif args.cmd == "connect-search":
        from .connect import connect_search_toolkit

        print(connect_search_toolkit())
    elif args.cmd == "export":
        from .pipeline import export

        b = export(apps, RESULTS / "results.json")
        print(f"exported {len(b['apps'])} apps -> {RESULTS / 'results.json'}")
    elif args.cmd == "evaluate":
        from .evaluate import main as eval_main

        eval_main()
    elif args.cmd == "patterns":
        from .patterns import main as patterns_main

        patterns_main()


def show(apps, md: bool) -> None:
    """Print final records (markdown for CI job summaries)."""
    from .pipeline import FINAL, PASS1, load
    from .schema import CLAIM_FIELDS

    for a in apps:
        rec = load(FINAL, a) or load(PASS1, a)
        if rec is None:
            print(f"{a.app}: no results")
            continue
        if not md:
            print(rec.model_dump_json(indent=2))
            continue
        print(f"## {rec.app} ({rec.category})\n")
        print(
            f"Composio toolkit: `{rec.composio.slug or 'none'}`"
            f"{f' ({rec.composio.tools} tools)' if rec.composio.found else ''}\n"
        )
        print("| Field | Value | Conf. | Evidence | Verifier flags |\n|---|---|---|---|---|")
        for f in CLAIM_FIELDS:
            c = getattr(rec, f)
            ev = next((e for e in c.evidence if e.grounded), c.evidence[0] if c.evidence else None)
            evs = (
                f"[{'✓' if ev.grounded else '✗'} source]({ev.url}) “{ev.quote[:90]}”" if ev else "—"
            )
            print(
                f"| {f} | {c.value} | {c.confidence} | {evs.replace('|', '/')} | "
                f"{'; '.join(c.flags)[:120].replace('|', '/') or '—'} |"
            )
        changes = rec.verification.get("changes", [])
        if changes:
            print("\n**Changed by verification:**\n")
            for ch in changes:
                print(f"- `{ch['field']}`: {ch['from']} → {ch['to']}")
        print()
