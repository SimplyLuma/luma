# SPDX-License-Identifier: Apache-2.0
"""Outputs end to end: one encode teed to three streams (RTMP ×2, SRT), a late server (reconnect),
and two recordings (auto codec .mkv, H.264 .mp4), fed by test pictures and sound.

    python3 tests/outputs_smoke.py OUT_DIR RTMP_BASE SRT_BASE LATE_RTMP_BASE

Writes OUT_DIR/outputs.json (every row's stats and the encodes) and the recordings; the caller
probes what the servers received and the files (outputs-smoke.sh).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from luma_streamy.outputs import Outputs  # noqa: E402
from luma_streamy.output_graph import Preset  # noqa: E402

out, rtmp, srt, late = sys.argv[1:5]
Gst.init(None)
loop = GLib.MainLoop()
outputs = Outputs()
feed = Gst.parse_launch(
    "videotestsrc is-live=true pattern=ball ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 ! "
    "appsink name=v emit-signals=true sync=true max-buffers=4 drop=true "
    "audiotestsrc is-live=true wave=ticks ! audio/x-raw,format=S16LE,layout=interleaved,rate=48000,channels=2 ! "
    "appsink name=a emit-signals=true sync=true max-buffers=16 drop=true")


def pulled(sink, media):
    sample = sink.emit("pull-sample")
    if sample is not None:
        (outputs.push_video("landscape", sample) if media == "v" else outputs.push_sound(sample))
    return Gst.FlowReturn.OK


feed.get_by_name("v").connect("new-sample", pulled, "v")
feed.get_by_name("a").connect("new-sample", pulled, "a")
states: dict[str, list[str]] = {}
outputs.connect("row-state", lambda _o, row, state, words: states.setdefault(row, []).append(state)
                or print(f"{row}: {state} {words}", flush=True))
stream = Preset("landscape", "h264", 4000, 160)
record = Preset("landscape", "", 8000, 192)
report: dict = {}


def start() -> bool:
    outputs.start("a", stream, f"{rtmp}/a")
    outputs.start("b", stream, f"{rtmp}/b")
    outputs.start("c", stream, f"{srt}?streamid=publish:live/c&mode=caller")
    outputs.start("late", stream, f"{late}/late")
    outputs.start("mkv", record, f"{out}/rec.mkv", record=True)
    outputs.start("mp4", Preset("landscape", "h264", 8000, 192), f"{out}/rec.mp4", record=True, container="mp4")
    report["encodes"] = sorted(f"{p.key}:{e.choice.video}+{e.choice.audio}:{len(e.rows)}"
                               for p, e in outputs.encodes.items())
    return False


def snapshot() -> bool:
    report["live"] = {k: {"state": s.state, "kbps": round(s.bitrate_kbps), "frames": s.frames,
                          "dropped_network": s.dropped_network, "dropped_encoder": s.dropped_encoder,
                          "reconnects": s.reconnects, "rtt_ms": s.rtt_ms, "history": len(s.history)}
                      for k, s in outputs.stats_of.items()}
    return False


def stop() -> bool:
    outputs.stop_all()
    GLib.timeout_add(250, wait)
    return False


def wait() -> bool:
    if outputs.rows:
        return True
    report["states"] = states
    feed.set_state(Gst.State.NULL)
    loop.quit()
    return False


feed.set_state(Gst.State.PLAYING)
GLib.timeout_add(500, start)
GLib.timeout_add(14000, snapshot)
GLib.timeout_add(15000, stop)
GLib.timeout_add(60000, lambda: (print("timeout", flush=True), loop.quit()))
loop.run()
json.dump(report, open(f"{out}/outputs.json", "w"), indent=1)
print(json.dumps(report, indent=1))
