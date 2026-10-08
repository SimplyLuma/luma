# SPDX-License-Identifier: Apache-2.0
"""Leaf in the dock's media module, while it reads aloud.

The player is published only while the narrator is active, so the dock shows
Leaf exactly when there is something to control. The "track" is the chapter;
next and previous mean a sentence.
"""
from __future__ import annotations

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from .narrator import Narrator, NarratorState

MPRIS_PATH = "/org/mpris/MediaPlayer2"
MPRIS_NAME = "org.mpris.MediaPlayer2.leaf"

INTROSPECTION_XML = """
<node>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/><method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Next"/><method name="Previous"/><method name="Pause"/>
    <method name="PlayPause"/><method name="Stop"/><method name="Play"/>
    <method name="Seek"><arg direction="in" name="Offset" type="x"/></method>
    <method name="SetPosition"><arg direction="in" name="TrackId" type="o"/><arg direction="in" name="Position" type="x"/></method>
    <method name="OpenUri"><arg direction="in" name="Uri" type="s"/></method>
    <signal name="Seeked"><arg name="Position" type="x"/></signal>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="Rate" type="d" access="readwrite"/>
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


class MprisService:
    def __init__(self, narrator: Narrator, *, raise_window, quit_application, cover_for) -> None:
        self.narrator = narrator
        self.raise_window = raise_window
        self.quit_application = quit_application
        self.cover_for = cover_for
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        self.registrations: list[int] = []
        self.owner_id = 0
        self._previous: dict = {}
        self._unsubscribe = narrator.subscribe(self._changed)

    def _publish(self) -> None:
        if self.registrations:
            return
        for interface in self.node.interfaces:
            self.registrations.append(self.connection.register_object(
                MPRIS_PATH, interface, self._method_call, self._get_property, None))
        self.owner_id = Gio.bus_own_name_on_connection(self.connection, MPRIS_NAME, Gio.BusNameOwnerFlags.NONE, None, None)

    def _withdraw(self) -> None:
        if self.owner_id:
            Gio.bus_unown_name(self.owner_id)
            self.owner_id = 0
        for registration in self.registrations:
            self.connection.unregister_object(registration)
        self.registrations = []

    def _metadata(self, state: NarratorState) -> dict:
        V = GLib.Variant
        if not state.book_id:
            return {}
        track = "/org/projectluma/Leaf/book/" + "".join(c if c.isalnum() else "_" for c in state.book_id)
        values = {
            "mpris:trackid": V("o", track),
            "xesam:title": V("s", state.chapter_label or state.title),
            "xesam:album": V("s", state.title),
            "xesam:artist": V("as", [state.author] if state.author else []),
        }
        cover = self.cover_for(state.book_id)
        if cover:
            values["mpris:artUrl"] = V("s", Gio.File.new_for_path(cover).get_uri())
        return values

    @staticmethod
    def _status(state: NarratorState) -> str:
        if not state.active:
            return "Stopped"
        return "Playing" if state.playing else "Paused"

    def _root_property(self, name: str):
        V = GLib.Variant
        return {
            "CanQuit": V("b", True), "CanRaise": V("b", True), "HasTrackList": V("b", False),
            "Identity": V("s", "Leaf"), "DesktopEntry": V("s", "org.projectluma.Leaf"),
            "SupportedUriSchemes": V("as", []), "SupportedMimeTypes": V("as", []),
        }.get(name)

    def _player_property(self, name: str):
        V = GLib.Variant
        state = self.narrator.state
        return {
            "PlaybackStatus": V("s", self._status(state)),
            "Rate": V("d", state.speed),
            "Metadata": V("a{sv}", self._metadata(state)),
            "Volume": V("d", 1.0),
            "Position": V("x", 0),
            "MinimumRate": V("d", 0.75),
            "MaximumRate": V("d", 2.0),
            "CanGoNext": V("b", state.active),
            "CanGoPrevious": V("b", state.active and state.can_previous),
            "CanPlay": V("b", state.active),
            "CanPause": V("b", state.active),
            "CanSeek": V("b", False),
            "CanControl": V("b", True),
        }.get(name)

    def _get_property(self, _connection, _sender, _path, interface, name):
        if interface == "org.mpris.MediaPlayer2":
            return self._root_property(name)
        return self._player_property(name)

    def _method_call(self, _connection, _sender, _path, interface, method, parameters, invocation) -> None:
        if interface == "org.mpris.MediaPlayer2":
            if method == "Raise":
                self.raise_window()
            elif method == "Quit":
                self.quit_application()
            invocation.return_value(None)
            return
        narrator = self.narrator
        actions = {
            "Next": narrator.next_sentence, "Previous": narrator.previous_sentence,
            "Pause": narrator.pause, "PlayPause": narrator.toggle, "Stop": narrator.stop, "Play": narrator.resume,
        }
        if method in actions:
            actions[method]()
        elif method in ("OpenUri", "Seek", "SetPosition"):
            invocation.return_dbus_error("org.mpris.MediaPlayer2.Error.NotSupported",
                                         "A book is read by sentence; it has no timeline to seek.")
            return
        invocation.return_value(None)

    def _changed(self, state: NarratorState) -> None:
        if state.active:
            self._publish()
        else:
            self._withdraw()
            self._previous = {}
            return
        current = {
            "PlaybackStatus": self._status(state), "Rate": state.speed,
            "Metadata": (state.book_id, state.chapter_label, state.title),
            "CanGoPrevious": state.can_previous,
        }
        changed = {name: self._player_property(name) for name, value in current.items() if self._previous.get(name) != value}
        self._previous = current
        if changed:
            self.connection.emit_signal(None, MPRIS_PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                        GLib.Variant("(sa{sv}as)", ("org.mpris.MediaPlayer2.Player", changed, [])))

    def close(self) -> None:
        self._unsubscribe()
        self._withdraw()
