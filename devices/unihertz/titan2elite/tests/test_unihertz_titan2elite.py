# SPDX-License-Identifier: Apache-2.0
"""Unihertz Titan 2 Elite rules that the shared device checks cannot express."""
import hashlib
import json
import re
import struct
import subprocess
import sys
import tempfile
import tomllib
import zlib
import unittest
from pathlib import Path

DEVICE = Path(__file__).resolve().parents[1]
ROOT = DEVICE.parents[2]


def toml(path):
    return tomllib.loads(path.read_text())


class UnihertzTitan2Elite(unittest.TestCase):
    def setUp(self):
        self.device = toml(DEVICE / "device.toml")
        self.boot = toml(DEVICE / "boot.toml")
        self.soc = toml(ROOT / "socs" / "mediatek" / "mt6878" / "soc.toml")
        self.guide = (DEVICE / "INSTALL.md").read_text()

    def test_guide_writes_only_boot_a(self):
        writes = re.findall(r"^fastboot\s+(?:flash|erase|format)(?::\S+)?\s+(\S+)", self.guide, re.M)
        self.assertEqual(writes, ["boot_a"])
        self.assertEqual(self.boot["partition"], "boot_a")

    def test_identity_partitions_are_preserved(self):
        identity = {"nvram", "nvdata", "nvcfg", "protect1", "protect2", "proinfo"}
        self.assertLessEqual(identity, set(self.device["install"]["preserve"]))
        for partition in identity:
            self.assertIn(f"`{partition}`", self.guide)

    def test_bootloader_checked_partitions_stay_stock(self):
        self.assertEqual(self.boot["dtbo"]["policy"], "stock")
        self.assertEqual(self.boot["avb"]["vbmeta"], "untouched")
        self.assertEqual(self.boot["avb"]["footer"], "hash")
        self.assertLessEqual({"dtbo_a", "vbmeta_a", "vendor_boot_a"}, set(self.device["install"]["preserve"]))

    def test_kernel_matches_its_source_pins(self):
        pins = json.loads((ROOT / "config" / "mobile" / "titan2e-kernel-source.json").read_text())
        kernel = self.device["kernel"]
        self.assertEqual(pins["release"], kernel["release"])
        self.assertEqual(pins["dtb"], kernel["dtb"])
        self.assertEqual(pins["config"], kernel["config"])
        self.assertEqual(pins["boot_partition_size"], self.boot["partition_size"])
        self.assertEqual(len(list((ROOT / pins["patches"]).glob("[0-9][0-9][0-9][0-9]-*.patch"))), 53)

    def test_kernel_config_has_no_bring_up_options(self):
        config = (ROOT / self.device["kernel"]["config"]).read_text()
        self.assertIn('CONFIG_INITRAMFS_SOURCE=""', config)
        self.assertIn("CONFIG_TITAN2E_BUILTIN_DTB=y", config)
        for option in ("CONFIG_NETCONSOLE=", "CONFIG_TITAN2E_BRINGUP_LED="):
            self.assertNotIn(option, config)

    def test_initramfs_only_starts_the_microsd_root(self):
        pins = json.loads((ROOT / "config" / "mobile" / "titan2e-kernel-source.json").read_text())
        init = (ROOT / pins["initramfs"]["init"]).read_text()
        self.assertIn("switch_root /sysroot /usr/lib/systemd/systemd", init)
        self.assertIn("ostree-prepare-root", init)
        for dev_only in ("telnetd", "usb_gadget", "devmem", "ttyGS0", "udhcpd"):
            self.assertNotIn(dev_only, init)

    def test_cmdline_matches_the_built_in_device_tree(self):
        patch = (ROOT / "patches" / "linux-mt6878" /
                 "0012-arm64-dts-mediatek-add-the-Unihertz-Titan-2-Elite.patch").read_text()
        bootargs = re.search(r'bootargs = "([^"]*)"', patch).group(1)
        self.assertEqual(bootargs.split(), self.boot["cmdline"]["args"])

    def test_firmware_is_listed_not_shipped(self):
        rows = (DEVICE / "firmware.manifest").read_text().splitlines()
        files = [r.split("\t")[3] for r in rows[1:] if r.startswith("F\t")]
        self.assertEqual(len(files), self.device["firmware"]["files"])
        self.assertNotIn("WIFI", files)
        self.assertNotIn("BT_Addr", files)
        fw = (ROOT / "src" / "luma-titan2e-platform" / "luma-titan2e-connsys-fw").read_text()
        self.assertIn(f"DEST = '{self.device['firmware']['store']}'", fw)

    def test_public_file_manifest_matches_the_tree(self):
        for entry in json.loads((DEVICE / "public-file-manifest.json").read_text()):
            data = (ROOT / entry["path"]).read_bytes()
            self.assertEqual(len(data), entry["bytes"], entry["path"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"], entry["path"])

    def test_boot_android_switches_the_active_slot_to_b(self):
        tool = ROOT / "src" / "luma-titan2e-platform" / "luma-titan2e-boot-android"
        block = bytearray(32)
        block[0:4] = b"_a\0\0"
        struct.pack_into("<IB", block, 4, 0x42414342, 1)
        struct.pack_into("<HH", block, 12, 0x00FF, 0x008E)  # a: prio 15 ok; b: prio 14 ok
        struct.pack_into("<I", block, 28, zlib.crc32(block[:28]))
        with tempfile.NamedTemporaryFile() as misc:
            misc.write(bytes(4096))
            misc.seek(2048)
            misc.write(block)
            misc.flush()
            subprocess.run([sys.executable, str(tool), "--device", misc.name, "--no-reboot"],
                           check=True, capture_output=True)
            misc.seek(2048)
            out = misc.read(32)
        self.assertEqual(out[0:4], b"_b\0\0")
        a, b = struct.unpack_from("<HH", out, 12)
        self.assertEqual((a & 0x0F, b & 0x0F), (14, 15))
        self.assertEqual((a & ~0x0F, b & ~0x0F), (0x00F0, 0x0080))
        self.assertEqual(struct.unpack_from("<I", out, 28)[0], zlib.crc32(out[:28]))

    def test_boot_android_leaves_an_invalid_block_alone(self):
        tool = ROOT / "src" / "luma-titan2e-platform" / "luma-titan2e-boot-android"
        with tempfile.NamedTemporaryFile() as misc:
            misc.write(bytes(4096))
            misc.flush()
            run = subprocess.run([sys.executable, str(tool), "--device", misc.name, "--no-reboot"],
                                 capture_output=True, text=True)
            misc.seek(0)
            self.assertEqual(misc.read(), bytes(4096))
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("no valid boot-control block", run.stderr)

    def test_guide_explains_both_ways_between_luma_and_android(self):
        self.assertIn("sudo luma-titan2e-boot-android", self.guide)
        self.assertIn("fastboot set_active a", self.guide)
        spec = (ROOT / "packaging" / "rpm" / "luma-titan2e-platform.spec").read_text()
        self.assertIn("%{_sbindir}/luma-titan2e-boot-android", spec)

    def test_no_modem_services_ship(self):
        spec = (ROOT / "packaging" / "rpm" / "luma-titan2e-platform.spec").read_text()
        for name in ("luma-modemd", "luma-titan2e-speech", "VoiceCall", "lpac", "esim"):
            self.assertNotIn(name, spec)
        platform = ROOT / "src" / "luma-titan2e-platform"
        for pattern in ("*modem*", "*speech*", "*VoiceCall*", "*esim*"):
            self.assertFalse(list(platform.glob(pattern)), pattern)


if __name__ == "__main__":
    unittest.main()
