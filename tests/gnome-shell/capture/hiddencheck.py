#!/usr/bin/env python3
# hiddencheck.py DIR: decode the "hidden" case recording at 15 fps and report, per
# frame, dark disc pixels where the countdown was and near-white pixels over a
# grid of the screen (the flash). Run inside a container with gst and gi.
import gi, glob, json, os, subprocess, sys
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf
d = sys.argv[1]
h = json.load(open(f"{d}/results.json"))["cases"]["hidden"]
rec = h["recording"]
for f in glob.glob(f"{d}/hidden-frame*.png"):
    os.unlink(f)
subprocess.run(["gst-launch-1.0", "-q", "filesrc", f"location={rec}", "!", "decodebin", "!", "videoconvert", "!",
    "videorate", "!", "video/x-raw,framerate=15/1", "!", "pngenc", "snapshot=false", "!", "multifilesink",
    f"location={d}/hidden-frame%03d.png"], check=True)
x, y, w, hgt = h["countdown"]["rect"]
cx, cy = x + w // 2, y + hgt // 2
def px(pb, xx, yy):
    p = pb.get_pixels(); o = yy * pb.get_rowstride() + xx * pb.get_n_channels()
    return p[o], p[o + 1], p[o + 2]
frames = sorted(glob.glob(f"{d}/hidden-frame*.png"))
base = GdkPixbuf.Pixbuf.new_from_file(frames[0])
ring = ((-40, 0), (40, 0), (0, -40), (0, 40), (-30, -30), (30, 30), (-30, 30), (30, -30))
grid = [(gx, gy) for gx in range(1, 10) for gy in range(1, 10)]
lum = lambda c: sum(c) / 3
disc = flash = 0
for f in frames[1:]:
    pb = GdkPixbuf.Pixbuf.new_from_file(f)
    # The disc darkens what is under it by about half; the Capture screen dim
    # (shown while the bar is open) only by a fifth.
    darker = sum(1 for dx, dy in ring
                 if lum(px(pb, cx + dx, cy + dy)) < 0.6 * lum(px(base, cx + dx, cy + dy)) - 8)
    # The flash whitens the whole screen.
    W, H = pb.get_width(), pb.get_height()
    whiter = sum(1 for gx, gy in grid
                 if lum(px(pb, W * gx // 10, H * gy // 10)) > lum(px(base, W * gx // 10, H * gy // 10)) + 60)
    disc += darker >= 6
    flash += whiter >= 40
print(json.dumps({"frames": len(frames), "framesWithCountdownDisc": disc, "framesWithFlash": flash}))
