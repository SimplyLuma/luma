#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write the initramfs anchor of a device's firmware store.

usage: firmware-store-anchor.py DEVICE_DIR OUTPUT

DEVICE_DIR is devices/<vendor>/<codename>. OUTPUT (the initramfs file
/etc/luma-firmware-manifest.sha256) receives firmware.manifest's SHA-256 and a
newline, mode 0644. luma_verify_firmware_store (fp6-native-init-functions.sh)
accepts /var/lib/luma/device-firmware only when its luma-firmware.manifest has
that digest and the store holds exactly the rows it lists.

Refuses unless the manifest matches device.toml [firmware] manifest_sha256 and
its header, its F rows and [firmware] files agree on the file count.
"""
import hashlib
import os
import sys
import tomllib
from pathlib import Path


def anchor(device_dir: Path) -> str:
    firmware = tomllib.loads((device_dir / "device.toml").read_text()).get("firmware", {})
    if firmware.get("manifest") != "firmware.manifest":
        raise ValueError("device.toml [firmware] manifest must be firmware.manifest")
    data = (device_dir / "firmware.manifest").read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != firmware.get("manifest_sha256"):
        raise ValueError("firmware.manifest differs from device.toml [firmware] manifest_sha256")
    rows = data.decode().split("\n")
    if rows[-1] != "":
        raise ValueError("firmware.manifest must end with a newline")
    header = rows[0].split("\t")
    count = header[1] if len(header) == 5 else ""
    if header[0] != "H" or header[2:] != ["-", "-", "-"] or not count.isdigit() or count.startswith("0"):
        raise ValueError("firmware.manifest header must be H<TAB><file count><TAB>-<TAB>-<TAB>-")
    files = sum(row.startswith("F\t") for row in rows[1:])
    if not files == int(count) == firmware.get("files"):
        raise ValueError(f"file counts differ: header {count}, F rows {files}, "
                         f"device.toml {firmware.get('files')}")
    return digest


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    try:
        digest = anchor(Path(sys.argv[1]))
    except (OSError, ValueError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    output = Path(sys.argv[2])
    output.write_text(digest + "\n")
    os.chmod(output, 0o644)
    return 0


if __name__ == "__main__":
    sys.exit(main())
