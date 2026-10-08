# SPDX-License-Identifier: Apache-2.0
"""MPRIS adapter for Tide's single playback controller."""
from __future__ import annotations

from .identity import APP_ID, PREVIEW_APP_ID, mpris_name, validate_application_id

from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlsplit

from .model import RepeatMode
from .playback import PlaybackController, PlaybackSnapshot, PlaybackState

MPRIS_PATH = "/org/mpris/MediaPlayer2"
MPRIS_NAME = "org.mpris.MediaPlayer2.Tide"

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
    <property name="LoopStatus" type="s" access="readwrite"/>
    <property name="Rate" type="d" access="readwrite"/>
    <property name="Shuffle" type="b" access="readwrite"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
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
    application_id = APP_ID

    def __init__(
        self,
        controller: PlaybackController,
        *,
        application_id: str = APP_ID,
        raise_window: Callable[[], None],
        quit_application: Callable[[], None],
    ) -> None:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        self.application_id = validate_application_id(application_id)
        self.Gio = Gio
        self.GLib = GLib
        self.controller = controller
        self.raise_window = raise_window
        self.quit_application = quit_application
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        self.registrations: list[int] = []
        for interface in self.node.interfaces:
            self.registrations.append(
                self.connection.register_object(
                    MPRIS_PATH,
                    interface,
                    self._method_call,
                    self._get_property,
                    self._set_property,
                )
            )
        self.owner_id = Gio.bus_own_name_on_connection(
            self.connection, mpris_name(self.application_id), Gio.BusNameOwnerFlags.NONE, None, None
        )
        self._previous = controller.snapshot
        self._unsubscribe = controller.subscribe(self._changed)

    def _track_path(self, snapshot: PlaybackSnapshot) -> str:
        if snapshot.track is None:
            return "/org/mpris/MediaPlayer2/TrackList/NoTrack"
        return "/org/projectluma/Tide/track/" + snapshot.track.id.replace("-", "_")

    def _metadata(self, snapshot: PlaybackSnapshot):
        V = self.GLib.Variant
        if snapshot.track is None:
            return {}
        track = snapshot.track
        values = {
            "mpris:trackid": V("o", self._track_path(snapshot)),
            "mpris:length": V("x", max(0, snapshot.duration_ns // 1000)),
            "xesam:title": V("s", track.title),
            "xesam:artist": V("as", [track.artist]),
            "xesam:album": V("s", track.album),
            "xesam:albumArtist": V("as", [track.album_artist]),
            "xesam:genre": V("as", [track.genre] if track.genre else []),
        }
        art_uri = snapshot.copy.artwork_uri if snapshot.copy else None
        if art_uri:
            parsed = urlsplit(art_uri)
            # Only artwork already on this device is published. A server's
            # artwork URL would need credentials, and Tide never shares those.
            if parsed.scheme == "file" and Path(unquote(parsed.path)).is_file():
                values["mpris:artUrl"] = V("s", art_uri)
        return values

    @staticmethod
    def _status(snapshot: PlaybackSnapshot) -> str:
        if snapshot.state is PlaybackState.PLAYING:
            return "Playing"
        if snapshot.state in (PlaybackState.PAUSED, PlaybackState.LOADING):
            return "Paused"
        return "Stopped"

    @staticmethod
    def _loop(snapshot: PlaybackSnapshot) -> str:
        return {
            RepeatMode.OFF: "None",
            RepeatMode.ALL: "Playlist",
            RepeatMode.ONE: "Track",
        }[snapshot.repeat]

    def _root_property(self, name: str):
        V = self.GLib.Variant
        values = {
            "CanQuit": V("b", True),
            "CanRaise": V("b", True),
            "HasTrackList": V("b", False),
            "Identity": V("s", "Tide (LumaUI preview)" if self.application_id == PREVIEW_APP_ID else "Tide"),
            "DesktopEntry": V("s", self.application_id),
            # Tide does not implement OpenUri, so it advertises no schemes or
            # types a controller could hand it.
            "SupportedUriSchemes": V("as", []),
            "SupportedMimeTypes": V("as", []),
        }
        return values.get(name)

    def _player_property(self, name: str):
        V = self.GLib.Variant
        snapshot = self.controller.snapshot
        values = {
            "PlaybackStatus": V("s", self._status(snapshot)),
            "LoopStatus": V("s", self._loop(snapshot)),
            "Rate": V("d", 1.0),
            "Shuffle": V("b", snapshot.shuffle),
            "Metadata": V("a{sv}", self._metadata(snapshot)),
            "Volume": V("d", snapshot.volume),
            "Position": V("x", max(0, snapshot.position_ns // 1000)),
            "MinimumRate": V("d", 1.0),
            "MaximumRate": V("d", 1.0),
            "CanGoNext": V("b", snapshot.can_next),
            "CanGoPrevious": V("b", snapshot.can_previous),
            "CanPlay": V("b", snapshot.track is not None and snapshot.copy is not None),
            "CanPause": V("b", snapshot.track is not None),
            "CanSeek": V("b", snapshot.can_seek),
            "CanControl": V("b", True),
        }
        return values.get(name)

    def _get_property(
        self,
        _connection: object,
        _sender: str,
        _path: str,
        interface: str,
        name: str,
    ):
        if interface == "org.mpris.MediaPlayer2":
            return self._root_property(name)
        return self._player_property(name)

    def _set_property(
        self,
        _connection: object,
        _sender: str,
        _path: str,
        interface: str,
        name: str,
        value: object,
    ) -> bool:
        if interface != "org.mpris.MediaPlayer2.Player":
            return False
        unpacked = value.unpack()
        if name == "LoopStatus":
            modes = {"None": RepeatMode.OFF, "Playlist": RepeatMode.ALL, "Track": RepeatMode.ONE}
            if unpacked not in modes:
                return False
            self.controller.set_repeat(modes[unpacked])
            return True
        if name == "Shuffle":
            self.controller.set_shuffle(bool(unpacked))
            return True
        if name == "Volume":
            self.controller.set_volume(float(unpacked))
            return True
        return name == "Rate" and float(unpacked) == 1.0

    def _method_call(
        self,
        _connection: object,
        _sender: str,
        _path: str,
        interface: str,
        method: str,
        parameters: object,
        invocation: object,
    ) -> None:
        if interface == "org.mpris.MediaPlayer2":
            if method == "Raise":
                self.raise_window()
            elif method == "Quit":
                self.quit_application()
            invocation.return_value(None)
            return
        actions = {
            "Next": self.controller.next,
            "Previous": self.controller.previous,
            "Pause": self.controller.pause,
            "PlayPause": self.controller.toggle,
            "Stop": self.controller.stop,
            "Play": self.controller.play,
        }
        if method in actions:
            actions[method]()
        elif method == "Seek":
            offset = int(parameters.unpack()[0]) * 1000
            self.controller.seek_relative(offset)
            self._emit_seeked()
        elif method == "SetPosition":
            track_path, microseconds = parameters.unpack()
            if track_path == self._track_path(self.controller.snapshot):
                self.controller.seek(int(microseconds) * 1000)
                self._emit_seeked()
        elif method == "OpenUri":
            invocation.return_dbus_error(
                "org.mpris.MediaPlayer2.Error.NotSupported",
                "Add the file to Tide's source-aware library before playing it.",
            )
            return
        invocation.return_value(None)

    def _emit_seeked(self) -> None:
        self.connection.emit_signal(
            None,
            MPRIS_PATH,
            "org.mpris.MediaPlayer2.Player",
            "Seeked",
            self.GLib.Variant("(x)", (self.controller.snapshot.position_ns // 1000,)),
        )

    def _changed(self, snapshot: PlaybackSnapshot) -> None:
        previous = self._previous
        self._previous = snapshot
        changed = {}
        for name, old, new in (
            ("PlaybackStatus", self._status(previous), self._status(snapshot)),
            ("LoopStatus", self._loop(previous), self._loop(snapshot)),
            ("Shuffle", previous.shuffle, snapshot.shuffle),
            ("Volume", previous.volume, snapshot.volume),
            ("Metadata", previous.track, snapshot.track),
            ("CanGoNext", previous.can_next, snapshot.can_next),
            ("CanGoPrevious", previous.can_previous, snapshot.can_previous),
            ("CanPlay", (previous.track, previous.copy), (snapshot.track, snapshot.copy)),
            ("CanPause", previous.track, snapshot.track),
            ("CanSeek", previous.can_seek, snapshot.can_seek),
        ):
            if old != new:
                changed[name] = self._player_property(name)
        if not changed:
            return
        self.connection.emit_signal(
            None,
            MPRIS_PATH,
            "org.freedesktop.DBus.Properties",
            "PropertiesChanged",
            self.GLib.Variant(
                "(sa{sv}as)", ("org.mpris.MediaPlayer2.Player", changed, [])
            ),
        )

    def close(self) -> None:
        self._unsubscribe()
        self.Gio.bus_unown_name(self.owner_id)
        for registration in self.registrations:
            self.connection.unregister_object(registration)
