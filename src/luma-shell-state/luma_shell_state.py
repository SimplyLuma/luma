#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

"""Pure validation and snapshot helpers for Luma shell state."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from collections.abc import Iterable, Mapping
from typing import Any


SCHEMA_VERSION = 2
APPEARANCE_VALUES = frozenset({"default", "prefer-light", "prefer-dark"})
LUMA_GTK3_THEMES = frozenset({"Luma", "Luma-dark"})
MAX_APP_ID_BYTES = 255
MAX_WALLPAPER_URI_BYTES = 4096
SHELF_LAYOUT_VERSION = 1
SHELF_EDGE_VALUES = frozenset({"bottom", "top", "left", "right"})
SHELF_ANCHOR_VALUES = frozenset({"start", "center", "end"})
SHELF_GROUP_LAYOUT_VALUES = frozenset({"centered-combined", "split-ends", "luma"})
SHELF_SURFACE_MODE_VALUES = frozenset({"separate", "connected"})
SHELF_EDGE_MODE_VALUES = frozenset({"floating", "protruding"})
SHELF_MATERIAL_VALUES = frozenset({"dark", "light", "glass", "frost"})
SHELF_MONITOR_MODE_VALUES = frozenset({"primary", "all"})
SHELF_OVERFLOW_MODE_VALUES = frozenset({"scroll"})
SHELF_ISLAND_VALUES = frozenset({"dock", "actions"})
# ADR-044: the islands a user can place, in the default row's order, and the
# placements a group of them can take.
SHELF_PLACEABLE_ISLANDS = (
    "dock", "live", "media", "well", "quick-options", "clock", "notifications",
)
SHELF_PLACEMENT_ANCHORS = frozenset({"start", "center", "end", "free"})
MAX_DISPLAY_ID_BYTES = 512


class InvalidState(ValueError):
    """Raised when a shell-state mutation violates the public contract."""


PRESENTATION_MODES = frozenset({"automatic", "desktop", "tablet", "mobile"})


def normalize_home_layout(profile: str, value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a cell-based Home transaction, including widget occupancy."""
    match = re.fullmatch(r"(mobile|tablet):([1-9][0-9]?)x([1-9][0-9]?)", profile) if isinstance(profile, str) else None
    if not match or not isinstance(value, Mapping):
        raise InvalidState("invalid Home layout profile")
    columns, rows = int(match[2]), int(match[3])
    if not 1 <= columns <= 32 or not 1 <= rows <= 32:
        raise InvalidState("Home grid dimensions are out of range")
    if set(value) - {"version", "columns", "rows", "pages"} or type(value.get("version", 1)) is not int or value.get("version", 1) != 1:
        raise InvalidState("unsupported Home layout")
    for key, expected in (("columns", columns), ("rows", rows)):
        if type(value.get(key)) is not int or value[key] != expected:
            raise InvalidState("Home dimensions do not match profile")
    pages = value.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= 64:
        raise InvalidState("Home must contain between one and 64 pages")
    normalized, seen = [], set()
    for page in pages:
        if not isinstance(page, list):
            raise InvalidState("Home pages must contain item lists")
        occupied, result = set(), []
        for item in page:
            if not isinstance(item, Mapping) or set(item) != {"id", "kind", "x", "y", "w", "h"}:
                raise InvalidState("invalid Home item")
            identity, kind = item["id"], item["kind"]
            if kind == "app":
                normalize_app_ids([identity])
            elif kind == "widget":
                if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}", identity):
                    raise InvalidState("invalid widget identity")
            else:
                raise InvalidState("unrecognized Home item kind")
            if identity in seen or len(seen) >= 1024:
                raise InvalidState("duplicate or excessive Home items")
            seen.add(identity)
            if any(type(item[key]) is not int for key in ("x", "y", "w", "h")):
                raise InvalidState("Home cells must use whole integers")
            x, y, width, height = (item[key] for key in ("x", "y", "w", "h"))
            if x < 0 or y < 0 or width < 1 or height < 1 or x + width > columns or y + height > rows:
                raise InvalidState("Home item extends beyond the grid")
            if kind == "app" and (width, height) != (1, 1):
                raise InvalidState("app icons occupy one cell")
            cells = {(xx, yy) for xx in range(x, x + width) for yy in range(y, y + height)}
            if cells & occupied:
                raise InvalidState("Home items overlap")
            occupied.update(cells)
            result.append(dict(item))
        normalized.append(result)
    return {"version": 1, "columns": columns, "rows": rows, "pages": normalized}


def presentation_profile(requested: str = "", device_class: str = "",
                         chassis_type: int | None = None,
                         device_tree_chassis: str = "") -> str:
    """Use an explicit boot profile, then standardized hardware categories."""
    if requested == "handheld":
        return "mobile"
    if requested in {"desktop", "tablet", "mobile"}:
        return requested
    if device_class in {"mobile", "handheld"} or device_tree_chassis == "handset":
        return "mobile"
    if device_class == "tablet" or device_tree_chassis == "tablet" or chassis_type in {30, 32}:
        return "tablet"  # SMBIOS Tablet and Detachable, independent of vendor.
    return "desktop"


def read_presentation_profile(root: Path = Path("/")) -> str:
    """Read bounded boot/device hints; input availability never identifies a form factor."""
    def read(name: str) -> str:
        try:
            with (root / name).open("rb") as stream:
                value = stream.read(129)
            return value.decode("utf-8").rstrip("\0").strip() if len(value) <= 128 else ""
        except (OSError, UnicodeError):
            return ""

    raw_chassis = read("sys/class/dmi/id/chassis_type")
    return presentation_profile(
        read("run/luma/experience"), read("etc/luma-device-class"),
        int(raw_chassis) if raw_chassis.isascii() and raw_chassis.isdecimal() else None,
        read("sys/firmware/devicetree/base/chassis-type") or read("proc/device-tree/chassis-type"))


def normalize_presentation(value: str) -> str:
    if not isinstance(value, str) or value not in PRESENTATION_MODES:
        raise InvalidState("presentation must be automatic, desktop, tablet or mobile")
    return value


def normalize_capabilities(value: Mapping[str, Any]) -> dict[str, Any]:
    """Unknown posture stays unknown; a missing keyboard is not a tablet signal."""
    allowed = {"touchscreen", "tablet_mode", "keyboard", "pointer"}
    if not isinstance(value, Mapping) or set(value) - allowed:
        raise InvalidState("unrecognized input capability")
    result = {key: value.get(key) for key in sorted(allowed)}
    if any(item is not None and type(item) is not bool for item in result.values()):
        raise InvalidState("capabilities must be booleans or null")
    return result


def presentation_state(selected: str = "automatic", *, profile: str = "desktop",
                       capabilities: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Resolve policy independently of CPU, viewport and last-used input.

    A compositor reports native seat posture (including the standard tablet
    switch). A boot experience supplies the fallback when hardware is unknown.
    Phone profiles remain phones when a keyboard is connected. User choice wins.
    """
    selected = normalize_presentation(selected)
    capabilities = normalize_capabilities(capabilities or {})
    if profile == "handheld":
        profile = "mobile"
    if profile not in {"desktop", "tablet", "mobile"}:
        profile = "desktop"
    if selected != "automatic":
        effective, reason = selected, "user"
    elif profile == "mobile":
        effective, reason = "mobile", "profile"
    elif capabilities["touchscreen"] is True and capabilities["tablet_mode"] is not None:
        effective = "tablet" if capabilities["tablet_mode"] else "desktop"
        reason = "posture"
    else:
        effective, reason = profile, "profile"
    return {"selected": selected, "effective": effective, "reason": reason,
            "profile": profile, "capabilities": capabilities}


def normalize_app_ids(values: Iterable[str]) -> list[str]:
    """Validate, de-duplicate, and retain the caller's application order."""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise InvalidState("application IDs must be strings")
        if not value or len(value.encode("utf-8")) > MAX_APP_ID_BYTES:
            raise InvalidState("application ID has an invalid length")
        if not value.endswith(".desktop") or "/" in value or "\x00" in value:
            raise InvalidState(f"invalid desktop application ID: {value!r}")
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def normalize_wallpaper_uri(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidState("wallpaper URI must be a string")
    if not value.startswith("file://"):
        raise InvalidState("wallpaper URI must use the local file scheme")
    if "\x00" in value or len(value.encode("utf-8")) > MAX_WALLPAPER_URI_BYTES:
        raise InvalidState("wallpaper URI has an invalid length")
    return value


def normalize_appearance(value: str) -> str:
    if value not in APPEARANCE_VALUES:
        choices = ", ".join(sorted(APPEARANCE_VALUES))
        raise InvalidState(f"appearance must be one of: {choices}")
    return value


def gtk_theme_for_appearance(value: str, treatment: str | None = None) -> str:
    """Return the supported GTK 3 theme matching Luma's appearance state."""

    appearance = normalize_appearance(value)
    if treatment in {"frost", "glass"}:
        # GTK 3 has no live translucent renderer, and neither has anything
        # that reads its window colours from the GTK 3 theme -- Chromium and
        # the Electron applications on it draw their decorations that way.
        # They take one defined stand-in for the title band rather than
        # Adwaita's window colour, which was a different colour again from
        # every window that can be translucent.
        return "Luma-translucent"
    if treatment in {"light", "dark"}:
        return "Luma-dark" if treatment == "dark" else "Luma"
    return "Luma-dark" if appearance == "prefer-dark" else "Luma"


def presentation_idle_default(mode: str, user_value: int | None, owned_value: int = -1) -> int | None:
    """Choose a screen-idle default without replacing a person's preference.

    owned_value records the broker's last projection across restarts. A value
    changed through another settings client immediately becomes user-owned.
    Zero is an explicit never-idle preference and is preserved.
    """
    if mode not in {"desktop", "tablet", "mobile"}:
        raise InvalidState("an effective presentation is required")
    if user_value is not None and (owned_value < 0 or user_value != owned_value):
        return None
    return {"desktop": 300, "tablet": 120, "mobile": 60}[mode]


HANDHELD_BUTTON_LAYOUT = ":"


def presentation_button_layout(mode: str, user_value: str | None, saved: str | None) -> tuple[str | None, str | None]:
    """Window controls per presentation, without losing a person's desktop layout.

    Tablet and Mobile navigation own close and switching, so their windows show
    no minimize/maximize/close; Desktop keeps the person's layout. `saved` is
    the desktop layout kept while a handheld presentation owns the setting
    ("" means it had no user value). Returns (layout, saved): layout None
    leaves the setting alone, "" resets it to the system default. A layout
    chosen while handheld is the person's and ends the ownership.
    """
    if mode not in {"desktop", "tablet", "mobile"}:
        raise InvalidState("an effective presentation is required")
    if mode == "desktop":
        return (None, None) if saved is None else (saved, None)
    if saved is not None:
        return (None, saved) if user_value == HANDHELD_BUTTON_LAYOUT else (None, None)
    if user_value == HANDHELD_BUTTON_LAYOUT:
        return None, None
    return HANDHELD_BUTTON_LAYOUT, user_value or ""


def normalize_idle_delay(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidState("idle delay must be an integer")
    if value != 0 and not 15 <= value <= 86400:
        raise InvalidState("idle delay must be 0 or between 15 and 86400 seconds")
    return value


def _normalize_choice(name: str, value: str, choices: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        allowed = ", ".join(sorted(choices))
        raise InvalidState(f"{name} must be one of: {allowed}")
    return value


def normalize_status(status: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Bounded, additive Dash appearance state; malformed colours use the UI default."""
    values = {
        "frame": "none", "frame_scope": "all", "divider": True,
        "glyph_tiles": True, "controls_right": True, "fill_strength": "14",
        "clock_fill": "#7f8892", "controls_fill": "#7f8892",
        "time_size": 21, "date_size": 11,
    }
    if status is not None:
        values.update({key: value for key, value in status.items() if key in values})
    for key, choices in {
        "frame": {"none", "stroke", "fill", "both"},
        "frame_scope": {"all", "controls", "split", "joined", "flush"},
        "fill_strength": {"14", "34", "100"},
    }.items():
        values[key] = _normalize_choice(key, values[key], frozenset(choices))
    for key, low, high in (("time_size", 10, 21), ("date_size", 7, 14)):
        if type(values[key]) is not int or not low <= values[key] <= high:
            raise InvalidState(f"{key} must be an integer between {low} and {high}")
    for key in ("divider", "glyph_tiles", "controls_right"):
        if type(values[key]) is not bool:
            raise InvalidState(f"{key} must be a boolean")
    for key in ("clock_fill", "controls_fill"):
        color = values[key]
        if not isinstance(color, str) or len(color) != 7 or color[0] != "#" or any(
            c not in "0123456789abcdefABCDEF" for c in color[1:]
        ):
            values[key] = "#7f8892"
    return values


def normalize_shelf(
    *,
    layout_version: int,
    edge: str,
    dock_anchor: str,
    actions_anchor: str,
    group_layout: str,
    surface_mode: str,
    edge_mode: str,
    material: str,
    monitor_mode: str,
    overflow_mode: str,
    dock_visible: bool,
    actions_visible: bool,
    reserve_work_area: bool,
    auto_hide: bool,
    islands: Iterable[str],
    span_full: bool = False,
    float_ends: bool = True,
    padding: int = 10,
    status: Mapping[str, Any] | None = None,
    arrangement: Iterable[Mapping[str, Any]] | None = None,
    free_placement: bool = False,
) -> dict[str, Any]:
    """Validate the renderer-independent Shelf layout contract."""

    if layout_version != SHELF_LAYOUT_VERSION:
        raise InvalidState(f"unsupported Shelf layout version: {layout_version}")
    if type(padding) is not int or not 4 <= padding <= 24:
        raise InvalidState("Dash padding must be an integer between 4 and 24 pixels")
    normalized_islands: list[str] = []
    for island in islands:
        if island not in SHELF_ISLAND_VALUES:
            raise InvalidState(f"unknown Shelf island: {island!r}")
        if island not in normalized_islands:
            normalized_islands.append(island)
    if bool(auto_hide):
        raise InvalidState("Shelf auto-hide is reserved and unavailable in layout v1")
    return {
        "layout_version": layout_version,
        "edge": _normalize_choice("Shelf edge", edge, SHELF_EDGE_VALUES),
        "dock_anchor": _normalize_choice("Shelf dock anchor", dock_anchor, SHELF_ANCHOR_VALUES),
        "actions_anchor": _normalize_choice("Shelf actions anchor", actions_anchor, SHELF_ANCHOR_VALUES),
        "group_layout": _normalize_choice("Shelf group layout", group_layout, SHELF_GROUP_LAYOUT_VALUES),
        "span_full": bool(span_full),
        "float_ends": bool(float_ends),
        "padding": padding,
        "status": normalize_status(status),
        "surface_mode": _normalize_choice("Shelf surface mode", surface_mode, SHELF_SURFACE_MODE_VALUES),
        "edge_mode": _normalize_choice("Shelf edge mode", edge_mode, SHELF_EDGE_MODE_VALUES),
        "material": _normalize_choice("Shelf material", material, SHELF_MATERIAL_VALUES),
        "monitor_mode": _normalize_choice("Shelf monitor mode", monitor_mode, SHELF_MONITOR_MODE_VALUES),
        "overflow_mode": _normalize_choice("Shelf overflow mode", overflow_mode, SHELF_OVERFLOW_MODE_VALUES),
        "dock_visible": bool(dock_visible),
        "actions_visible": bool(actions_visible),
        "reserve_work_area": bool(reserve_work_area),
        "auto_hide": False,
        "islands": normalized_islands,
        "arrangement": normalize_arrangement(arrangement, free_placement=bool(free_placement)),
        "free_placement": bool(free_placement),
    }


def _nearest_anchor(position: float) -> str:
    return "start" if position < 0.25 else "end" if position > 0.75 else "center"


def normalize_arrangement(
    groups: Iterable[Mapping[str, Any]] | None,
    *,
    free_placement: bool = True,
) -> list[dict[str, Any]]:
    """Normalise shelf-arrangement (aa{sv}) the way the Shell reads it.

    Unknown ids are dropped, duplicate ids keep their first placement, a group
    on an unknown edge is discarded, a position is clamped to 0..1, and an
    anchor outside start/center/end/free becomes center. An empty result means
    the default layout. Islands missing from a non-empty arrangement are left
    for the Shell to place beside their default neighbours: this model keeps
    what the user stored and never invents a placement.
    """

    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for group in groups or []:
        if not isinstance(group, Mapping):
            continue
        edge = group.get("edge")
        if edge not in SHELF_EDGE_VALUES:
            continue
        islands: list[str] = []
        raw_islands = group.get("islands") or []
        if isinstance(raw_islands, str):
            raw_islands = [raw_islands]
        for island in raw_islands:
            if island in SHELF_PLACEABLE_ISLANDS and island not in seen:
                seen.add(island)
                islands.append(island)
        if not islands:
            continue
        anchor = group.get("anchor")
        if anchor not in SHELF_PLACEMENT_ANCHORS:
            anchor = "center"
        try:
            position = float(group.get("position", 0.5))
        except (TypeError, ValueError):
            position = 0.5
        if position != position:  # NaN
            position = 0.5
        display = group.get("display", "")
        if not isinstance(display, str) or len(display.encode("utf-8")) > MAX_DISPLAY_ID_BYTES:
            display = ""
        position = min(1.0, max(0.0, position))
        # Without free placement a free group is drawn at the nearest anchor.
        if anchor == "free" and not free_placement:
            anchor = _nearest_anchor(position)
        normalized.append({
            "display": display,
            "edge": edge,
            "anchor": anchor,
            "position": position if anchor == "free" else 0.5,
            "islands": islands,
        })
    return normalized


def detect_renderer(environment: Mapping[str, str] | None = None) -> str:
    environment = os.environ if environment is None else environment
    desktop = ":".join(
        filter(
            None,
            (
                environment.get("XDG_CURRENT_DESKTOP", ""),
                environment.get("XDG_SESSION_DESKTOP", ""),
                environment.get("DESKTOP_SESSION", ""),
            ),
        )
    ).lower()
    if "phosh" in desktop:
        return "phosh"
    if "gnome" in desktop or "luma" in desktop:
        return "gnome"
    return "unknown"


def build_snapshot(
    *,
    favorites: Iterable[str],
    home_apps: Iterable[str],
    wallpaper_uri: str,
    wallpaper_uri_dark: str,
    wallpaper_style: str,
    appearance: str,
    idle_delay: int,
    screen_keyboard_enabled: bool,
    launcher_columns: int,
    input_method: str,
    restore_app_state: bool,
    renderer: str,
    shelf: Mapping[str, Any] | None = None,
    presentation: Mapping[str, Any] | None = None,
    home_layouts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    canonical_favorites = normalize_app_ids(favorites)
    canonical_home_apps = normalize_app_ids(home_apps)
    return {
        "schema_version": SCHEMA_VERSION,
        "renderer": renderer,
        "favorites": canonical_favorites,
        "home_apps": canonical_home_apps or canonical_favorites,
        "wallpaper": {
            "light": normalize_wallpaper_uri(wallpaper_uri),
            "dark": normalize_wallpaper_uri(wallpaper_uri_dark),
            "style": wallpaper_style,
        },
        "appearance": normalize_appearance(appearance),
        "power": {"idle_delay_seconds": normalize_idle_delay(idle_delay)},
        "input": {
            "screen_keyboard_enabled": bool(screen_keyboard_enabled),
            "method": input_method,
        },
        "mobile": {
            "launcher_columns": int(launcher_columns),
            "restore_app_state": bool(restore_app_state),
        },
        "shelf": dict(shelf or {}),
        "presentation": dict(presentation or presentation_state()),
        "home_layouts": dict(home_layouts or {}),
    }


def encode_snapshot(snapshot: Mapping[str, Any]) -> str:
    return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
