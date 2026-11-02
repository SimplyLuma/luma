# SPDX-License-Identifier: Apache-2.0
"""What Depot knows about an app on a TV: does it run there, and how is it driven.

Depot on a TV lists only apps that run on a TV. Two facts per app, read from data the
app or the catalogue publishes, never from a list in code:

1. The Luma catalogue's ``tv`` field (schema 4, optional: ``{"runs": bool, "input":
   "remote" | "controller" | "keyboard"}``), Luma's word where an app's own metadata is
   missing or wrong.
2. The app's own declarations:
   - its TV opt-in (``X-Luma-TV`` in its desktop entry, from ``[tv]`` in luma-app.toml): it
     runs on a TV; with its own TV presentation (``X-Luma-TV-Presentation=tv``) it is driven
     with the remote, as every TV presentation is;
   - its AppStream ``<supports>``/``<recommends>``/``<requires>`` ``<control>`` and
     ``<display_length>``: ``tv-remote`` means the remote works; ``gamepad`` recommended or
     required means it needs a controller; ``keyboard`` or ``pointing`` required (with no
     remote) means it is best with a keyboard; a display length that excludes a TV-sized
     screen means it does not run on one.

An app with none of these facts is not listed on a TV. The architecture rule is the
catalogue's: an app is listed only when it is installed here or has a build for this
machine (``App.installable`` already includes ``arch in entry.architectures``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
import xml.etree.ElementTree as ET

from gi.repository import Gio, GLib

REMOTE, CONTROLLER, KEYBOARD = "remote", "controller", "keyboard"
#: The words the app page uses for each (the desktop's wording; non-remote needs in amber).
INPUT_TEXT = {REMOTE: "Works with your remote", CONTROLLER: "Needs a controller", KEYBOARD: "Best with a keyboard"}
#: AppStream display_length names, in logical pixels (AppStream 1.0).
_LENGTHS = {"xsmall": 360, "small": 420, "medium": 760, "large": 900, "xlarge": 1200}
#: The shortest edge of a TV's layout in logical pixels (1920 x 1080 at scale 1).
_TV_SHORT_EDGE = 1080


@dataclass(frozen=True)
class TvFacts:
    runs: bool
    input: str = ""
    #: Where the facts came from: "catalogue", "app" (its own TV opt-in or AppStream).
    source: str = ""

    @property
    def input_text(self) -> str:
        return INPUT_TEXT.get(self.input, "")


@dataclass(frozen=True)
class AppStreamFacts:
    """What an installed app's own AppStream metadata says (empty when it has none here)."""

    controls: dict
    display_max: int = 0          # the largest display it allows (0: no limit)
    keywords: tuple[str, ...] = ()
    brand: str = ""               # <branding><color type="primary">
    categories: tuple[str, ...] = ()


_EMPTY = AppStreamFacts({})


def component_id(app) -> str:
    """The AppStream component / application id of a Depot `App`."""
    identity = getattr(app, "flatpak_id", "") or ""
    if identity:
        return identity
    app_id = getattr(app, "app_id", "")
    return app_id.removesuffix(".desktop") if not app_id.startswith("catalog:") else ""


def desktop_info(app) -> Gio.DesktopAppInfo | None:
    """The installed launcher of an app, as the session sees it (user entries first)."""
    candidates = []
    app_id = getattr(app, "app_id", "")
    if app_id.endswith(".desktop"):
        candidates.append(app_id)
    component = component_id(app)
    if component:
        candidates.append(component + ".desktop")
    for candidate in candidates:
        try:
            info = Gio.DesktopAppInfo.new(candidate)
        except (TypeError, GLib.Error):
            info = None
        if info is not None:
            return info
    return None


def _metainfo_paths(component: str):
    dirs = [GLib.get_user_data_dir()] + list(GLib.get_system_data_dirs())
    for base in dirs:
        for sub, suffix in (("metainfo", ".metainfo.xml"), ("metainfo", ".appdata.xml"),
                            ("appdata", ".appdata.xml"), ("appdata", ".metainfo.xml")):
            yield os.path.join(base, sub, component + suffix)


def _length(node) -> int:
    text = (node.text or "").strip()
    if text.isdigit():
        return int(text)
    return _LENGTHS.get(text, 0)


@lru_cache(maxsize=256)
def _read_metainfo(path: str, _stamp: int) -> AppStreamFacts:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return _EMPTY
    component = root if root.tag == "component" else root.find("component")
    if component is None:
        return _EMPTY
    controls: dict[str, set[str]] = {}
    display_max = 0
    for relation in ("supports", "recommends", "requires"):
        for group in component.findall(relation):
            for control in group.findall("control"):
                controls.setdefault(relation, set()).add((control.text or "").strip())
            for length in group.findall("display_length"):
                compare = length.get("compare", "ge")
                value = _length(length)
                if compare in ("le", "lt") and value and relation in ("requires", "recommends"):
                    display_max = value if not display_max else min(display_max, value)
    keywords = tuple((k.text or "").strip() for k in component.findall("keywords/keyword")
                     if (k.text or "").strip() and not k.get("{http://www.w3.org/XML/1998/namespace}lang"))
    brand = ""
    for colour in component.findall("branding/color"):
        if colour.get("type") == "primary":
            brand = (colour.text or "").strip()
            if colour.get("scheme_preference") == "dark":
                break
    categories = tuple((c.text or "").strip() for c in component.findall("categories/category"))
    return AppStreamFacts({k: frozenset(v) for k, v in controls.items()}, display_max, keywords, brand, categories)


def appstream(component: str) -> AppStreamFacts:
    """The installed app's own AppStream metadata (metainfo in the session's data directories)."""
    if not component:
        return _EMPTY
    for path in _metainfo_paths(component):
        try:
            stamp = os.stat(path).st_mtime_ns
        except OSError:
            continue
        return _read_metainfo(path, stamp)
    return _EMPTY


def from_appstream(meta: AppStreamFacts) -> TvFacts | None:
    """TV facts from AppStream alone; None when it says nothing about a TV."""
    if meta.display_max and meta.display_max < _TV_SHORT_EDGE:
        return TvFacts(False, "", "app")
    every = set().union(*meta.controls.values()) if meta.controls else set()
    wanted = meta.controls.get("requires", frozenset()) | meta.controls.get("recommends", frozenset())
    if "gamepad" in wanted:
        return TvFacts(True, CONTROLLER, "app")
    if "tv-remote" in every:
        return TvFacts(True, REMOTE, "app")
    if "gamepad" in every:
        return TvFacts(True, CONTROLLER, "app")
    return None


def from_opt_in(info: Gio.DesktopAppInfo | None, meta: AppStreamFacts) -> TvFacts | None:
    """TV facts from the app's TV opt-in (X-Luma-TV); None when it has not opted in or out."""
    if info is None or not info.has_key("X-Luma-TV"):
        return None
    if not info.get_boolean("X-Luma-TV"):
        return TvFacts(False, "", "app")
    found = from_appstream(meta)
    if found is not None and found.input == CONTROLLER:
        return TvFacts(True, CONTROLLER, "app")
    if (info.get_string("X-Luma-TV-Presentation") or "") == "tv":
        return TvFacts(True, REMOTE, "app")
    if found is not None and found.runs:
        return found
    required = meta.controls.get("requires", frozenset())
    return TvFacts(True, KEYBOARD if required & {"keyboard", "pointing"} or not found else REMOTE, "app")


def resolve(catalogue_tv, info: Gio.DesktopAppInfo | None, meta: AppStreamFacts) -> TvFacts | None:
    """The catalogue's word first, then the app's own TV opt-in, then its AppStream."""
    if catalogue_tv is not None:
        mine = from_opt_in(info, meta) or from_appstream(meta)
        given = catalogue_tv.input or (mine.input if mine is not None else "") or (REMOTE if catalogue_tv.runs else "")
        return TvFacts(catalogue_tv.runs, given if catalogue_tv.runs else "", "catalogue")
    return from_opt_in(info, meta) or from_appstream(meta)


@lru_cache(maxsize=1)
def _catalogue_entries(_stamp: int):
    try:
        from luma_installer.depot_catalog import local_catalog
        return {"catalog:" + entry.id: entry for entry in local_catalog().applications}
    except Exception:  # noqa: BLE001 - no readable catalogue: the apps' own facts decide
        return {}


def catalogue_entry(app):
    try:
        from luma_installer.depot_catalog import cache_path
        stamp = os.stat(cache_path()).st_mtime_ns
    except (OSError, ImportError):
        stamp = 0
    return _catalogue_entries(stamp).get(getattr(app, "app_id", ""))


@dataclass(frozen=True)
class TvApp:
    """An app as Depot on a TV sees it: the listing, its TV facts and what search reads."""

    app: object
    facts: TvFacts
    info: Gio.DesktopAppInfo | None
    keywords: tuple[str, ...]
    brand: str
    game: bool
    icon: Gio.Icon | None


def tv_app(app, *, record=None) -> TvApp | None:
    """`app` with its TV facts, or None when it has none (it is then not listed on a TV)."""
    info = desktop_info(app)
    meta = appstream(component_id(app) or (info.get_id().removesuffix(".desktop") if info else ""))
    entry = catalogue_entry(app)
    facts = resolve(getattr(entry, "tv", None), info, meta)
    if facts is None or not facts.runs:
        return None
    keywords = list(getattr(entry, "keywords", ()) or ()) + list(meta.keywords)
    if info is not None:
        keywords += list(info.get_keywords() or ())
    categories = set(meta.categories)
    if info is not None and info.get_categories():
        categories |= set(info.get_categories().split(";"))
    entry_categories = {c.casefold() for c in getattr(entry, "categories", ()) or ()}
    game = "Game" in categories or bool(entry_categories & {"games", "game"})
    icon = info.get_icon() if info is not None else None
    if icon is None and record is not None and record.app is not None and record.app.icon_name:
        try:
            icon = Gio.Icon.new_for_string(record.app.icon_name)
        except GLib.Error:
            icon = None
    return TvApp(app, facts, info, tuple(dict.fromkeys(k for k in keywords if k)), meta.brand, game, icon)


def matches(item: TvApp, query: str) -> bool:
    """Search over what people know an app by: its name, summary, developer and keywords."""
    text = query.strip().casefold()
    if not text:
        return False
    app = item.app
    words = " ".join((app.name, app.summary, getattr(app, "developer", ""), *item.keywords)).casefold()
    return text in words


def forget() -> None:
    """Read the apps' metadata again (after installs, removals and updates)."""
    _read_metainfo.cache_clear()
    _catalogue_entries.cache_clear()
