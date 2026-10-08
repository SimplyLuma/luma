# SPDX-License-Identifier: Apache-2.0

"""GTK-free state transitions for the map, place, directions and guidance views."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math


LAYERS = ("map", "transit", "sat")
TRAVEL_MODES = ("drive", "transit", "walk", "bike")
PANELS = ("", "search", "layers", "share")


@dataclass(frozen=True)
class RouteProgress:
    x: float
    y: float
    segment: int
    to_turn: float
    remaining: float


def route_progress(points: tuple[tuple[float, float], ...], distance: float) -> RouteProgress:
    """Position along the fixture polyline, independent of GTK or wall time."""
    if len(points) < 2:
        raise ValueError("a route needs at least two points")
    lengths = tuple(math.dist(a, b) for a, b in zip(points, points[1:]))
    total = sum(lengths)
    if total <= 0:
        raise ValueError("a route needs a nonzero length")
    travelled = max(0.0, min(distance, total))
    along = travelled
    for index, length in enumerate(lengths):
        if along <= length or index == len(lengths) - 1:
            fraction = 1.0 if length == 0 else min(1.0, along / length)
            start, end = points[index], points[index + 1]
            return RouteProgress(start[0] + (end[0] - start[0]) * fraction,
                                 start[1] + (end[1] - start[1]) * fraction,
                                 index, max(0.0, length - along), 1.0 - travelled / total)
        along -= length
    raise AssertionError("route walk passed its final segment")


@dataclass(frozen=True)
class MapState:
    place: str | None = None
    directions: bool = False
    mode: str = "drive"
    navigating: bool = False
    layer: str = "map"
    query: str = ""
    guide: str | None = None
    #: What the phone's bar has grown into: "" (nothing), "search", "layers" or "share".
    panel: str = ""

    def select(self, place_id: str) -> "MapState":
        return replace(self, place=place_id, directions=False, navigating=False)

    def close_place(self) -> "MapState":
        return replace(self, place=None, directions=False, navigating=False)

    def show_directions(self) -> "MapState":
        if self.place is None:
            raise ValueError("directions need a destination")
        return replace(self, directions=True, navigating=False)

    def back_to_place(self) -> "MapState":
        return replace(self, directions=False, navigating=False)

    def set_mode(self, mode: str) -> "MapState":
        if mode not in TRAVEL_MODES:
            raise ValueError(f"unknown travel mode {mode!r}")
        return replace(self, mode=mode)

    def go(self) -> "MapState":
        if not self.directions or self.place is None:
            raise ValueError("guidance needs a route")
        return replace(self, directions=False, navigating=True)

    def end(self) -> "MapState":
        return replace(self, navigating=False)

    def set_layer(self, layer: str) -> "MapState":
        if layer not in LAYERS:
            raise ValueError(f"unknown map layer {layer!r}")
        return replace(self, layer=layer)

    def search(self, query: str) -> "MapState":
        return replace(self, query=query)

    def open_guide(self, guide_id: str | None) -> "MapState":
        return replace(self, guide=guide_id)

    def with_panel(self, panel: str) -> "MapState":
        if panel not in PANELS:
            raise ValueError(f"unknown phone panel {panel!r}")
        return replace(self, panel=panel)


def fixture_state(value: str, place_ids: set[str]) -> MapState:
    """A deterministic start state for one conform capture (no data access)."""
    if not value:
        return MapState()
    action, _, rest = value.partition(":")
    if action == "search":
        return MapState(query=rest)
    if action == "layer":
        return MapState().set_layer(rest)
    # The phone's grown bar (v71 MP.px): "bar:search", "bar:guide:coffee", "bar:layers", "bar:share:loft".
    if action == "bar":
        kind, _, arg = rest.partition(":")
        if kind == "search":
            return MapState().with_panel("search")
        if kind == "guide":
            return MapState().with_panel("search").open_guide(arg)
        if kind == "layers":
            return MapState().with_panel("layers")
        if kind == "share":
            if arg not in place_ids:
                raise ValueError(f"unknown fixture place {arg!r}")
            return MapState().select(arg).with_panel("share")
        raise ValueError(f"unknown fixture state {value!r}")
    place, _, mode = rest.partition(":")
    if place not in place_ids:
        raise ValueError(f"unknown fixture place {place!r}")
    state = MapState().select(place)
    if action == "place" and not mode:
        return state
    if action == "directions":
        return state.show_directions().set_mode(mode or "drive")
    if action == "navigating" and not mode:
        return state.show_directions().go()
    raise ValueError(f"unknown fixture state {value!r}")
