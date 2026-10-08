#!/usr/bin/env python3
"""Report the vertical metrics and representative glyph bounds of UI fonts."""

from __future__ import annotations

import argparse
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont


GLYPHS = "CDFHMWacegilnorst0123456789"


def value(table: object, field: str) -> object:
    return getattr(table, field, "n/a")


def audit(path: Path, weights: list[int]) -> None:
    source = TTFont(path, recalcBBoxes=False, recalcTimestamp=False)
    print(f"font: {path}")
    print(f"tables: {' '.join(source.keys())}")
    head = source["head"]
    hhea = source["hhea"]
    os2 = source["OS/2"]
    print(
        "base: "
        f"upm={head.unitsPerEm} head=({head.yMin},{head.yMax}) "
        f"hhea=({hhea.ascent},{hhea.descent},{hhea.lineGap}) "
        f"typo=({os2.sTypoAscender},{os2.sTypoDescender},{os2.sTypoLineGap}) "
        f"win=({os2.usWinAscent},{os2.usWinDescent}) "
        f"cap={value(os2, 'sCapHeight')} x={value(os2, 'sxHeight')} "
        f"fsSelection=0x{os2.fsSelection:04x}"
    )
    if "MVAR" in source:
        records = source["MVAR"].table.ValueRecord or []
        print("mvar: " + " ".join(record.ValueTag for record in records))

    cmap = source.getBestCmap()
    variable = "fvar" in source
    for weight in weights:
        font = (
            instantiateVariableFont(source, {"wght": weight}, inplace=False)
            if variable
            else TTFont(path, recalcBBoxes=False, recalcTimestamp=False)
        )
        glyph_set = font.getGlyphSet()
        bounds: list[tuple[str, tuple[float, float, float, float]]] = []
        for character in GLYPHS:
            glyph_name = cmap.get(ord(character))
            if glyph_name is None:
                continue
            pen = BoundsPen(glyph_set)
            glyph_set[glyph_name].draw(pen)
            if pen.bounds is not None:
                bounds.append((character, pen.bounds))
        highest = sorted(bounds, key=lambda item: item[1][3], reverse=True)[:8]
        lowest = sorted(bounds, key=lambda item: item[1][1])[:8]
        hhea = font["hhea"]
        os2 = font["OS/2"]
        print(
            f"wght={weight}: hhea=({hhea.ascent},{hhea.descent},{hhea.lineGap}) "
            f"typo=({os2.sTypoAscender},{os2.sTypoDescender},{os2.sTypoLineGap})"
        )
        print(
            "  highest: "
            + " ".join(f"{char}:{bound[3]:.1f}" for char, bound in highest)
        )
        print(
            "  lowest:  "
            + " ".join(f"{char}:{bound[1]:.1f}" for char, bound in lowest)
        )
    print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("fonts", nargs="+", type=Path)
    parser.add_argument("--weights", nargs="+", type=int, default=[400, 500, 600, 700])
    args = parser.parse_args()
    for font in args.fonts:
        audit(font, args.weights)


if __name__ == "__main__":
    main()
