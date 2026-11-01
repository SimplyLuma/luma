#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bake installed application artwork at standard theme sizes during packaging.

Keep the editable SVGs and vector fallback. GTK also loads window icons during
realization, before the first app frame; decoding filtered SVGs there can block
the whole launch. Fixed-size PNGs move that rendering work to the build machine.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
from pathlib import Path
import shutil
import struct
import subprocess

# Logical sizes, with up to 512 physical pixels at 4x. Advertising huge logical
# sizes also makes GTK realize huge window-icon textures on every app launch.
SIZES = (16, 24, 32, 48, 64, 96, 128)
SCALES = (1, 2, 3, 4)
# GTK enumerates logical window-icon sizes, then requests each at the display
# scale. Advertise native scale variants; pixel-size-only directories leave
# holes (for example 96 logical pixels at 2x) that fall back to expensive SVG.
VARIANTS = tuple((size, scale) for scale in SCALES for size in SIZES if size * scale <= 512)


def icon_directory(size: int, scale: int) -> str:
    suffix = f"@{scale}" if scale > 1 else ""
    return f"{size}x{size}{suffix}/apps"


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
        raise ValueError(f"Not a PNG: {path}")
    return struct.unpack(">II", header[16:24])


def prepare(theme: Path, *, check: bool = False) -> int:
    index = configparser.ConfigParser(interpolation=None)
    index.optionxform = str
    index.read(theme / "index.theme")
    directories = index["Icon Theme"]["Directories"].split(",")
    fixed = [icon_directory(size, scale) for size, scale in VARIANTS]
    if check:
        if directories[:len(fixed)] != fixed:
            raise ValueError("Fixed-size application artwork must precede the scalable fallback")
    else:
        # A scalable directory can match every size. Theme lookup tries exact
        # matches in directory order, so appending PNGs would still select SVG.
        directories = fixed + [name for name in directories if name not in fixed]
    sources = sorted((theme / "scalable/apps").glob("*.svg"))
    if not sources:
        raise ValueError("The application artwork directory is empty")
    cached: dict[tuple[str, int], Path] = {}
    count = 0
    for size, scale in VARIANTS:
        pixels = size * scale
        directory = icon_directory(size, scale)
        if check:
            if directory not in directories:
                raise ValueError(f"Theme does not advertise {directory}")
            section = index[directory]
            if (section["Context"], section["Type"], section["Size"], section.get("Scale", "1")) != (
                    "Applications", "Fixed", str(size), str(scale)):
                raise ValueError(f"Invalid fixed-size theme directory: {directory}")
        else:
            (theme / directory).mkdir(parents=True, exist_ok=True)
            if directory not in directories:
                directories.append(directory)
            index[directory] = {"Context": "Applications", "Size": str(size), "Scale": str(scale), "Type": "Fixed"}
        for source in sources:
            output = theme / directory / (source.stem + ".png")
            if not check:
                key = (hashlib.sha256(source.read_bytes()).hexdigest(), pixels)
                if key in cached:
                    shutil.copyfile(cached[key], output)
                else:
                    subprocess.run([
                        "rsvg-convert", "--keep-aspect-ratio", "--width", str(pixels),
                        "--height", str(pixels), "--output", str(output), str(source),
                    ], check=True, timeout=120)
                    cached[key] = output
            if png_size(output) != (pixels, pixels):
                raise ValueError(f"Wrong application icon dimensions: {output}")
            count += 1
    if not check:
        index["Icon Theme"]["Directories"] = ",".join(directories)
        with (theme / "index.theme").open("w") as stream:
            index.write(stream, space_around_delimiters=False)
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--theme", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(f"Application icon variants: {prepare(args.theme, check=args.check)}")
