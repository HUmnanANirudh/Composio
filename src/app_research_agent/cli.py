from __future__ import annotations

import argparse

from .apps import load_apps, select


def main() -> None:
    p = argparse.ArgumentParser(prog="research", description="App buildability research agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="list apps in the research set")
    ls.add_argument("--apps", help="selection: '1-10', '3,7', 'Slack,Stripe'")
    args = p.parse_args()

    if args.cmd == "list":
        for a in select(load_apps(), args.apps):
            print(f"{a.id:>3}  {a.app:<28} {a.category:<36} {a.hint_url}")
