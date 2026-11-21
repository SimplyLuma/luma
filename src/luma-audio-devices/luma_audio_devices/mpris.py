# SPDX-License-Identifier: MPL-2.0
"""What an app is playing, and its transport, from MPRIS.

The mixer shows one row per app; a media player the app publishes on the
session bus (org.mpris.MediaPlayer2.*) gives that row its title, artist,
artwork and play, pause, next and previous. ``match`` decides which player
belongs to which app: by desktop entry, then by bus name, then by process.

``Players`` follows the bus by signals only (NameOwnerChanged and
PropertiesChanged); it never polls. The position is read once when a player
pauses, for "Paused at 14:02".

The model (``Player``, ``match``, ``describe``) has no GObject imports so it
can be tested anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar
import logging
import re
from urllib.parse import urlsplit

__all__ = ("PREFIX", "Player", "match", "describe", "site_host", "Players")

log = logging.getLogger("luma-audio-devices")

PREFIX = "org.mpris.MediaPlayer2."
OBJECT_PATH = "/org/mpris/MediaPlayer2"
ROOT_INTERFACE = "org.mpris.MediaPlayer2"
PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"
_INSTANCE = re.compile(r"\.(instance[-_]?\w+|\d+)$", re.IGNORECASE)


@dataclass
class Player:
    bus_name: str
    owner: str = ""
    pid: int = 0
    desktop_entry: str = ""
    identity: str = ""
    status: str = "Stopped"          # "Playing", "Paused" or "Stopped"
    title: str = ""
    artist: str = ""
    art_url: str = ""
    url: str = ""
    position: int = -1               # microseconds, read when paused; -1 unknown
    can_control: bool = True
    can_play: bool = False
    can_pause: bool = False
    can_go_next: bool = False
    can_go_previous: bool = False
    can_raise: bool = False
    changed: int = 0                 # order of the last change, newest highest
    started: int = 0                 # order of the last time it began playing
    starts: ClassVar[int] = 0        # the counter behind started

    @property
    def short_name(self) -> str:
        """The bus name's own part: "tide" of org.mpris.MediaPlayer2.tide,
        "chromium" of org.mpris.MediaPlayer2.chromium.instance4321."""
        name = self.bus_name[len(PREFIX):] if self.bus_name.startswith(PREFIX) else self.bus_name
        return _INSTANCE.sub("", name)


def _stem(desktop_id: str) -> str:
    desktop_id = (desktop_id or "").strip()
    if desktop_id.endswith(".desktop"):
        desktop_id = desktop_id[:-len(".desktop")]
    return desktop_id.casefold()


def match(players, *, key: str, desktop_id: str = "", binary: str = "", pids=()) -> Player | None:
    """The player that belongs to an app, or None: the first of matches()."""
    found = matches(players, key=key, desktop_id=desktop_id, binary=binary, pids=pids)
    return found[0] if found else None


def matches(players, *, key: str, desktop_id: str = "", binary: str = "", pids=()) -> list[Player]:
    """The players that belong to an app, the one to show first.

    Desktop entry first (the player says which app it is), then the bus name
    against the app's key, desktop entry and binary, then the process that
    owns the name. Of several players of one app (a browser may publish one
    per window or tab) the ones playing come first, then the one that
    changed last.
    """
    stem = _stem(desktop_id)
    names = {n for n in (stem, stem.rsplit(".", 1)[-1], (key or "").casefold(),
                         (key or "").casefold().rsplit(".", 1)[-1], (binary or "").casefold()) if n}
    pids = {int(p) for p in pids if p}
    ranked = []
    for player in players:
        entry = _stem(player.desktop_entry)
        if entry and (entry == stem or entry == (key or "").casefold()):
            rank = 0
        elif player.short_name.casefold() in names or (entry and entry in names):
            rank = 1
        elif player.pid and player.pid in pids:
            rank = 2
        else:
            continue
        ranked.append((rank, player.status != "Playing", -player.changed, player.bus_name, player))
    if not ranked:
        return []
    best_rank = min(r[0] for r in ranked)
    return [r[-1] for r in sorted((r for r in ranked if r[0] == best_rank), key=lambda r: r[:-1])]


def site_host(url: str) -> str:
    """"simplyluma.com" of https://www.simplyluma.com/film; "" for anything
    that is not a web address."""
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    host = parts.hostname
    return host[4:] if host.startswith("www.") else host


def describe(player: Player | None, *, browser: bool = False, others=()) -> dict:
    """The row fields a player gives its app. Without a player the row keeps
    the app's own name and icon and has no transport.

    A browser playing in several tabs (others: its other playing players) is
    one row: "2 tabs playing" over the tabs' sites, with the transport of the
    tab that began playing last."""
    if player is None:
        return {"player": "", "title": "", "artist": "", "art": "", "status": "", "tabs": 0,
                "position": -1, "can_play": False, "can_pause": False,
                "can_go_next": False, "can_go_previous": False}
    if browser and others:
        playing = sorted([player, *others], key=lambda p: (-p.started, p.bus_name))
        hosts = []
        for tab in sorted(playing, key=lambda p: (p.started, p.bus_name)):
            host = site_host(tab.url)
            if host and host not in hosts:
                hosts.append(host)
        row = describe(playing[0], browser=True)
        row.update(title=f"{len(playing)} tabs playing", artist=", ".join(hosts), tabs=len(playing))
        return row
    control = player.can_control
    artist = player.artist or (site_host(player.url) if browser else "")
    return {
        "player": player.bus_name, "title": player.title, "artist": artist, "art": player.art_url,
        "tabs": 1 if browser else 0,
        "status": player.status.lower(),
        "position": player.position if player.status == "Paused" else -1,
        "can_play": control and player.can_play, "can_pause": control and player.can_pause,
        "can_go_next": control and player.can_go_next,
        "can_go_previous": control and player.can_go_previous,
    }


def _text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return ", ".join(v.strip() for v in value if isinstance(v, str) and v.strip())
    return ""


def apply_properties(player: Player, interface: str, changed: dict) -> bool:
    """Fold a GetAll reply or a PropertiesChanged into the player. True when
    something the mixer shows changed."""
    before = (player.desktop_entry, player.identity, player.status, player.title, player.artist,
              player.art_url, player.url, player.can_control, player.can_play, player.can_pause,
              player.can_go_next, player.can_go_previous, player.can_raise)
    if interface == ROOT_INTERFACE:
        if "DesktopEntry" in changed:
            player.desktop_entry = _text(changed["DesktopEntry"])
        if "Identity" in changed:
            player.identity = _text(changed["Identity"])
        if "CanRaise" in changed:
            player.can_raise = changed["CanRaise"] is True
    elif interface == PLAYER_INTERFACE:
        if "PlaybackStatus" in changed:
            status = _text(changed["PlaybackStatus"])
            status = status if status in ("Playing", "Paused") else "Stopped"
            if status == "Playing" and player.status != "Playing":
                Player.starts += 1
                player.started = Player.starts
            player.status = status
        metadata = changed.get("Metadata")
        if isinstance(metadata, dict):
            player.title = _text(metadata.get("xesam:title"))
            player.artist = _text(metadata.get("xesam:artist")) or _text(metadata.get("xesam:albumArtist"))
            player.art_url = _text(metadata.get("mpris:artUrl"))
            player.url = _text(metadata.get("xesam:url"))
        for name, attribute in (("CanControl", "can_control"), ("CanPlay", "can_play"),
                                ("CanPause", "can_pause"), ("CanGoNext", "can_go_next"),
                                ("CanGoPrevious", "can_go_previous")):
            if name in changed:
                setattr(player, attribute, changed[name] is True)
    return before != (player.desktop_entry, player.identity, player.status, player.title, player.artist,
                      player.art_url, player.url, player.can_control, player.can_play, player.can_pause,
                      player.can_go_next, player.can_go_previous, player.can_raise)


class Players:
    """Every MPRIS player on the session bus, kept current by signals."""

    def __init__(self, on_changed) -> None:
        self._on_changed = on_changed
        self._connection = None
        self._players: dict[str, Player] = {}
        self._subscriptions: list[int] = []
        self._serial = 0

    def players(self) -> list[Player]:
        return list(self._players.values())

    def get(self, bus_name: str) -> Player | None:
        return self._players.get(bus_name)

    # Bus --------------------------------------------------------------------

    def start(self, connection) -> None:
        from gi.repository import Gio, GLib
        if self._connection is not None or connection is None:
            return
        self._connection = connection
        self._subscriptions.append(connection.signal_subscribe(
            "org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged", "/org/freedesktop/DBus",
            PREFIX.rstrip("."), Gio.DBusSignalFlags.MATCH_ARG0_NAMESPACE, self._name_owner_changed))
        self._subscriptions.append(connection.signal_subscribe(
            None, "org.freedesktop.DBus.Properties", "PropertiesChanged", OBJECT_PATH,
            None, Gio.DBusSignalFlags.NONE, self._properties_changed))
        connection.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames",
                        None, GLib.VariantType.new("(as)"), Gio.DBusCallFlags.NONE, -1, None, self._names_listed)

    def stop(self) -> None:
        if self._connection is not None:
            for subscription in self._subscriptions:
                self._connection.signal_unsubscribe(subscription)
        self._subscriptions = []
        self._connection = None
        self._players = {}

    def _names_listed(self, connection, result) -> None:
        try:
            (names,) = connection.call_finish(result).unpack()
        except Exception as error:
            log.warning("cannot list media players: %s", error)
            return
        for name in names:
            if name.startswith(PREFIX):
                self._add(name)

    def _name_owner_changed(self, _connection, _sender, _path, _interface, _signal, parameters) -> None:
        name, _old, new = parameters.unpack()
        if not name.startswith(PREFIX):
            return
        if new:
            self._add(name, new)
        elif self._players.pop(name, None) is not None:
            self._on_changed()

    def _add(self, name: str, owner: str = "") -> None:
        from gi.repository import Gio, GLib
        self._serial += 1
        player = Player(bus_name=name, owner=owner, changed=self._serial)
        self._players[name] = player
        connection = self._connection

        def got_owner(_c, result) -> None:
            try:
                (player.owner,) = connection.call_finish(result).unpack()
            except Exception:
                pass

        def got_pid(_c, result) -> None:
            try:
                (player.pid,) = connection.call_finish(result).unpack()
            except Exception:
                return
            self._touch(player)

        def got_all(interface):
            def done(_c, result) -> None:
                try:
                    (properties,) = connection.call_finish(result).unpack()
                except Exception as error:
                    log.debug("%s does not answer %s: %s", name, interface, error)
                    return
                if self._players.get(name) is not player:
                    return
                if apply_properties(player, interface, properties):
                    self._touch(player)
                if interface == PLAYER_INTERFACE and player.status == "Paused":
                    self._read_position(player)
            return done

        bus = ("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus")
        if not owner:
            connection.call(*bus, "GetNameOwner", GLib.Variant("(s)", (name,)), GLib.VariantType.new("(s)"),
                            Gio.DBusCallFlags.NONE, -1, None, got_owner)
        connection.call(*bus, "GetConnectionUnixProcessID", GLib.Variant("(s)", (name,)),
                        GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, -1, None, got_pid)
        for interface in (ROOT_INTERFACE, PLAYER_INTERFACE):
            connection.call(name, OBJECT_PATH, "org.freedesktop.DBus.Properties", "GetAll",
                            GLib.Variant("(s)", (interface,)), GLib.VariantType.new("(a{sv})"),
                            Gio.DBusCallFlags.NO_AUTO_START, 3000, None, got_all(interface))
        self._on_changed()

    def _properties_changed(self, _connection, sender, _path, _interface, _signal, parameters) -> None:
        interface, changed, invalidated = parameters.unpack()
        if interface not in (ROOT_INTERFACE, PLAYER_INTERFACE):
            return
        player = next((p for p in self._players.values() if p.owner == sender), None)
        if player is None:
            return
        was = player.status
        if apply_properties(player, interface, changed):
            self._touch(player)
        if player.status == "Paused" and (was != "Paused" or "Metadata" in changed):
            self._read_position(player)
        elif player.status != "Paused":
            player.position = -1

    def _read_position(self, player: Player) -> None:
        from gi.repository import Gio, GLib
        connection = self._connection
        if connection is None:
            return

        def done(_c, result) -> None:
            try:
                (value,) = connection.call_finish(result).unpack()
            except Exception:
                return
            if isinstance(value, int) and player.status == "Paused" and value != player.position:
                player.position = value
                self._touch(player)

        connection.call(player.bus_name, OBJECT_PATH, "org.freedesktop.DBus.Properties", "Get",
                        GLib.Variant("(ss)", (PLAYER_INTERFACE, "Position")), GLib.VariantType.new("(v)"),
                        Gio.DBusCallFlags.NO_AUTO_START, 3000, None, done)

    def _touch(self, player: Player) -> None:
        self._serial += 1
        player.changed = self._serial
        self._on_changed()

    # Transport --------------------------------------------------------------

    def call(self, bus_name: str, method: str) -> bool:
        """PlayPause, Next, Previous (the Player interface) or Raise."""
        from gi.repository import Gio
        if self._connection is None or bus_name not in self._players:
            return False
        interface = ROOT_INTERFACE if method == "Raise" else PLAYER_INTERFACE
        self._connection.call(bus_name, OBJECT_PATH, interface, method, None, None,
                              Gio.DBusCallFlags.NO_AUTO_START, 3000, None, None)
        return True
