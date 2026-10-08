# SPDX-License-Identifier: Apache-2.0
"""Camera framing for search results, independent of the GTK map widget."""

from __future__ import annotations

import math
from collections.abc import Sequence

from .store import Place


def nearby_cluster(places: Sequence[Place], radius_km: float = 60) -> tuple[Place, ...]:
    """Frame the first hit and its local siblings, without zooming to the globe.

    Search providers also return similarly named places in other cities. Those
    remain in Results and can be opened individually, but do not determine the
    initial camera for a search such as a restaurant chain or a city name.
    """
    positioned = tuple(p for p in places if p.latitude is not None and p.longitude is not None)
    if not positioned:
        return ()
    first = positioned[0]
    local = []
    for place in positioned:
        dlat = math.radians(place.latitude - first.latitude)
        dlon = math.radians(place.longitude - first.longitude)
        haversine = (math.sin(dlat / 2) ** 2 + math.cos(math.radians(first.latitude))
                     * math.cos(math.radians(place.latitude)) * math.sin(dlon / 2) ** 2)
        distance = 12742 * math.asin(min(1, math.sqrt(haversine)))
        if distance <= radius_km:
            local.append(place)
    return tuple(local)


def _mercator(latitude: float) -> float:
    latitude = max(-85.05112878, min(85.05112878, latitude))
    sine = math.sin(math.radians(latitude))
    return (1 - math.log((1 + sine) / (1 - sine)) / (2 * math.pi)) / 2


def _latitude(y: float) -> float:
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y))))


def camera_for_places(places: Sequence[Place], width: int, height: int) -> tuple[float, float, float] | None:
    """Return latitude, longitude and zoom that keep every result visible.

    A single result gets street-level context. For multiple results the map
    leaves room for pins and the floating place card on each edge.
    """
    points = [(p.latitude, p.longitude) for p in places
              if p.latitude is not None and p.longitude is not None]
    if not points:
        return None
    if len(points) == 1:
        return (*points[0], 15.0)
    xs = [(longitude + 180) / 360 for _, longitude in points]
    ys = [_mercator(latitude) for latitude, _ in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    usable_width = max(64, width - 160)
    usable_height = max(64, height - 160)
    span_x = max_x - min_x
    span_y = max_y - min_y
    zoom_x = math.log2(usable_width / (256 * span_x)) if span_x else 16.0
    zoom_y = math.log2(usable_height / (256 * span_y)) if span_y else 16.0
    zoom = max(2.0, min(15.0, zoom_x, zoom_y))
    return (_latitude((min_y + max_y) / 2), (min_x + max_x) * 180 - 180, zoom)


def fixture_view_for_places(places: Sequence[Place], width: int, height: int) -> tuple[float, float, float] | None:
    """The same fit for the local, pixel-coordinate conform scene."""
    points = [(p.map_x, p.map_y) for p in places if hasattr(p, "map_x")]
    if not points:
        return None
    if len(points) == 1:
        return (*points[0], 1.35)
    xs, ys = zip(*points)
    span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
    zoom = min(1.6, (max(64, width - 160) / max(1, span_x)),
               (max(64, height - 160) / max(1, span_y)))
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
            max(.4, zoom))
