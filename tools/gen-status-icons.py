#!/usr/bin/env python3
"""Generate Prairie's fill-only GNOME status icon geometry.

Battery assets are derived from one 16 px geometry definition.  The generated
SVGs are committed so consumers never need Shapely; maintainers regenerate
them with Shapely 2.0.7 when changing the geometry.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
    from shapely.geometry.polygon import orient
    from shapely.ops import unary_union
except ImportError as error:  # pragma: no cover - exercised by the build host
    raise SystemExit(
        "error: gen-status-icons.py requires Shapely 2.0.7"
    ) from error


INK = "#2e3436"
LEVELS = tuple(range(0, 101, 10))
STATUS_FILENAMES = {
    "battery-action-symbolic.svg",
    "battery-caution-symbolic.svg",
    "battery-low-symbolic.svg",
    "battery-missing-symbolic.svg",
    "battery-level-100-charged-symbolic.svg",
    *(f"battery-level-{level}-symbolic.svg" for level in LEVELS),
    *(f"battery-level-{level}-plugged-in-symbolic.svg" for level in LEVELS),
    *(f"battery-level-{level}-charging-symbolic.svg" for level in LEVELS[:-1]),
}
LEGACY_FILENAMES = {
    "battery-caution-charging-symbolic.svg",
    "battery-empty-charging-symbolic.svg",
    "battery-empty-symbolic.svg",
    "battery-full-charged-symbolic.svg",
    "battery-full-charging-symbolic.svg",
    "battery-full-symbolic.svg",
    "battery-good-charging-symbolic.svg",
    "battery-good-symbolic.svg",
    "battery-low-charging-symbolic.svg",
}
EXPECTED_FILENAMES = tuple(sorted(STATUS_FILENAMES | LEGACY_FILENAMES))


def rounded_rect(x: float, y: float, width: float, height: float, radius: float):
    radius = min(radius, width / 2, height / 2)
    horizontal = box(x + radius, y, x + width - radius, y + height)
    vertical = box(x, y + radius, x + width, y + height - radius)
    corners = [
        Point(cx, cy).buffer(radius, quad_segs=8)
        for cx, cy in (
            (x + radius, y + radius),
            (x + width - radius, y + radius),
            (x + width - radius, y + height - radius),
            (x + radius, y + height - radius),
        )
    ]
    return unary_union([horizontal, vertical, *corners])


FRAME = rounded_rect(1.0, 4.5, 12.0, 7.0, 2.0).difference(
    rounded_rect(2.0, 5.5, 10.0, 5.0, 1.2)
)
TERMINAL = rounded_rect(13.6, 6.6, 1.4, 2.8, 0.7)
BOLT = Polygon(
    ((7.6, 4.5), (5.0, 8.1), (6.65, 8.1), (6.25, 11.5), (9.2, 7.25), (7.45, 7.25))
)
BOLT_HALO = BOLT.buffer(0.5, quad_segs=8, join_style="mitre")
SLASH = LineString(((3.2, 11.0), (11.8, 5.0))).buffer(
    0.7, quad_segs=8, cap_style="round", join_style="round"
)
SLASH_HALO = SLASH.buffer(0.5, quad_segs=8, cap_style="round", join_style="round")


def level_fill(level: int):
    if level == 0:
        return None
    width = round(8.4 * level / 100, 2)
    return rounded_rect(2.8, 6.3, width, 3.4, 0.8)


def battery(level: int, powered: bool = False):
    fill = level_fill(level)
    if powered:
        parts = [FRAME.difference(BOLT_HALO), TERMINAL, BOLT]
        if fill is not None:
            parts.append(fill.difference(BOLT_HALO))
        return unary_union(parts)
    parts = [FRAME, TERMINAL]
    if fill is not None:
        parts.append(fill)
    return unary_union(parts)


def missing_battery():
    return unary_union([FRAME.difference(SLASH_HALO), TERMINAL, SLASH])


def clean_number(value: float) -> str:
    value = 0.0 if abs(value) < 0.0005 else value
    rendered = f"{value:.3f}".rstrip("0").rstrip(".")
    return rendered or "0"


def ring_path(coordinates) -> str:
    points = list(coordinates)[:-1]
    if not points:
        return ""
    commands = [f"M {clean_number(points[0][0])} {clean_number(points[0][1])}"]
    commands.extend(
        f"L {clean_number(x)} {clean_number(y)}" for x, y in points[1:]
    )
    commands.append("Z")
    return " ".join(commands)


def geometry_path(geometry) -> str:
    if geometry.is_empty:
        raise ValueError("generated icon geometry is empty")
    if isinstance(geometry, Polygon):
        polygons = [geometry]
    elif isinstance(geometry, MultiPolygon):
        polygons = list(geometry.geoms)
    else:
        polygons = [part for part in geometry.geoms if isinstance(part, Polygon)]
    polygons.sort(key=lambda polygon: tuple(round(item, 6) for item in polygon.bounds))
    paths = []
    for polygon in polygons:
        polygon = orient(polygon, sign=1.0)
        paths.append(ring_path(polygon.exterior.coords))
        paths.extend(ring_path(interior.coords) for interior in polygon.interiors)
    return " ".join(path for path in paths if path)


def svg_document(geometry) -> str:
    path = geometry_path(geometry)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg width="16px" height="16px" viewBox="0 0 16 16" '
        'xmlns="http://www.w3.org/2000/svg">\n'
        f'  <path d="{path}" fill="{INK}" fill-rule="evenodd"/>\n'
        '</svg>\n'
    )


def generated_documents() -> dict[str, str]:
    documents: dict[str, str] = {}
    for level in LEVELS:
        documents[f"battery-level-{level}-symbolic.svg"] = svg_document(battery(level))
        documents[f"battery-level-{level}-plugged-in-symbolic.svg"] = svg_document(
            battery(level, powered=True)
        )
        if level < 100:
            documents[f"battery-level-{level}-charging-symbolic.svg"] = svg_document(
                battery(level, powered=True)
            )

    documents["battery-level-100-charged-symbolic.svg"] = svg_document(battery(100))
    documents["battery-action-symbolic.svg"] = svg_document(missing_battery())
    documents["battery-caution-symbolic.svg"] = svg_document(battery(10))
    documents["battery-low-symbolic.svg"] = svg_document(battery(20))
    documents["battery-missing-symbolic.svg"] = svg_document(missing_battery())

    legacy_levels = {"empty": 0, "good": 60, "full": 100}
    for label, level in legacy_levels.items():
        documents[f"battery-{label}-symbolic.svg"] = svg_document(battery(level))
    for label, level in {"empty": 0, "caution": 10, "low": 20, "good": 60, "full": 100}.items():
        documents[f"battery-{label}-charging-symbolic.svg"] = svg_document(
            battery(level, powered=True)
        )
    documents["battery-full-charged-symbolic.svg"] = svg_document(battery(100))

    if tuple(sorted(documents)) != EXPECTED_FILENAMES:
        raise AssertionError("generated battery state matrix does not match the target")
    return documents


def write_documents(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for name, document in generated_documents().items():
        (output / name).write_text(document, encoding="utf-8")


def check_documents(output: Path) -> int:
    failures = []
    for name, expected in generated_documents().items():
        path = output / name
        if not path.is_file():
            failures.append(f"missing: {name}")
        elif path.read_text(encoding="utf-8") != expected:
            failures.append(f"stale: {name}")
    actual = {path.name for path in output.glob("battery-*-symbolic.svg")}
    for name in sorted(actual - set(EXPECTED_FILENAMES)):
        failures.append(f"unexpected: {name}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    default_output = repo_root / "assets/icon-theme/Prairie/symbolic/status"
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=default_output)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        print("\n".join(EXPECTED_FILENAMES))
        return 0
    if args.check:
        return check_documents(args.output)
    write_documents(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
