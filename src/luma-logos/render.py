#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Render the Luma logo set from the canonical wordmark (ADR-040).

Every file is derived from website/public/brand/luma-wordmark.svg, whose ink
is currentColor and whose full stop is Luma orange. Nothing here draws a new
mark: the square logo is the wordmark on an Ink tile, the "-text" logos are
the wordmark alone in Ink (light surfaces) or Paper (dark surfaces).

  render.py WORDMARK OUTDIR        write the SVGs
"""

import re
import sys
from pathlib import Path

INK = "#21252b"
PAPER = "#f2f3f4"
ORANGE = "#FE6819"


def wordmark_body(source: str, ink: str) -> tuple[str, float, float]:
    view = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', source)
    group = re.search(r"(<g\b.*</g>)", source, re.S)
    if not view or not group:
        raise SystemExit("the wordmark SVG has no viewBox or group")
    body = group.group(1).replace('fill="currentColor"', f'fill="{ink}"')
    if ORANGE not in body:
        raise SystemExit("the wordmark lost its orange stop")
    return body, float(view.group(1)), float(view.group(2))


def text_logo(source: str, ink: str) -> str:
    body, width, height = wordmark_body(source, ink)
    pad = height * 0.12
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-pad:g} {-pad:g} {width + 2 * pad:g} '
            f'{height + 2 * pad:g}" width="{width + 2 * pad:g}" height="{height + 2 * pad:g}">'
            f'<title>Luma</title>{body}</svg>\n')


def tile_logo(source: str, tile: str, ink: str) -> str:
    body, width, height = wordmark_body(source, ink)
    size, radius, inner = 256, 56, 184
    scale = inner / width
    x = (size - inner) / 2
    y = (size - height * scale) / 2
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}">'
            f'<title>Luma</title><rect width="{size}" height="{size}" rx="{radius}" fill="{tile}"/>'
            f'<g transform="translate({x:g} {y:g}) scale({scale:.6f})">{body}</g></svg>\n')


def main() -> int:
    source = Path(sys.argv[1]).read_text(encoding="utf-8")
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "luma-logo.svg": tile_logo(source, INK, PAPER),
        "luma-logo-dark.svg": tile_logo(source, "#343a42", PAPER),
        "luma-logo-text.svg": text_logo(source, INK),
        "luma-logo-text-dark.svg": text_logo(source, PAPER),
        "luma-logo-white.svg": text_logo(source, "#ffffff"),
    }
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
