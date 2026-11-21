# SPDX-License-Identifier: Apache-2.0
"""Measured review of a panels check run (the JSON tests/panels_shot.py writes beside its picture).

    python3 tests/panels_conform.py oshots/a1.json [more.json ...]

Each rule is one LumaUI rule about the Destinations, Stats and Chat panels, checked in pixels; a rule
that fails prints what it measured, and the exit status is the number of failures.
"""
from __future__ import annotations

import json
import sys

TOL = 1.0


def centre_y(box):
    return box[1] + box[3] / 2


def inside(inner, outer) -> bool:
    return inner[0] >= outer[0] - TOL and inner[0] + inner[2] <= outer[0] + outer[2] + TOL


def check(path: str) -> list[str]:
    m = json.load(open(path))
    fails: list[str] = []

    def rule(ok: bool, words: str) -> None:
        if not ok:
            fails.append(words)

    rule(m["window"][0] == m["requested_width"], f"window is {m['window'][0]} wide, asked for {m['requested_width']}")
    panel = m["panels"].get("destinations")
    rows = m["destination_rows"]
    if panel and rows:
        leads = {r["lead"][0] for r in rows if r["lead"]}
        titles = {r["labels"][0]["box"][0] for r in rows if r["labels"]}
        # One lead column and one title column, whatever the platform (a logo or a well).
        rule(max(leads) - min(leads) <= TOL, f"destination leads start at {sorted(leads)}")
        rule(max(titles) - min(titles) <= TOL, f"destination titles start at {sorted(titles)}")
        switches = {round(r["switch"][0] + r["switch"][2]) for r in rows if r["switch"]}
        rule(max(switches) - min(switches) <= TOL, f"switches end at {sorted(switches)}")
        for r in rows:
            name = r["labels"][0]["text"] if r["labels"] else r["id"]
            rule(inside(r["row"], panel), f"{name}: row {r['row']} leaves the panel {panel}")
            if r["lead"]:
                rule(abs(centre_y(r["lead"]) - centre_y(r["row"])) <= TOL + 1,
                     f"{name}: lead centre {centre_y(r['lead'])} vs row centre {centre_y(r['row'])}")
            for part in ("chip", "switch"):
                if r[part]:
                    rule(abs(centre_y(r[part]) - centre_y(r["row"])) <= TOL + 1,
                         f"{name}: {part} centre {centre_y(r[part])} vs row centre {centre_y(r['row'])}")
            if r["chip"] and r["labels"]:
                text_end = max(lab["box"][0] + lab["box"][2] for lab in r["labels"]
                               if lab["box"][0] < r["chip"][0])
                rule(text_end <= r["chip"][0] + TOL, f"{name}: words run under the canvas chip")
            rule(not (r["labels"] and r["labels"][0]["ellipsized"]), f"{name}: title is cut short")
        heights = {round(r["row"][3]) for r in rows}
        rule(len(heights) == 1, f"destination rows differ in height: {sorted(heights)}")
    stats = m.get("stats")
    if stats and m["panels"].get("stats"):
        box = m["panels"]["stats"]
        for section, items in stats.items():
            for item in items:
                if item["row"]:
                    rule(inside(item["row"], box), f"stats {section}/{item['key']} leaves the panel")
                if item["row"] and item["trail"]:
                    rule(abs(centre_y(item["trail"]) - centre_y(item["row"])) <= TOL + 1,
                         f"stats {section}/{item['key']}: meter off the row's centre line")
                    rule(item["trail"][2] >= 48, f"stats {section}/{item['key']}: meter only {item['trail'][2]} wide")
    chat_m = m.get("chat")
    if chat_m and m["panels"].get("chat"):
        box = m["panels"]["chat"]
        for line in chat_m["lines"]:
            if line:
                rule(inside(line, box), f"chat line {line} leaves the panel {box}")
        if chat_m["composer"] and chat_m["scroll"]:
            rule(chat_m["composer"][1] >= chat_m["scroll"][1] + chat_m["scroll"][3] - TOL,
                 "the composer overlaps the feed")
    return fails


def main(paths: list[str]) -> int:
    failures = 0
    for path in paths:
        fails = check(path)
        failures += len(fails)
        print(f"{path}: {'PASS' if not fails else 'FAIL'}")
        for words in fails:
            print(f"  - {words}")
    return failures


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
