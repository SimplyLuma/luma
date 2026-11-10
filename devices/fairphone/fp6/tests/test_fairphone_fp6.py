# SPDX-License-Identifier: Apache-2.0
"""Fairphone 6 rules that the shared device checks cannot express."""
import hashlib
import re
import tomllib
import unittest
from pathlib import Path

DEVICE = Path(__file__).resolve().parents[1]
ROOT = DEVICE.parents[2]


def toml(path):
    return tomllib.loads(path.read_text())


class FairphoneFp6(unittest.TestCase):
    def setUp(self):
        self.device = toml(DEVICE / "device.toml")
        self.boot = toml(DEVICE / "boot.toml")
        self.soc = toml(ROOT / "socs" / "qcom" / "milos" / "soc.toml")
        self.guide = (DEVICE / "INSTALL.md").read_text()

    def test_install_guide_is_the_published_alpha1_guide(self):
        # Verbatim copy of https://dl.simplyluma.com/mobile/fp6/alpha1/INSTALL.md.
        digest = hashlib.sha256((DEVICE / "INSTALL.md").read_bytes()).hexdigest()
        self.assertEqual(digest, "daa3f2dc5a655a162771eb41e798fd6c50f1a46dea37703f4d0b336d4e7b2416")

    def test_guide_flashes_the_published_boot_image(self):
        release = self.device["releases"][0]
        name = release["boot_image"].rsplit("/", 1)[1]
        self.assertIn(f"fastboot flash boot_a {name}", self.guide)
        self.assertIn(f"fastboot flash boot_b {name}", self.guide)

    def test_first_boot_is_fastboot_boot_so_the_modem_store_survives(self):
        # A normal first restart lets the bootloader wipe modemst (the IMEI).
        commands = re.findall(r"^fastboot .*$", self.guide, re.M)
        self.assertTrue(commands[-1].startswith("fastboot boot "), commands[-1])
        self.assertNotIn("fastboot reboot\n```", self.guide)

    def test_guide_keeps_modemst_and_never_touches_identity(self):
        self.assertIn("/erase modemst/d", self.guide)
        self.assertLessEqual({"modemst1", "modemst2"}, set(self.device["install"]["preserve"]))
        self.assertNotRegex(self.guide, r"fastboot\s+(flash|erase)\s+(modemst|fsg|fsc|persist)")

    def test_blank_dtbo_matches_the_guide(self):
        dtbo = self.boot["dtbo"]
        self.assertEqual(dtbo["policy"], "blank")
        self.assertIn(f"head -c {dtbo['size']} /dev/zero > dtbo-blank.bin", self.guide)
        for partition in dtbo["partitions"]:
            self.assertIn(f"fastboot flash {partition} dtbo-blank.bin", self.guide)

    def test_boot_partition_fits_the_image(self):
        self.assertEqual(self.boot["partition_size"], 96 * 1024 * 1024)
        self.assertEqual(self.boot["avb"]["vbmeta"], "untouched")
        self.assertIn("never change `vbmeta`", self.guide)

    def test_selinux_is_enforcing(self):
        self.assertIn("enforcing=1", self.boot["cmdline"]["args"])
        self.assertTrue(self.device["kernel"]["release"].endswith(".selinux1"))

    def test_fuse_is_built_in(self):
        # rpm-ostree runs package scripts under rofiles-fuse, before
        # kernel-modules-luma-fp6 (which would carry fuse.ko) is layered.
        fragments = self.device["kernel"]["config_fragments"]
        text = "".join((ROOT / f).read_text() for f in fragments)
        self.assertRegex(text, r"(?m)^CONFIG_FUSE_FS=y$")
        spec = (ROOT / "packaging" / "rpm" / "kernel-modules-luma-fp6.spec").read_text()
        self.assertIn("test ! -e $m/kernel/fs/fuse/fuse.ko.zst", spec)

    def test_uses_the_shared_milos_kernel(self):
        self.assertEqual(self.device["soc"], "qcom/milos")
        self.assertEqual(self.soc["kernel"]["branch"], "milos")
        self.assertNotIn("branch", self.device["kernel"])

    def test_initramfs_overlay_sources_are_in_this_tree(self):
        # The third cpio archive in the boot image's ramdisk replaces
        # postmarketOS's /init_functions.sh with this file, byte for byte.
        initramfs = self.device["initramfs"]
        script = (ROOT / initramfs["init_functions"]).read_bytes()
        self.assertEqual(hashlib.sha256(script).hexdigest(), initramfs["init_functions_sha256"])
        self.assertIn(b"luma_verify_firmware_store", script)
        self.assertIn(self.device["firmware"]["store"].encode(), script)
        self.assertTrue((ROOT / "patches" / "ostree").is_dir())
        self.assertTrue((ROOT / "packaging" / "rpm" / "ostree-fp6.spec").is_file())

    def test_firmware_is_listed_not_shipped(self):
        rows = (DEVICE / "firmware.manifest").read_text().splitlines()
        device_files = [r.split("\t")[3] for r in rows[1:] if r.startswith("F\t")]
        fp6 = [p for p in device_files if p.startswith(self.soc["firmware"]["directory"] + "/fairphone/fp6/")]
        self.assertEqual(len(device_files), self.device["firmware"]["files"])
        self.assertGreater(len(fp6), 100)
        blobs = [p for p in DEVICE.rglob("*") if p.suffix in (".mbn", ".mdt", ".bin", ".zst", ".jsn")]
        self.assertEqual(blobs, [])


if __name__ == "__main__":
    unittest.main()
