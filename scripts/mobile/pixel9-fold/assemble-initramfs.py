#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Assemble the Pixel 9 Pro Fold boot initramfs.

The tree is the FP6 Alpha 1 boot image's ramdisk, trimmed, plus the files in
config/mobile/comet-initramfs, the phone's own firmware from its factory
image's vendor partition and the built payloads (Mesa, modem stack, phosh,
GNSS daemon, preload shims). config/mobile/comet-initramfs.manifest lists
every entry of the result and where it comes from:

  base            the FP6 Alpha 1 ramdisk
  branch          config/mobile/comet-initramfs/<path>
  vendor:<path>   the factory image's vendor partition (see firmware.manifest)
  payload:<name>  <payload-dir>/<path>, built as the README describes
  builder         your own file (the USB SSH key); left out if not given

Run as root: entries keep root ownership and /dev holds device nodes.

Usage: assemble-initramfs.py --fp6-boot BOOT.bin --vendor DIR --payload DIR
                             [--authorized-keys FILE] [--strict] OUT
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct
import sys

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "config/mobile/comet-initramfs.manifest"
BRANCH = ROOT / "config/mobile/comet-initramfs"
# devices/fairphone/fp6 release alpha1 boot_image_sha256
FP6_BOOT_SHA256 = "6b93fa901d85152efb29e9a58874d040e86fcad597c4dae0c9b11c897cf1051b"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ramdisk(boot: bytes) -> bytes:
    """The ramdisk of an Android boot image (header v0-v2)."""
    if boot[:8] != b"ANDROID!":
        raise SystemExit("not an Android boot image")
    kernel_size, _, ramdisk_size = struct.unpack_from("<III", boot, 8)
    page = struct.unpack_from("<I", boot, 36)[0]
    start = page * (1 + (kernel_size + page - 1) // page)
    return gzip.decompress(boot[start:start + ramdisk_size])


def cpio_entries(data: bytes):
    """Entries of back-to-back newc archives; later ones replace earlier ones."""
    i = 0
    while i + 110 <= len(data):
        if data[i:i + 6] != b"070701":
            i += 1  # padding between archives
            continue
        h = [int(data[i + 6 + 8 * k:i + 14 + 8 * k], 16) for k in range(13)]
        mode, size, namesize = h[1], h[6], h[11]
        rdev = (h[9], h[10])
        name = data[i + 110:i + 110 + namesize - 1].decode()
        j = i + 110 + namesize
        j += -j % 4
        body = data[j:j + size]
        i = j + size
        i += -i % 4
        if name == "TRAILER!!!":
            continue
        name = name.lstrip("./") if name not in (".", "./") else ""
        if name:
            yield name, mode, body, rdev


def load_manifest():
    rows = []
    for number, line in enumerate(MANIFEST.read_text().splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        kind, mode, value, path, origin = line.split("\t")
        p = PurePosixPath(path)
        if p.is_absolute() or ".." in p.parts:
            raise SystemExit(f"manifest line {number}: unsafe path {path}")
        rows.append((kind, int(mode, 8), value, path, origin))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fp6-boot", type=Path, required=True)
    ap.add_argument("--vendor", type=Path, required=True, help="extracted vendor partition (holds firmware/, etc/)")
    ap.add_argument("--payload", type=Path, required=True)
    ap.add_argument("--authorized-keys", type=Path)
    ap.add_argument("--strict", action="store_true", help="payloads must match the manifest's SHA-256 too")
    ap.add_argument("out", type=Path)
    a = ap.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("run as root")
    if a.out.exists():
        raise SystemExit(f"refusing to replace {a.out}")
    boot = a.fp6_boot.read_bytes()
    if sha256(boot) != FP6_BOOT_SHA256:
        raise SystemExit(f"{a.fp6_boot}: not the FP6 Alpha 1 boot image")
    base = {name: (mode, body, rdev) for name, mode, body, rdev in cpio_entries(ramdisk(boot))}

    problems, warnings = [], []
    a.out.mkdir(parents=True)
    os.chmod(a.out, 0o755)
    for kind, mode, value, path, origin in load_manifest():
        dst = a.out / path
        if kind == "D":
            dst.mkdir(exist_ok=True)
            os.chmod(dst, mode)
            continue
        if kind == "L":
            dst.symlink_to(value)
            continue
        if kind == "N":
            major, minor = (int(x) for x in value.split(":")[1:])
            os.mknod(dst, stat.S_IFCHR | mode, os.makedev(major, minor))
            continue
        if origin == "base":
            if path not in base:
                problems.append(f"{path}: not in the FP6 ramdisk"); continue
            data = base[path][1]
        elif origin == "branch":
            data = (BRANCH / path).read_bytes()
        elif origin.startswith("vendor:"):
            src = a.vendor / origin[len("vendor:"):].removeprefix("/vendor/")
            if not src.is_file():
                problems.append(f"{path}: missing {src}"); continue
            data = src.read_bytes()
        elif origin.startswith("payload:"):
            src = a.payload / path
            if not src.is_file():
                problems.append(f"{path}: missing {src} ({origin})"); continue
            data = src.read_bytes()
        elif origin == "builder":
            if not a.authorized_keys:
                continue
            data = a.authorized_keys.read_bytes()
        else:
            raise SystemExit(f"{path}: unknown origin {origin}")
        if sha256(data) != value:
            message = f"{path}: SHA-256 differs from the manifest ({origin})"
            if origin.startswith("payload:") and not a.strict:
                warnings.append(message)
            elif origin != "builder":
                problems.append(message)
        dst.write_bytes(data)
        os.chmod(dst, mode)
    for w in warnings:
        print("warning:", w, file=sys.stderr)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        shutil.rmtree(a.out)
        return 1
    print(f"assembled {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
