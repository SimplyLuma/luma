#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Regenerate Tide's committed MP3 gapless fixtures. See fixtures/README.md."""
import json
import pathlib
import subprocess
import sys

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst
Gst.init(None)

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
RATE = 44100
SAMPLES_PER_BUFFER = 1024
BUFFERS = 43
SOURCE_SAMPLES = SAMPLES_PER_BUFFER * BUFFERS


def run(args):
    subprocess.run(args, check=True, capture_output=True)


def decoded_samples(path):
    uri = pathlib.Path(path).as_uri()
    pl = Gst.parse_launch(
        f"uridecodebin uri={uri} ! audioconvert ! audioresample "
        f"! audio/x-raw,format=S16LE,rate={RATE},channels=1 ! appsink name=s sync=false")
    s = pl.get_by_name("s"); s.set_property("emit-signals", True)
    total = [0]
    def cb(a):
        total[0] += a.emit("pull-sample").get_buffer().get_size(); return Gst.FlowReturn.OK
    s.connect("new-sample", cb)
    bus = pl.get_bus(); pl.set_state(Gst.State.PLAYING)
    m = bus.timed_pop_filtered(30 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
    pl.set_state(Gst.State.NULL)
    if m is None or m.type == Gst.MessageType.ERROR:
        raise SystemExit(f"decode of {path} failed")
    return total[0] // 2


manifest = {"source_samples": SOURCE_SAMPLES, "rate": RATE}
for name, freq in (("a", 440), ("b", 880)):
    wav = OUT / f"_{name}.wav"
    run(["gst-launch-1.0", "-q", "audiotestsrc", f"num-buffers={BUFFERS}",
         f"samplesperbuffer={SAMPLES_PER_BUFFER}", "wave=sine", f"freq={freq}", "!",
         f"audio/x-raw,rate={RATE},channels=1,format=S16LE", "!", "wavenc", "!",
         "filesink", f"location={wav}"])
    lame_path = OUT / f"gapless_lame_{name}.mp3"
    run(["lame", "--quiet", "-b", "64", "-m", "m", str(wav), str(lame_path)])
    # Same audio with no Xing/Info tag at all, plus an iTunes-style iTunSMPB
    # frame -- the shape of gapless metadata GStreamer does not read.
    itun_path = OUT / f"gapless_itunsmpb_{name}.mp3"
    run(["lame", "--quiet", "-b", "64", "-m", "m", "-t", str(wav), str(itun_path)])
    untrimmed = decoded_samples(itun_path)
    delay = 576
    padding = untrimmed - delay - SOURCE_SAMPLES
    from mutagen.id3 import ID3, TXXX
    value = (" 00000000 %08X %08X %016X 00000000 00000000 00000000 00000000"
             % (delay, padding, SOURCE_SAMPLES))
    tag = ID3()
    tag.add(TXXX(encoding=0, desc="iTunSMPB", text=[value]))
    tag.save(str(itun_path))
    manifest.setdefault("itunsmpb", {})[name] = {
        "delay": delay, "padding": padding,
        "decoded_samples": decoded_samples(itun_path), "tag": value.strip(),
    }
    manifest.setdefault("lame", {})[name] = {"decoded_samples": decoded_samples(lame_path)}
    wav.unlink()

(OUT / "gapless_mp3_fixtures.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
print(json.dumps(manifest, indent=2, sort_keys=True))
