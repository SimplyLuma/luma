# SPDX-License-Identifier: Apache-2.0
"""Pixel 5a: the manifests, source lock, boot policy and install must agree."""
import hashlib
import json
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DEVICE = ROOT / "devices/google/barbet"
BUILD = ROOT / "scripts/mobile/build-barbet-boot-image.sh"
LOCK = ROOT / "config/kernel/barbet-7.3.json"
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

    def test_source_lock_pins_the_tree(self) -> None:
        device = toml("device.toml")
        lock = json.loads(LOCK.read_text())
        self.assertEqual(device["kernel"]["source_lock"],
                         "config/kernel/barbet-7.3.json")
        self.assertEqual(device["kernel"]["release"], lock["release"])
        self.assertEqual(device["kernel"]["dtb"], lock["device_tree"])
        config = ROOT / device["kernel"]["config"]
        self.assertEqual(
            hashlib.sha256(config.read_bytes()).hexdigest(),
            lock["base_config"]["sha256"],
        )
        self.assertEqual(len(lock["patches"]), 21)
        self.assertEqual([p["path"] for p in lock["patches"][14:]], [
            "patches/linux-barbet/7.3/0001-arm64-dts-qcom-add-google-barbet.patch",
            "patches/linux-barbet/7.3/0002-Input-add-ST-FTS-SPI-touchscreen.patch",
            "patches/linux-barbet/7.3/0003-remoteproc-add-the-sm7250-adsp-resource.patch",
            "patches/linux-barbet/7.3/0004-net-qrtr-and-barbet-modem.patch",
            "patches/linux-barbet/7.3/0005-ASoC-qcom-barbet-speaker.patch",
            "patches/linux-barbet/7.3/0006-ASoC-qcom-barbet-microphone.patch",
            "patches/linux-barbet/7.3/0007-drm-panel-add-the-barbet-sofef01-panel-mode.patch",
        ])

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
