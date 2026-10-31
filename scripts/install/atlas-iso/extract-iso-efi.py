#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Extract the actual UEFI El Torito image, including hidden appended images."""
from pathlib import Path
import re
import subprocess
import sys


def extent(report: str, iso_bytes: int) -> tuple[int, int]:
    images = []
    for line in report.splitlines():
        match = re.fullmatch(r"El Torito boot img\s*:\s*\d+\s+UEFI\s+y\s+none\s+0x[0-9a-fA-F]+\s+0x[0-9a-fA-F]+\s+(\d+)\s+(\d+)\s*", line)
        if match:
            images.append((int(match[2]) * 2048, int(match[1]) * 512))
    if len(images) != 1:
        raise ValueError("expected exactly one bootable UEFI El Torito image")
    offset, size = images[0]
    if not 0 < size <= 256 * 1024 * 1024 or offset < 0 or offset + size > iso_bytes:
        raise ValueError("UEFI image extent is outside the ISO or exceeds 256 MiB")
    return offset, size


def main() -> None:
    iso, output = map(Path, sys.argv[1:])
    report = subprocess.check_output(["xorriso", "-indev", str(iso), "-report_el_torito", "plain"], text=True)
    offset, size = extent(report, iso.stat().st_size)
    with iso.open("rb") as source, output.open("xb") as target:
        source.seek(offset)
        remaining = size
        while remaining:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("truncated UEFI image extent")
            target.write(chunk)
            remaining -= len(chunk)


if __name__ == "__main__":
    main()
