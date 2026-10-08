# SPDX-License-Identifier: Apache-2.0
"""GTK-free Darkroom library data and v70 fixture loading.

The fixture is an in-memory copy of Studio v70's sample library.  It never
opens the user's Pictures folder, Photos database, or Darkroom document store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
from typing import Mapping
from urllib.parse import unquote, urlparse

from .library_metadata import MetadataStore


@dataclass
class LibraryPhoto:
    id: str
    name: str
    path: Path
    stars: int = 0
    flag: int = 0
    label: str | None = None
    adjustments: dict[str, int] = field(default_factory=dict)
    look: str = "As shot"
    crop: dict[str, float] | None = None
    rotation: float = 0
    heal: list[list[float]] = field(default_factory=list)
    masks: dict[str, dict[str, int]] = field(default_factory=dict)
    brush_points: list[list[float]] = field(default_factory=list)
    camera: tuple[str, str, str, str, int] = ("", "", "", "", 0)
    date: str = ""
    time: str = ""
    keywords: tuple[str, ...] = ()
    shoot: str = ""
    albums: tuple[str, ...] = ()
    imported_at: str = ""
    file_label: str = ""
    histogram: tuple[tuple[int, ...], ...] = ()

    @property
    def edited(self) -> bool:
        return self.look != "As shot" or any(self.adjustments.values()) or bool(self.crop or self.rotation or self.heal or self.masks or self.brush_points)


@dataclass
class LibrarySnapshot:
    photos: list[LibraryPhoto]
    source: str = "Pictures"
    shoot: str = ""
    shoot_date: str = ""
    total_count: int = 0
    pick_count: int = 0
    import_count: int = 0
    shoots: tuple[tuple[str, str], ...] = ()
    albums: tuple[tuple[str, int], ...] = ()
    selected: int = 0
    fixture: bool = False

    def current(self) -> LibraryPhoto | None:
        return self.photos[self.selected] if 0 <= self.selected < len(self.photos) else None

    def indices_for(self, collection: str) -> list[int]:
        if collection == "picks":
            return [index for index, photo in enumerate(self.photos) if photo.flag == 1]
        if collection == "last-import":
            latest = max((photo.imported_at[:10] for photo in self.photos if photo.imported_at), default="")
            return [index for index, photo in enumerate(self.photos) if photo.imported_at[:10] == latest] if latest else []
        if collection.startswith("shoot:"):
            name = collection[6:]
            return [index for index, photo in enumerate(self.photos) if photo.shoot == name]
        if collection.startswith("album:"):
            name = collection[6:]
            return [index for index, photo in enumerate(self.photos) if name in photo.albums]
        return list(range(len(self.photos)))


def load_fixture(path: str | Path) -> LibrarySnapshot:
    """Load v70's finite sample exactly, resolving only paths beside its JSON."""
    fixture = Path(path)
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    photos = []
    for item in payload["photos"]:
        image = (fixture.parent / item["image"]).resolve()
        if not image.is_file() or not image.is_relative_to(fixture.parent.resolve()):
            raise ValueError(f"fixture image is missing or outside fixture: {item['image']}")
        photos.append(LibraryPhoto(
            id=item["id"], name=item["name"], path=image,
            stars=item["stars"], flag=item["flag"], label=item["label"],
            adjustments=dict(item.get("adjustments", {})), look=item.get("look", "As shot"),
            crop=item.get("crop"), rotation=item.get("rotation", 0),
            heal=item.get("heal", []), masks=item.get("masks", {}),
            camera=tuple(item["camera"]), date=item["date"], time=item["time"],
            keywords=tuple(item.get("keywords", ())),
            shoot=payload["shoot"], imported_at="2026-09-21",
            file_label=f"DSCF_{item['id'].removeprefix('life-').replace('-', '')}.RAF · 26 MB",
            histogram=tuple(tuple(channel) for channel in item.get("histogram", ())),
        ))
    return LibrarySnapshot(
        photos=photos, source=payload["source"], shoot=payload["shoot"],
        shoot_date=payload["shoot_date"], total_count=payload["total_count"],
        pick_count=payload["pick_count"], import_count=payload["import_count"],
        shoots=tuple(tuple(row) for row in payload["shoots"]),
        albums=tuple((name, count) for name, count in payload["albums"]),
        selected=payload["selected"], fixture=True,
    )


IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp", ".gif",
                            ".heif", ".heic", ".avif", ".dng", ".nef", ".cr2", ".cr3", ".arw",
                            ".raf", ".orf", ".rw2"})


def _photo_path(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme not in ("", "file") or parsed.netloc not in ("", "localhost"):
        return None
    return Path(unquote(parsed.path if parsed.scheme else uri))


def _date_parts(value: str | None) -> tuple[str, str]:
    if not value:
        return "", ""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "", ""
    return moment.strftime("%b %-d"), moment.strftime("%-I:%M %p")


def load_real_library(metadata: MetadataStore, environment: Mapping[str, str] | None = None) -> LibrarySnapshot:
    """Read Photos' library through SQLite mode=ro; fall back to Pictures files.

    Never instantiate PhotosBackend: its constructor can migrate/scan.  The
    fallback reads filenames and mtimes without creating any library data.
    """
    env = os.environ if environment is None else environment
    data = Path(env.get("XDG_DATA_HOME") or Path(env.get("HOME", str(Path.home()))) / ".local/share")
    database = data / "luma-photos" / "library.sqlite3"
    edits = metadata.all()
    photos: list[LibraryPhoto] = []
    albums: list[tuple[str, int]] = []
    source_names: set[str] = set()
    if database.is_file():
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(assets)")}
            imported_column = "a.created_at" if "created_at" in columns else "a.modified_at"
            rows = connection.execute(
                f"""SELECT a.id, a.display_name, a.captured_at, a.modified_at, {imported_column} AS imported_at,
                          c.uri, s.name AS source_name
                   FROM assets a
                   JOIN copies c ON c.asset_id = a.id
                   JOIN sources s ON s.id = c.source_id
                   WHERE a.media_type = 'image' AND c.trashed = 0 AND c.reachable = 1
                   ORDER BY COALESCE(a.captured_at, a.modified_at) DESC, a.id, c.modified_ns DESC"""
            )
            memberships: dict[str, list[str]] = {}
            for membership in connection.execute(
                """SELECT aa.asset_id, a.name FROM album_assets aa
                   JOIN albums a ON a.id = aa.album_id"""):
                memberships.setdefault(membership["asset_id"], []).append(membership["name"])
            seen: set[str] = set()
            for row in rows:
                if row["id"] in seen:
                    continue
                path = _photo_path(row["uri"])
                if path is None:
                    continue
                seen.add(row["id"])
                source_names.add(row["source_name"])
                saved = edits.get(row["id"], {})
                date, time = _date_parts(row["captured_at"] or row["modified_at"])
                photos.append(LibraryPhoto(
                    id=row["id"], name=row["display_name"] or path.stem, path=path,
                    stars=saved.get("stars", 0), flag=saved.get("flag", 0), label=saved.get("label"),
                    date=date, time=time, keywords=tuple(saved.get("keywords", ())),
                    shoot=saved.get("shoot", ""),
                    albums=tuple(dict.fromkeys((*memberships.get(row["id"], ()), *saved.get("albums", ())))),
                    imported_at=row["imported_at"] or "",
                ))
            albums = [(row["name"], row["count"]) for row in connection.execute(
                """SELECT a.name, COUNT(aa.asset_id) AS count FROM albums a
                   LEFT JOIN album_assets aa ON aa.album_id = a.id
                   GROUP BY a.id ORDER BY a.name COLLATE NOCASE""")]
    else:
        pictures = Path(env.get("XDG_PICTURES_DIR") or Path(env.get("HOME", str(Path.home()))) / "Pictures")
        if pictures.is_dir():
            for path in sorted(pictures.iterdir(), key=lambda item: item.stat().st_mtime if item.is_file() else 0,
                               reverse=True):
                if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                identity = path.resolve().as_uri()
                saved = edits.get(identity, {})
                date, time = _date_parts(datetime.fromtimestamp(path.stat().st_mtime).isoformat())
                photos.append(LibraryPhoto(
                    id=identity, name=path.stem, path=path,
                    stars=saved.get("stars", 0), flag=saved.get("flag", 0), label=saved.get("label"),
                    date=date, time=time, keywords=tuple(saved.get("keywords", ())),
                    shoot=saved.get("shoot", ""), albums=tuple(saved.get("albums", ())),
                    imported_at=datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                ))
        source_names.add(pictures.name)
    shoots = sorted({photo.shoot for photo in photos if photo.shoot})
    latest = max((photo.imported_at[:10] for photo in photos if photo.imported_at), default="")
    source = next(iter(source_names)) if len(source_names) == 1 else "Photos"
    return LibrarySnapshot(
        photos=photos, source=source, shoot="", shoot_date="",
        total_count=len(photos), pick_count=sum(photo.flag == 1 for photo in photos),
        import_count=sum(photo.imported_at[:10] == latest for photo in photos) if latest else 0,
        shoots=tuple((name, "") for name in shoots), albums=tuple(albums), selected=0,
    )
