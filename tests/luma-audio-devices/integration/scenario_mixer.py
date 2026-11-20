#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
"""Per-app mixer, level meters and capture sessions in a headless session.

Runs against real pipewire, wireplumber (with luma/app-audio.lua), pipewire-
pulse and the installed luma-audio-devices service. Apps are GStreamer test
tones with distinct application names, plus paplay through pipewire-pulse.

Prints one line per check and exits non-zero if any check failed.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

import gi

gi.require_version("Gio", "2.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gio, GLib, Gst  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scenario_outputs import (  # noqa: E402
    DAC, RESULTS, SPEAKER, Service, add, check, dump, remove, run, session_bus, wait_for,
)

BUS_NAME = "org.projectluma.AudioDevices"
PATH = "/org/projectluma/AudioDevices"
IFACE = "org.projectluma.AudioDevices1"
Gst.init(None)
TONE_FILE = "/tmp/luma-mixer-tone.wav"


def call(method: str, args=None, reply: str | None = None, timeout_ms: int = 8000):
    result = session_bus().call_sync(BUS_NAME, PATH, IFACE, method, args,
                                     GLib.VariantType.new(reply) if reply else None,
                                     Gio.DBusCallFlags.NONE, timeout_ms, None)
    return result.unpack() if result is not None else None


def applications() -> dict[str, dict]:
    (rows,) = call("GetApplications", None, "(aa{sv})")
    return {row["id"]: row for row in rows}


def tone(name: str, freq: int) -> subprocess.Popen:
    """A test tone through pipewire-pulse, as most apps play. (GStreamer's
    pipewiresink wedges in format renegotiation when its volume changes, so it
    is not a stand-in for an app here.)"""
    props = f"props,application.name={name},media.role=music"
    return subprocess.Popen(["gst-launch-1.0", "-q", "audiotestsrc", f"freq={freq}", "volume=0.6",
                             "is-live=true", "!", "audio/x-raw,channels=2", "!",
                             "pulsesink", f"stream-properties={props}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def native_tone(name: str) -> subprocess.Popen:
    """A native PipeWire client (pw-cat), for the second client stack."""
    return subprocess.Popen(["pw-play", "-P", f"{{ application.name = {name} }}", TONE_FILE],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop(process: subprocess.Popen) -> None:
    process.terminate()
    process.wait(5)


def streams_of(name: str) -> list[dict]:
    found = []
    for obj in dump():
        props = (obj.get("info") or {}).get("props") or {}
        if props.get("media.class") == "Stream/Output/Audio" and props.get("application.name") == name:
            found.append(obj)
    return found


def stream_volume(name: str) -> tuple[float | None, bool | None]:
    for obj in streams_of(name):
        for param in ((obj.get("info") or {}).get("params") or {}).get("Props") or ():
            if "channelVolumes" in param:
                return max(param["channelVolumes"]), param.get("mute")
    return None, None


def linked_to(stream_name: str, sink_name: str) -> bool:
    graph = dump()
    nodes = {o["id"]: (o.get("info") or {}).get("props") or {} for o in graph
             if o["type"] == "PipeWire:Interface:Node"}
    sink = next((i for i, p in nodes.items() if p.get("node.name") == sink_name), None)
    sources = {i for i, p in nodes.items() if p.get("application.name") == stream_name}
    for obj in graph:
        if obj["type"] != "PipeWire:Interface:Link":
            continue
        info = obj.get("info") or {}
        if info.get("output-node-id") in sources and info.get("input-node-id") == sink:
            return True
    return False


def record_level(target: str, seconds: float = 1.2, client_props: str | None = None) -> float | None:
    """Peak level (dB) recorded from a node, or None if nothing arrived."""
    source = Gst.ElementFactory.make("pipewiresrc")
    source.set_property("target-object", target)
    source.set_property("stream-properties", Gst.Structure.new_from_string(
        "props, node.dont-reconnect=(string)true, application.name=(string)LevelProbe"))
    if client_props:
        source.set_property("client-properties", Gst.Structure.new_from_string(client_props))
    convert = Gst.ElementFactory.make("audioconvert")
    level = Gst.ElementFactory.make("level")
    level.set_property("interval", 100_000_000)
    sink = Gst.ElementFactory.make("fakesink")
    sink.set_property("sync", False)
    pipeline = Gst.Pipeline.new("probe")
    for element in (source, convert, level, sink):
        pipeline.add(element)
    source.link(convert)
    convert.link(level)
    level.link(sink)
    pipeline.set_state(Gst.State.PLAYING)
    bus = pipeline.get_bus()
    peaks: list[float] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        message = bus.timed_pop_filtered(100 * Gst.MSECOND, Gst.MessageType.ELEMENT | Gst.MessageType.ERROR)
        if message is None:
            continue
        if message.type == Gst.MessageType.ERROR:
            break
        structure = message.get_structure()
        if structure and structure.get_name() == "level":
            peaks.append(max(structure.get_value("peak")))
    pipeline.set_state(Gst.State.NULL)
    return max(peaks) if peaks else None


def level_streams() -> int:
    return sum(1 for o in dump() if ((o.get("info") or {}).get("props") or {}).get("luma.audio-levels"))


class LevelWatch:
    def __init__(self, targets: list[str]) -> None:
        self.values: dict[str, float] = {}
        self.signals = 0
        bus = session_bus()
        self._sub = bus.signal_subscribe(BUS_NAME, IFACE, "Levels", PATH, None,
                                         Gio.DBusSignalFlags.NONE, self._on_levels)
        (self.watch_id,) = call("WatchLevels", GLib.Variant("(as)", (targets,)), "(u)")

    def _on_levels(self, _c, _s, _p, _i, _n, parameters) -> None:
        watch_id, levels = parameters.unpack()
        if watch_id == self.watch_id:
            self.signals += 1
            for key, value in levels.items():
                self.values[key] = max(self.values.get(key, 0.0), value)

    def pump(self, seconds: float) -> None:
        context = GLib.MainContext.default()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            context.iteration(False)
            time.sleep(0.01)

    def close(self) -> None:
        call("UnwatchLevels", GLib.Variant("(u)", (self.watch_id,)))
        session_bus().signal_unsubscribe(self._sub)


def main() -> int:
    add(SPEAKER)
    add(DAC)
    run("pw-metadata", "-n", "default", "0", "default.configured.audio.sink",
        json.dumps({"name": SPEAKER[0]}), "Spa:String:JSON")
    if not Path(TONE_FILE).exists():
        run("gst-launch-1.0", "-q", "audiotestsrc", "num-buffers=6000", "freq=880", "!",
            "audio/x-raw,channels=2,rate=48000", "!", "wavenc", "!", "filesink", f"location={TONE_FILE}")
    service = Service()
    service.start()
    a = tone("ToneA", 440)
    b = tone("ToneB", 1320)
    pulse = native_tone("NativePlayer")
    try:
        rows = wait_for(lambda: (lambda r: r if {"ToneA", "ToneB", "NativePlayer"} <= set(r) else None)(applications()), 10)
        check(bool(rows), f"the mixer lists every app playing ({sorted(applications())})")
        rows = rows or applications()
        check(rows.get("ToneA", {}).get("name") == "ToneA" and rows.get("ToneA", {}).get("kind") == "app",
              "an app without a desktop entry keeps its own name")
        check(rows.get("@system", {}).get("kind") == "system",
              "System sounds is always a row, even with no sound playing")

        # Volume and mute, per app.
        call("SetApplicationVolume", GLib.Variant("(sd)", ("ToneA", 0.5)))
        check(wait_for(lambda: (stream_volume("ToneA")[0] or 0) and abs(stream_volume("ToneA")[0] - 0.125) < 0.01, 3),
              "setting ToneA to 50% sets its stream (cubic 0.5 = linear 0.125)")
        check(abs((stream_volume("ToneB")[0] or 0) - 1.0) < 0.01, "ToneB keeps its own volume")
        check(wait_for(lambda: abs(applications()["ToneA"]["volume"] - 0.5) < 0.01, 3),
              "GetApplications reports ToneA at 50%")
        call("SetApplicationMute", GLib.Variant("(sb)", ("ToneB", True)))
        check(wait_for(lambda: stream_volume("ToneB")[1] is True, 3), "muting ToneB mutes its stream")

        # Output per app.
        call("SetApplicationOutput", GLib.Variant("(ss)", ("ToneA", DAC[0])))
        check(wait_for(lambda: linked_to("ToneA", DAC[0]), 3), "ToneA plays on the USB DAC")
        check(linked_to("ToneB", SPEAKER[0]) and not linked_to("ToneB", DAC[0]), "ToneB stays on the speaker")
        check(wait_for(lambda: applications()["ToneA"]["output"] == DAC[0], 3), "GetApplications reports ToneA's output")
        check(applications()["ToneB"]["output"] == "" and applications()["ToneB"]["playing_on"] == SPEAKER[0],
              "ToneB follows the default and reports where it plays")

        # Remembered across a new stream.
        stop(a)
        time.sleep(0.5)
        a = tone("ToneA", 440)
        check(wait_for(lambda: (stream_volume("ToneA")[0] or 0) and abs(stream_volume("ToneA")[0] - 0.125) < 0.01, 5),
              "a new ToneA stream comes back at 50%")
        check(wait_for(lambda: linked_to("ToneA", DAC[0]), 5), "a new ToneA stream comes back on the USB DAC")
        stop(b)
        time.sleep(0.5)
        b = tone("ToneB", 1320)
        check(wait_for(lambda: stream_volume("ToneB")[1] is True, 5), "a new ToneB stream comes back muted")
        check(wait_for(lambda: linked_to("ToneB", SPEAKER[0]), 5) and not linked_to("ToneB", DAC[0]),
              "ToneB shares ToneA's media role but not its output")
        call("SetApplicationMute", GLib.Variant("(sb)", ("ToneB", False)))
        wait_for(lambda: stream_volume("ToneB")[1] is False, 3)

        # Level meters: only while watched.
        check(level_streams() == 0, "no level meter runs while nobody watches")
        watch = LevelWatch(["ToneA", "ToneB", "output:", "@system", "nobody"])
        watch.pump(1.5)
        check(watch.signals >= 10, f"levels arrive about 20 times a second ({watch.signals} in 1.5 s)")
        check(watch.values.get("ToneA", 0) > 0.3 and watch.values.get("ToneB", 0) > 0.3,
              f"playing apps show a level ({watch.values})")
        check(watch.values.get("output:", 0) > 0.3, "the default output shows a level")
        check(watch.values.get("nobody", 1) == 0, "an unknown target reads silent")
        check(level_streams() >= 3, "one passive meter stream per watched stream")
        watch.close()
        check(wait_for(lambda: level_streams() == 0, 3), "unwatching stops every meter stream")

        # Capture: only the chosen app, across its restarts.
        capture_id, node_id, serial = call(
            "CreateCapture", GLib.Variant("(ssasbs)", ("org.example.Recorder", "apps", ["ToneB"], False, "Test")),
            "(sut)")
        check(node_id > 0 and serial > 0, f"a capture node is created ({node_id}, serial {serial})")
        sources = run("pactl", "list", "short", "sources", check_rc=False)
        check("luma-audio-capture" not in sources, "the capture node is never offered as a microphone")
        check(wait_for(lambda: linked_to("ToneB", f"luma-audio-capture.{capture_id}"), 3), "ToneB feeds the capture")
        check(not linked_to("ToneA", f"luma-audio-capture.{capture_id}"), "ToneA does not feed the capture")
        peak = record_level(str(serial))
        check(peak is not None and peak > -20, f"the capture carries ToneB ({peak} dB)")
        stop(b)
        time.sleep(0.8)
        peak = record_level(str(serial))
        check(peak is None or peak < -60, f"with ToneB stopped the capture is silent although ToneA plays ({peak} dB)")
        b = tone("ToneB", 1320)
        check(wait_for(lambda: linked_to("ToneB", f"luma-audio-capture.{capture_id}"), 5),
              "a new ToneB stream joins the capture")
        peak = record_level(str(serial))
        check(peak is not None and peak > -20, f"the capture carries the new ToneB stream ({peak} dB)")

        # Portal-restricted remotes only reach their own capture.
        portal = "props, pipewire.access.portal.app_id=(string){}, pipewire.access.portal.media_roles=(string)\"\""
        peak = record_level(SPEAKER[0], client_props=portal.format("org.example.Recorder"))
        check(peak is None, f"a portal remote is not linked to the default microphone or a monitor ({peak})")
        peak = record_level(str(serial), client_props=portal.format("org.example.Other"))
        check(peak is None, f"another app's portal remote cannot record the capture ({peak})")
        peak = record_level(str(serial), client_props=portal.format("org.example.Recorder"))
        check(peak is not None and peak > -20, f"the requester's portal remote records its capture ({peak} dB)")

        # System mode: everything except the requester.
        sys_id, _sys_node, sys_serial = call(
            "CreateCapture", GLib.Variant("(ssasbs)", ("ToneA", "system", [], False, "")), "(sut)")
        check(wait_for(lambda: linked_to("ToneB", f"luma-audio-capture.{sys_id}"), 3) and
              not linked_to("ToneA", f"luma-audio-capture.{sys_id}"),
              "a system capture takes every app but the requester")
        call("CloseCapture", GLib.Variant("(s)", (sys_id,)))
        call("CloseCapture", GLib.Variant("(s)", (capture_id,)))
        check(wait_for(lambda: not any(((o.get("info") or {}).get("props") or {}).get("luma.audio-capture.id")
                                       for o in dump()), 3), "closing removes the capture nodes")

        # A capture dies with its owner.
        owner = subprocess.Popen([sys.executable, "-c", f"""
import gi; gi.require_version('Gio','2.0'); from gi.repository import Gio, GLib
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
bus.call_sync('{BUS_NAME}', '{PATH}', '{IFACE}', 'CreateCapture',
              GLib.Variant('(ssasbs)', ('x', 'apps', ['ToneA'], False, '')), None, 0, 5000, None)
print('made', flush=True)
import time; time.sleep(30)
"""], stdout=subprocess.PIPE, text=True)
        owner.stdout.readline()
        check(any(((o.get("info") or {}).get("props") or {}).get("luma.audio-capture.id") for o in dump()),
              "a second client's capture exists")
        owner.kill()
        owner.wait()
        check(wait_for(lambda: not any(((o.get("info") or {}).get("props") or {}).get("luma.audio-capture.id")
                                       for o in dump()), 5), "a capture ends when its owner leaves the bus")

        # Volumes survive a service and WirePlumber restart.
        service.stop()
        service.start()
        check(wait_for(lambda: abs(applications().get("ToneA", {}).get("volume", 0) - 0.5) < 0.01, 5),
              "after a service restart ToneA still reads 50%")
    finally:
        for process in (a, b, pulse):
            if process.poll() is None:
                process.kill()
        service.stop()
        remove(DAC)

    state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "wireplumber" / "luma-app-audio"
    check(state.exists() and "ToneA" in state.read_text(), "WirePlumber keeps per-app state on disk")
    failed = [text for ok, text in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
