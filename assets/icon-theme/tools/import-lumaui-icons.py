#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Import the Lucide glyphs LumaUI draws, as stable Prairie symbolic icons.

    import-lumaui-icons.py --source ICONS-DIR --html luma-next-70.html [NAME ...]
    import-lumaui-icons.py --source ICONS-DIR --source-origin URL [NAME ...]
    import-lumaui-icons.py --check

Every Lucide name the design uses (the ICONS list of the live mockup, plus
any NAME given) is vendored verbatim in upstream/lucide/lumaui/ and adapted
by adapt-lucide-symbolic.py, geometry unchanged, at the 1.6 stroke the
mockup draws, to symbolic/actions/lumaui-<name>-symbolic.svg. The lumaui-
prefix keeps them apart from standard names (Lucide's `folder` is not
Adwaita's `folder-symbolic`) and from the older luma-* glyphs, so adding one
never restyles a third-party application. The names never change: a glyph
that Lucide redraws keeps its name, and one the design stops using stays.

upstream/lucide/lumaui-manifest.json records each source digest and the
digest of the icon made from it. --check rebuilds every icon from its
vendored source and fails on any drift, a missing licence or a stray file,
without needing the mockup.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

THEME = Path(__file__).resolve().parents[1] / "Prairie"
UPSTREAM = THEME / "upstream/lucide/lumaui"
MANIFEST = THEME / "upstream/lucide/lumaui-manifest.json"
ACTIONS = THEME / "symbolic/actions"
STROKE_WIDTH = "1.6"
PREFIX = "lumaui-"
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_spec = importlib.util.spec_from_file_location("adapt", Path(__file__).with_name("adapt-lucide-symbolic.py"))
_adapt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_adapt)


def symbolic_name(lucide: str) -> str:
    return f"{PREFIX}{lucide}-symbolic"


def digest(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def names_in_mockup(html: Path) -> list[str]:
    text = html.read_text(encoding="utf-8")
    match = re.search(r"const ICONS = \[(.*?)\];", text, re.S)
    if not match:
        raise SystemExit(f"{html}: no ICONS list")
    return sorted(set(re.findall(r"'([a-z0-9-]+)'", match.group(1))))


# Glyphs v70 also draws filled when they are on (the Favourite heart, .ib.loved):
# lumaui-<name>-filled-symbolic, the same geometry with a foreground fill.
FILLED = ("heart", "star", "bookmark", "square", "circle")


def filled(icon: str) -> str:
    return (icon.replace("transparent-fill foreground-stroke", "foreground-fill foreground-stroke")
            .replace("adapted to GTK 4.20+ symbolic stroke classes.", "adapted to GTK 4.20+ symbolic stroke classes; filled (on)."))


def build(source_dir: Path, names: list[str], source_origin: str | None = None) -> None:
    previous = json.loads(MANIFEST.read_text()) if MANIFEST.is_file() else {"icons": []}
    previous_by_name = {entry["lucide"]: entry for entry in previous["icons"]}
    keep = {entry["lucide"] for entry in previous["icons"]}
    names = sorted(keep | set(names))
    UPSTREAM.mkdir(parents=True, exist_ok=True)
    licence = source_dir / "LUCIDE_LICENSE.txt"
    if licence.is_file():
        shutil.copyfile(licence, UPSTREAM / "LICENSE.txt")
    entries = []
    for name in names:
        if not NAME.match(name):
            raise SystemExit(f"not a Lucide name: {name}")
        vendored = UPSTREAM / f"{name}.svg"
        source = source_dir / f"{name}.svg"
        if source.is_file():
            shutil.copyfile(source, vendored)
        if not vendored.is_file():
            raise SystemExit(f"{name}: no source in {source_dir} and none vendored")
        raw = vendored.read_text(encoding="utf-8")
        icon = _adapt.adapt(raw, name, STROKE_WIDTH)
        (ACTIONS / f"{symbolic_name(name)}.svg").write_text(icon, encoding="utf-8")
        if name in FILLED:
            (ACTIONS / f"{symbolic_name(name + '-filled')}.svg").write_text(filled(icon), encoding="utf-8")
        version = re.search(r"lucide-static v([0-9.]+)", raw)
        entry = {
            "lucide": name,
            "icon": symbolic_name(name),
            "source": f"upstream/lucide/lumaui/{name}.svg",
            "sha256": digest(raw),
            "upstream_version": version.group(1) if version else "not stated in supplied SVG",
            "gtk_symbolic_sha256": digest(icon),
        }
        origin = ((source_origin.rstrip("/") + f"/{name}.svg") if source.is_file() and source_origin
                  else previous_by_name.get(name, {}).get("source_origin"))
        if origin:
            entry["source_origin"] = origin
        entries.append(entry)
    MANIFEST.write_text(json.dumps({
        "source": "Lucide glyphs used by luma-next-70 (the LumaUI spec), vendored verbatim from the "
                  "supplied icons directory and pinned upstream Lucide Static sources identified per entry; "
                  "upstream/lucide/lumaui/LICENSE.txt retains the ISC/MIT notices.",
        "adaptation": "tools/adapt-lucide-symbolic.py at stroke-width 1.6 (the mockup's weight): GTK 4.20+ "
                      "symbolic stroke classes, line/polyline/polygon as paths, geometry unchanged.",
        "naming": "lumaui-<lucide name>-symbolic; stable, never reused. Share is share-2, Export is share, "
                  "Upload is upload.",
        "icons": entries,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"{len(entries)} LumaUI icons")


def check() -> int:
    problems: list[str] = []
    if not (UPSTREAM / "LICENSE.txt").is_file():
        problems.append("upstream/lucide/lumaui/LICENSE.txt is missing")
    manifest = json.loads(MANIFEST.read_text())
    listed = set()
    for entry in manifest["icons"]:
        name = entry["lucide"]
        listed.add(name)
        vendored = THEME / entry["source"]
        if not vendored.is_file():
            problems.append(f"{name}: source missing")
            continue
        raw = vendored.read_text(encoding="utf-8")
        if digest(raw) != entry["sha256"]:
            problems.append(f"{name}: source digest drifted")
        icon_path = ACTIONS / f"{entry['icon']}.svg"
        expected = _adapt.adapt(raw, name, STROKE_WIDTH)
        if not icon_path.is_file() or icon_path.read_text(encoding="utf-8") != expected:
            problems.append(f"{name}: {icon_path.name} is not what its source makes")
        elif digest(expected) != entry["gtk_symbolic_sha256"]:
            problems.append(f"{name}: icon digest drifted")
        if name in FILLED:
            listed.add(f"{name}-filled")
            filled_path = ACTIONS / f"{symbolic_name(name + '-filled')}.svg"
            if not filled_path.is_file() or filled_path.read_text(encoding="utf-8") != filled(expected):
                problems.append(f"{name}: {filled_path.name} is not what its source makes")
        primitives = len(re.findall(r"<(?:path|circle|rect|ellipse|line|polyline|polygon)\b", raw))
        if primitives != expected.count("foreground-stroke"):
            problems.append(f"{name}: adaptation dropped a primitive")
    strays = {p.stem for p in UPSTREAM.glob("*.svg")} - listed
    strays |= {p.name[len(PREFIX):-len("-symbolic.svg")] for p in ACTIONS.glob(f"{PREFIX}*-symbolic.svg")} - listed
    problems += [f"{name}: not in the manifest" for name in sorted(strays)]
    for problem in problems:
        print(problem, file=sys.stderr)
    if not problems:
        print(f"current: {len(listed)} LumaUI icons")
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source", type=Path)
    parser.add_argument("--source-origin", help="Pinned URL base for newly supplied SVGs; recorded per icon")
    parser.add_argument("--html", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("names", nargs="*")
    args = parser.parse_args()
    if args.check:
        return check()
    if not args.source:
        parser.error("--source is required unless --check")
    names = list(args.names) + (names_in_mockup(args.html) if args.html else [])
    build(args.source, names, args.source_origin)
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
