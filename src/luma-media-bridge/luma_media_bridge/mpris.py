# SPDX-License-Identifier: Apache-2.0
"""An MPRIS player on the session bus, fed by an adapter.

MPRIS is how the desktop learns what is playing: the shelf's live island,
media keys, the lock screen and Ari all read it. This exports one player
for a program that has its own local control interface but no MPRIS, and
passes the desktop's commands back to that program through its adapter.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

PATH = "/org/mpris/MediaPlayer2"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"

INTROSPECTION = """
<node>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/>
    <method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Next"/>
    <method name="Previous"/>
    <method name="Pause"/>
    <method name="PlayPause"/>
    <method name="Stop"/>
    <method name="Play"/>
    <method name="Seek"><arg name="Offset" type="x" direction="in"/></method>
    <method name="SetPosition"><arg name="TrackId" type="o" direction="in"/><arg name="Position" type="x" direction="in"/></method>
    <method name="OpenUri"><arg name="Uri" type="s" direction="in"/></method>
    <signal name="Seeked"><arg name="Position" type="x"/></signal>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="Rate" type="d" access="read"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="read"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>
"""


@dataclass
class Track:
    track_id: str = NO_TRACK
    title: str = ""
    artists: list[str] = field(default_factory=list)
    album: str = ""
    length_us: int = 0
    art_url: str = ""

    def metadata(self) -> dict[str, GLib.Variant]:
        if self.track_id == NO_TRACK:
            return {"mpris:trackid": GLib.Variant("o", NO_TRACK)}
        value = {"mpris:trackid": GLib.Variant("o", self.track_id),
                 "xesam:title": GLib.Variant("s", self.title)}
        if self.artists:
            value["xesam:artist"] = GLib.Variant("as", self.artists)
        if self.album:
            value["xesam:album"] = GLib.Variant("s", self.album)
        if self.length_us > 0:
            value["mpris:length"] = GLib.Variant("x", self.length_us)
        if self.art_url:
            value["mpris:artUrl"] = GLib.Variant("s", self.art_url)
        return value


@dataclass
class State:
    status: str = "Stopped"   # Playing, Paused or Stopped
    track: Track = field(default_factory=Track)
    position_us: int = 0
    rate: float = 1.0
    can_seek: bool = False
    can_go_next: bool = False
    can_go_previous: bool = False
    measured_at: float = field(default_factory=time.monotonic)

    def position_now(self) -> int:
        if self.status != "Playing":
            return self.position_us
        elapsed = time.monotonic() - self.measured_at
        position = self.position_us + int(elapsed * 1e6 * self.rate)
        return min(position, self.track.length_us) if self.track.length_us > 0 else position


class Player:
    """One exported player. Commands go to `adapter`, which calls update()."""

    def __init__(self, bus_suffix: str, identity: str, desktop_entry: str, adapter) -> None:
        self.bus_name = f"org.mpris.MediaPlayer2.{bus_suffix}"
        self.identity, self.desktop_entry, self.adapter = identity, desktop_entry, adapter
        self.state = State()
        self.connection: Gio.DBusConnection | None = None
        self._owner_id = 0
        self._registrations: list[int] = []
        self._node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)

    # ── Presence: the player exists only while the program can be reached ──

    def appear(self, connection: Gio.DBusConnection) -> None:
        if self._owner_id:
            return
        self.connection = connection
        for interface in self._node.interfaces:
            self._registrations.append(connection.register_object(
                PATH, interface, self._method_call, self._get_property, None))
        self._owner_id = Gio.bus_own_name_on_connection(connection, self.bus_name, Gio.BusNameOwnerFlags.NONE,
                                                        None, None)

    def disappear(self) -> None:
        if not self._owner_id or self.connection is None:
            return
        Gio.bus_unown_name(self._owner_id)
        self._owner_id = 0
        for registration in self._registrations:
            self.connection.unregister_object(registration)
        self._registrations.clear()
        self.state = State()

    @property
    def present(self) -> bool:
        return bool(self._owner_id)

    # ── Updates from the adapter ───────────────────────────────────────────

    def update(self, state: State, *, seeked: bool = False) -> None:
        old, self.state = self.state, state
        if not self.present:
            return
        changed: dict[str, GLib.Variant] = {}
        if old.status != state.status:
            changed["PlaybackStatus"] = GLib.Variant("s", state.status)
        if old.track != state.track:
            changed["Metadata"] = GLib.Variant("a{sv}", state.track.metadata())
        if old.rate != state.rate:
            changed["Rate"] = GLib.Variant("d", state.rate)
        for name, attribute in (("CanSeek", "can_seek"), ("CanGoNext", "can_go_next"),
                                ("CanGoPrevious", "can_go_previous")):
            if getattr(old, attribute) != getattr(state, attribute):
                changed[name] = GLib.Variant("b", getattr(state, attribute))
        if changed:
            self.connection.emit_signal(None, PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                        GLib.Variant("(sa{sv}as)", ("org.mpris.MediaPlayer2.Player", changed, [])))
        if seeked:
            self.connection.emit_signal(None, PATH, "org.mpris.MediaPlayer2.Player", "Seeked",
                                        GLib.Variant("(x)", (state.position_us,)))

    # ── D-Bus ──────────────────────────────────────────────────────────────

    def _properties(self, interface: str) -> dict[str, GLib.Variant]:
        if interface == "org.mpris.MediaPlayer2":
            return {"CanQuit": GLib.Variant("b", False), "CanRaise": GLib.Variant("b", False),
                    "HasTrackList": GLib.Variant("b", False), "Identity": GLib.Variant("s", self.identity),
                    "DesktopEntry": GLib.Variant("s", self.desktop_entry),
                    "SupportedUriSchemes": GLib.Variant("as", []), "SupportedMimeTypes": GLib.Variant("as", [])}
        state = self.state
        active = state.status != "Stopped"
        return {"PlaybackStatus": GLib.Variant("s", state.status), "Rate": GLib.Variant("d", state.rate),
                "Metadata": GLib.Variant("a{sv}", state.track.metadata()), "Volume": GLib.Variant("d", 1.0),
                "Position": GLib.Variant("x", state.position_now()),
                "MinimumRate": GLib.Variant("d", 1.0), "MaximumRate": GLib.Variant("d", 1.0),
                "CanGoNext": GLib.Variant("b", state.can_go_next),
                "CanGoPrevious": GLib.Variant("b", state.can_go_previous),
                "CanPlay": GLib.Variant("b", active), "CanPause": GLib.Variant("b", active),
                "CanSeek": GLib.Variant("b", state.can_seek), "CanControl": GLib.Variant("b", True)}

    def _get_property(self, _connection, _sender, _path, interface, name):
        return self._properties(interface).get(name)

    def _method_call(self, _connection, _sender, _path, interface, method, parameters, invocation) -> None:
        if interface == "org.freedesktop.DBus.Properties":
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
            return
        args = parameters.unpack()
        commands = {
            "Play": lambda: self.adapter.play(), "Pause": lambda: self.adapter.pause(),
            "PlayPause": lambda: self.adapter.play_pause(), "Stop": lambda: self.adapter.stop(),
            "Next": lambda: self.adapter.next(), "Previous": lambda: self.adapter.previous(),
            "Seek": lambda: self.adapter.seek_by(args[0]),
            "SetPosition": lambda: args[0] == self.state.track.track_id and self.adapter.seek_to(args[1]),
        }
        command = commands.get(method)
        if command is not None and self.state.status != "Stopped":
            command()
        invocation.return_value(None)
