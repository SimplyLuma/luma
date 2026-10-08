# SPDX-License-Identifier: Apache-2.0

"""Finding a place by name.

Two providers, for two different questions, because their usage policies differ:

* **Photon** answers search-as-you-type. It is built for it.
* **Nominatim** answers precise questions only, at most once a second, with a
  genuine identifying User-Agent. Its policy forbids autocomplete outright,
  which is exactly why typing never reaches it.

Every request happens on a worker thread, is debounced, and is abandoned the
moment the person types again — a geocoder hit per keystroke is both a policy
violation and a needless description of someone's thinking to a server.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import threading
import time
import urllib.parse
import urllib.request

from gi.repository import GLib

from .providers import SearchProvider
from .store import Place


_CATEGORY_TONES = {
    "house": ("Home", "green", "user-home-symbolic"),
    "residential": ("Home", "green", "user-home-symbolic"),
    "office": ("Workplace", "blue", "network-workgroup-symbolic"),
    "restaurant": ("Restaurant", "amber", "starred-symbolic"),
    "cafe": ("Café", "amber", "starred-symbolic"),
    "station": ("Transit", "violet", "mark-location-symbolic"),
    "railway": ("Transit", "violet", "mark-location-symbolic"),
    "park": ("Park", "green", "mark-location-symbolic"),
}


def _describe(properties: dict) -> tuple[str, str, str]:
    for key in ("osm_value", "osm_key", "type"):
        value = str(properties.get(key, "")).lower()
        if value in _CATEGORY_TONES:
            return _CATEGORY_TONES[value]
    return ("Place", "blue", "mark-location-symbolic")


def _address(properties: dict) -> str:
    parts = [properties.get(key, "") for key in ("street", "district", "city", "state")]
    return ", ".join(part for part in parts if part)


@dataclass
class SearchOutcome:
    places: tuple[Place, ...]
    reached_network: bool
    error: str = ""


class PlaceSearch:
    """Debounced, cancellable place search."""

    def __init__(self, provider: SearchProvider, user_agent: str) -> None:
        self.provider = provider
        self.user_agent = user_agent
        self._timer = 0
        self._generation = 0
        self._last_precise = 0.0
        self._lock = threading.Lock()

    def cancel(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self._generation += 1

    def suggest(self, text: str, on_result, *, near: tuple[float, float, float] | None = None) -> None:
        """Ask for suggestions once typing has paused."""
        self.cancel()
        query = text.strip()
        if len(query) < 2:
            on_result(SearchOutcome((), False))
            return
        generation = self._generation

        def fire() -> bool:
            self._timer = 0
            threading.Thread(target=self._run, args=(query, generation, on_result, near),
                             daemon=True).start()
            return GLib.SOURCE_REMOVE

        self._timer = GLib.timeout_add(self.provider.autocomplete_debounce_ms, fire)

    def _run(self, query: str, generation: int, on_result, near) -> None:
        outcome = self._fetch(query, near)
        if generation != self._generation:
            return  # They kept typing; this answer is already stale.
        GLib.idle_add(lambda: (on_result(outcome), False)[1])

    def _fetch(self, query: str, near: tuple[float, float, float] | None = None) -> SearchOutcome:
        if not self.provider.autocomplete_url:
            return SearchOutcome((), False, "No search provider is configured.")
        params: dict[str, str | int | float] = {"q": query, "limit": 16}
        if near is not None:
            # Shumate exposes fractional zoom; Photon's zoom query parameter
            # accepts an integer and rejects e.g. "12.0" with HTTP 400.
            params.update(lat=near[0], lon=near[1], zoom=round(near[2]), location_bias_scale=0.1)
        url = f"{self.provider.autocomplete_url}?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.load(response)
        except Exception as error:
            return SearchOutcome((), True, str(error))
        places = []
        for feature in payload.get("features", []):
            properties = feature.get("properties", {})
            coordinates = feature.get("geometry", {}).get("coordinates", [])
            if len(coordinates) != 2:
                continue
            kind, tone, icon = _describe(properties)
            places.append(Place(
                name=properties.get("name") or properties.get("street") or query,
                kind=kind,
                address=_address(properties),
                latitude=float(coordinates[1]),
                longitude=float(coordinates[0]),
                tone=tone,
                icon=icon,
            ))
        return SearchOutcome(tuple(places), True)

    def precise(self, query: str, on_result) -> None:
        """A single exact lookup, rate-limited the way Nominatim requires."""
        def work() -> None:
            with self._lock:
                wait = self.provider.geocode_min_interval_s - (time.monotonic() - self._last_precise)
                if wait > 0:
                    time.sleep(wait)
                self._last_precise = time.monotonic()
            url = f"{self.provider.geocode_url}?" + urllib.parse.urlencode(
                {"q": query, "format": "jsonv2", "limit": 1}
            )
            request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    payload = json.load(response)
            except Exception as error:
                GLib.idle_add(lambda: (on_result(SearchOutcome((), True, str(error))), False)[1])
                return
            places = tuple(
                Place(name=entry.get("name") or entry.get("display_name", query).split(",")[0],
                      kind=(entry.get("type") or "Place").replace("_", " ").title(),
                      address=entry.get("display_name", ""),
                      latitude=float(entry["lat"]), longitude=float(entry["lon"]))
                for entry in payload if "lat" in entry and "lon" in entry
            )
            GLib.idle_add(lambda: (on_result(SearchOutcome(places, True)), False)[1])

        threading.Thread(target=work, daemon=True).start()
