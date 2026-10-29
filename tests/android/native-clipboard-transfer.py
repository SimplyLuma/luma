#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compile the complete clipboard source with explicit Wayland/HIDL fixtures.

The pipe transport is real. This does not replace Android/Wayland IPC tests.
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hardware_source", type=Path)
    parser.add_argument("--sanitize", action="store_true")
    parser.add_argument("--sanitizer", choices=("address", "thread"), default="address")
    parser.add_argument("--compiler", default=os.environ.get("CXX", "c++"))
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="luma-clipboard-transfer-") as directory:
        work = Path(directory)
        target = work / "hwcomposer"
        target.mkdir()
        for name in ("WaydroidClipboard.cpp", "WaydroidClipboard.h", "wayland-hwc.cpp", "wayland-hwc.h", "Android.bp"):
            shutil.copyfile(args.hardware_source / "hwcomposer" / name, target / name)
        for patch in ("0015-luma-native-clipboard-caller-admission.patch", "0016-luma-bounded-native-clipboard-transfer.patch"):
            subprocess.run(["patch", "--batch", "--fuzz=0", "-p1", "-d", str(work), "-i", str(repo / "patches/android-hardware-waydroid" / patch)], check=True)
        header = repo / "patches/android-hardware-waydroid/native/luma-clipboard-transfer.h"
        assert (target / header.name).read_bytes() == header.read_bytes()
        shutil.copyfile(repo / "patches/android-hardware-waydroid/native/luma-window-callbacks.h", target / "luma-window-callbacks.h")
        production = (target / "WaydroidClipboard.cpp").read_text()
        # Substitute only the platform includes, never production statements.
        production = re.sub(r"^#include[^\n]*\n", "", production, flags=re.MULTILINE)
        harness = (repo / "tests/android/native-clipboard-transfer.cpp").read_text()
        unit = work / "clipboard.cpp"
        unit.write_text(harness.replace("// PRODUCTION_CLIPBOARD", production))
        command = [args.compiler, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-pthread", "-I" + str(target), str(unit), "-o", str(work / "clipboard")]
        if args.sanitize:
            command += ["-fsanitize=" + ("address,undefined" if args.sanitizer == "address" else "thread"), "-fno-omit-frame-pointer"]
        subprocess.run(command, check=True)
        if os.environ.get("DEV_ARTIFACT_DIR"):
            evidence = Path(os.environ["DEV_ARTIFACT_DIR"])
            shutil.copyfile(unit, evidence / "clipboard.cpp")
            shutil.copyfile(work / "clipboard", evidence / "clipboard")
        result = subprocess.run([str(work / "clipboard")], check=True, timeout=15, capture_output=True, text=True)
        print(result.stdout, end="", flush=True)
        print(result.stderr, end="", flush=True)
        if "PASS: complete production clipboard" not in result.stdout:
            raise RuntimeError("Clipboard executable returned without its completed-test receipt")


if __name__ == "__main__":
    main()
