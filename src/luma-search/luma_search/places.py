# SPDX-License-Identifier: Apache-2.0
"""Filer's places (Home, Applications, Trash, the standard folders, the
user's bookmarks) as Search results.

The table ships as filer-places.json and is shared with the Shell's twin,
js/ui/lumaFilerPlaces.js. Everything that depends on the machine (folders,
bookmarks, language, settings) comes in through ``PlaceEnvironment`` so the
same code runs in the service and in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Callable, Iterable, Mapping
from urllib.parse import unquote, urlparse

from . import ranking

PLACES_FILE = "filer-places.json"
FILER_DOMAIN = "nautilus"
DEFAULT_LIMIT = 4


@dataclass(frozen=True)
class PlaceEnvironment:
    home: str
    special_dirs: Mapping[str, str | None] = field(default_factory=dict)
    bookmarks: str = ""
    translate: Callable[[str], str] = lambda text: text
    remember_recent: bool = True
    personal_storage_only: bool = False
    exists: Callable[[str], bool] = os.path.isdir


def load_table(paths: Iterable[Path]) -> dict:
    for path in paths:
        try:
            return json.loads(Path(path).read_text())
        except (OSError, ValueError):
            continue
    return {"places": []}


def _path_uri(path: str) -> str:
    return Path(path).as_uri()


def _same_uri(uri: str) -> str:
    return uri.rstrip("/") if uri.count("/") > 3 else uri


def _short_path(path: str, home: str) -> str:
    if path == home:
        return "~"
    if path.startswith(home.rstrip("/") + "/"):
        return "~/" + path[len(home.rstrip("/")) + 1:]
    return path


def _bookmarks(text: str) -> list[tuple[str, str]]:
    marks = []
    for line in text.splitlines():
        line = line.strip()
        if not line or "://" not in line.split(" ", 1)[0]:
            continue
        uri, _, label = line.partition(" ")
        marks.append((uri, label.strip()))
    return marks


def _bookmark_name(uri: str) -> str:
    parsed = urlparse(uri)
    name = unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1])
    return name or parsed.netloc or uri


def resolve_places(table: Mapping, env: PlaceEnvironment) -> list[dict]:
    """Every place Filer offers on this machine, in Filer's own words."""
    places: list[dict] = []
    seen: set[str] = set()
    home = env.home.rstrip("/") or "/"

    def add(place_id: str, title: str, uri: str, icon: str, keywords: list[str],
            description: str) -> None:
        key = _same_uri(uri)
        if not title.strip() or key in seen:
            return
        seen.add(key)
        places.append({
            "id": place_id,
            "title": title,
            "uri": uri,
            "icon": icon,
            "keywords": keywords,
            "description": description,
        })

    for entry in table.get("places", []):
        when = entry.get("when")
        if when == "remember-recent-files" and not env.remember_recent:
            continue
        if when == "not-personal-storage-only" and env.personal_storage_only:
            continue
        keywords = list(entry.get("keywords", []))
        label = entry.get("label")
        title = env.translate(label) if label else ""
        # Typing Filer's English word still finds the place in any language.
        if label and title != label:
            keywords.append(label)
        if entry.get("home"):
            path = home
        elif entry.get("xdg"):
            path = env.special_dirs.get(entry["xdg"]) or ""
            path = path.rstrip("/")
            # Unset, missing, or pointing at home: Filer has no such place.
            if not path or path == home or not env.exists(path):
                continue
            title = title or os.path.basename(path)
        else:
            path = ""
        if path:
            add(entry["id"], title, _path_uri(path), entry.get("icon", "folder-symbolic"),
                keywords, _short_path(path, home))
        elif entry.get("uri"):
            add(entry["id"], title, entry["uri"], entry.get("icon", "folder-symbolic"),
                keywords, "Filer")

    for uri, label in _bookmarks(env.bookmarks):
        local = urlparse(uri).scheme == "file"
        path = unquote(urlparse(uri).path) if local else ""
        if local and not env.exists(path):
            continue
        add(f"bookmark:{uri}", label or (os.path.basename(path.rstrip("/")) if local else _bookmark_name(uri)),
            uri, "folder-symbolic" if local else "folder-remote-symbolic", [],
            _short_path(path.rstrip("/") or "/", home) if local else "Filer")
    return places


def _optional_settings(schema_id: str, key: str):
    from gi.repository import Gio

    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(schema_id, True) if source else None
    return Gio.Settings.new(schema_id) if schema and schema.has_key(key) else None


def system_environment() -> PlaceEnvironment:
    """This session's folders, bookmarks, language and Filer settings."""
    import gettext
    from gi.repository import GLib

    special = {}
    for key in ("DESKTOP", "DOCUMENTS", "DOWNLOAD", "MUSIC", "PICTURES", "VIDEOS"):
        special[key] = GLib.get_user_special_dir(getattr(GLib.UserDirectory, f"DIRECTORY_{key}"))
    try:
        bookmarks = (Path(GLib.get_user_config_dir()) / "gtk-3.0" / "bookmarks").read_text()
    except (OSError, UnicodeDecodeError):
        bookmarks = ""
    privacy = _optional_settings("org.gnome.desktop.privacy", "remember-recent-files")
    filer = _optional_settings("org.gnome.nautilus.preferences", "personal-storage-only")
    return PlaceEnvironment(
        home=GLib.get_home_dir(),
        special_dirs=special,
        bookmarks=bookmarks,
        translate=lambda text: gettext.dgettext(FILER_DOMAIN, text),
        remember_recent=privacy.get_boolean("remember-recent-files") if privacy else True,
        personal_storage_only=filer.get_boolean("personal-storage-only") if filer else False,
    )


def score_place(place: Mapping, terms) -> int | None:
    """Filer places rank like Settings pages: name first, then the words
    people use for them."""
    return ranking.score_setting_page(
        {"title": place.get("title"), "keywords": place.get("keywords")}, terms)


def rank_places(places: Iterable[Mapping], terms, limit: int = DEFAULT_LIMIT) -> list[dict]:
    ranked = []
    for index, place in enumerate(places):
        score = score_place(place, terms)
        if score is not None:
            ranked.append({"place": place, "index": index, "score": score})
    ranked.sort(key=lambda item: (-item["score"], item["index"]))
    return ranked[:limit]
