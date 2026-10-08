#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Adapt a vendored Lucide SVG to a Prairie GTK symbolic icon, geometry unchanged.

    adapt-lucide-symbolic.py SOURCE.svg OUTPUT-symbolic.svg [LUCIDE-NAME]

The adaptation recorded in upstream/lucide/navigation-manifest.json: GTK 4.20+
symbolic classes on every primitive so open strokes stay strokes, line,
polyline and polygon rewritten losslessly as path commands, and the stroke
attributes copied onto each primitive. No coordinate is changed.
"""
import re
import sys
from pathlib import Path

STROKE = 'class="transparent-fill foreground-stroke" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"'
PRIMITIVE = re.compile(r"<(path|circle|rect|ellipse|line|polyline|polygon)\b([^>]*?)\s*/>")
ATTRIBUTE = re.compile(r'([a-zA-Z][a-zA-Z0-9-]*)="([^"]*)"')


def as_path(kind: str, attributes: list[tuple[str, str]]) -> tuple[str, list[tuple[str, str]]]:
    values = dict(attributes)
    if kind == "line":
        return "path", [("d", f"M{values['x1']} {values['y1']}L{values['x2']} {values['y2']}")]
    if kind in ("polyline", "polygon"):
        numbers = values["points"].replace(",", " ").split()
        pairs = [f"{numbers[i]} {numbers[i + 1]}" for i in range(0, len(numbers), 2)]
        return "path", [("d", "M" + "L".join(pairs) + ("Z" if kind == "polygon" else ""))]
    return kind, [(name, value) for name, value in attributes if name != "key"]


def adapt(source: str, icon: str, stroke_width: str = "2") -> str:
    """stroke_width "2" is Lucide's own and reproduces the transport row; LumaUI's
    import passes "1.6", the weight luma-next-70 draws every glyph at."""
    lines = ["<!-- Lucide geometry, ISC; adapted to GTK 4.20+ symbolic stroke classes. -->",
             f'<svg xmlns="http://www.w3.org/2000/svg" class="lucide lucide-{icon}"' +
             f' width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="{stroke_width}"'
             ' stroke-linecap="round" stroke-linejoin="round">']
    body = source[source.index(">", source.index("<svg")) + 1:]
    for kind, raw in PRIMITIVE.findall(body):
        kind, attributes = as_path(kind, ATTRIBUTE.findall(raw))
        rendered = " ".join(f'{key}="{value}"' for key, value in attributes)
        lines.append(f"  <{kind} {rendered} {STROKE.format(width=stroke_width)} />")
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    source = Path(sys.argv[1])
    Path(sys.argv[2]).write_text(adapt(source.read_text(), sys.argv[3] if len(sys.argv) > 3 else source.stem))
