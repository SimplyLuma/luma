#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Turn the screenshots into checks. $1: the output root all.sh wrote.

- live: the playing window's preview differs between two screenshots taken
  700 ms apart, and a still window's preview does not;
- edge on screen: a shared screen's border is violet in the screenshot;
- edge not in the stream: the frames the application received have no violet
  border and no violet pill mark, for the shared screen and the shared window.
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

root = Path(sys.argv[1])
VIOLET = (125, 92, 240)
results = []


def check(name, ok, detail=None):
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'} {name} {json.dumps(detail) if detail else ''}")


def violet_share(image, pixels):
    rgb = image.convert("RGB")
    hits = sum(1 for p in pixels if all(abs(rgb.getpixel(p)[i] - VIOLET[i]) <= 45 for i in range(3)))
    return hits / max(1, len(pixels))


def border(width, height, inset):
    pts = []
    for x in range(0, width, 7):
        pts += [(x, inset), (x, height - 1 - inset)]
    for y in range(0, height, 7):
        pts += [(inset, y), (width - 1 - inset, y)]
    return pts


full = root / "full-light-100"
if full.exists():
    state = json.loads((full / "state-02-picker-windows.json").read_text())
    a = Image.open(full / "02-picker-windows-a.png").convert("RGB")
    b = Image.open(full / "02-picker-windows-b.png").convert("RGB")
    for tile in state["tiles"]:
        f = tile["frame"]
        area = (f["x"], f["y"], f["x"] + f["width"], f["y"] + f["height"])
        diff = ImageStat.Stat(ImageChops.difference(a.crop(area), b.crop(area))).mean
        mean = round(sum(diff) / 3, 2)
        if tile["title"] == "Prairie 0.1, launch film":
            check("live: the playing window's preview moves between two shots", mean > 1.0, {"meanDiff": mean})
        else:
            check(f"live: the still preview '{tile['title']}' does not", mean < 0.5, {"meanDiff": mean})

    shot = Image.open(full / "06-sharing-screen.png")
    w, h = shot.size
    share = violet_share(shot, border(w, h, 1))
    check("edge: the shared screen wears a violet edge on screen", share > 0.8, {"violet": round(share, 3)})

    for tag, what in (("s3", "screen"), ("s1", "window")):
        frame_path = full / f"stream-{tag}-0.png"
        if not frame_path.exists():
            check(f"stream: the application's {what} frame exists", False)
            continue
        frame = Image.open(frame_path)
        fw, fh = frame.size
        share = max(violet_share(frame, border(fw, fh, i)) for i in range(0, 4))
        check(f"stream: no violet edge in the {what} the application received",
              share < 0.05, {"violet": round(share, 3), "size": [fw, fh]})
        if tag == "s3":
            pill = json.loads((full / "state-06-sharing-screen.json").read_text())["pill"]
            if pill:
                sx, sy = fw / w, fh / h
                b = pill["box"]
                pts = [(int((b["x"] + dx) * sx), int((b["y"] + dy) * sy))
                       for dx in range(0, b["width"], 3) for dy in range(0, b["height"], 3)
                       if 0 <= (b["x"] + dx) * sx < fw and 0 <= (b["y"] + dy) * sy < fh]
                share = violet_share(frame, pts)
                on_screen = violet_share(shot, [(b["x"] + dx, b["y"] + dy)
                                                for dx in range(0, b["width"], 3)
                                                for dy in range(0, b["height"], 3)])
                check("stream: the Stop pill is not in the screen the application received",
                      share < 0.3 * max(on_screen, 0.01) or share < 0.005,
                      {"violetInStream": round(share, 4), "violetOnScreen": round(on_screen, 4)})

probe_checks = []
for p in sorted(root.glob("*/checks*.json")):
    for c in json.loads(p.read_text()):
        probe_checks.append({**c, "run": p.parent.name})
all_checks = probe_checks + [{**r, "run": "measure"} for r in results]
(root / "summary.json").write_text(json.dumps(all_checks, indent=1))
failed = [c for c in all_checks if not c["ok"]]
print(f"{len(all_checks) - len(failed)}/{len(all_checks)} checks pass")
sys.exit(1 if failed else 0)
