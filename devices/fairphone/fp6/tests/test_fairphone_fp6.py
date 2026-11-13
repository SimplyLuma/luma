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

    def kit_release(self):
        return [release for release in self.device["releases"] if "kit_image" in release][-1]

    def test_install_guide_is_the_newest_kit_guide(self):
        # INSTALL.md is published beside the newest device kit and links its files.
        release = self.kit_release()
        self.assertTrue(release["guide"].endswith("/INSTALL.md"), release["guide"])
        for key in ("kit_image", "boot_image", "dtbo_image"):
            self.assertIn(release[key], self.guide)

    def test_guide_flashes_in_the_kit_order(self):
        release = self.kit_release()
        names = {
            "userdata": "luma-nightly-<build>-aarch64-disk.sparse",
            "kit": release["kit_image"].rsplit("/", 1)[1],
            "boot": release["boot_image"].rsplit("/", 1)[1],
            "dtbo": release["dtbo_image"].rsplit("/", 1)[1],
        }
        commands = "\n".join("fastboot " + line.format(**names) for line in self.device["kit"]["flash"])
        self.assertIn(commands, self.guide)

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
        self.assertTrue(self.kit_release()["dtbo_image"].endswith("/dtbo-blank.bin"))
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

    def test_sensor_stack_is_built(self):
        # Auto-rotate and automatic brightness read these through iio-sensor-proxy.
        patches = self.device["kernel"]["patches"]
        for name in ("0039-iio-fp6-add-motion-environment-sensor-stack.patch",
                     "0040-iio-imu-inv-icm42600-add-icm42630.patch"):
            self.assertIn(f"patches/linux-milos/{name}", patches)
        self.assertEqual(patches, sorted(patches))
        fragments = self.device["kernel"]["config_fragments"]
        text = "".join((ROOT / f).read_text() for f in fragments)
        for option in ("I2C_GPIO", "SPI_GPIO", "INV_ICM42600_SPI", "STK3310", "QMC6308", "DPS310"):
            self.assertRegex(text, rf"(?m)^CONFIG_{option}=m$")
        spec = (ROOT / "packaging" / "rpm" / "kernel-modules-luma-fp6.spec").read_text()
        self.assertIn("iio/light/stk3310", spec)
        build = (ROOT / "scripts" / "kernel" / "build-fp6.sh").read_text()
        self.assertIn("compose-fp6-dtb.sh", build)

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
