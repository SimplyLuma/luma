#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Measure the shared window's ring in MODE=ring screenshots (probe.js).

  measure.py OUTDIR

The ring sits over a flat #808080 backdrop. For every shot:

- halo-edge: at the middle of each side of the ring's actor, the difference
  between the last pixel inside and the pixels just beyond. A halo cut off by
  its bounds leaves a step there; one that fades out leaves nothing.
- corner: walking from the window's corner centre along the diagonal, where
  the violet line begins, against where it begins on a straight edge. A ring
  concentric with the window starts its line at the same distance from the
  window everywhere: radius + 0 on the diagonal, 0 past the frame on the edge.

Writes ring-measure.json, appends its checks to checks.json and makes
ring-sheet.png (the top-left corner of every shot, magnified).
"""

import json
import math
import sys

from PIL import Image, ImageDraw

out = sys.argv[1]
state = json.load(open(f"{out}/state.json"))
BACKDROP = (128, 128, 128)
LINE = (125, 92, 240)


def diff(a, b):
    return max(abs(a[i] - b[i]) for i in range(3))


def violet(p):
    return diff(p, LINE) <= 48


results, checks, crops = [], [], []
for shot in state.get("shots", []):
    image = Image.open(f"{out}/{shot['name']}.png").convert("RGB")
    W, H = image.size
    s = shot["scale"] or 1
    f, a = shot["frame"], shot["actor"]
    px = lambda x, y: image.getpixel((min(W - 1, max(0, int(x * s))), min(H - 1, max(0, int(y * s)))))
    on = lambda x, y: 0 <= x * s < W and 0 <= y * s < H

    # The halo at the actor's bounds, mid-side: the last pixel inside against
    # the first pixels beyond. A halo cut off by its bounds is a step there;
    # one that has faded out matches whatever lies beyond (the backdrop, or
    # the shelf where the ring runs under it).
    edge = []
    inset = 0.5 / s
    for x, y, dx, dy in ((a["x"] + a["width"] / 2, a["y"] + inset, 0, -1),
                         (a["x"] + a["width"] / 2, a["y"] + a["height"] - inset - 1 / s, 0, 1),
                         (a["x"] + inset, a["y"] + a["height"] / 2, -1, 0),
                         (a["x"] + a["width"] - inset - 1 / s, a["y"] + a["height"] / 2, 1, 0)):
        beyond = (x + dx * 2 / s, y + dy * 2 / s)
        if on(x, y) and on(*beyond):
            edge.append(diff(px(x, y), px(*beyond)))

    # Where the line begins, straight edge (top, mid) and corner diagonal.
    radius = float(str(shot.get("radius") or "0:1").split(":")[0]) if shot.get("radius") else None

    def line_start(x0, y0, dx, dy, start, stop):
        steps = int((stop - start) * s * 4)
        for i in range(steps + 1):
            t = start + i / (4 * s)
            if on(x0 + dx * t, y0 + dy * t) and violet(px(x0 + dx * t, y0 + dy * t)):
                return t
        return None

    whole = on(f["x"], f["y"]) and on(f["x"] + f["width"], f["y"] + f["height"])
    top = line_start(f["x"] + f["width"] / 2, f["y"], 0, -1, -2, 8)
    diag = None
    if radius is not None:
        cx, cy = f["x"] + radius, f["y"] + radius
        k = 1 / math.sqrt(2)
        t = line_start(cx, cy, -k, -k, radius - 3, radius + 8)
        diag = None if t is None else t - radius
    results.append({"name": shot["name"], "state": shot.get("state"), "scale": s,
                    "surface": shot.get("surface"), "halo_edge": edge, "line_top": top,
                    "line_corner": diag, "radius": radius})

    if shot.get("state") in ("floating", "moved", "resized", "resizing"):
        checks.append({"name": f"{shot['name']}: the halo fades out inside its actor",
                       "ok": bool(edge) and max(edge) <= 3, "detail": edge})
    if not whole and shot.get("state") not in ("fullscreen", "maximized"):
        pass  # the window reaches past the screen (a small monitor): nothing to measure
    elif shot.get("state") not in ("fullscreen", "maximized") and top is not None and diag is not None:
        checks.append({"name": f"{shot['name']}: the ring is concentric with the window",
                       "ok": abs(diag - top) <= 1.0 + 1 / s, "detail": {"edge": top, "corner": diag}})
    elif shot.get("state") not in ("fullscreen", "maximized"):
        checks.append({"name": f"{shot['name']}: the ring's line was found",
                       "ok": False, "detail": {"edge": top, "corner": diag}})

    # A magnified crop of the top-left corner for the sheet.
    box = [int((f["x"] - 24) * s), int((f["y"] - 24) * s), int((f["x"] + 40) * s), int((f["y"] + 40) * s)]
    if box[0] >= 0 and box[1] >= 0:
        crop = image.crop(box).resize((256, 256), Image.NEAREST)
        crops.append((shot["name"], crop))

json.dump(results, open(f"{out}/ring-measure.json", "w"), indent=1)
try:
    existing = json.load(open(f"{out}/checks.json"))
except FileNotFoundError:
    existing = []
json.dump(existing + checks, open(f"{out}/checks.json", "w"), indent=1)

if crops:
    columns = 6
    rows = math.ceil(len(crops) / columns)
    sheet = Image.new("RGB", (columns * 264, rows * 284), (40, 40, 40))
    draw = ImageDraw.Draw(sheet)
    for i, (name, crop) in enumerate(crops):
        x, y = (i % columns) * 264 + 4, (i // columns) * 284 + 4
        sheet.paste(crop, (x, y + 20))
        draw.text((x, y + 4), name.replace("ring-", ""), fill=(230, 230, 230))
    sheet.save(f"{out}/ring-sheet.png")

for c in checks:
    print(("PASS " if c["ok"] else "FAIL ") + c["name"] + ("" if c["ok"] else f"  -- {c['detail']}"))
print(f"{sum(c['ok'] for c in checks)} passed, {sum(not c['ok'] for c in checks)} failed")
sys.exit(min(sum(not c["ok"] for c in checks), 100))
