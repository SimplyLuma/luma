#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Restore the hash-locked, read-only QREL container inputs after a reboot.

This restores host loop mounts only. It never starts Android, exposes loop or
block devices to a container, changes modem/audio ownership, or overwrites an
existing mount. Any conflicting mount is a hard failure.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


IMAGES = {
    "system": (
        Path("/var/tmp/luma-stock-qrel1695-images/system_a.img"),
        Path("/var/tmp/luma-stock-qrel1695-system"),
        "c817290357023c4831e31aaf26ef2ec9ffc984c43c98ab6902deb19739746122",
    ),
    "vendor": (
        Path("/var/tmp/luma-stock-qrel1695-images/vendor_a.img"),
        Path("/var/tmp/luma-stock-qrel1695-vendor"),
        "4159a51642c7f6462012891197a237ad86768a1f55853c6ab3b38d6ff2b144b4",
    ),
    "system_ext": (
        Path("/var/tmp/luma-stock-qrel1695-images/system_ext_a.img"),
        Path("/var/tmp/luma-stock-qrel1695-system_ext"),
        "71e9c5b743ec957562788340ffae8cdfea758c586da858421950525079bacb09",
    ),
    "product": (
        Path("/var/tmp/luma-stock-qrel1695-images/product_a.img"),
        Path("/var/tmp/luma-stock-qrel1695-product"),
        "c5b458671c560dda0da70c8e2c5e9c1ea0e01b11d6f45c3f8009dc5964b15937",
    ),
    "odm": (
        Path("/var/tmp/luma-stock-qrel1695-images/odm_a.img"),
        Path("/var/tmp/luma-stock-qrel1695-odm"),
        "3d31463d172b485f866e25340887e36b96986bb5c2255bff9a6b7777277bb831",
    ),
}
APEX_ROOT = Path("/var/tmp/luma-stock-qrel1695-apex-runtime1")
APEX_INVENTORY = APEX_ROOT / "metadata/activated-apex-inventory.json"
APEX_INVENTORY_SHA256 = "4c0812666bb23251111a0f23b10db404a489c078e1ac7fdaa8975c1448a74869"
APEX_SEAL = APEX_ROOT / "metadata/HASHES.sha256"
MANAGER_ROOT = Path("/var/tmp/luma-stock-qrel1695-manager-boundary1")
MANAGERS = {
    "servicemanager": "69d4beaf4fb0cdd93ec9efd86ecc2fa3a06786024c6b6e475dc87434318e8552",
    "hwservicemanager": "03728b6e2d1b5de38aa281b4485767c677dd7eb5a623adfb326ca41cac5f8f91",
    "manifest.json": "22ff2b4887d7d17ba8a1f174ba43bc044382ae06f5a376bee588664f2bfde627",
}


def die(message: str) -> "None":
    raise RuntimeError(message)


def run(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_file(path: Path, expected: str) -> None:
    if not path.is_file() or path.is_symlink():
        die(f"missing or linked file: {path}")
    actual = sha256(path)
    if actual != expected:
        die(f"hash mismatch for {path}: {actual}")


def mount_options(target: Path) -> set[str] | None:
    result = subprocess.run(
        ("findmnt", "-rn", "-T", str(target), "-o", "TARGET,OPTIONS"),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode:
        return None
    fields = result.stdout.strip().split(maxsplit=1)
    if not fields or Path(fields[0]) != target:
        return None
    return set(fields[1].split(",")) if len(fields) == 2 else set()


def ensure_read_only_mount(source: Path, target: Path, added: list[Path]) -> None:
    options = mount_options(target)
    if options is not None:
        if "ro" not in options:
            die(f"existing mount is writable: {target}")
        device = run("findmnt", "-rn", "-T", str(target), "-o", "SOURCE", capture=True).stdout.strip()
        backing = run("losetup", "-n", "-O", "BACK-FILE", device, capture=True).stdout.strip()
        if Path(backing).resolve() != source.resolve():
            die(f"existing mount has different backing file: {target}")
        return
    target.mkdir(parents=True, mode=0o700, exist_ok=True)
    run("mount", "-o", "loop,ro,nosuid,nodev", str(source), str(target))
    added.append(target)
    options = mount_options(target)
    if options is None or "ro" not in options:
        die(f"new mount is not read-only: {target}")


def main() -> int:
    if os.geteuid() != 0:
        die("run as root on the Fairphone")
    model = Path("/sys/firmware/devicetree/base/model").read_bytes().rstrip(b"\0").decode()
    if model != "The Fairphone (Gen. 6)":
        die("device identity mismatch")
    cmdline = Path("/proc/cmdline").read_text()
    if "androidboot.slot_suffix=_b" not in cmdline.split():
        die("device is not running slot b")
    release = os.uname().release
    if release not in {"7.1.2-luma-fp-ims1", "7.1.2-luma-fp-ims-container1"}:
        die(f"unexpected kernel: {release}")

    for name, expected in MANAGERS.items():
        require_file(MANAGER_ROOT / name, expected)
    require_file(APEX_INVENTORY, APEX_INVENTORY_SHA256)
    if not APEX_SEAL.is_file() or APEX_SEAL.is_symlink():
        die("APEX seal missing or linked")
    run("sha256sum", "-c", str(APEX_SEAL))

    with APEX_INVENTORY.open(encoding="utf-8") as stream:
        inventory = json.load(stream)
    if len(inventory) != 41:
        die(f"APEX inventory count differs: {len(inventory)}")

    added: list[Path] = []
    try:
        for _, (image, target, expected) in IMAGES.items():
            require_file(image, expected)
            ensure_read_only_mount(image, target, added)
        seen: set[str] = set()
        for entry in inventory:
            name = entry["name"]
            version = entry["version"]
            if name in seen:
                die(f"duplicate APEX name: {name}")
            seen.add(name)
            package = f"{name}@{version}"
            payload = APEX_ROOT / "images" / f"{package}.img"
            target = APEX_ROOT / "mounts" / package
            if Path(entry["mountpoint"]) != target:
                die(f"APEX mountpoint differs: {package}")
            require_file(payload, entry["payload_sha256"])
            ensure_read_only_mount(payload, target, added)
    except BaseException:
        for target in reversed(added):
            subprocess.run(("umount", str(target)), check=False)
        raise

    print("STOCK_ANDROID_INPUTS_RESTORED=true")
    print("PARTITION_MOUNTS=5")
    print("APEX_MOUNTS=41")
    print("MANAGER_BOUNDARY=hash_verified")
    print("LOOP_CONTROL_EXPOSED_TO_CONTAINER=false")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
