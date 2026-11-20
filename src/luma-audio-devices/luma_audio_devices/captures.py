# SPDX-License-Identifier: MPL-2.0
"""Capture sessions for the audio capture portal.

luma-portal (org.freedesktop.impl.portal.AudioCapture) asks for a capture
once the person has chosen what to share. The capture itself lives in
WirePlumber (luma/app-audio.lua): one node, fed by every stream of the chosen
apps, or of every app but the requester. This module writes the request to
the luma-app-audio metadata, waits for WirePlumber's node to appear in the
graph, and answers with its id and serial for the portal to grant.

A capture ends when its owner closes it, when the owner leaves the session
bus, or (in WirePlumber) when this service leaves PipeWire.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import secrets
from typing import Callable

__all__ = ("CaptureManager", "CaptureError", "MODES")

log = logging.getLogger("luma-audio-devices")

MODES = ("apps", "system")
WAIT_MS = 5000


class CaptureError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _Capture:
    capture_id: str
    owner: str
    requester: str
    mode: str
    apps: list[str]
    name_watch: int = 0
    node_id: int | None = None
    serial: int | None = None
    waiters: list = None
    timeout_id: int = 0


class CaptureManager:
    def __init__(self, control, graph_nodes: Callable[[str, str], list[tuple[int, dict]]],
                 connection: Callable[[], object]) -> None:
        self._control = control
        self._graph_nodes = graph_nodes
        self._connection = connection
        self._captures: dict[str, _Capture] = {}

    def create(self, owner: str, requester: str, mode: str, chosen: list[str],
               include_self: bool, label: str, reply: Callable) -> None:
        """reply(capture_id, node_id, serial) or reply(CaptureError)."""
        from gi.repository import Gio, GLib
        if mode not in MODES:
            raise CaptureError("InvalidArgument", f"Unknown capture mode “{mode}”.")
        chosen = [key for key in chosen if isinstance(key, str) and key]
        if mode == "apps" and not chosen:
            raise CaptureError("InvalidArgument", "Choose at least one app to capture.")
        if not self._control.app_audio_ready:
            raise CaptureError("Unavailable", "Sound capture isn’t available right now.")
        capture_id = secrets.token_hex(8)
        capture = _Capture(capture_id, owner, requester or "", mode, chosen, waiters=[reply])
        request = {"mode": mode, "apps": chosen, "requester": requester or "",
                   "include_self": bool(include_self), "name": label or "",
                   "owner": self._control.own_client_id()}
        if not self._control.app_audio_command("capture:" + capture_id, request):
            raise CaptureError("Unavailable", "Sound capture isn’t available right now.")
        connection = self._connection()
        if connection is not None:
            capture.name_watch = Gio.bus_watch_name_on_connection(
                connection, owner, Gio.BusNameWatcherFlags.NONE, None,
                lambda *_: self.close(owner, capture_id, "its owner left the bus"))
        capture.timeout_id = GLib.timeout_add(WAIT_MS, lambda: self._timed_out(capture_id))
        self._captures[capture_id] = capture
        log.info("capture %s requested by %s for %s (%s)", capture_id, owner,
                 requester or "a host app", mode if mode == "system" else ", ".join(chosen))
        self.graph_changed()

    def graph_changed(self) -> None:
        """Answer requests whose node has appeared; end captures whose node
        disappeared (WirePlumber restarted)."""
        for capture in list(self._captures.values()):
            nodes = self._graph_nodes("luma.audio-capture.id", capture.capture_id)
            if nodes and capture.node_id is None:
                capture.node_id, props = nodes[0]
                try:
                    capture.serial = int(props.get("object.serial"))
                except (TypeError, ValueError):
                    capture.serial = 0
                self._answer(capture)
            elif not nodes and capture.node_id is not None:
                log.info("capture %s lost its node", capture.capture_id)
                self.close(capture.owner, capture.capture_id, "its node went away")

    def _answer(self, capture: _Capture) -> None:
        from gi.repository import GLib
        if capture.timeout_id:
            GLib.source_remove(capture.timeout_id)
            capture.timeout_id = 0
        waiters, capture.waiters = capture.waiters or [], []
        for reply in waiters:
            reply(capture.capture_id, capture.node_id, capture.serial)

    def _timed_out(self, capture_id: str) -> bool:
        from gi.repository import GLib
        capture = self._captures.get(capture_id)
        if capture is not None:
            capture.timeout_id = 0
            waiters, capture.waiters = capture.waiters or [], []
            self.close(capture.owner, capture_id, "WirePlumber did not create it")
            for reply in waiters:
                reply(CaptureError("Failed", "The capture could not be started."))
        return GLib.SOURCE_REMOVE

    def close(self, owner: str, capture_id: str, reason: str = "closed") -> bool:
        from gi.repository import Gio, GLib
        capture = self._captures.get(capture_id)
        if capture is None or capture.owner != owner:
            return False
        del self._captures[capture_id]
        if capture.name_watch:
            Gio.bus_unwatch_name(capture.name_watch)
        if capture.timeout_id:
            GLib.source_remove(capture.timeout_id)
        self._control.app_audio_command("capture:" + capture_id, None)
        log.info("capture %s ended: %s", capture_id, reason)
        return True

    def describe(self) -> list[dict]:
        return [{"id": c.capture_id, "requester": c.requester, "mode": c.mode,
                 "apps": list(c.apps), "node": c.node_id or 0}
                for c in self._captures.values()]
