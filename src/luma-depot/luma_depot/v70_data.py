# SPDX-License-Identifier: Apache-2.0
"""Depot's v70 fixture and GTK-free list operations.

The fixture is a copy of the Studio sample. It reads only the supplied JSON
file; no catalogue, installation, update service or user setting is opened.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable


SYSTEM_APPS = frozenset({"filer", "viola", "terminal"})
KIND_LABELS = {
    "system": "Part of Luma", "luma": "Luma app", "flatpak": "Flatpak",
    "deb": "Universal .deb", "rpm": "Universal .rpm", "layered": "Layered package",
}


@dataclass(frozen=True)
class Listing:
    id: str
    name: str
    tagline: str
    category: str
    size: str = "—"
    rating: float = 4.7
    reviews: int = 320
    luma: bool = False
    icon: str = ""
    shot: str = ""
    mono: tuple = ()
    installed: bool = False
    update: str = ""
    package: str = ""
    reach: tuple = ()

    @property
    def kind(self) -> str:
        return "system" if self.id in SYSTEM_APPS else self.package or ("luma" if self.luma else "flatpak")

    @property
    def kind_label(self) -> str:
        return KIND_LABELS[self.kind]


def load_fixture(path: str | Path) -> tuple[Listing, ...]:
    """Load the exact v70 sample from a caller-provided path, in memory."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != "org.projectluma.depot.v70-fixture/1":
        raise ValueError("Unsupported Depot fixture schema")
    listings = tuple(Listing(
        id=item["id"], name=item["name"], tagline=item["tagline"], category=item["category"],
        size=item["size"], rating=float(item["rating"]), reviews=int(item["reviews"]),
        luma=bool(item.get("luma")), icon=item.get("icon", ""), shot=item.get("shot", ""),
        mono=tuple(item.get("mono", ())), installed=bool(item.get("inst")),
        update=item.get("upd", ""), package=item.get("pk", ""),
        reach=tuple(tuple(row) for row in item.get("reach", ())),
    ) for item in data["apps"])
    if len({app.id for app in listings}) != len(listings):
        raise ValueError("Depot fixture has duplicate app IDs")
    return listings


def search(listings: Iterable[Listing], query: str) -> tuple[Listing, ...]:
    term = query.casefold().strip()
    return tuple(app for app in listings if term in
                 f"{app.name} {app.tagline} {app.category}".casefold())


def installed_sorted(listings: Iterable[Listing], key: str = "name", direction: int = 1) -> tuple[Listing, ...]:
    """Your apps uses v70's four table sorts with a stable name and ID tie break."""
    if key not in {"name", "cat", "kind", "size"}:
        raise ValueError(f"Unknown Depot sort column: {key}")
    if direction not in (-1, 1):
        raise ValueError("Sort direction must be -1 or 1")

    def size_mb(app: Listing) -> float:
        amount, _, unit = app.size.partition(" ")
        try:
            value = float(amount)
        except ValueError:
            return 0.0
        return value * (1000 if unit == "GB" else 1)

    selected = (app for app in listings if app.installed)
    value = {
        "name": lambda app: app.name.casefold(), "cat": lambda app: app.category.casefold(),
        "kind": lambda app: app.kind_label.casefold(), "size": size_mb,
    }[key]
    return tuple(sorted(selected, key=lambda app: (value(app), app.name.casefold(), app.id), reverse=direction < 0))
