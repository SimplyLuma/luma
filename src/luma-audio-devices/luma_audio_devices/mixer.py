# SPDX-License-Identifier: MPL-2.0
"""The per-app mixer: every app that has a sound stream, as one row.

Rows are built from the pw-dump graph (graph.StreamNode) and the shared app
key (apps.app_key). Volume, mute and output changes go to WirePlumber as
luma-app-audio commands (luma/app-audio.lua), which applies them to every
stream of the app and remembers them for its next stream.

Volumes on the bus use the cubic 0..1 scale GNOME's sliders and wpctl use;
1.0 is the stream's full level, values up to 1.5 amplify.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging

from . import apps

__all__ = ("Application", "Mixer", "MAX_VOLUME")

log = logging.getLogger("luma-audio-devices")

MAX_VOLUME = 1.5


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

    def as_dict(self) -> dict:
        return {
            "id": self.key, "name": self.name, "icon": self.icon, "desktop_id": self.desktop_id,
            "kind": self.kind, "volume": round(self.volume, 4), "muted": self.muted,
            "output": self.output, "playing": self.playing, "streams": list(self.streams),
            "pid": self.pid,
        }


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

    # Model ------------------------------------------------------------------

    def update(self, graph) -> bool:
        """Rebuild the rows from the graph. True when anything a client shows
        changed."""
        rows: dict[str, Application] = {}
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
                                  pid=_as_int(stream.props.get("application.process.id")))
                rows[key] = row
            volume = apps.stream_volume(stream.channel_volumes)
            if volume is not None:
                row.volume = max(row.volume, apps.linear_to_cubic(volume))
            row.muted = row.muted and stream.mute is True
            row.playing = row.playing or stream.state == "running"
            row.streams.append(stream.node_id)
            if stream.serial is not None:
                row.serials.append(stream.serial)
            if stream.target_serial is not None and not row.output:
                row.output = graph.node_name_for_serial(stream.target_serial) or ""
        for row in rows.values():
            if not row.streams:
                row.muted = False
        changed = _public(rows) != _public(self.applications)
        self.applications = rows
        return changed

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
        """System sounds first, then apps by name."""
        ordered = sorted(self.applications.values(),
                         key=lambda row: (row.kind != "system", row.name.casefold(), row.key))
        return [row.as_dict() for row in ordered]

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
                   row.output, row.playing, tuple(row.streams)) for row in rows.values())
