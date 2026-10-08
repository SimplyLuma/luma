# SPDX-License-Identifier: Apache-2.0

"""Saved places and recents — kept on this machine and nowhere else.

Nothing here is transmitted. There is no history sync, no analytics and no
account. The clear() calls exist because a person should be able to remove
what Maps knows about where they have been, and see it go.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
import math
import os
import pathlib
import shutil
import tempfile
from typing import Callable


RECENTS_LIMIT = 12


@dataclass(frozen=True)
class Place:
    name: str
    kind: str
    address: str
    latitude: float | None
    longitude: float | None
    tone: str = "blue"
    icon: str = "mark-location-symbolic"

    @property
    def subtitle(self) -> str:
        return " · ".join(part for part in (self.kind, self.address) if part)


def _data_dir() -> pathlib.Path:
    root = os.environ.get("XDG_DATA_HOME", "") or str(pathlib.Path.home() / ".local/share")
    return pathlib.Path(root) / "luma-maps"


_PLACE_FIELDS = {field.name for field in fields(Place)}


def _identity(entry: dict) -> tuple[object, object, object]:
    return entry.get("name"), entry.get("latitude"), entry.get("longitude")


class PlaceStore:
    def __init__(self) -> None:
        self.path = _data_dir() / "places.json"
        self.saved: list[Place] = []
        self.recents: list[Place] = []
        self.viewport: tuple[float, float, float] | None = None
        self.load()

    def load(self) -> None:
        try:
            payload = self._read_payload()
        except (OSError, ValueError, TypeError):
            return
        self.saved = self._places(payload.get("saved", []))
        self.recents = self._places(payload.get("recents", []))
        self.viewport = self._viewport(payload.get("viewport"))

    @staticmethod
    def _places(rows: object) -> list[Place]:
        places = []
        for entry in rows if isinstance(rows, list) else []:
            if not isinstance(entry, dict):
                continue
            try:
                places.append(Place(**{key: value for key, value in entry.items() if key in _PLACE_FIELDS}))
            except TypeError:
                continue
        return places

    def _read_payload(self) -> dict:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("places.json is not an object; refusing to overwrite it")
        return payload

    def _change(self, field: str, edit: Callable[[list], list]) -> None:
        """Read the latest file, change only one list, preserve everything else."""
        payload = self._read_payload()  # corrupt data is an error, never an empty store to overwrite
        old = payload.get(field, [])
        if not isinstance(old, list):
            raise ValueError(f"places.json {field} is not a list; refusing to overwrite it")
        changed = edit(list(old))
        if changed == old:
            return
        payload[field] = changed
        self._write_payload(payload)

    def _write_payload(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        backup = self.path.with_name("places.json.bak-before-lumaui")
        if self.path.exists() and not backup.exists():
            shutil.copy2(self.path, backup)
        # Keep the existing atomic replace, with a fresh read before each edit.
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent, delete=False
        )
        try:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            handle.close()
        os.replace(handle.name, self.path)
        self.load()

    @staticmethod
    def _viewport(value):
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            return None
        try:
            lat, lon, zoom = (float(item) for item in value)
        except (TypeError, ValueError):
            return None
        if all(math.isfinite(item) for item in (lat, lon, zoom)) and -90 <= lat <= 90 and -180 <= lon <= 180 and 0 <= zoom <= 20:
            return (lat, lon, zoom)
        return None

    def remember_viewport(self, latitude, longitude, zoom) -> None:
        """Keep only a manually chosen view, preserving the latest place data."""
        view = self._viewport((latitude, longitude, zoom))
        if view is None:
            return
        payload = self._read_payload()
        if self._viewport(payload.get("viewport")) == view:
            return
        payload["viewport"] = list(view)
        self._write_payload(payload)

    def remember(self, place: Place) -> None:
        def edit(rows: list) -> list:
            # Reordering an existing recent is the only edit. Keep that row
            # verbatim, including fields a newer or other Maps may know.
            existing = next((row for row in rows if isinstance(row, dict)
                             and _identity(row) == (place.name, place.latitude, place.longitude)), None)
            kept = [row for row in rows if not isinstance(row, dict)
                    or _identity(row) != (place.name, place.latitude, place.longitude)]
            return [existing if existing is not None else asdict(place), *kept][:RECENTS_LIMIT]
        self._change("recents", edit)

    def save(self, place: Place) -> None:
        def edit(rows: list) -> list:
            if any(isinstance(row, dict) and _identity(row) == (place.name, place.latitude, place.longitude)
                   for row in rows):
                return rows
            return [*rows, asdict(place)]
        self._change("saved", edit)

    def unsave(self, place: Place) -> None:
        self._change("saved", lambda rows: [row for row in rows if not isinstance(row, dict)
                     or _identity(row) != (place.name, place.latitude, place.longitude)])

    def is_saved(self, place: Place) -> bool:
        return any(item.latitude == place.latitude and item.longitude == place.longitude
                   and item.name == place.name for item in self.saved)

    def clear_recents(self) -> None:
        self._change("recents", lambda _rows: [])

    def clear_saved(self) -> None:
        self._change("saved", lambda _rows: [])
