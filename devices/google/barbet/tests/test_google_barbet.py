# SPDX-License-Identifier: Apache-2.0
"""Pixel 5a: the manifests, kernel tag, boot policy and install must agree."""
import hashlib
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DEVICE = ROOT / "devices/google/barbet"
BUILD = ROOT / "scripts/mobile/build-barbet-boot-image.sh"
SOC = ROOT / "socs/qcom/sm7250/soc.toml"
INITRAMFS = ROOT / "config/mobile/barbet-initramfs"
INITRAMFS_MANIFEST = ROOT / "config/mobile/barbet-initramfs.manifest"


def toml(name: str) -> dict:
    return tomllib.loads((DEVICE / name).read_text())


class GoogleBarbet(unittest.TestCase):
    def test_identity(self) -> None:
        device = toml("device.toml")
        self.assertEqual(device["vendor"], "google")
        self.assertEqual(device["codename"], "barbet")
        self.assertEqual(device["soc"], "qcom/sm7250")
        self.assertEqual(device["tier"], "experimental")
        self.assertEqual(device["maintainers"], ["@Gibbz"])
        self.assertEqual(device["fastboot_product"], "barbet")

    def test_kernel_tag_and_fragment(self) -> None:
        device = toml("device.toml")
        kernel = device["kernel"]
        self.assertTrue(kernel.get("tag", "").startswith("luma-kernel-"),
                        "Blocked: an actual published Luma kernel tag is required")
        self.assertEqual(kernel["release"], "7.2.15")
        self.assertEqual(kernel["dtb"], "qcom/sm7250-google-barbet.dtb")
        self.assertEqual(kernel["config"], "luma/config/sm7250.fragment")
        self.assertNotIn("source_lock", kernel)
        self.assertNotIn("patches", kernel)

    def test_boot_cmdline_matches_the_builder(self) -> None:
        boot = toml("boot.toml")["cmdline"]
        line = re.search(r'^cmdline="([^"]*)"', BUILD.read_text(), re.M).group(1).split()
        generated = [a for a in line if a.split("=", 1)[0] in boot["generated"]]
        self.assertEqual([a.split("=", 1)[0] for a in generated], boot["generated"])
        self.assertEqual([a for a in line if a not in generated], boot["args"])

    def test_boot_policy(self) -> None:
        boot = toml("boot.toml")
        self.assertEqual(boot["partition"], "boot_b")
        self.assertEqual(boot["partition_size"], 100663296)
        self.assertEqual(boot["avb"]["footer"], "hash")
        self.assertEqual(boot["avb"]["algorithm"], "NONE")
        self.assertEqual(boot["avb"]["rollback_index"], 1783209600)
        self.assertEqual(boot["avb"]["vbmeta"], "untouched")
        self.assertEqual(boot["dtbo"]["policy"], "none")

    def test_install_writes_only_userdata_and_boot_b(self) -> None:
        text = (DEVICE / "INSTALL.md").read_text()
        device = toml("device.toml")
        for command in ("fastboot flash userdata userdata.img",
                        "fastboot flash boot_b boot.img",
                        "fastboot --set-active=b",
                        "fastboot reboot"):
            self.assertIn(command, text)
        for partition in device["install"]["preserve"]:
            pattern = re.compile(
                rf"fastboot\s+(flash|erase|format)(:\S+)?\s+{re.escape(partition)}\b"
            )
            self.assertIsNone(pattern.search(text), partition)

    def test_firmware_manifest(self) -> None:
        device = toml("device.toml")
        path = DEVICE / "firmware.manifest"
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         device["firmware"]["manifest_sha256"])
        lines = path.read_text().splitlines()
        header = lines[0].split("\t")
        self.assertEqual(header[0], "H")
        files = [line.split("\t") for line in lines[1:] if line.startswith("F\t")]
        self.assertEqual(int(header[1]), len(files))
        self.assertEqual(device["firmware"]["files"], len(files))
        self.assertEqual(device["firmware"]["store"],
                         "/var/lib/luma/device-firmware")

    def test_initramfs_manifest(self) -> None:
        listed = {}
        for line in INITRAMFS_MANIFEST.read_text().splitlines():
            if line.startswith("F\t"):
                kind, mode, digest, path, origin = line.split("\t")
                listed[path] = (mode, digest)
        actual = {p.relative_to(INITRAMFS).as_posix()
                  for p in INITRAMFS.rglob("*") if p.is_file()}
        self.assertEqual(set(listed), actual)
        for path, (mode, digest) in listed.items():
            file = INITRAMFS / path
            self.assertEqual(mode, oct(file.stat().st_mode & 0o777)[2:].zfill(4))
            self.assertEqual(digest, hashlib.sha256(file.read_bytes()).hexdigest())

    def test_no_release_infrastructure(self) -> None:
        device = toml("device.toml")
        self.assertNotIn("kit", device)
        self.assertNotIn("device_repo", device)
        self.assertNotIn("releases", device)
        self.assertEqual(device["updates"]["ostree_ref"], "luma/1/aarch64/nightly")

    def test_soc_offsets(self) -> None:
        soc = tomllib.loads(SOC.read_text())
        self.assertEqual(soc["boot"]["offsets"], {
            "base": 0x00000000, "kernel": 0x00008000, "ramdisk": 0x10000000,
            "second": 0x00000000, "tags": 0x00000100, "dtb": 0x18000000,
        })


if __name__ == "__main__":
    unittest.main()
