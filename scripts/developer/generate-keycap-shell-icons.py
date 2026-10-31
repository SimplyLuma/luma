#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Derive the Shell's Keycap icons from Stevia's Keycap series.

Stevia (patches/stevia/0004) carries the Lucide outlines the Keycap design
uses, drawn for GTK3. GNOME Shell recolours symbolic icons through St's
`.foreground-stroke` / `.transparent-fill` classes, so each element gets those
classes and keeps its geometry and stroke width. The Shell icons are written to
src/luma-shell-presentation/keycap-icons/ and must stay derived: --check fails
when they differ from what Stevia's outlines produce.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STEVIA = ROOT / "patches/stevia/0004-luma-keycap-input-tools.patch"
OUTPUT = ROOT / "src/luma-shell-presentation/keycap-icons"
ICONS = ("arrow-big-up", "arrow-big-up-filled", "arrow-right", "chevron-down", "chevron-left",
         "chevron-right", "chevron-up", "clipboard", "corner-down-left", "delete", "move",
         "settings-2", "smile")
SHAPES = ("path", "rect", "circle", "line", "polyline", "polygon", "ellipse")


def stevia_files() -> dict[str, str]:
    text = STEVIA.read_text(encoding="utf-8")
    files = {}
    for match in re.finditer(r"^\+\+\+ b/src/icons/([^\n]+)\n@@[^\n]*\n((?:\+[^\n]*\n?)*)", text, re.M):
        files[match.group(1)] = "".join(line[1:] + "\n" for line in match.group(2).splitlines())
    return files


def convert(svg: str, filled: bool) -> str:
    classes = "foreground-fill foreground-stroke" if filled else "foreground-stroke transparent-fill"
    shapes = []
    for match in re.finditer(r"<(%s)\b([^>]*?)/?>" % "|".join(SHAPES), svg):
        tag, attributes = match.groups()
        attributes = re.sub(r'\s(?:style|class|fill|stroke)="[^"]*"', "", attributes).strip()
        shapes.append(f'  <{tag} class="{classes}" {attributes}/>')
    if not shapes:
        raise SystemExit("no shapes in a Stevia Keycap icon")
    width = re.search(r'stroke-width="([\d.]+)"', svg)
    stroke = width.group(1) if width else "1.8"
    return ("<!-- Lucide geometry, ISC (keycap-icons/LICENSE); derived from Stevia's Keycap icons\n"
            "     by scripts/developer/generate-keycap-shell-icons.py for St symbolic recolouring. -->\n"
            '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
            f'fill="none" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">\n'
            + "\n".join(shapes) + "\n</svg>\n")


def expected() -> dict[str, str]:
    files = stevia_files()
    result = {"LICENSE": files["LICENSE"]}
    for icon in ICONS:
        name = f"luma-keycap-{icon}-symbolic.svg"
        result[name] = convert(files[name], icon.endswith("-filled"))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    wanted = expected()
    if args.check:
        present = {path.name for path in OUTPUT.glob("*")} if OUTPUT.is_dir() else set()
        stale = sorted(name for name, value in wanted.items()
                       if not (OUTPUT / name).is_file() or (OUTPUT / name).read_text(encoding="utf-8") != value)
        stale += sorted(present - set(wanted))
        if stale:
            print("out of date: " + ", ".join(stale))
            return 1
        print(f"current: {OUTPUT} ({len(wanted)} files)")
        return 0
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, value in wanted.items():
        (OUTPUT / name).write_text(value, encoding="utf-8")
    print(f"wrote: {OUTPUT} ({len(wanted)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
