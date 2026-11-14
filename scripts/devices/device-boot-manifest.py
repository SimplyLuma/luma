#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write a device package's boot image declaration for luma-device-boot.

From devices/<vendor>/<codename>/device.toml ([boot_update], [kit] compatible,
[kernel] release) and boot.toml (partition, partition_size), plus the boot
image of the same kernel build, this writes OUTPUT_DIR/boot.bin and
OUTPUT_DIR/device-boot.json, which the package installs as
/usr/lib/luma/device-boot/<vendor>-<codename>/. The result is checked with
luma-device-boot's own parser (src/luma-update/luma_update/device_boot.py).

An Android boot image must start with ANDROID!; with --kernel-sha256 (the
kernel build's IMAGE_SHA256) its kernel must be that build's.

  device-boot-manifest.py --device devices/fairphone/fp6 --boot-image FILE \\
      [--kernel-sha256 HEX] [--kernel-notes-sha256 HEX] --output DIR
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import struct
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "luma-update"))
from luma_update import device_boot  # noqa: E402

SHA256 = re.compile(r"^[0-9a-f]{64}$")


def fail(message: str) -> None:
    raise SystemExit(f"device-boot-manifest: {message}")


def android_kernel(image: bytes) -> bytes:
    """The kernel of an Android boot image (header v0-v2: page-aligned after the header)."""
    if image[:8] != b"ANDROID!":
        fail("the boot image does not start with ANDROID!")
    kernel_size = struct.unpack_from("<I", image, 8)[0]
    version = struct.unpack_from("<I", image, 40)[0]
    page = 4096 if version >= 3 else struct.unpack_from("<I", image, 36)[0]
    return image[page:page + kernel_size]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--device", type=Path, required=True)
    parser.add_argument("--boot-image", type=Path, required=True)
    parser.add_argument("--kernel-sha256", default="")
    parser.add_argument("--kernel-notes-sha256", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    device_dir = args.device if args.device.is_absolute() else ROOT / args.device
    device = tomllib.loads((device_dir / "device.toml").read_text(encoding="utf-8"))
    boot = tomllib.loads((device_dir / "boot.toml").read_text(encoding="utf-8"))
    update = device.get("boot_update") or fail(f"{device_dir}/device.toml has no [boot_update]")
    partition = boot.get("partition") or fail("boot.toml names no boot partition")
    partition_size = boot.get("partition_size") or fail("boot.toml has no partition_size")
    for value, what in ((args.kernel_sha256, "--kernel-sha256"), (args.kernel_notes_sha256, "--kernel-notes-sha256")):
        if value and not SHA256.match(value):
            fail(f"{what} is not a sha256")

    image = args.boot_image.read_bytes()
    if not 0 < len(image) <= partition_size:
        fail(f"the boot image is {len(image)} bytes; the {partition} partition holds {partition_size}")
    if boot.get("format") == "uefi":
        fail("[boot_update] covers Android boot partitions only")
    kernel = android_kernel(image)
    if args.kernel_sha256 and hashlib.sha256(kernel).hexdigest() != args.kernel_sha256:
        fail("the boot image's kernel is not the kernel build's (IMAGE_SHA256)")

    args.output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.boot_image, args.output / "boot.bin")
    manifest = {
        "schema": device_boot.SCHEMA,
        "device": f"{device['vendor']}/{device['codename']}",
        "compatible": device["kit"]["compatible"],
        "slots": update["slots"],
        "partitions": update["partitions"],
        "kernel_release": device["kernel"]["release"],
        "kernel_notes_sha256": args.kernel_notes_sha256,
        "images": [{"partition": partition, "file": "boot.bin", "sha256": hashlib.sha256(image).hexdigest(),
                    "size": len(image), "partition_size": partition_size}],
    }
    path = args.output / device_boot.MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    device_boot.load_manifest(path)
    print(f"{path}: {manifest['images'][0]['sha256']} ({manifest['kernel_release']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
