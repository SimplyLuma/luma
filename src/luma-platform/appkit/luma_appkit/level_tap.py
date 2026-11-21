# SPDX-License-Identifier: Apache-2.0
"""Feed a level meter from GStreamer's ``level`` element while it is on screen.

    from luma_appkit import LevelMeter, LevelTap

    meter = LevelMeter(2, horizontal=True, segments=28)
    tap = LevelTap(meter, node_serial)       # a PipeWire node: serial or name
    tap = LevelTap(meter, capture)           # an AudioCapture (audio_capture.py)
    tap = LevelTap(meter, lambda: Gst.ElementFactory.make("audiotestsrc"))

The meter is anything with ``set_levels_db(left, right)``: an ``AudioMeter``
(``LevelMeter``), a ``MixerStrip``, a ``VolumeSlider``'s ``get_meter()``. A
meter that is not itself a widget names the widget whose mapping it follows
with ``widget=``.

The tap runs only while that widget is mapped. Mapping builds and plays the
pipeline; unmapping or destroying the widget sets it to NULL and drops it, so
a hidden meter costs no CPU, and the next map builds it again. ``stop()``
stops it now (the next map starts it again); ``close()`` stops it for good
and lets go of the meter.

A PipeWire target plays
``pipewiresrc target-object=T ! audioconvert ! level ! fakesink sync=false``;
an AudioCapture's ``make_source()`` and a callable's element take
pipewiresrc's place, and either is asked for a new source on every start.
Levels arrive as the bus's ``level`` element messages on the GLib main
context (no threads), ``peak`` by default or ``rms`` with ``measure="rms"``,
in dB per channel, every ``interval_ms``. One channel feeds both bars;
silence (``-inf``) and anything below the meter's floor reads as the floor.
``on_error(text)`` hears a source that would not start or failed while
playing; the tap stops, and starts again on the next map.
"""
from __future__ import annotations

import math
import re
from typing import Callable, Optional

import gi

__all__ = ["FLOOR_DB", "LevelTap", "is_available"]

#: The meter's floor: silence and anything quieter read as this.
FLOOR_DB = -60.0


def _gst():
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    if not Gst.is_initialized():
        Gst.init(None)
    return Gst


def is_available(pipewire: bool = False) -> bool:
    """True when GStreamer and its ``level`` element are installed (and pipewiresrc, with ``pipewire``)."""
    try:
        Gst = _gst()
    except (ImportError, ValueError):
        return False
    names = ["audioconvert", "level", "fakesink"] + (["pipewiresrc"] if pipewire else [])
    return all(Gst.ElementFactory.find(name) is not None for name in names)


def _floor(value: float) -> float:
    return value if math.isfinite(value) and value > FLOOR_DB else FLOOR_DB


def _channel_values(structure, field: str) -> list[float]:
    """The per-channel doubles of a level message's ``rms`` or ``peak``."""
    try:
        value = structure.get_value(field)
        if isinstance(value, (list, tuple)):
            return [float(v) for v in value]
    except (TypeError, ValueError):
        pass
    # A GValueArray PyGObject cannot unpack: read the serialised structure.
    match = re.search(rf"\b{field}=\([^)]*\)\s*[<{{]([^>}}]*)[>}}]", structure.to_string())
    if match is None:
        return []
    values = []
    for text in match.group(1).split(","):
        try:
            values.append(float(text.strip()))
        except ValueError:
            values.append(FLOOR_DB)
    return values


class LevelTap:
    """Levels from a source to a meter, only while the meter is on screen."""

    def __init__(self, meter, source, *, widget=None, measure: str = "peak", interval_ms: int = 50,
                 on_error: Optional[Callable[[str], None]] = None):
        if not callable(getattr(meter, "set_levels_db", None)):
            raise TypeError("a level tap's meter needs set_levels_db(left, right)")
        if measure not in ("peak", "rms"):
            raise ValueError(f"a level tap measures 'peak' or 'rms', not {measure!r}")
        if interval_ms <= 0:
            raise ValueError("a level tap's interval is above 0 ms")
        if isinstance(source, bool) or not (
                isinstance(source, (int, str)) or callable(getattr(source, "make_source", None)) or callable(source)):
            raise TypeError("a level tap's source is a PipeWire node (serial or name), an AudioCapture "
                            "or a callable returning a GStreamer element")
        if widget is None:
            gi.require_version("Gtk", "4.0")
            from gi.repository import Gtk

            if not isinstance(meter, Gtk.Widget):
                raise TypeError("a meter that is not a widget needs widget=")
            widget = meter
        self._meter = meter
        self._widget = widget
        self._source = source
        self._measure = measure
        self._interval = int(interval_ms) * 1_000_000
        self._on_error = on_error
        self._pipeline = None
        self._closed = False
        self._handlers = [widget.connect("map", lambda _w: self._start()),
                          widget.connect("unmap", lambda _w: self.stop()),
                          widget.connect("destroy", lambda _w: self._on_destroy())]
        if widget.get_mapped():
            self._start()

    @property
    def running(self) -> bool:
        """True while a pipeline is built and playing."""
        return self._pipeline is not None

    def stop(self) -> None:
        """Set the pipeline to NULL and drop it; the next map starts it again."""
        self._teardown(reset=True)

    def close(self) -> None:
        """Stop for good: no restart on map, and the tap lets go of the meter and widget."""
        if self._closed:
            return
        self._closed = True
        self._teardown(reset=False)
        for handler in self._handlers:
            self._widget.disconnect(handler)
        self._handlers = []
        self._meter = self._widget = self._source = None

    def _on_destroy(self) -> None:
        # The widget is being disposed (perhaps at interpreter exit): stop, and
        # leave its handlers to go with it.
        if getattr(self, "_closed", True):
            return
        self._closed = True
        self._teardown(reset=False)
        self._handlers = []
        self._meter = self._widget = self._source = None

    # ── pipeline ──
    def _make_source(self, Gst):
        source = self._source
        if isinstance(source, (int, str)):
            element = Gst.ElementFactory.make("pipewiresrc", None)
            if element is None:
                raise RuntimeError("pipewiresrc is not available")
            element.set_property("target-object", str(source))
            return element
        if callable(getattr(source, "make_source", None)):
            return source.make_source()
        element = source()
        if element is None:
            raise RuntimeError("the level tap's source made no element")
        return element

    def _start(self) -> None:
        if self._pipeline is not None or self._closed:
            return
        Gst = _gst()
        try:
            source = self._make_source(Gst)
        except Exception as error:  # noqa: BLE001 - a source that cannot start is reported, not raised from map
            self._fail(str(error))
            return
        pipeline = Gst.Pipeline.new("luma-level-tap")
        convert = Gst.ElementFactory.make("audioconvert", None)
        level = Gst.ElementFactory.make("level", None)
        sink = Gst.ElementFactory.make("fakesink", None)
        if convert is None or level is None or sink is None:
            self._fail("GStreamer's audioconvert, level or fakesink is not available")
            return
        level.set_property("interval", self._interval)
        level.set_property("post-messages", True)
        sink.set_property("sync", False)
        for element in (source, convert, level, sink):
            pipeline.add(element)
        if not (source.link(convert) and convert.link(level) and level.link(sink)):
            pipeline.set_state(Gst.State.NULL)
            self._fail("the level tap's source would not link")
            return
        from gi.repository import GLib

        self._pipeline = pipeline
        pipeline.get_bus().add_watch(GLib.PRIORITY_DEFAULT, self._on_message)
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self._teardown(reset=True)
            self._fail("the level tap's source would not play")

    def _teardown(self, *, reset: bool) -> None:
        pipeline, self._pipeline = self._pipeline, None
        if pipeline is None:
            return
        Gst = _gst()
        pipeline.get_bus().remove_watch()
        pipeline.set_state(Gst.State.NULL)
        if reset and self._meter is not None:
            self._meter.set_levels_db(FLOOR_DB, FLOOR_DB)

    def _fail(self, text: str) -> None:
        if self._on_error is not None:
            self._on_error(text)

    def _on_message(self, bus, message) -> bool:
        Gst = _gst()
        if self._pipeline is None:
            return False
        kind = message.type
        if kind == Gst.MessageType.ELEMENT:
            structure = message.get_structure()
            if structure is not None and structure.get_name() == "level":
                values = _channel_values(structure, self._measure)
                if values:
                    left = _floor(values[0])
                    right = _floor(values[1]) if len(values) > 1 else left
                    self._meter.set_levels_db(left, right)
        elif kind in (Gst.MessageType.ERROR, Gst.MessageType.EOS):
            text = "the level tap's source ended"
            if kind == Gst.MessageType.ERROR:
                error, _debug = message.parse_error()
                text = error.message
            self._teardown(reset=True)
            self._fail(text)
            return False
        return True
