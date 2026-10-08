# SPDX-License-Identifier: GPL-2.0-or-later
# Pixel measurements of each badge in the device-resolution dock screenshots:
# the pill's height (rows nearer Ember than the ring in the pill's centre
# column) and the ring's thickness above it. Usage: measure.py OUTPUT_ROOT
import json
import sys

from PIL import Image


root = sys.argv[1]
report = {}
for run in ("matrix-100", "matrix-200"):
    data = json.load(open(f"{root}/{run}/matrix.json"))
    for treatment in ("light", "dark", "frost", "glass"):
        shot = data[f"{treatment}-screenshot"]
        s = shot["scale"]
        bx, by = shot["box"][0], shot["box"][1]
        im = Image.open(f"{root}/{run}/matrix-{treatment}.png").convert("RGB")
        px = im.load()
        rows = []
        for g in data[treatment]:
            b = g["badge"]
            if not b:
                continue
            pill = b["pill"]
            top = round((pill["y"] - by) * s)
            bottom = top + round(pill["height"] * s)
            x0 = round((pill["x"] - bx) * s)
            x1 = x0 + round(pill["width"] * s)
            # The tallest run of Ember in any column of the pill; columns
            # through the count's white glyphs are shorter and lose.
            height = 0
            for x in range(max(0, x0), min(im.size[0], x1)):
                streak = best = 0
                for y in range(max(0, top - 3 * s), min(im.size[1], bottom + 3 * s)):
                    c = px[x, y]
                    if c[0] - c[1] > 68 and c[0] - c[2] > 100:
                        streak += 1
                        best = max(best, streak)
                    else:
                        streak = 0
                height = max(height, best)
            rows.append({"app": g["desktopId"].split(".")[2], "text": b["text"] or "dot",
                         "pill height px": height, "logical": height / s})
        report[f"{run}-{treatment}"] = rows
for key, rows in report.items():
    print(key, " ".join(f"{r['text']}={r['logical']:g}" for r in rows))
