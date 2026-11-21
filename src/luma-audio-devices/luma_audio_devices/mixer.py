# SPDX-License-Identifier: MPL-2.0
"""The per-app mixer: every app that is playing, as one row.

Rows are built from the pw-dump graph (graph.StreamNode) and the shared app
key (apps.app_key): one row per app, however many streams it has, running or
corked. A row stays for LINGER_SECONDS after the app's last stream closes, so
an app does not leave and come back between two tracks; an app without a
stream is never listed. What the app is playing and its transport come from
its MPRIS player when it has one (mpris.py); without one the row is the app's
own name and icon.

"Other sounds" (System sounds, Messages, Phone) are rows that stay while
silent: their volume holds for sounds not yet played.

Volume, mute and output changes go to WirePlumber as luma-app-audio commands
(luma/app-audio.lua), which applies them to every stream of the app and
remembers them for its next stream, by app key.

Volumes on the bus use the cubic 0..1 scale GNOME's sliders and wpctl use;
1.0 is the stream's full level, values up to 1.5 amplify.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging

from . import apps, mpris

__all__ = ("Application", "Mixer", "MAX_VOLUME", "LINGER_SECONDS", "OTHER_SOUNDS")

log = logging.getLogger("luma-audio-devices")

MAX_VOLUME = 1.5
LINGER_SECONDS = 10.0
SYSTEM_ROUTE_KEY = "restore.stream.Output/Audio.media.role:Notification"
# Apps whose sounds are listed under "Other sounds" whether or not they are
# sounding: (app key, name when no desktop entry is installed).
OTHER_SOUNDS = (
    ("org.projectluma.Messages", "Messages"),
    ("org.projectluma.Phone", "Phone"),
)
SAVED_STATE_PREFIX = "app.state:"


@dataclass
class Application:
    key: str
    name: str
    icon: str
    desktop_id: str
    kind: str                      # "app" or "system"
    volume: float                  # cubic
    muted: bool
    output: str                    # node.name of the app's own output, "" for the default
    playing: bool
    streams: list[int] = field(default_factory=list)
    serials: list[int] = field(default_factory=list)
    pid: int = 0
    playing_on: str = ""           # node.name of the output it plays on now
    listed: bool = True            # in the playing list: has a stream, or had one LINGER_SECONDS ago
    other: bool = False            # an "Other sounds" row
    browser: bool = False
    binary: str = ""
    pids: set = field(default_factory=set)
    order: int = 0                 # when the row first appeared
    gone_at: float | None = None   # when its last stream closed
    media: dict = field(default_factory=lambda: mpris.describe(None))

    def as_dict(self) -> dict:
        row = {
            "id": self.key, "name": self.name, "icon": self.icon, "desktop_id": self.desktop_id,
            "kind": self.kind, "volume": round(self.volume, 4), "muted": self.muted,
            "output": self.output, "playing": self.playing, "streams": list(self.streams),
            "pid": self.pid, "playing_on": self.playing_on, "listed": self.listed, "other": self.other,
        }
        row.update(self.media)
        return row


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class Mixer:
    def __init__(self, index: apps.DesktopIndex | None, control) -> None:
        self._index = index
        self._control = control
        self.applications: dict[str, Application] = {}
        self._order = 0

    # Model ------------------------------------------------------------------

    def update(self, graph, now: float = 0.0, players=()) -> bool:
        """Rebuild the rows from the graph and the media players. True when
        anything a client shows changed."""
        rows: dict[str, Application] = {}
        measured: set[str] = set()   # rows with a stream that reported its volume
        for stream in graph.stream_nodes():
            if not apps.is_app_playback(stream.props):
                continue
            if apps.is_system_sound(stream.props):
                key = apps.SYSTEM_KEY
            else:
                key, _source = apps.app_key(stream.props, stream.client)
                if key is None:
                    continue
            row = rows.get(key)
            if row is None:
                identity = self._identify(key, stream.props, stream.client)
                row = Application(key, identity.name, identity.icon, identity.desktop_id,
                                  "system" if key == apps.SYSTEM_KEY else "app",
                                  0.0, True, "", False,
                                  pid=_as_int(stream.props.get("application.process.id")),
                                  browser=self._is_browser(identity.desktop_id))
                rows[key] = row
            binary = stream.props.get("application.process.binary") or \
                stream.client.get("application.process.binary")
            if isinstance(binary, str) and binary and not row.binary:
                row.binary = binary.rsplit("/", 1)[-1]
            for value in (stream.client.get("pipewire.sec.pid"), stream.props.get("application.process.id"),
                          stream.client.get("application.process.id")):
                if _as_int(value):
                    row.pids.add(_as_int(value))
            volume = apps.stream_volume(stream.channel_volumes)
            if volume is not None:
                row.volume = max(row.volume, apps.linear_to_cubic(volume))
                measured.add(key)
            row.muted = row.muted and stream.mute is True
            row.playing = row.playing or stream.state == "running"
            row.streams.append(stream.node_id)
            if stream.serial is not None:
                row.serials.append(stream.serial)
            # The app's own output is the one it was sent to and plays on
            # now. A target left in the metadata that the stream does not use
            # (an output that is unplugged, or a link to the default) is
            # reported as following the default.
            if stream.target_serial is not None and not row.output:
                chosen = graph.node_name_for_serial(stream.target_serial)
                if chosen and chosen == stream.linked_sink:
                    row.output = chosen
            if stream.linked_sink and not row.playing_on:
                row.playing_on = stream.linked_sink

        # A stream on its way in or out reports no volume: the row keeps
        # what it showed rather than dropping to zero for a moment.
        for key, row in rows.items():
            previous = self.applications.get(key)
            if key not in measured and previous is not None and row.kind == "app":
                row.volume, row.muted = previous.volume, previous.muted

        # An app whose last stream closed keeps its row for LINGER_SECONDS.
        for key, old in self.applications.items():
            if key in rows or old.kind != "app" or not old.listed:
                continue
            gone_at = old.gone_at if old.gone_at is not None else now
            if now - gone_at >= LINGER_SECONDS:
                continue
            rows[key] = Application(key, old.name, old.icon, old.desktop_id, "app", old.volume, old.muted,
                                    "", False, pid=old.pid, browser=old.browser, binary=old.binary,
                                    pids=set(old.pids), order=old.order, gone_at=gone_at)

        # System sounds is always a row: its volume holds for sounds not yet
        # played (WirePlumber's route-settings value, as pavucontrol uses).
        if apps.SYSTEM_KEY not in rows:
            saved = graph.metadata_json("route-settings", SYSTEM_ROUTE_KEY) or {}
            volume = saved.get("volume") if isinstance(saved, dict) else None
            mute = saved.get("mute") if isinstance(saved, dict) else None
            identity = self._identify(apps.SYSTEM_KEY, {}, {})
            rows[apps.SYSTEM_KEY] = Application(
                apps.SYSTEM_KEY, identity.name, identity.icon, "", "system",
                apps.linear_to_cubic(volume) if isinstance(volume, (int, float)) else 1.0,
                mute is True, "", False)
        system = rows[apps.SYSTEM_KEY]
        system.other = True
        system.listed = False
        # So are the other sounds: silent, they show the level WirePlumber
        # remembers for the app (published as app.state:<key>).
        for key, fallback_name in OTHER_SOUNDS:
            row = rows.get(key)
            if row is None:
                saved = graph.metadata_json("luma-app-audio", SAVED_STATE_PREFIX + key) or {}
                volume = saved.get("volume") if isinstance(saved, dict) else None
                mute = saved.get("mute") if isinstance(saved, dict) else None
                identity = self._identify(key, {"application.name": fallback_name}, {})
                row = rows[key] = Application(
                    key, identity.name, identity.icon, identity.desktop_id, "app",
                    apps.linear_to_cubic(volume) if isinstance(volume, (int, float)) else 1.0,
                    mute is True, "", False, listed=False)
            row.other = True

        for row in rows.values():
            if not row.streams and row.kind != "system" and not row.other:
                row.muted = row.muted if row.gone_at is not None else False
            previous = self.applications.get(row.key)
            if previous is not None and previous.order:
                row.order = previous.order
            elif row.listed and not row.order:
                self._order += 1
                row.order = self._order
            if row.listed and row.kind == "app":
                player = mpris.match(players, key=row.key, desktop_id=row.desktop_id,
                                     binary=row.binary, pids=row.pids)
                row.media = mpris.describe(player, browser=row.browser)
                # A player that says it is paused is paused, whatever its
                # stream does (a browser keeps a silent stream running).
                if row.media["status"] == "paused":
                    row.playing = False
        changed = _public(rows) != _public(self.applications)
        self.applications = rows
        return changed

    def next_deadline(self) -> float | None:
        """When the next lingering row leaves, for the service's one timer."""
        deadlines = [row.gone_at + LINGER_SECONDS for row in self.applications.values()
                     if row.gone_at is not None]
        return min(deadlines) if deadlines else None

    def _is_browser(self, desktop_id: str) -> bool:
        if self._index is None or not desktop_id:
            return False
        try:
            return self._index.is_browser(desktop_id)
        except Exception:
            return False

    def _identify(self, key, props, client) -> apps.AppIdentity:
        if self._index is None:
            if key == apps.SYSTEM_KEY:
                return apps.SYSTEM_IDENTITY
            return apps.AppIdentity(key, props.get("application.name") or key,
                                    props.get("application.icon-name") or apps.FALLBACK_ICON, "")
        try:
            return self._index.identify(key, props, client)
        except Exception:  # a broken desktop entry must not hide the row
            log.exception("cannot identify %s", key)
            return apps.AppIdentity(key, props.get("application.name") or key, apps.FALLBACK_ICON, "")

    def rows(self) -> list[dict]:
        """System sounds and the other sounds first, then the playing apps in
        the order they started (a row never jumps while it is on screen)."""
        ordered = sorted(self.applications.values(),
                         key=lambda row: (row.kind != "system", not row.other, row.order,
                                          row.name.casefold(), row.key))
        return [row.as_dict() for row in ordered]

    def player_of(self, key: str) -> str:
        row = self.applications.get(key)
        return row.media.get("player", "") if row else ""

    # Commands ---------------------------------------------------------------

    def set_volume(self, key: str, volume: float) -> bool:
        volume = min(MAX_VOLUME, max(0.0, float(volume)))
        return self._control.app_audio_command(
            "app.volume:" + key, {"volume": apps.cubic_to_linear(volume)})

    def set_mute(self, key: str, muted: bool) -> bool:
        return self._control.app_audio_command("app.volume:" + key, {"mute": bool(muted)})

    def set_output(self, key: str, node_name: str) -> bool:
        return self._control.app_audio_command("app.target:" + key, {"name": node_name or None})


def _public(rows: dict[str, Application]) -> list:
    return sorted((row.key, row.name, row.icon, row.kind, round(row.volume, 3), row.muted,
                   row.output, row.playing, tuple(row.streams), row.playing_on, row.listed, row.other,
                   tuple(sorted(row.media.items()))) for row in rows.values())
