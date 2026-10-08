# SPDX-License-Identifier: Apache-2.0
"""Tier 1 tools: personal settings and apps (brief §8.2).

Every change goes through the backend the Settings app and the shell already
use: the shell-state service and its GSettings schema, GNOME's interface keys
and Mutter's DisplayConfig. Nothing writes a value Settings doesn't know about.
Each change returns an undo handle: the tool and arguments that put it back.
"""
from __future__ import annotations

import os
import random
import re
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from ari.mcp import serve  # noqa: E402

SHELL_STATE = "org.project_luma.shell-state"
INTERFACE = "org.gnome.desktop.interface"
BACKGROUND = "org.gnome.desktop.background"
COLOR = "org.gnome.settings-daemon.plugins.color"
EDGES = ("bottom", "top", "left", "right")
TREATMENTS = ("light", "dark", "frost", "glass")
ACCENTS = ("blue", "teal", "green", "yellow", "orange", "red", "pink", "purple", "slate")
WALLPAPER_DIRS = (Path("/usr/share/backgrounds/luma"), Path.home() / ".local/share/backgrounds")
WALLPAPER_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".jxl", ".svg"}


def _settings(schema: str) -> Gio.Settings | None:
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(schema, True) is None:
        return None
    return Gio.Settings.new(schema)


def _missing(what: str) -> dict:
    return {"ok": False, "summary": f"{what} isn't available on this machine."}


def _sync() -> None:
    Gio.Settings.sync()


# The evaluation suite runs these tools against GSettings' memory backend and
# must not open windows or change a real display while it does.
DRY_RUN = os.environ.get("ARI_EVAL_DRY_RUN") == "1"


# ── Dash ─────────────────────────────────────────────────────────────────

def set_dock_position(arguments: dict) -> dict:
    edge = str(arguments.get("edge", "")).lower()
    if edge not in EDGES:
        return {"ok": False, "summary": f"The dock can sit at the {', '.join(EDGES[:-1])} or {EDGES[-1]}."}
    settings = _settings(SHELL_STATE)
    if settings is None:
        return _missing("The Luma shell")
    before = settings.get_string("shelf-edge")
    if before == edge:
        return {"ok": True, "summary": f"The dock is already at the {edge}.", "unchanged": True}
    settings.set_string("shelf-edge", edge)
    _sync()
    return {"ok": True, "summary": f"Dock moved to the {edge}",
            "undo": {"tool": "set_dock_position", "arguments": {"edge": before}}}


# ── Look ─────────────────────────────────────────────────────────────────

def set_theme(arguments: dict) -> dict:
    treatment = str(arguments.get("theme", "")).lower()
    if treatment not in TREATMENTS:
        return {"ok": False, "summary": "Luma's treatments are light, dark, frost and glass."}
    state = _settings(SHELL_STATE)
    interface = _settings(INTERFACE)
    if state is None or interface is None:
        return _missing("Appearance settings")
    before = {"theme": state.get_string("surface-treatment"), "scheme": interface.get_string("color-scheme")}
    if before["theme"] == treatment:
        return {"ok": True, "summary": f"The treatment is already {treatment}.", "unchanged": True}
    scheme = arguments.get("scheme") or ("prefer-dark" if treatment in ("dark", "glass") else "prefer-light")
    state.set_string("surface-treatment", treatment)
    # The session service keeps GTK 3 themes in step with color-scheme.
    interface.set_string("color-scheme", scheme)
    _sync()
    return {"ok": True, "summary": f"Treatment set to {treatment}",
            "undo": {"tool": "set_theme", "arguments": {"theme": before["theme"], "scheme": before["scheme"]}}}


def set_accent(arguments: dict) -> dict:
    colour = str(arguments.get("color", "")).lower().replace("grey", "slate").replace("gray", "slate")
    if colour not in ACCENTS:
        return {"ok": False, "summary": f"Accent colours are {', '.join(ACCENTS)}."}
    settings = _settings(INTERFACE)
    if settings is None:
        return _missing("Accent colours")
    before = settings.get_string("accent-color")
    settings.set_string("accent-color", colour)
    _sync()
    return {"ok": True, "summary": f"Accent colour set to {colour}",
            "undo": {"tool": "set_accent", "arguments": {"color": before}}}


def set_interface_scale(arguments: dict) -> dict:
    try:
        percent = float(arguments.get("percent"))
    except (TypeError, ValueError):
        return {"ok": False, "summary": "Give a size as a percentage, like 125."}
    if not 75 <= percent <= 200:
        return {"ok": False, "summary": "Text size can be between 75% and 200%."}
    settings = _settings(INTERFACE)
    if settings is None:
        return _missing("Text size")
    before = settings.get_double("text-scaling-factor")
    settings.set_double("text-scaling-factor", round(percent / 100, 2))
    _sync()
    return {"ok": True, "summary": f"Text size set to {percent:.0f}%",
            "undo": {"tool": "set_interface_scale", "arguments": {"percent": round(before * 100)}}}


def set_night_light(arguments: dict) -> dict:
    wanted = str(arguments.get("state", "")).lower()
    if wanted not in ("on", "off"):
        return {"ok": False, "summary": "Night light can be on or off."}
    settings = _settings(COLOR)
    if settings is None:
        return _missing("Night light")
    before = settings.get_boolean("night-light-enabled")
    settings.set_boolean("night-light-enabled", wanted == "on")
    _sync()
    return {"ok": True, "summary": f"Night light turned {wanted}",
            "undo": {"tool": "set_night_light", "arguments": {"state": "on" if before else "off"}}}


def _background_lists() -> list[Path]:
    """The wallpaper lists Settings reads: the person's own first, then the system's."""
    roots = [Path(GLib.get_user_data_dir())] + [Path(d) for d in GLib.get_system_data_dirs()]
    lists = []
    for root in roots:
        directory = root / "gnome-background-properties"
        if directory.is_dir():
            lists.extend(sorted(directory.glob("*.xml")))
    return lists


def _wallpapers() -> dict[str, Path]:
    """Every wallpaper a person can pick in Settings, by the name Settings shows."""
    import xml.etree.ElementTree as ElementTree
    found: dict[str, Path] = {}
    for listing in _background_lists():
        try:
            root = ElementTree.parse(listing).getroot()
        except (OSError, ElementTree.ParseError):
            continue
        for item in root.findall("wallpaper"):
            if item.get("deleted") == "true":
                continue
            path = Path(item.findtext("filename-dark") or item.findtext("filename") or "")
            name = (item.findtext("name") or _wallpaper_name(path)).strip()
            if path.is_file() and path.suffix.lower() in WALLPAPER_SUFFIXES:
                found.setdefault(name, path)
    for directory in WALLPAPER_DIRS:
        if directory.is_dir():
            for path in sorted(directory.rglob("*")):
                if path.suffix.lower() in WALLPAPER_SUFFIXES and path.is_file():
                    found.setdefault(_wallpaper_name(path), path)
    return found


def _wallpaper_name(path: Path) -> str:
    return path.stem.replace("luma-", "").replace("-", " ").replace("_", " ").title()


def _current_wallpaper(uri: str, options: dict[str, Path]) -> str:
    try:
        current = Path(GLib.filename_from_uri(uri)[0])
    except GLib.Error:
        return ""
    by_path = next((name for name, path in options.items() if path == current), None)
    return by_path or _wallpaper_name(current)


def set_wallpaper(arguments: dict) -> dict:
    choice = str(arguments.get("wallpaper", "random")).strip()
    settings = _settings(BACKGROUND)
    if settings is None:
        return _missing("Wallpaper settings")
    before = {"uri": settings.get_string("picture-uri"), "dark": settings.get_string("picture-uri-dark")}
    if choice.startswith("file://"):
        uri = choice
        name = _wallpaper_name(Path(GLib.filename_from_uri(uri)[0]))
    else:
        options = _wallpapers()
        if not options:
            return {"ok": False, "summary": "There are no wallpapers installed to choose from."}
        wanted = re.sub(r"\b(wallpapers?|backgrounds?|the|one|ones|of|other|a|an|another|different|new|my|to|some|something|"
                        r"defaults?|options?|system|installed|please|random|surprise|me|any)\b", " ", choice.lower()).strip()
        if wanted and not any(wanted in n.lower() or n.lower() in wanted for n in options):
            # Typing is imperfect: "Embr" means Ember, "defautls" means any of them.
            import difflib
            names = {n.lower(): n for n in options}
            close = difflib.get_close_matches(wanted, list(names), n=1, cutoff=0.7)
            if close:
                wanted = close[0]
            elif all(difflib.get_close_matches(word, ["default", "defaults", "random", "other", "option"], n=1,
                                               cutoff=0.7) for word in wanted.split()):
                wanted = ""
        if not wanted.strip():
            # A different picture, judged by what it is called, not by which copy of the file is set.
            current = _current_wallpaper(before["dark"] or before["uri"], options)
            fresh = [name for name in options if name.lower() != current.lower()] or list(options)
            name = random.choice(fresh)
        else:
            name = next((n for n in options if wanted in n.lower() or n.lower() in wanted), None)
            if name is None:
                return {"ok": False, "summary": f"I don't have a wallpaper called {choice}. "
                                                f"There's {', '.join(options)}."}
        path = options[name]
        uri = path.as_uri()
    if arguments.get("preview"):
        return {"ok": True, "preview": f"Change the wallpaper to {name}"}
    # Use the shell-state service's validated path, the one Settings uses.
    result = subprocess.run(["true"] if DRY_RUN else ["luma-shell-statectl", "wallpaper", uri],
                            capture_output=True, text=True, timeout=20)
    if DRY_RUN or result.returncode != 0:
        settings.set_string("picture-uri", uri)
        settings.set_string("picture-uri-dark", uri)
        _sync()
    undo = before["dark"] or before["uri"]
    return {"ok": True, "summary": f"Wallpaper changed to {name}",
            "undo": {"tool": "set_wallpaper", "arguments": {"wallpaper": undo}} if undo else None,
            "data": {"name": name}}


# ── Displays ─────────────────────────────────────────────────────────────

def _displays():
    try:
        from luma_displays import model
        from luma_displays.displayconfig import TEMPORARY, VERIFY, DisplayConfig
    except ImportError:
        return None
    return model, DisplayConfig, TEMPORARY, VERIFY


def _backlights() -> tuple[Gio.DBusProxy, int, list[dict]] | None:
    try:
        proxy = Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
                                               "org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
                                               "org.gnome.Mutter.DisplayConfig", None)
    except GLib.Error:
        return None
    value = proxy.get_cached_property("Backlight")
    if value is None:
        return None
    serial, panels = value.unpack()
    return proxy, serial, [p for p in panels if p.get("active", True) and p.get("max", 0) > p.get("min", 0)]


def set_brightness(arguments: dict) -> dict:
    loaded = _backlights()
    if loaded is None:
        return _missing("Brightness control")
    proxy, serial, panels = loaded
    if not panels:
        return {"ok": False, "summary": "None of the connected displays let the computer set their brightness."}
    panel = panels[0]
    low, high, now = panel["min"], panel["max"], panel["value"]
    percent_now = round((now - low) * 100 / (high - low))
    wanted = str(arguments.get("level", "")).strip().lower().rstrip("%")
    if wanted in ("up", "brighter", "higher", "more"):
        percent = min(100, percent_now + 20)
    elif wanted in ("down", "dimmer", "lower", "less"):
        percent = max(5, percent_now - 20)
    elif wanted in ("max", "full", "highest"):
        percent = 100
    else:
        try:
            percent = max(5, min(100, round(float(wanted))))
        except ValueError:
            return {"ok": False, "summary": "Give a brightness as a percentage, or say brighter or dimmer."}
    if percent == percent_now:
        return {"ok": True, "unchanged": True, "summary": f"Brightness is already {percent}%."}
    target = round(low + (high - low) * percent / 100)
    if not DRY_RUN:
        try:
            proxy.call_sync("SetBacklight", GLib.Variant("(usi)", (serial, panel["connector"], target)),
                            Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error as error:
            return {"ok": False, "summary": f"The display didn't take the change: {error.message.split(': ')[-1]}"}
    return {"ok": True, "summary": f"Brightness set to {percent}%",
            "undo": {"tool": "set_brightness", "arguments": {"level": str(percent_now)}}}


def list_display_modes(_arguments: dict) -> dict:
    loaded = _displays()
    if loaded is None:
        return _missing("Display control")
    model, DisplayConfig, _t, _v = loaded
    state = DisplayConfig().state()
    displays = []
    for placement in state.layout.placements:
        monitor = state.monitor(placement.connector)
        mode = monitor.mode(placement.mode_id)
        rates = [round(m.rate) for m in model.refresh_modes(monitor, mode)]
        displays.append({"display": model.short_name(monitor), "connector": placement.connector,
                         "resolution": f"{mode.width}x{mode.height}", "refresh_hz": round(mode.rate),
                         "available_hz": rates})
    text = "; ".join(f"{d['display']}: {d['refresh_hz']} Hz (can do {', '.join(map(str, d['available_hz']))})"
                     for d in displays)
    return {"ok": True, "summary": text, "data": {"displays": displays}}


def set_refresh_rate(arguments: dict) -> dict:
    loaded = _displays()
    if loaded is None:
        return _missing("Display control")
    model, DisplayConfig, TEMPORARY, VERIFY = loaded
    config = DisplayConfig()
    state = config.state()
    wanted = str(arguments.get("display", "")).lower()
    direction = str(arguments.get("hz", "")).lower()
    placement = next((p for p in state.layout.placements if wanted and (
                      wanted == p.connector.lower() or wanted in model.short_name(state.monitor(p.connector)).lower())), None)
    if placement is None:
        placement = next((p for p in state.layout.placements if p.primary), state.layout.placements[0])
    monitor = state.monitor(placement.connector)
    current = monitor.mode(placement.mode_id)
    rates = model.refresh_modes(monitor, current)
    if direction in ("lower", "down", "less"):
        choice = next((m for m in rates if m.rate < current.rate - 0.5), None)
    elif direction in ("higher", "up", "more", "max", "highest"):
        choice = next((m for m in reversed(rates) if m.rate > current.rate + 0.5), None)
        choice = rates[0] if direction in ("max", "highest") else choice
    else:
        try:
            target = float(direction)
        except ValueError:
            return {"ok": False, "summary": "Give a refresh rate in hertz, or say higher or lower."}
        choice = min(rates, key=lambda m: abs(m.rate - target))
    name = model.short_name(monitor)
    if choice is None or choice.id == current.id:
        return {"ok": True, "unchanged": True,
                "summary": f"{name} is already at {round(current.rate)} Hz, the {'lowest' if direction in ('lower', 'down') else 'closest'} it offers."}
    layout = state.layout.copy()
    next(p for p in layout.placements if p.connector == placement.connector).mode_id = choice.id
    try:
        config.apply(state, layout, VERIFY)
        if not DRY_RUN:
            config.apply(state, layout, TEMPORARY)
    except GLib.Error as error:
        return {"ok": False, "summary": f"The display refused {round(choice.rate)} Hz: {error.message}"}
    return {"ok": True, "summary": f"{name} set to {round(choice.rate)} Hz",
            "confirm_within": 15,
            "undo": {"tool": "set_refresh_rate", "arguments": {"display": placement.connector,
                                                               "hz": str(round(current.rate))}}}


# ── Apps ─────────────────────────────────────────────────────────────────

def _match_app(name: str) -> Gio.DesktopAppInfo | None:
    # "the Tide app", "Discord application": people name the app, not its launcher.
    wanted = re.sub(r"^(?:the|my)\s+|\s+(?:app|application|program)$", "", name.strip().lower()).strip()
    if not wanted:
        return None
    best = None
    for group in Gio.DesktopAppInfo.search(wanted):
        for desktop_id in group:
            try:
                info = Gio.DesktopAppInfo.new(desktop_id)
            except TypeError:
                continue
            if info is None or not info.should_show():
                continue
            if info.get_name().lower() == wanted:
                return info
            best = best or info
        if best:
            break
    return best


def find_app(arguments: dict) -> dict:
    info = _match_app(str(arguments.get("name", "")))
    if info is None:
        return {"ok": True, "summary": f"{arguments.get('name')} isn't installed.", "data": {"installed": False}}
    return {"ok": True, "summary": f"{info.get_name()} is installed.",
            "data": {"installed": True, "name": info.get_name(), "id": info.get_id()}}


def open_app(arguments: dict) -> dict:
    info = _match_app(str(arguments.get("name", "")))
    if info is None:
        return {"ok": False, "summary": f"{arguments.get('name')} isn't installed on this machine.",
                "data": {"installed": False}}
    if DRY_RUN:
        return {"ok": True, "summary": f"Opened {info.get_name()}", "data": {"id": info.get_id(), "dry_run": True}}
    try:
        launched = info.launch([], None)
    except GLib.Error as error:
        return {"ok": False, "summary": f"{info.get_name()} didn't open: {error.message}"}
    if not launched:
        return {"ok": False, "summary": f"{info.get_name()} didn't open."}
    return {"ok": True, "summary": f"Opened {info.get_name()}", "data": {"id": info.get_id()}}


TOOLS = {
    "set_dock_position": ("Move the Dash (the dock) to an edge of the screen.",
                          {"type": "object", "properties": {"edge": {"type": "string", "enum": list(EDGES)}},
                           "required": ["edge"], "additionalProperties": False}, set_dock_position),
    "set_theme": ("Change Luma's surface treatment: light, dark, frost or glass. 'Dark mode' means dark.",
                  {"type": "object", "properties": {"theme": {"type": "string", "enum": list(TREATMENTS)},
                                                    "scheme": {"type": "string"}},
                   "required": ["theme"], "additionalProperties": False}, set_theme),
    "set_accent": ("Change the accent colour.",
                   {"type": "object", "properties": {"color": {"type": "string", "enum": list(ACCENTS)}},
                    "required": ["color"], "additionalProperties": False}, set_accent),
    "set_interface_scale": ("Make text and the interface bigger or smaller, as a percentage (100 is normal).",
                            {"type": "object", "properties": {"percent": {"type": "number"}},
                             "required": ["percent"], "additionalProperties": False}, set_interface_scale),
    "set_night_light": ("Turn night light (warmer colours) on or off.",
                        {"type": "object", "properties": {"state": {"type": "string", "enum": ["on", "off"]}},
                         "required": ["state"], "additionalProperties": False}, set_night_light),
    "set_brightness": ("Change the built-in screen's brightness: a percentage, or 'brighter' or 'dimmer'.",
                       {"type": "object", "properties": {"level": {"type": "string"}},
                        "required": ["level"], "additionalProperties": False}, set_brightness),
    "set_wallpaper": ("Change the wallpaper to one the person names (as Settings lists them), or 'random' for a "
                      "different one.",
                      {"type": "object", "properties": {"wallpaper": {"type": "string"}},
                       "required": ["wallpaper"], "additionalProperties": False}, set_wallpaper),
    "list_display_modes": ("List each display with its refresh rate and the rates it can use.",
                           {"type": "object", "properties": {}, "additionalProperties": False}, list_display_modes),
    "set_refresh_rate": ("Change a display's refresh rate: a number of hertz, or 'lower' or 'higher'. "
                         "Leave display empty for the main display.",
                         {"type": "object", "properties": {"display": {"type": "string"}, "hz": {"type": "string"}},
                          "required": ["hz"], "additionalProperties": False}, set_refresh_rate),
    "find_app": ("Check whether an application is installed.",
                 {"type": "object", "properties": {"name": {"type": "string"}},
                  "required": ["name"], "additionalProperties": False}, find_app),
    "open_app": ("Open an installed application by name.",
                 {"type": "object", "properties": {"name": {"type": "string"}},
                  "required": ["name"], "additionalProperties": False}, open_app),
}

if __name__ == "__main__":
    serve("ari-desktop", TOOLS)
