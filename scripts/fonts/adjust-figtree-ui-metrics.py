#!/usr/bin/env python3
"""Create Prairie's metrics-only Figtree derivative deterministically.

Figtree's 1.20-em line box is retained. Only the ascent/descent split moves so
the baseline agrees with Fedora's established UI faces. Glyph outlines,
spacing, variation data, family names, and axes are left untouched.

Revision 2 maps U+2010 HYPHEN and U+2011 NON-BREAKING HYPHEN to the glyph of
U+002D, so its outline and metrics serve all three (Figtree has neither; a
fallback face drew them and lifted the line, e.g. "Wi‑Fi").
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fontTools.ttLib import TTFont


ASCENT = 980
DESCENT = -220
MODIFICATION = "Prairie OS vertical-metrics revision 1; hyphen-variants revision 2"
HYPHEN_VARIANTS = (0x2010, 0x2011)


def append_name(font: TTFont, name_id: int, suffix: str) -> None:
    name_table = font["name"]
    records = [record for record in name_table.names if record.nameID == name_id]
    for record in records:
        current = record.toUnicode()
        if suffix not in current:
            name_table.setName(
                f"{current}; {suffix}",
                name_id,
                record.platformID,
                record.platEncID,
                record.langID,
            )


def map_hyphen_variants(font: TTFont, source: Path) -> None:
    """U+2010 and U+2011 share U+002D's glyph (its outline, advance and variation deltas) in every
    Unicode cmap subtable that has U+002D."""
    mapped = False
    for table in font["cmap"].tables:
        if table.isUnicode() and 0x2D in table.cmap:
            for codepoint in HYPHEN_VARIANTS:
                table.cmap.setdefault(codepoint, table.cmap[0x2D])
            mapped = True
    if not mapped:
        raise SystemExit(f"no Unicode cmap with U+002D in {source}")


def verify_hyphen_variants(destination: Path) -> None:
    best = TTFont(destination, recalcBBoxes=False, recalcTimestamp=False).getBestCmap()
    if any(best.get(codepoint) != best.get(0x2D) for codepoint in HYPHEN_VARIANTS):
        raise SystemExit(f"failed to map U+2010/U+2011 to U+002D's glyph in {destination}")


def hyphens_only(source: Path, destination: Path) -> None:
    """The package build's step: the pinned faces with only the hyphen variants mapped."""
    font = TTFont(source, recalcBBoxes=False, recalcTimestamp=False)
    map_hyphen_variants(font, source)
    append_name(font, 3, "Luma hyphen variants")
    append_name(font, 5, "Luma hyphen variants")
    destination.parent.mkdir(parents=True, exist_ok=True)
    font.save(destination, reorderTables=False)
    verify_hyphen_variants(destination)


def adjust(source: Path, destination: Path) -> None:
    font = TTFont(source, recalcBBoxes=False, recalcTimestamp=False)
    hhea = font["hhea"]
    os2 = font["OS/2"]

    if (hhea.ascent, hhea.descent, hhea.lineGap) != (950, -250, 0):
        raise SystemExit(f"unexpected hhea metrics in {source}")
    if (os2.sTypoAscender, os2.sTypoDescender, os2.sTypoLineGap) != (
        950,
        -250,
        0,
    ):
        raise SystemExit(f"unexpected typographic metrics in {source}")
    if not os2.fsSelection & (1 << 7):
        raise SystemExit(f"USE_TYPO_METRICS is not enabled in {source}")

    hhea.ascent = ASCENT
    hhea.descent = DESCENT
    os2.sTypoAscender = ASCENT
    os2.sTypoDescender = DESCENT
    os2.usWinAscent = max(os2.usWinAscent, ASCENT)
    os2.usWinDescent = max(os2.usWinDescent, -DESCENT)

    map_hyphen_variants(font, source)

    # No Reserved Font Name is declared in Figtree's OFL. Keep the requested
    # primary family name while making the downstream modification explicit in
    # the version, unique-ID, and description metadata.
    append_name(font, 3, MODIFICATION)
    append_name(font, 5, MODIFICATION)
    append_name(font, 10, MODIFICATION)

    destination.parent.mkdir(parents=True, exist_ok=True)
    font.save(destination, reorderTables=False)

    verified = TTFont(destination, recalcBBoxes=False, recalcTimestamp=False)
    if (verified["hhea"].ascent, verified["hhea"].descent) != (ASCENT, DESCENT):
        raise SystemExit(f"failed to write adjusted hhea metrics to {destination}")
    if (
        verified["OS/2"].sTypoAscender,
        verified["OS/2"].sTypoDescender,
    ) != (ASCENT, DESCENT):
        raise SystemExit(f"failed to write adjusted OS/2 metrics to {destination}")
    verify_hyphen_variants(destination)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--hyphens-only", action="store_true",
                        help="map U+2010/U+2011 to U+002D's glyph and change nothing else (the package build)")
    args = parser.parse_args()
    (hyphens_only if args.hyphens_only else adjust)(args.source, args.destination)


if __name__ == "__main__":
    main()
