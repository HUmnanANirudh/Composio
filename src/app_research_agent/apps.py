from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from .config import DATA


@dataclass(frozen=True)
class App:
    id: int
    category: str
    app: str
    hint: str

    @property
    def hint_url(self) -> str | None:
        """First URL-ish token of the hint, e.g. 'zoho.com/crm' -> https://zoho.com/crm."""
        for tok in re.findall(r"[\w.-]+\.[a-z]{2,}(?:/[\w./-]*)?", self.hint):
            return "https://" + tok.rstrip("/.)")
        return None

    @property
    def domain(self) -> str | None:
        u = self.hint_url
        return u.split("/")[2] if u else None


def load_apps(path: Path | None = None) -> list[App]:
    path = path or DATA / "apps.csv"
    with path.open(newline="") as f:
        return [
            App(
                int(r["id"]), r["category"].strip(), r["app"].strip(), r["hint"].strip()
            )
            for r in csv.DictReader(f)
        ]


def select(apps: list[App], spec: str | None) -> list[App]:
    """spec: None (all) | '1-10' | '3,7,42' | 'Slack,Stripe'."""
    if not spec:
        return apps
    out: list[App] = []
    for part in spec.split(","):
        part = part.strip()
        if re.fullmatch(r"\d+-\d+", part):
            a, b = map(int, part.split("-"))
            out += [x for x in apps if a <= x.id <= b]
        elif part.isdigit():
            out += [x for x in apps if x.id == int(part)]
        else:
            out += [x for x in apps if x.app.lower() == part.lower()]
    seen: set[int] = set()
    return [x for x in out if not (x.id in seen or seen.add(x.id))]
