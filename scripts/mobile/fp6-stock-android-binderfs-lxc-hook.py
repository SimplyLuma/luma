#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Allocate exactly three Binder devices in the container-private binderfs.

The LXC mount hook runs after `/dev` tmpfs and `/dev/binderfs` have been
mounted in the container mount/IPC namespace, but before Android init starts.
It touches no global Binder node.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import struct
import subprocess
import sys


NODES = ("binder", "hwbinder", "vndbinder")


def ioc(direction: int, kind: int, number: int, size: int) -> int:
    return (direction << 30) | (kind << 8) | number | (size << 16)


BINDER_CTL_ADD = ioc(3, ord("b"), 1, 264)


def die(message: str) -> "None":
    raise RuntimeError(message)


def main() -> int:
    if os.geteuid() != 0:
        die("hook must run as root")
    if os.environ.get("LXC_NAME") != "luma-stock-android-c1":
        die("unexpected LXC name")
    root_text = os.environ.get("LXC_ROOTFS_MOUNT")
    if not root_text:
        die("LXC_ROOTFS_MOUNT is absent")
    root = Path(root_text)
    binderfs = root / "dev/binderfs"
    control = binderfs / "binder-control"
    if not control.is_char_device() or control.is_symlink():
        die("private binder-control is absent or linked")
    fs_type = subprocess.run(
        ("findmnt", "-rn", "-T", str(control), "-o", "FSTYPE"),
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    if fs_type != "binder":
        die(f"binder-control is not on binderfs: {fs_type}")
    entries = {path.name for path in binderfs.iterdir()}
    infrastructure = {"binder-control"}
    features = binderfs / "features"
    if features.exists():
        if not features.is_dir() or features.is_symlink():
            die("BinderFS features entry has an unexpected type")
        infrastructure.add("features")
    existing = entries - infrastructure
    if existing and existing != set(NODES):
        die(f"unexpected pre-created BinderFS contents: {sorted(entries)}")
    for name in NODES:
        if (root / "dev" / name).exists() or (root / "dev" / name).is_symlink():
            die(f"refuse pre-existing Binder link: {name}")

    if not existing:
        with control.open("rb", buffering=0) as stream:
            for name in NODES:
                request = bytearray(struct.pack("256sII", name.encode(), 0, 0))
                fcntl.ioctl(stream.fileno(), BINDER_CTL_ADD, request, True)
                returned_name, major, minor = struct.unpack("256sII", request)
                if returned_name.rstrip(b"\0").decode() != name or major == 0:
                    die(f"kernel returned invalid Binder identity for {name}")
                node = binderfs / name
                if not node.is_char_device() or node.is_symlink():
                    die(f"Binder node was not created: {name}")
                stat = node.stat()
                if os.major(stat.st_rdev) != major or os.minor(stat.st_rdev) != minor:
                    die(f"Binder device numbers differ: {name}")

    for name in NODES:
        node = binderfs / name
        if not node.is_char_device() or node.is_symlink():
            die(f"Binder node has an unexpected type: {name}")
        if os.major(node.stat().st_rdev) == 0:
            die(f"Binder node has an invalid device number: {name}")
        node.chmod(0o666)
        os.symlink(f"binderfs/{name}", root / "dev" / name)

    entries = {path.name for path in binderfs.iterdir()}
    created = sorted(entries - infrastructure)
    if created != sorted(NODES) or entries != infrastructure | set(NODES):
        die(f"unexpected BinderFS contents: {sorted(entries)}")
    print("PRIVATE_BINDERFS_READY=true", file=sys.stderr)
    print("PRIVATE_BINDER_DEVICE_COUNT=3", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
