# SPDX-License-Identifier: Apache-2.0

"""The v71 Maps scene. Fixture mode reads one JSON file and never opens a live store or endpoint."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path

from .store import Place


@dataclass(frozen=True)
class FixturePlace(Place):
    id: str = ""
    map_x: float = 0
    map_y: float = 0
    favourite: str = ""
    rating: float | None = None
    price: str = ""
    hours: str = ""
    image: str = ""


@dataclass(frozen=True)
class Guide:
    """A short list of places worth your time, from someone you know or made by you (v71 MPG)."""

    id: str
    title: str
    by: str
    initials: str
    image: str
    place_ids: tuple[str, ...]
    note: str


def place_matches(place: Place, query: str) -> bool:
    """v71 searches name, category and address, case insensitively."""
    query = query.casefold().strip()
    return bool(query) and query in f"{place.name} {place.kind} {place.address}".casefold()


_STREET_SUFFIXES = ("st", "ave", "blvd", "rd", "way", "dr", "ln", "ct", "pl")


def address_offer(query: str) -> str:
    """A typed street address is a result in itself: "710 central" -> "710 Central Ave" (v71 mpSide0)."""
    text = " ".join(query.split())
    first, _, rest = text.partition(" ")
    if not (first.isdigit() and rest[:1].isalpha()):
        return ""
    words = [word if word[:1].isdigit() else word[:1].upper() + word[1:].lower() for word in text.split(" ")]
    if words[-1].rstrip(".").lower() not in _STREET_SUFFIXES:
        words.append("Ave")
    return " ".join(words)


def drive_minutes(place: Place) -> int:
    """The drive time on a place's Directions key and a favorite's tile (v71: 9 to The Loft, 3 more elsewhere)."""
    return 9 if getattr(place, "id", "") == "loft" else 12


def favorite_minutes(place: Place) -> int:
    """The small line under a favorite (v71 mpfavs): 9 min, and 15 to Home."""
    return 15 if getattr(place, "id", "") == "home" else 9


class MapsFixture:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        source = Path(path)
        payload = json.loads(source.read_text(encoding="utf-8"))
        self.asset_dir = source.with_suffix("")
        self.opening = payload["opening"]
        self.places = tuple(FixturePlace(
            name=row["name"], kind=row["category"], address=row["address"],
            latitude=None, longitude=None, icon=row["icon"],
            id=row["id"], map_x=row["x"], map_y=row["y"],
            favourite=row.get("favourite", ""), rating=row.get("rating"),
            price=row.get("price", ""), hours=row.get("hours", ""), image=row["image"],
        ) for row in payload["places"])
        self.by_id = {place.id: place for place in self.places}
        self.saved = [place for place in self.places if place.favourite]
        self.recents = [self.by_id[key] for key in payload["recent_ids"]]
        self.route = payload["route"]
        self.route_points = tuple(tuple(point) for point in payload["route_points"])
        self.steps = tuple(tuple(step) for step in payload["steps"])
        self.guides = tuple(Guide(row["id"], row["title"], row["by"], row["initials"], row["image"],
                                  tuple(row["places"]), row["note"]) for row in payload.get("guides", ()))
        self.guides_by_id = {guide.id: guide for guide in self.guides}

    def find(self, query: str) -> tuple[FixturePlace, ...]:
        return tuple(place for place in self.places if place_matches(place, query))

    def is_saved(self, place: Place) -> bool:
        return place in self.saved

    def remember(self, _place: Place) -> None:
        """v70's recents are fixed in the fixture, including after a click."""

    def save(self, place: Place) -> None:
        if place not in self.saved:
            self.saved.append(place)

    def unsave(self, place: Place) -> None:
        if place in self.saved:
            self.saved.remove(place)

    def clear_recents(self) -> None:
        self.recents.clear()

    def clear_saved(self) -> None:
        self.saved.clear()


def from_environment() -> MapsFixture | None:
    path = os.environ.get("LUMA_MAPS_FIXTURE")
    return MapsFixture(path) if path else None
