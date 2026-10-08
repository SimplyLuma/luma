#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Integrate the tested software allocator into an official-patched source tree.

Only the four allocator files owned by the cumulative Luma patch are replaced.
Their current bytes must match the exact upstream patch series, or the already
integrated result. No unrelated Waydroid HAL or source modifications are erased.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
REVISION = "0eb202d7ebd7d2410eb2f62c908c0341964a4829"
PATCH = ROOT / "patches/android-hardware-libhardware/0001-luma-software-yuv-buffers.patch"
FILES = ("gr.h", "gralloc.cpp", "gralloc_priv.h", "mapper.cpp")


def integrate(source_root):
    hardware = source_root / "hardware/libhardware"
    upstream = source_root / "vendor/extra/waydroid-patches/base-patches-33/hardware/libhardware"
    archive = subprocess.check_output(["git", "-C", str(hardware), "archive", REVISION])
    with tempfile.TemporaryDirectory(prefix="luma-allocator-") as directory:
        work = Path(directory)
        expected, desired = work / "upstream", work / "luma"
        for tree in (expected, desired):
            tree.mkdir()
            subprocess.run(["tar", "-xf", "-", "-C", str(tree)], input=archive, check=True)
        patches = sorted(upstream.glob("*.patch"))
        if len(patches) != 5:
            raise RuntimeError("Unexpected upstream libhardware patch series")
        for patch in patches:
            subprocess.run(["git", "apply", str(patch)], cwd=expected, check=True)
        subprocess.run(["git", "apply", str(PATCH)], cwd=desired, check=True)
        relative = [Path("modules/gralloc") / name for name in FILES]
        if all((hardware / p).read_bytes() == (desired / p).read_bytes() for p in relative):
            print("Software-YUV allocator already matches the canonical source")
            return
        for path in relative:
            if (hardware / path).read_bytes() != (expected / path).read_bytes():
                raise RuntimeError(f"Refusing to overwrite unexpected allocator source: {path}")
        for path in relative:
            shutil.copyfile(desired / path, hardware / path)
    print("Integrated the canonical software-YUV allocator; unrelated HAL files preserved")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    integrate(parser.parse_args().source_root.resolve())
