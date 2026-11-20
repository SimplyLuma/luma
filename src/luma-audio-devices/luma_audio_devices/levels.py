# SPDX-License-Identifier: MPL-2.0
"""Live level meters, only while someone is looking at them.

A client (the Shell's sound panel, Settings' mixer, the capture picker) asks
for the levels of some targets and gets a unicast ``Levels`` signal about 20
times a second until it stops watching or leaves the bus. Each watched stream
or output gets one passive PipeWire capture stream (stream.monitor,
node.passive: it never wakes a device or keeps one awake) feeding GStreamer's
level element and a fakesink. When nobody watches, nothing runs: GStreamer is
not even loaded until the first watch.

Targets: an app id from GetApplications (every stream of the app, the loudest
wins), "@system" for System sounds, "output:<node.name>" for an output device,
or "output:" for the current default output.

Values are meter positions from 0 (-60 dB or quieter) to 1 (0 dBFS), from
the level element's decaying peak, so every client draws the same meter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import math
from typing import Callable

__all__ = ("LevelMeters", "meter_position", "FLOOR_DB")

log = logging.getLogger("luma-audio-devices")

FLOOR_DB = -60.0
INTERVAL_NS = 50_000_000
CLIENT_NAME = "luma-audio-levels"
APPLICATION_ID = "org.projectluma.AudioDevices"


def meter_position(db: float) -> float:
    if db is None or not math.isfinite(db) or db <= FLOOR_DB:
        return 0.0
    return min(1.0, (db - FLOOR_DB) / -FLOOR_DB)


@dataclass
class _Probe:
    """One PipeWire stream being metered."""

    target_serial: int
    is_device: bool
    pipeline: object = None
    level: float = 0.0


@dataclass
class _Watch:
    watch_id: int
    sender: str
    targets: list[str]
    name_watch: int = 0
    probes: dict = field(default_factory=dict)   # target -> [serial]


class LevelMeters:
    """Owns the probes; the service tells it which serials a target means."""

    def __init__(self, connection_for_signals: Callable[[], object],
                 resolve: Callable[[str], list[tuple[int, bool]]],
                 object_path: str, interface: str) -> None:
        self._connection = connection_for_signals
        self._resolve = resolve
        self._path = object_path
        self._interface = interface
        self._watches: dict[int, _Watch] = {}
        self._probes: dict[int, _Probe] = {}
        self._next_id = 1
        self._tick_id = 0
        self._Gst = None

    @property
    def active(self) -> bool:
        return bool(self._watches)

    # Watches ----------------------------------------------------------------

    def watch(self, sender: str, targets: list[str]) -> int:
        from gi.repository import Gio
        watch_id = self._next_id
        self._next_id += 1
        watch = _Watch(watch_id, sender, [t for t in targets if isinstance(t, str)][:64])
        connection = self._connection()
        if connection is not None:
            watch.name_watch = Gio.bus_watch_name_on_connection(
                connection, sender, Gio.BusNameWatcherFlags.NONE, None,
                lambda *_: self.unwatch(sender, watch_id))
        self._watches[watch_id] = watch
        log.info("levels for %s: %s", sender, ", ".join(watch.targets) or "nothing")
        self.refresh()
        return watch_id

    def unwatch(self, sender: str, watch_id: int) -> bool:
        from gi.repository import Gio
        watch = self._watches.get(watch_id)
        if watch is None or watch.sender != sender:
            return False
        del self._watches[watch_id]
        if watch.name_watch:
            Gio.bus_unwatch_name(watch.name_watch)
        self.refresh()
        return True

    def refresh(self) -> None:
        """Start probes for every serial a watch needs; stop the rest. Called
        after every graph change, so a new stream of a watched app is metered
        and a gone one stops."""
        wanted: dict[int, bool] = {}
        for watch in self._watches.values():
            watch.probes = {}
            for target in watch.targets:
                serials = self._resolve(target)
                watch.probes[target] = [serial for serial, _device in serials]
                for serial, device in serials:
                    wanted[serial] = device
        for serial in list(self._probes):
            if serial not in wanted:
                self._stop(self._probes.pop(serial))
        for serial, device in wanted.items():
            if serial not in self._probes:
                probe = _Probe(serial, device)
                if self._start(probe):
                    self._probes[serial] = probe
        self._schedule_tick()

    # GStreamer --------------------------------------------------------------

    def _gst(self):
        if self._Gst is None:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            Gst.init(None)
            self._Gst = Gst
        return self._Gst

    def _start(self, probe: _Probe) -> bool:
        try:
            Gst = self._gst()
        except (ImportError, ValueError) as error:
            log.warning("level meters need GStreamer: %s", error)
            return False
        props = Gst.Structure.new_from_string(
            "props, stream.monitor=(string)true, node.passive=(string)true, "
            "node.dont-reconnect=(string)true, luma.audio-levels=(string)true, "
            f"application.id=(string){APPLICATION_ID}, media.category=(string)Monitor, "
            f"stream.capture.sink=(string){'true' if probe.is_device else 'false'}")
        source = Gst.ElementFactory.make("pipewiresrc")
        level = Gst.ElementFactory.make("level")
        sink = Gst.ElementFactory.make("fakesink")
        convert = Gst.ElementFactory.make("audioconvert")
        if not all((source, level, sink, convert)):
            log.warning("pipewiresrc, audioconvert, level or fakesink is missing")
            return False
        source.set_property("target-object", str(probe.target_serial))
        source.set_property("client-name", CLIENT_NAME)
        source.set_property("stream-properties", props)
        level.set_property("interval", INTERVAL_NS)
        level.set_property("post-messages", True)
        sink.set_property("sync", False)
        sink.set_property("async", False)
        pipeline = Gst.Pipeline.new(f"levels-{probe.target_serial}")
        for element in (source, convert, level, sink):
            pipeline.add(element)
        source.link(convert)
        convert.link(level)
        level.link(sink)
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::element", lambda _b, m: self._on_level(probe, m))
        bus.connect("message::error", lambda _b, m: self._on_error(probe, m))
        probe.pipeline = pipeline
        pipeline.set_state(Gst.State.PLAYING)
        return True

    def _stop(self, probe: _Probe) -> None:
        if probe.pipeline is None:
            return
        bus = probe.pipeline.get_bus()
        bus.remove_signal_watch()
        probe.pipeline.set_state(self._Gst.State.NULL)
        probe.pipeline = None

    def _on_level(self, probe: _Probe, message) -> None:
        structure = message.get_structure()
        if structure is None or structure.get_name() != "level":
            return
        values = structure.get_value("decay")
        try:
            loudest = max(float(v) for v in values)
        except (TypeError, ValueError):
            return
        probe.level = meter_position(loudest)

    def _on_error(self, probe: _Probe, message) -> None:
        error, _debug = message.parse_error()
        log.info("level meter for %s stopped: %s", probe.target_serial, error.message)
        probe.level = 0.0

    # Signals ----------------------------------------------------------------

    def _schedule_tick(self) -> None:
        from gi.repository import GLib
        if self._watches and not self._tick_id:
            self._tick_id = GLib.timeout_add(INTERVAL_NS // 1_000_000, self._tick)
        elif not self._watches and self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0

    def _tick(self) -> bool:
        from gi.repository import GLib
        connection = self._connection()
        if not self._watches or connection is None:
            self._tick_id = 0
            return GLib.SOURCE_REMOVE
        for watch in self._watches.values():
            levels = {}
            for target, serials in watch.probes.items():
                levels[target] = max((self._probes[s].level for s in serials if s in self._probes), default=0.0)
            try:
                connection.emit_signal(watch.sender, self._path, self._interface, "Levels",
                                       GLib.Variant("(ua{sd})", (watch.watch_id, levels)))
            except GLib.Error as error:
                log.debug("cannot send levels to %s: %s", watch.sender, error.message)
        return GLib.SOURCE_CONTINUE

    def stop_all(self) -> None:
        for watch_id, watch in list(self._watches.items()):
            self.unwatch(watch.sender, watch_id)
        for probe in self._probes.values():
            self._stop(probe)
        self._probes.clear()
