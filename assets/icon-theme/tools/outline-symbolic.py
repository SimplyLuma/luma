#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Turn a stroked 24px glyph into a fills-only 16px GTK symbolic icon.

    outline-symbolic.py SOURCE.svg OUTPUT-symbolic.svg [--width W]
    outline-symbolic.py --check SOURCE.svg OUTPUT-symbolic.svg [--width W]

Every stroke is outlined at its own width (or --width, for sources authored
without one), round caps and joins kept, and all shapes are merged into one
nonzero path, then scaled from the 24 unit Lucide grid to 16 units. The
result has no stroke, no class and no currentColor: one path filled with
GNOME's symbolic ink #2e3436, which GTK, St and GTK 3 all recolor, so the
glyph follows the text color in light, dark and high-contrast themes.

--check rebuilds the output from the source and fails when the committed
file differs, so an edited glyph cannot drift from its recorded geometry.

Needs picosvg and skia-pathops (development only; nothing here ships).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import pathops
    from picosvg.svg import SVG
    from picosvg.svg_pathops import skia_path, svg_commands
    from picosvg.svg_types import SVGPath
except ImportError:  # pragma: no cover - development tool
    sys.exit("outline-symbolic.py needs: pip install picosvg (brings skia-pathops)")

SCALE = 16 / 24
INK = "#2e3436"


def outline(source: str, width: float | None = None) -> str:
    if width is not None:
        source = re.sub(r'stroke-width="[^"]*"', f'stroke-width="{width:g}"', source)
    picture = SVG.fromstring(source).topicosvg()
    merged = pathops.Path()
    for shape in picture.shapes():
        path = skia_path(shape.as_cmd_seq(), shape.fill_rule)
        merged = pathops.op(merged, path, pathops.PathOp.UNION)
    merged = merged.transform(SCALE, 0, 0, SCALE, 0, 0)
    merged.simplify()
    data = SVGPath.from_commands(svg_commands(merged)).round_floats(3).d
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="16px" height="16px" viewBox="0 0 16 16">\n'
            f'  <path fill="{INK}" d="{data}"/>\n'
            '</svg>\n')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--width", type=float)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = outline(args.source.read_text(), args.width)
    if args.check:
        if args.output.read_text() != result:
            print(f"{args.output}: differs from the outline of {args.source}", file=sys.stderr)
            return 1
        return 0
    args.output.write_text(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
