#!/usr/bin/env python3
"""Materialize Filer's v70 data inside a private conform run directory.

The resulting tree is read-only and never reads or writes the user's files.
The caller owns and removes --root after the native capture process exits.
"""

from __future__ import annotations

import argparse
import configparser
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import struct
import zlib
from pathlib import Path

SIZE_SCALE = {"KB": 1000, "MB": 1000**2, "GB": 1000**3}


def size_bytes(text: str) -> int:
    match = re.fullmatch(r"([\d.]+)\s*(KB|MB|GB)", text)
    return round(float(match.group(1)) * SIZE_SCALE[match.group(2)]) if match else 0


def modified_time(text: str, today: dt.date) -> float:
    if text.startswith("Today"):
        day = today
    elif text.startswith("Yesterday"):
        day = today - dt.timedelta(days=1)
    else:
        match = re.match(r"([A-Za-z]{3}) (\d+)", text)
        day = dt.datetime.strptime(f"{match.group(1)} {match.group(2)} {today.year}", "%b %d %Y").date() if match else today
    clock = re.search(r"(\d+):(\d+)\s*(AM|PM)", text)
    hour = (int(clock.group(1)) % 12) + (12 if clock.group(3) == "PM" else 0) if clock else 12
    minute = int(clock.group(2)) if clock else 0
    return dt.datetime.combine(day, dt.time(hour, minute)).timestamp()


def thumbnail_png(png: bytes, uri: str, mtime: int, size: int) -> bytes:
    """Give a prepared PNG the metadata GNOME uses to validate its cache."""
    signature = b"\x89PNG\r\n\x1a\n"
    if not png.startswith(signature):
        raise ValueError("fixture thumbnail is not PNG")

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    data = b"".join(chunk(b"tEXt", key + b"\0" + value.encode("utf-8"))
                    for key, value in ((b"Thumb::URI", uri), (b"Thumb::MTime", str(mtime)),
                                       (b"Thumb::Size", str(size))))
    offset = len(signature)
    output = bytearray(signature)
    while offset + 8 <= len(png):
        length = struct.unpack_from(">I", png, offset)[0]
        kind = png[offset + 4:offset + 8]
        payload = png[offset + 8:offset + 8 + length]
        if kind == b"tEXt" and payload.startswith(b"Thumb::"):
            offset += length + 12
            continue
        if kind == b"IDAT":
            return bytes(output) + data + png[offset:]
        output.extend(png[offset:offset + length + 12])
        offset += length + 12
    raise ValueError("fixture thumbnail has no image data")


def sized_picture(content: bytes, size: int) -> bytes:
    """Keep WebP's RIFF length valid while matching the sample's displayed size."""
    if len(content) >= size or not content.startswith(b"RIFF") or content[8:12] != b"WEBP":
        return content
    filler = size - len(content) - 8
    if filler < 0 or filler % 2:
        return content
    result = content + b"JUNK" + struct.pack("<I", filler) + bytes(filler)
    return result[:4] + struct.pack("<I", len(result) - 8) + result[8:]


def materialize(fixture: dict, root: Path, images: Path, today: dt.date | None = None) -> Path:
    """Create the isolated hierarchy and return Home/Projects/Launch."""
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"fixture root must be empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    today = today or dt.date.today()
    details = {entry["name"]: entry for entry in fixture["files"]}
    details.update(fixture.get("file_details", {}))
    tree = fixture["tree"]
    snippets = fixture.get("search_snippets", {})
    made_dirs: list[Path] = [root]
    thumb_sources: list[tuple[Path, Path, int]] = []

    def fill(directory: Path, key: str, depth: int = 0) -> None:
        if depth > 12:
            raise ValueError("fixture tree is too deep")
        children = ([(entry["name"], entry["kind"]) for entry in fixture["files"]]
                    if key == "Launch" else tree.get(key, []))
        for name, kind in children:
            if name in {"", ".", ".."} or any(c in name for c in "/\\\t\n"):
                raise ValueError(f"invalid fixture name: {name!r}")
            target = directory / name
            if kind == "folder":
                target.mkdir()
                made_dirs.append(target)
                fill(target, name, depth + 1)
                continue
            detail = details.get(name, {})
            thumbnail = detail.get("thumbnail")
            picture = images / thumbnail if thumbnail else None
            content = (picture.read_bytes() if picture and picture.is_file()
                       else snippets.get(name, "").encode("utf-8"))
            size = max(len(content), size_bytes(detail.get("size", "")))
            if target.suffix.lower() == ".webp":
                content = sized_picture(content, size)
            with target.open("wb") as stream:
                stream.write(content)
                stream.truncate(size)
            stamp = modified_time(detail.get("modified", ""), today)
            os.utime(target, (stamp, stamp))
            target.chmod(0o444)
            if picture is not None:
                thumb_sources.append((target, picture.with_suffix(".png"), int(stamp)))

    for top in ("Home", "Fable SSD", "Archive"):
        directory = root / top
        directory.mkdir()
        made_dirs.append(directory)
        fill(directory, top)
    recent = root / "Recent"
    recent.mkdir()
    made_dirs.append(recent)
    launch = root / "Home" / "Projects" / "Launch"
    for entry in fixture["files"]:
        if entry["kind"] != "folder" and entry.get("modified", "").startswith(("Today", "Yesterday")):
            copy = recent / entry["name"]
            shutil.copyfile(launch / entry["name"], copy)
            shutil.copystat(launch / entry["name"], copy)
            if entry.get("thumbnail"):
                thumb_sources.append((copy, images / Path(entry["thumbnail"]).with_suffix(".png"),
                                      int(copy.stat().st_mtime)))
    for subdir in ("config", "data", "cache"):
        (root / "Profile" / subdir).mkdir(parents=True, mode=0o700)
    (root / "Profile").chmod(0o700)
    (root / "Profile" / "config" / "filer-grid.tsv").write_text(
        "\n".join(entry["name"] + "\t" + entry["size"] for entry in fixture["files"]) + "\n"
    )
    (root / "Profile" / "config" / "filer-search.tsv").write_text(
        "\n".join(fixture.get("search_order", [])) + "\n"
    )
    info = configparser.ConfigParser(interpolation=None)
    info.optionxform = str
    launch_info = fixture.get("launch_information", {})
    info["Launch"] = {
        "kind": fixture["kind_labels"]["folder"],
        "contains": f"{len(fixture['files'])} items",
        "size_on_disk": launch_info.get("size_on_disk", ""),
        "modified": launch_info.get("modified", ""),
        "where": "Home › Projects",
        "shared_with": launch_info.get("shared_with", ""),
    }
    for entry in fixture["files"]:
        info[entry["name"]] = {
            "kind": fixture["kind_labels"].get(entry["kind"], ""),
            "size": entry.get("size", ""),
            "modified": entry.get("modified", ""),
            "where": "Home › Projects › Launch",
            "dimensions": entry.get("dimensions", ""),
            "length": entry.get("length", ""),
            "tags": ", ".join(tag.title() for tag in entry.get("tags", [])),
        }
    with (root / "Profile" / "config" / "filer-info.ini").open("w") as stream:
        info.write(stream)
    applications = root / "Profile" / "data" / "applications"
    applications.mkdir(mode=0o700)
    (applications / "org.gnome.Nautilus.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Filer\nIcon=luma-v3-files\n"
        "Exec=nautilus\nCategories=Utility;FileManager;\n"
    )
    cache = root / "Profile" / "cache" / "thumbnails"
    for file, png_path, stamp in thumb_sources:
        uri = file.as_uri()
        output = hashlib.md5(uri.encode("utf-8"), usedforsecurity=False).hexdigest() + ".png"
        thumb = thumbnail_png(png_path.read_bytes(), uri, stamp, file.stat().st_size)
        for size in ("normal", "large", "x-large", "xx-large"):
            directory = cache / size
            directory.mkdir(parents=True, exist_ok=True)
            (directory / output).write_bytes(thumb)
    # The conform harness owns and removes this disposable tree after capture.
    # Files stay read-only, while directories must allow that cleanup.
    for directory in reversed(made_dirs):
        directory.chmod(0o755)
    return launch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--images", type=Path)
    args = parser.parse_args()
    images = args.images or Path(__file__).resolve().parents[1] / "tests/fixtures/filer-images"
    launch = materialize(json.loads(args.fixture.read_text()), args.root, images)
    print(launch)


if __name__ == "__main__":
    main()
