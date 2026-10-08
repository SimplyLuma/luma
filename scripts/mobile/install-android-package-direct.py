#!/usr/bin/python3
"""Install one validated Android package set into a running diagnostic session.

This deliberately bypasses Luma's interactive-session launcher, not Android's
security boundary: Android PackageManager still performs one atomic signature,
identity, split, and native-library verification transaction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from luma_android.apk import (
    inspect_android_package,
    stage_android_package,
    staged_package_root,
)
from luma_android.engine import AndroidDeviceProfile, select_compatible_splits
from luma_android.receipts import write_install_receipt


MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024


def output(*arguments: str) -> str:
    completed = subprocess.run(
        [
            "/usr/bin/sudo",
            "-n",
            "/usr/bin/lxc-attach",
            "-P",
            "/var/lib/waydroid/lxc",
            "-n",
            "waydroid",
            "--clear-env",
            "--",
            *arguments,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout.strip()


def device_profile() -> AndroidDeviceProfile:
    abis = tuple(
        value
        for value in output("/system/bin/getprop", "ro.product.cpu.abilist").split(",")
        if value
    )
    density_text = output("/system/bin/wm", "density")
    density_values = [
        int(line.rsplit(":", 1)[1].strip())
        for line in density_text.splitlines()
        if line.startswith(("Physical density:", "Override density:"))
    ]
    locale = output("/system/bin/getprop", "persist.sys.locale") or output(
        "/system/bin/getprop", "ro.product.locale"
    )
    if not abis or not density_values or not locale:
        raise RuntimeError("Android returned an incomplete compatibility profile")
    return AndroidDeviceProfile(abis, density_values[-1], locale)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--sha256", required=True)
    arguments = parser.parse_args()

    package = arguments.package.expanduser()
    if sha256(package) != arguments.sha256.lower():
        raise RuntimeError("Android package hash differs from the authorized input")
    if output("/system/bin/getprop", "sys.boot_completed") != "1":
        raise RuntimeError("Android has not completed boot")

    inspection = inspect_android_package(package, MAX_PACKAGE_BYTES)
    payloads = stage_android_package(package, inspection)
    transaction: list[Path] = []
    try:
        profile = device_profile()
        selected_names = select_compatible_splits(inspection.apk_entries, profile)
        by_name = dict(zip(inspection.apk_entries, payloads, strict=True))
        selected_payloads = [by_name[name] for name in selected_names]

        shared = Path.home() / ".local/share/waydroid/data/waydroid_tmp"
        shared.mkdir(mode=0o700, parents=True, exist_ok=True)
        shared.chmod(0o700)
        transaction_id = uuid.uuid4().hex
        android_paths: list[str] = []
        for index, source in enumerate(selected_payloads):
            destination = shared / f"luma-{transaction_id}-{index:04d}.apk"
            with source.open("rb") as source_stream, destination.open("xb") as target:
                os.chmod(destination, 0o600)
                shutil.copyfileobj(source_stream, target, length=1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
            transaction.append(destination)
            android_paths.append(f"/data/waydroid_tmp/{destination.name}")

        install = subprocess.run(
            [
                "/usr/bin/sudo",
                "-n",
                "/usr/bin/lxc-attach",
                "-P",
                "/var/lib/waydroid/lxc",
                "-n",
                "waydroid",
                "--clear-env",
                "--",
                "/system/bin/pm",
                "install",
                "--user",
                "0",
                "-r",
                *android_paths,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if install.returncode != 0 or "Success" not in install.stdout:
            detail = install.stderr.strip() or install.stdout.strip() or "unknown error"
            raise RuntimeError(f"Android rejected the package transaction: {detail}")

        write_install_receipt(inspection, "installed")
        print(
            json.dumps(
                {
                    "abis": profile.abis,
                    "density": profile.density,
                    "locale": profile.locale,
                    "selected_parts": len(selected_names),
                    "sha256": inspection.sha256,
                    "status": "installed",
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        for path in transaction:
            path.unlink(missing_ok=True)
        if payloads:
            shutil.rmtree(staged_package_root(payloads), ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
