# SPDX-License-Identifier: Apache-2.0
"""Pixel 9 Pro Fold: the build inputs must agree with the device manifests."""
import hashlib
import json
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DEVICE = ROOT / "devices/google/comet"
BUILD = ROOT / "scripts/mobile/pixel9-fold/build-boot-image.sh"
FRAGMENT = ROOT / "config/kernel/pixel9fold.fragment"
CONFIG = ROOT / "config/kernel/pixel9fold.config"
LOCK = ROOT / "config/kernel/pixel9fold-source.json"
INITRAMFS = ROOT / "config/mobile/comet-initramfs"
MANIFEST = ROOT / "config/mobile/comet-initramfs.manifest"


def toml(name):
    return tomllib.loads((DEVICE / name).read_text())


class GoogleComet(unittest.TestCase):
    def test_build_cmdline_is_boot_toml(self):
        boot = toml("boot.toml")["cmdline"]
        line = re.search(r'^CMDLINE="([^"]*)"', BUILD.read_text(), re.M).group(1).split()
        generated = [a for a in line if a.split("=", 1)[0] in boot["generated"]]
        self.assertEqual([a.split("=", 1)[0] for a in generated], boot["generated"])
        self.assertEqual([a for a in line if a not in generated], boot["args"])

    def test_boot_image_size_and_rollback(self):
        boot, text = toml("boot.toml"), BUILD.read_text()
        self.assertIn(f"BOOT_BYTES={boot['partition_size']}", text)
        self.assertIn(f"ROLLBACK={boot['avb']['rollback_index']}", text)

    def test_fragment_holds_in_resolved_config(self):
        resolved = set(CONFIG.read_text().splitlines())
        for line in FRAGMENT.read_text().splitlines():
            if line.startswith("CONFIG_") or re.fullmatch(r"# CONFIG_\w+ is not set", line):
                self.assertIn(line, resolved)
        for name in ("CMDLINE", "INITRAMFS_SOURCE", "EXTRA_FIRMWARE_DIR"):
            self.assertIn(f'CONFIG_{name}=""', resolved)

    def test_lock_patches_are_the_series_in_order(self):
        lock = json.loads(LOCK.read_text())
        series = sorted((ROOT / "patches/linux-zumapro/comet").glob("*.patch"))
        self.assertEqual([r["path"] for r in lock["patches"]], [str(p.relative_to(ROOT)) for p in series])
        self.assertEqual(lock["device_tree"], toml("device.toml")["kernel"]["dtb"])

    def test_initramfs_manifest(self):
        branch = {p.relative_to(INITRAMFS).as_posix() for p in INITRAMFS.rglob("*") if p.is_file()}
        listed, firmware = set(), {}
        for line in MANIFEST.read_text().splitlines():
            if line.startswith("#"):
                continue
            kind, mode, value, path, origin = line.split("\t")
            if kind == "F" and origin == "branch":
                listed.add(path)
                self.assertEqual(hashlib.sha256((INITRAMFS / path).read_bytes()).hexdigest(), value, path)
            if origin.startswith("vendor:"):
                firmware[path.removeprefix("luma-fold/firmware/")] = value
        self.assertEqual(listed, branch)
        rows = [r.split("\t") for r in (DEVICE / "firmware.manifest").read_text().splitlines()[1:]]
        self.assertEqual({r[3]: r[2] for r in rows if r[0] == "F"}, firmware)

    def test_no_builder_keys_in_branch(self):
        self.assertFalse((INITRAMFS / "luma-fold/usbnet/authorized_keys").exists())


if __name__ == "__main__":
    unittest.main()
