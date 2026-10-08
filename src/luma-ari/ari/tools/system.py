# SPDX-License-Identifier: Apache-2.0
"""System and media capabilities (ADR-025 §1–2).

Each change goes through the service the rest of the desktop uses: timedated
for the time zone, NetworkManager and the settings daemon for radios,
WirePlumber for volume, the Shell's extension interface for tiling, and MPRIS
for any media player. Every changing tool accepts `preview`, which says exactly
what would happen without doing it; Ari shows that text when it asks first.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from ari.mcp import serve  # noqa: E402

DRY_RUN = os.environ.get("ARI_EVAL_DRY_RUN") == "1"
TILING_SHELL = "tilingshell@ferrarodomenico.com"
ZONE_TABLE = Path("/usr/share/zoneinfo/zone1970.tab")
# The names people say for the zones they mean.
ZONE_WORDS = {
    "eastern": "America/New_York", "est": "America/New_York", "edt": "America/New_York", "et": "America/New_York",
    "central": "America/Chicago", "cst": "America/Chicago", "cdt": "America/Chicago", "ct": "America/Chicago",
    "mountain": "America/Denver", "mst": "America/Denver", "mdt": "America/Denver",
    "arizona": "America/Phoenix", "pacific": "America/Los_Angeles", "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles", "alaska": "America/Anchorage", "hawaii": "Pacific/Honolulu",
    "atlantic": "America/Halifax", "newfoundland": "America/St_Johns",
    "uk": "Europe/London", "british": "Europe/London", "gmt": "Etc/GMT", "utc": "Etc/UTC",
    "central european": "Europe/Paris", "cet": "Europe/Paris", "eastern european": "Europe/Athens",
    "india": "Asia/Kolkata", "ist": "Asia/Kolkata", "japan": "Asia/Tokyo", "jst": "Asia/Tokyo",
    "china": "Asia/Shanghai", "korea": "Asia/Seoul", "sydney": "Australia/Sydney",
    "new zealand": "Pacific/Auckland",
}


def _session(name: str, path: str, interface: str, *, system: bool = False) -> Gio.DBusProxy | None:
    try:
        return Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SYSTEM if system else Gio.BusType.SESSION,
                                              Gio.DBusProxyFlags.NONE, None, name, path, interface, None)
    except GLib.Error:
        return None


def _on_off(arguments: dict, key: str = "state") -> bool | None:
    value = str(arguments.get(key, "")).strip().lower()
    return True if value in ("on", "true", "enable", "enabled", "yes") else \
        False if value in ("off", "false", "disable", "disabled", "no") else None


# ── Time zone ────────────────────────────────────────────────────────────

def _zones() -> list[str]:
    try:
        return sorted({line.split("\t")[2] for line in ZONE_TABLE.read_text().splitlines()
                       if line and not line.startswith("#")})
    except (OSError, IndexError):
        return []


def resolve_zone(wanted: str) -> str | None:
    text = " ".join(re.sub(r"\b(time|zone|timezone|standard|daylight)\b", " ", wanted.lower()).split())
    if text in ZONE_WORDS:
        return ZONE_WORDS[text]
    zones = _zones()
    exact = next((z for z in zones if z.lower() == wanted.strip().lower()), None)
    if exact:
        return exact
    city = text.replace(" ", "_")
    return next((z for z in zones if z.lower().rsplit("/", 1)[-1] == city), None)


def set_timezone(arguments: dict) -> dict:
    wanted = str(arguments.get("zone", "")).strip()
    zone = resolve_zone(wanted)
    if zone is None:
        return {"ok": False, "summary": f"I don't know a time zone called {wanted}. Try a city, like New York."}
    before = GLib.TimeZone.new_local().get_identifier()
    if before == zone:
        return {"ok": True, "unchanged": True, "summary": f"The time zone is already {zone}."}
    place = zone.rsplit("/", 1)[-1].replace("_", " ")
    if arguments.get("preview"):
        return {"ok": True, "preview": f"Set the time zone to {place} time",
                "detail": f"{zone}, for everyone who uses this computer"}
    if not DRY_RUN:
        proxy = _session("org.freedesktop.timedate1", "/org/freedesktop/timedate1", "org.freedesktop.timedate1",
                         system=True)
        if proxy is None:
            return {"ok": False, "summary": "The time service isn't available."}
        try:
            # Interactive: the system asks for a password when policy requires one.
            proxy.call_sync("SetTimezone", GLib.Variant("(sb)", (zone, True)),
                            Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, 120000, None)
        except GLib.Error as error:
            if "NotAuthorized" in error.message or "not authorized" in error.message.lower():
                return {"ok": False, "summary": "The time zone wasn't changed: permission wasn't given."}
            return {"ok": False, "summary": f"The time zone wasn't changed: {error.message.split(': ')[-1]}"}
    return {"ok": True, "summary": f"Time zone set to {place} time",
            "undo": {"tool": "set_timezone", "arguments": {"zone": before}}}


# ── Sound and notifications ──────────────────────────────────────────────

def _volume() -> tuple[float, bool] | None:
    if not shutil.which("wpctl"):
        return None
    result = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True, timeout=5)
    match = re.search(r"Volume:\s*([\d.]+)", result.stdout)
    return (float(match.group(1)), "MUTED" in result.stdout) if match else None


def set_volume(arguments: dict) -> dict:
    state = _volume()
    if state is None:
        return {"ok": False, "summary": "Sound control isn't available on this machine."}
    level, muted = state
    now = round(level * 100)
    wanted = str(arguments.get("level", "")).strip().lower().rstrip("%")
    command: list[str]
    if wanted in ("mute", "muted", "off", "silent"):
        if muted:
            return {"ok": True, "unchanged": True, "summary": "Sound is already muted."}
        command, summary, undo = ["set-mute", "1"], "Sound muted", {"level": "unmute"}
        action = "Mute the sound"
    elif wanted in ("unmute", "on"):
        if not muted:
            return {"ok": True, "unchanged": True, "summary": f"Sound is on, at {now}%."}
        command, summary, undo = ["set-mute", "0"], "Sound unmuted", {"level": "mute"}
        action = "Unmute the sound"
    else:
        if wanted in ("up", "louder", "higher"):
            target = min(100, now + 10)
        elif wanted in ("down", "quieter", "lower", "softer"):
            target = max(0, now - 10)
        else:
            try:
                target = max(0, min(100, round(float(wanted))))
            except ValueError:
                return {"ok": False, "summary": "Give a volume as a percentage, or say louder, quieter or mute."}
        command, summary, undo = ["set-volume", f"{target / 100:.2f}"], f"Volume set to {target}%", {"level": str(now)}
        action = f"Set the volume to {target}%"
    if arguments.get("preview"):
        return {"ok": True, "preview": action}
    if not DRY_RUN:
        subprocess.run(["wpctl", command[0], "@DEFAULT_AUDIO_SINK@", command[1]], timeout=5, check=False)
        if command[0] == "set-volume" and muted:
            subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"], timeout=5, check=False)
    return {"ok": True, "summary": summary, "undo": {"tool": "set_volume", "arguments": undo}}


def set_do_not_disturb(arguments: dict) -> dict:
    wanted = _on_off(arguments)
    if wanted is None:
        return {"ok": False, "summary": "Do Not Disturb can be on or off."}
    settings = Gio.Settings.new("org.gnome.desktop.notifications")
    before = not settings.get_boolean("show-banners")
    if before == wanted:
        return {"ok": True, "unchanged": True, "summary": f"Do Not Disturb is already {'on' if wanted else 'off'}."}
    summary = f"Do Not Disturb turned {'on' if wanted else 'off'}"
    if arguments.get("preview"):
        return {"ok": True, "preview": f"Turn Do Not Disturb {'on' if wanted else 'off'}"}
    settings.set_boolean("show-banners", not wanted)
    Gio.Settings.sync()
    return {"ok": True, "summary": summary,
            "undo": {"tool": "set_do_not_disturb", "arguments": {"state": "on" if before else "off"}}}


# ── Radios ───────────────────────────────────────────────────────────────

def set_wifi(arguments: dict) -> dict:
    wanted = _on_off(arguments)
    if wanted is None:
        return {"ok": False, "summary": "Wi-Fi can be on or off."}
    if not shutil.which("nmcli"):
        return {"ok": False, "summary": "Network control isn't available on this machine."}
    before = subprocess.run(["nmcli", "radio", "wifi"], capture_output=True, text=True, timeout=5).stdout.strip()
    if (before == "enabled") == wanted:
        return {"ok": True, "unchanged": True, "summary": f"Wi-Fi is already {'on' if wanted else 'off'}."}
    summary = f"Wi-Fi turned {'on' if wanted else 'off'}"
    if arguments.get("preview"):
        return {"ok": True, "preview": f"Turn Wi-Fi {'on' if wanted else 'off'}",
                "detail": "This computer goes offline until it's back on" if not wanted else "Reconnect to known networks"}
    if not DRY_RUN:
        result = subprocess.run(["nmcli", "radio", "wifi", "on" if wanted else "off"],
                                capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return {"ok": False, "summary": f"Wi-Fi didn't change: {result.stderr.strip() or 'the network service refused'}"}
    return {"ok": True, "summary": summary,
            "undo": {"tool": "set_wifi", "arguments": {"state": "on" if before == "enabled" else "off"}}}


def set_bluetooth(arguments: dict) -> dict:
    wanted = _on_off(arguments)
    if wanted is None:
        return {"ok": False, "summary": "Bluetooth can be on or off."}
    proxy = _session("org.gnome.SettingsDaemon.Rfkill", "/org/gnome/SettingsDaemon/Rfkill",
                     "org.gnome.SettingsDaemon.Rfkill")
    present = proxy.get_cached_property("BluetoothHasAirplaneMode") if proxy else None
    if not present or not present.unpack():
        return {"ok": False, "summary": "This machine has no Bluetooth to turn on or off."}
    before = not proxy.get_cached_property("BluetoothAirplaneMode").unpack()
    if before == wanted:
        return {"ok": True, "unchanged": True, "summary": f"Bluetooth is already {'on' if wanted else 'off'}."}
    summary = f"Bluetooth turned {'on' if wanted else 'off'}"
    if arguments.get("preview"):
        return {"ok": True, "preview": f"Turn Bluetooth {'on' if wanted else 'off'}",
                "detail": "Connected headphones and keyboards disconnect" if not wanted else ""}
    if not DRY_RUN:
        try:
            proxy.call_sync("org.freedesktop.DBus.Properties.Set",
                            GLib.Variant("(ssv)", ("org.gnome.SettingsDaemon.Rfkill", "BluetoothAirplaneMode",
                                                   GLib.Variant("b", not wanted))),
                            Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error as error:
            return {"ok": False, "summary": f"Bluetooth didn't change: {error.message.split(': ')[-1]}"}
    return {"ok": True, "summary": summary,
            "undo": {"tool": "set_bluetooth", "arguments": {"state": "on" if before else "off"}}}


# ── Tiling ───────────────────────────────────────────────────────────────

def set_tiling(arguments: dict) -> dict:
    wanted = _on_off(arguments)
    if wanted is None:
        return {"ok": False, "summary": "Tiling can be on or off."}
    shell = Gio.Settings.new("org.gnome.shell")
    before = TILING_SHELL in shell.get_strv("enabled-extensions")
    if before == wanted:
        return {"ok": True, "unchanged": True, "summary": f"Tiling is already {'on' if wanted else 'off'}."}
    summary = f"Tiling turned {'on' if wanted else 'off'}"
    if arguments.get("preview"):
        return {"ok": True, "preview": f"Turn tiling {'on' if wanted else 'off'}"}
    proxy = _session("org.gnome.Shell.Extensions", "/org/gnome/Shell/Extensions", "org.gnome.Shell.Extensions")
    try:
        if DRY_RUN:
            raise GLib.Error("dry run")
        # The Shell's own interface, the same one the tiling toggle in Quick Options uses.
        proxy.call_sync("EnableExtension" if wanted else "DisableExtension", GLib.Variant("(s)", (TILING_SHELL,)),
                        Gio.DBusCallFlags.NONE, 5000, None)
    except (GLib.Error, AttributeError):
        extensions = [e for e in shell.get_strv("enabled-extensions") if e != TILING_SHELL]
        shell.set_strv("enabled-extensions", extensions + ([TILING_SHELL] if wanted else []))
        Gio.Settings.sync()
    return {"ok": True, "summary": summary, "undo": {"tool": "set_tiling", "arguments": {"state": "on" if before else "off"}}}


# ── Media (MPRIS) ────────────────────────────────────────────────────────

MPRIS = "org.mpris.MediaPlayer2"


def _players() -> list[dict]:
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    names = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames",
                          None, None, Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
    players = []
    for name in sorted(n for n in names if n.startswith(MPRIS + ".")):
        def prop(interface: str, key: str, default=None):
            try:
                return bus.call_sync(name, "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties", "Get",
                                     GLib.Variant("(ss)", (interface, key)), None, Gio.DBusCallFlags.NONE,
                                     2000, None).unpack()[0]
            except GLib.Error:
                return default
        metadata = prop(MPRIS + ".Player", "Metadata", {}) or {}
        artists = metadata.get("xesam:artist") or []
        players.append({"bus": name, "name": prop(MPRIS, "Identity", name.rsplit(".", 1)[-1]),
                        "desktop": prop(MPRIS, "DesktopEntry", ""), "status": prop(MPRIS + ".Player", "PlaybackStatus", ""),
                        "title": metadata.get("xesam:title", ""), "artist": ", ".join(artists)})
    return players


def _choose(players: list[dict], wanted: str, action: str) -> dict | None:
    if wanted:
        needle = wanted.lower()
        match = next((p for p in players if needle in p["name"].lower() or needle in p["desktop"].lower()
                      or needle in p["bus"].lower()), None)
        if match:
            return match
    playing = [p for p in players if p["status"] == "Playing"]
    paused = [p for p in players if p["status"] == "Paused"]
    if action in ("pause", "stop", "next", "previous", "play_pause") and playing:
        return playing[0]
    return (paused or playing or players or [None])[0]


def media_status(_arguments: dict) -> dict:
    players = _players()
    if not players:
        return {"ok": True, "summary": "Nothing that plays media is open.", "data": {"players": []}}
    text = "; ".join(f"{p['name']}: {p['status'].lower() or 'idle'}"
                     + (f", {p['title']}" + (f" by {p['artist']}" if p["artist"] else "") if p["title"] else "")
                     for p in players)
    return {"ok": True, "summary": text, "data": {"players": players}}


def media_control(arguments: dict) -> dict:
    action = str(arguments.get("action", "")).lower().replace("-", "_").replace(" ", "_")
    methods = {"play": "Play", "pause": "Pause", "play_pause": "PlayPause", "toggle": "PlayPause",
               "next": "Next", "skip": "Next", "previous": "Previous", "back": "Previous", "stop": "Stop"}
    if action not in methods:
        return {"ok": False, "summary": "I can play, pause, skip to the next or go back to the previous track."}
    players = _players()
    player = _choose(players, str(arguments.get("player", "")), action)
    if player is None:
        return {"ok": False, "summary": "Nothing that plays media is open. Open Tide or another player first."}
    if action == "pause" and player["status"] != "Playing":
        return {"ok": True, "unchanged": True, "summary": f"{player['name']} isn't playing."}
    if action == "play" and player["status"] == "Playing":
        return {"ok": True, "unchanged": True, "summary": f"{player['name']} is already playing."}
    words = {"Play": "Playing", "Pause": "Paused", "PlayPause": "Paused" if player["status"] == "Playing" else "Playing",
             "Next": "Skipped to the next track in", "Previous": "Went back a track in", "Stop": "Stopped"}
    summary = f"{words[methods[action]]} {player['name']}"
    if arguments.get("preview"):
        verbs = {"Play": "Play", "Pause": "Pause", "PlayPause": "Play or pause", "Next": "Skip to the next track in",
                 "Previous": "Go back a track in", "Stop": "Stop"}
        return {"ok": True, "preview": f"{verbs[methods[action]]} {player['name']}"}
    if not DRY_RUN:
        try:
            Gio.bus_get_sync(Gio.BusType.SESSION, None).call_sync(
                player["bus"], "/org/mpris/MediaPlayer2", MPRIS + ".Player", methods[action], None, None,
                Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error as error:
            return {"ok": False, "summary": f"{player['name']} didn't respond: {error.message.split(': ')[-1]}"}
    undo = {"Play": "pause", "Pause": "play", "PlayPause": "play_pause"}.get(methods[action])
    return {"ok": True, "summary": summary,
            "undo": {"tool": "media_control", "arguments": {"action": undo, "player": player["name"]}} if undo else None}


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": {**properties, "preview": {"type": "boolean"}},
            "required": required, "additionalProperties": False}


TOOLS = {
    "set_timezone": ("Change the computer's time zone. Accepts a place (New York, Tokyo), a name people use "
                     "(Eastern, Pacific) or an IANA zone.",
                     _schema({"zone": {"type": "string"}}, ["zone"]), set_timezone),
    "set_volume": ("Change the sound volume: a percentage, 'louder', 'quieter', 'mute' or 'unmute'.",
                   _schema({"level": {"type": "string"}}, ["level"]), set_volume),
    "set_do_not_disturb": ("Turn Do Not Disturb (no notification banners) on or off.",
                           _schema({"state": {"type": "string", "enum": ["on", "off"]}}, ["state"]),
                           set_do_not_disturb),
    "set_wifi": ("Turn Wi-Fi on or off.", _schema({"state": {"type": "string", "enum": ["on", "off"]}}, ["state"]),
                 set_wifi),
    "set_bluetooth": ("Turn Bluetooth on or off.",
                      _schema({"state": {"type": "string", "enum": ["on", "off"]}}, ["state"]), set_bluetooth),
    "set_tiling": ("Turn window tiling on or off.",
                   _schema({"state": {"type": "string", "enum": ["on", "off"]}}, ["state"]), set_tiling),
    "media_status": ("Say what media players are open and what they are playing.",
                     {"type": "object", "properties": {}, "additionalProperties": False}, media_status),
    "media_control": ("Control music or video in any open player (Tide, a browser, Spotify): play, pause, "
                      "next, previous or stop. Leave player empty for whatever is playing.",
                      _schema({"action": {"type": "string", "enum": ["play", "pause", "play_pause", "next",
                                                                      "previous", "stop"]},
                               "player": {"type": "string"}}, ["action"]), media_control),
}

if __name__ == "__main__":
    serve("ari-system", TOOLS)
