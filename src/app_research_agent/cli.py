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
