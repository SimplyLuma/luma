# SPDX-License-Identifier: Apache-2.0
"""Measured review of a check run's boxes (the JSON STREAMY_SHOT writes beside its picture).

    python3 tests/conform.py shots/x1.json [more.json ...]
    python3 tests/conform.py --same rest.json editing.json   # an open editor moved nothing

Each rule is one LumaUI rule about Streamy's window, checked in pixels, not by eye. A rule
that fails prints what it measured; the exit status is the number of failures.
"""
from __future__ import annotations

import json
import sys

TOL = 1.0  # sub-pixel rounding


def centre_y(box):
    return box[1] + box[3] / 2


def check(path: str) -> list[str]:
    m = json.load(open(path))
    fails: list[str] = []

    def rule(ok: bool, words: str) -> None:
        if not ok:
            fails.append(words)

    phone = m["window"][0] < 560
    # Sidebar: scenes and widgets share one column of leads and one of titles, and every
    # row's glyph sits on its title's centre line.
    rows = [r for r in m["scenes"] + m["widgets"] if r["lead"] and r["label"]]
    if m["sidebar"] and rows:
        scene_rows = [r for r in m["scenes"] if r["lead"]]
        widget_rows = [r for r in m["widgets"] if r["lead"]]
        if scene_rows and widget_rows:
            rule(abs(scene_rows[0]["row"][0] - widget_rows[0]["row"][0]) <= TOL and
                 abs(scene_rows[0]["row"][2] - widget_rows[0]["row"][2]) <= TOL,
                 f"scene and widget rows have different insets: {scene_rows[0]['row']} vs {widget_rows[0]['row']}")
            rule(abs(scene_rows[0]["lead"][0] - widget_rows[0]["lead"][0]) <= TOL + 6,
                 f"lead columns differ: {scene_rows[0]['lead'][0]} vs {widget_rows[0]['lead'][0]}")
        for r in m["widgets"]:  # one-line rows (a scene's two lines centre on its thumbnail)
            if r["lead"] and r["label"]:
                rule(abs(centre_y(r["lead"]) - centre_y(r["label"])) <= TOL,
                     f"{r['title']}: glyph centre {centre_y(r['lead'])} vs label centre {centre_y(r['label'])}")
        side = m["sidebar"]
        for r in rows:
            right_inset = side[0] + side[2] - (r["row"][0] + r["row"][2])
            left_inset = r["row"][0] - side[0]
            rule(abs(left_inset - right_inset) <= TOL + 1,
                 f"{r['title']}: row insets left {left_inset} right {right_inset}")
        if len(m["sections"]) >= 2:
            rule(abs(m["sections"][0][0] - m["sections"][1][0]) <= TOL,
                 f"section titles not aligned: {m['sections']}")
    heights = {round(r["row"][3]) for r in m["widgets"] if r["row"]}
    rule(len(heights) <= 1, f"widget rows differ in height: {sorted(heights)}")
    # Visibility reads correctly: an eye-off is only ever on a hidden widget.
    for r in m["widgets"]:
        if r.get("trail_icon") == "eye-off":
            rule(r["hidden"], f"{r['title']}: eye-off on a shown widget")
        if r.get("trail_icon") == "eye":
            rule(not r["hidden"], f"{r['title']}: an open eye on a hidden widget")
    # Insets: headers symmetric (the title glyph's inset equals the More glyph's), concentric radii
    # are CSS; the island's tiles stop above the bar.
    for key, t in m["tiles"].items():
        if not t["header"] or not t["glyph"] or not t["more_glyph"] or not t["box"]:
            continue
        # Glyphs are drawn at the dock's 16 px icon size, centred in their boxes.
        start = t["glyph"][0] + t["glyph"][2] / 2 - 8 - t["box"][0]
        end = t["box"][0] + t["box"][2] - (t["more_glyph"][0] + t["more_glyph"][2] / 2 + 8)
        rule(abs(start - end) <= TOL, f"{key} header: glyph inset {start} vs More inset {end}")
        rule(abs(centre_y(t["glyph"]) - centre_y(t["more_glyph"])) <= TOL,
             f"{key} header: glyph centre {centre_y(t['glyph'])} vs More centre {centre_y(t['more_glyph'])}")
        rule(t["header"][3] <= 32 + TOL, f"{key} header is {t['header'][3]} tall (32)")
    # Audio: the inset holds its strips, not empty well.
    audio = m["tiles"].get("audio")
    if audio and audio["box"] and m["strips"]:
        bottom = max(s[1] + s[3] for s in m["strips"])
        slack = audio["box"][1] + audio["box"][3] - bottom
        rule(slack <= 24, f"audio inset has {slack:.0f} px of empty well under its strips")
    # Preview: breathing room on every side of the video inside its stage.
    preview = m["tiles"].get("preview")
    if preview and preview["box"] and m["video"]:
        p, v = preview["box"], m["video"]
        gaps = (v[0] - p[0], p[0] + p[2] - (v[0] + v[2]), v[1] - p[1], p[1] + p[3] - (v[1] + v[3]))
        rule(min(gaps) >= 16 - TOL, f"video gaps to its stage {tuple(round(g) for g in gaps)} (16 at least)")
        if not phone:
            rule(min(gaps[2], gaps[3]) <= 16 + TOL or min(gaps[0], gaps[1]) <= 16 + TOL,
                 f"video fits neither way: gaps {tuple(round(g) for g in gaps)}")
    # The bar: an open editor never squeezes the selection's name.
    for chip in m["chip"]:
        rule(not chip["ellipsized"], f"chip '{chip['text']}' is ellipsized (grown: {m['grown']})")
    return fails


def same(rest: str, editing: str) -> list[str]:
    """The window with an editor grown is laid out exactly as it is at rest."""
    a, b = json.load(open(rest)), json.load(open(editing))
    fails = [f"{key} moved: {a[key]} -> {b[key]}" for key in ("island", "sidebar", "video") if a[key] != b[key]]
    fails += [f"tile {key} moved: {a['tiles'][key]['box']} -> {b['tiles'][key]['box']}"
              for key in a["tiles"] if a["tiles"][key]["box"] != b["tiles"].get(key, {}).get("box")]
    if not b["grown"]:
        fails.append("the second run has no editor grown")
    return fails


def main(paths: list[str]) -> int:
    if paths[:1] == ["--same"]:
        fails = same(paths[1], paths[2])
        print(f"{paths[2]} against {paths[1]}: {'PASS' if not fails else f'{len(fails)} FAIL'}")
        for words in fails:
            print(f"  - {words}")
        return len(fails)
    total = 0
    for path in paths:
        fails = check(path)
        total += len(fails)
        print(f"{path}: {'PASS' if not fails else f'{len(fails)} FAIL'}")
        for words in fails:
            print(f"  - {words}")
    return total


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
