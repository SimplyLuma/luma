#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""LevelTap: nothing runs while the meter is unmapped, levels arrive once it
is mapped, the pipeline is NULL after unmap, a restart works, mono feeds both
bars and silence reads as the floor. LevelMeter is AudioMeter, segmented."""
import sys
import time

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

try:
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # noqa: E402
except (ImportError, ValueError):
    print("SKIP: GStreamer's introspection data is not installed")
    sys.exit(77)
Gst.init(None)
for name in ("level", "audiotestsrc", "audioconvert", "fakesink"):
    if Gst.ElementFactory.find(name) is None:
        print(f"SKIP: GStreamer's {name} element is not installed")
        sys.exit(77)

Gtk.init()

import luma_appkit  # noqa: E402
from luma_appkit import AudioMeter, LevelMeter, LevelTap  # noqa: E402
from luma_appkit.level_tap import FLOOR_DB, is_available  # noqa: E402


def settle(seconds=0.3):
    end = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def until(condition, seconds=5.0):
    end = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < end:
        if condition():
            return True
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)
    return condition()


class Probe(Gtk.Box):
    """A meter that records what it is fed."""

    def __init__(self):
        super().__init__()
        self.set_size_request(40, 10)
        self.calls = []

    def set_levels_db(self, left, right):
        self.calls.append((left, right))


class Source:
    """A callable source: a live test tone, mono or stereo; it keeps every element it made.

    A stopped tap drops its pipeline, so a test holds the pipeline it checks."""

    def __init__(self, channels=2, wave="sine"):
        self.description = (f"audiotestsrc is-live=true wave={wave} volume=0.5 ! "
                            f'capsfilter caps="audio/x-raw,channels={channels}"')
        self.made = []

    def __call__(self):
        element = Gst.parse_bin_from_description(self.description, True)
        self.made.append(element)
        return element

    def pipeline(self, index=-1):
        return self.made[index].get_parent()


def state(pipeline):
    return pipeline.get_state(0)[1]


def parts():
    assert LevelMeter is AudioMeter, "LevelMeter is the same part"
    assert "LevelMeter" in luma_appkit.__all__ and "LevelTap" in luma_appkit.__all__
    meter = LevelMeter(2, horizontal=True, segments=28, label="Mic level")
    assert meter.get_segments() == 28 and meter.get_horizontal()
    assert meter.get_property("segments") == 28
    assert AudioMeter(1).get_segments() == 0
    for bad in (-1, 129, 2.5, True):
        try:
            AudioMeter(2, segments=bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"segments={bad!r} is refused")
    assert is_available()
    try:
        LevelTap(object(), 1)
    except TypeError:
        pass
    else:
        raise AssertionError("a meter needs set_levels_db")


def lifecycle():
    window = Gtk.Window()
    probe = Probe()
    probe.set_visible(False)
    window.set_child(probe)
    window.present()
    source = Source()
    tap = LevelTap(probe, source)
    settle(0.4)
    assert not probe.get_mapped()
    assert not tap.running and source.made == [] and probe.calls == [], "nothing runs while unmapped"

    probe.set_visible(True)
    assert until(lambda: len(probe.calls) >= 3), "levels arrive once mapped"
    assert tap.running and len(source.made) == 1
    first = source.pipeline()
    assert state(first) == Gst.State.PLAYING
    left, right = probe.calls[-1]
    # A sine at volume 0.5 peaks near -6 dB in both channels.
    assert -9 < left < -3 and -9 < right < -3, probe.calls[-1]

    probe.set_visible(False)
    assert not tap.running
    assert state(first) == Gst.State.NULL, "unmapped, the pipeline is NULL"
    assert probe.calls[-1] == (FLOOR_DB, FLOOR_DB), "a stopped tap leaves the meter silent"
    count = len(probe.calls)
    settle(0.4)
    assert len(probe.calls) == count, "no levels while unmapped"

    probe.set_visible(True)
    assert until(lambda: len(probe.calls) >= count + 3), "a restart brings levels back"
    second = source.pipeline()
    assert tap.running and len(source.made) == 2 and state(second) == Gst.State.PLAYING

    tap.stop()
    assert not tap.running and state(second) == Gst.State.NULL
    probe.set_visible(False)
    probe.set_visible(True)
    assert until(lambda: tap.running), "stop() lasts until the next map"

    third = source.pipeline()
    tap.close()
    assert not tap.running and state(third) == Gst.State.NULL
    probe.set_visible(False)
    probe.set_visible(True)
    settle(0.2)
    assert not tap.running and len(source.made) == 3, "a closed tap never starts again"

    # Destroying the window unmaps the meter: the tap stops.
    again = LevelTap(probe, source)
    fourth = source.pipeline()
    assert again.running and until(lambda: state(fourth) == Gst.State.PLAYING)
    window.destroy()
    assert not again.running and state(fourth) == Gst.State.NULL


def mono_and_silence():
    window = Gtk.Window()
    probe = Probe()
    window.set_child(probe)
    window.present()
    mono = LevelTap(probe, Source(channels=1), measure="rms")
    assert until(lambda: len(probe.calls) >= 3)
    left, right = probe.calls[-1]
    assert left == right, "one channel feeds both bars"
    # A sine's RMS sits 3 dB under its peak.
    assert -12 < left < -6, left
    mono.close()

    probe.calls.clear()
    quiet = LevelTap(probe, Source(wave="silence"))
    assert until(lambda: len(probe.calls) >= 3)
    assert all(call == (FLOOR_DB, FLOOR_DB) for call in probe.calls), probe.calls[:3]
    quiet.close()
    window.destroy()


def real_meter():
    """An AudioMeter fed by a tap shows the level in segments."""
    window = Gtk.Window()
    meter = LevelMeter(2, segments=24)
    window.set_child(meter)
    window.present()
    tap = LevelTap(meter, Source())
    assert until(lambda: meter.get_level_db(0) > -10)
    # -6 dB lights 18 of 24 segments.
    assert 17 <= meter.get_lit_segments(0) <= 19, meter.get_lit_segments(0)
    window.destroy()
    assert not tap.running


def errors():
    """A source that fails reports once and leaves the tap stopped."""
    window = Gtk.Window()
    probe = Probe()
    window.set_child(probe)
    window.present()
    heard = []

    def broken():
        raise RuntimeError("no such device")

    tap = LevelTap(probe, broken, on_error=heard.append)
    assert not tap.running and heard == ["no such device"]
    tap.close()
    if Gst.ElementFactory.find("pipewiresrc") is not None:
        # With no PipeWire daemon the source fails; with one, an unknown node waits.
        node = LevelTap(probe, "luma-level-tap-test-no-such-node", on_error=heard.append)
        settle(0.5)
        node.stop()
        assert not node.running
        node.close()
    else:
        print("note: pipewiresrc is not installed; the PipeWire target path is not exercised")
    window.destroy()


parts()
lifecycle()
mono_and_silence()
real_meter()
errors()
print("ok")
