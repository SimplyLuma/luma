# SPDX-License-Identifier: Apache-2.0
"""Device kit manifests, flash steps and the sparse images that carry them."""
import importlib.util
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("build_device_kit", ROOT / "scripts/devices/build-device-kit.py")
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)


def fp6():
    with (ROOT / "devices/fairphone/fp6/device.toml").open("rb") as f:
        return tomllib.load(f)


class KitManifest(unittest.TestCase):
    def test_fp6_kit_document_lists_its_repository(self):
        device = fp6()
        names = device["device_repo"]["install"] + device["device_repo"]["replace"] + ["libcxx"]
        doc = kit.kit_document(device, names, "20261107.1")
        self.assertEqual(doc["device"], "fairphone/fp6")
        self.assertEqual(doc["compatible"], ["fairphone,fp6"])
        self.assertEqual(doc["repo"]["id"], "luma-fairphone-fp6")
        self.assertIn("libcxx", doc["packages"])
        with self.assertRaises(kit.KitError):
            kit.kit_document(device, names[1:], "1")

    def test_fp6_flash_steps(self):
        files = {"userdata": "u.sparse", "kit": "k.sparse", "boot": "b.img", "dtbo": "dtbo-blank.img"}
        self.assertEqual(kit.flash_steps(fp6(), files), [
            "fastboot flash userdata u.sparse", "fastboot flash userdata k.sparse",
            "fastboot flash dtbo_a dtbo-blank.img", "fastboot flash dtbo_b dtbo-blank.img",
            "fastboot flash boot_a b.img", "fastboot flash boot_b b.img", "fastboot boot b.img"])

    def test_flash_steps_refuse_preserved_partitions_and_missing_files(self):
        device = {"install": {"preserve": ["modemst1"]}, "kit": {"flash": ["flash modemst1 {boot}"]}}
        with self.assertRaises(kit.KitError):
            kit.flash_steps(device, {"boot": "b"})
        device = {"kit": {"flash": ["flash userdata {userdata}", "flash userdata {kit}"]}}
        with self.assertRaises(kit.KitError):
            kit.flash_steps(device, {"userdata": "u"})
        with self.assertRaises(kit.KitError):
            kit.flash_steps({"kit": {"flash": ["oem unlock"]}}, {})

    def test_boot_image_must_name_the_generic_filesystems(self):
        good = f"x pmos_root_uuid={kit.ROOT_UUID} pmos_boot_uuid={kit.BOOT_UUID} y".encode()
        kit.check_boot_image(good)
        with self.assertRaises(kit.KitError):
            kit.check_boot_image(good.replace(b"032D-57AD", b"1234-5678"))

    def test_userdata_builder_and_kit_agree_on_the_layout(self):
        text = (ROOT / "scripts/mobile/build-phone-userdata.sh").read_text()
        self.assertIn(f"root_uuid={kit.ROOT_UUID}", text)
        self.assertIn(f"boot_uuid={kit.BOOT_UUID}", text)
        self.assertIn("start=256, size=480MiB", text)
        self.assertEqual((kit.LUMABOOT_OFFSET, kit.LUMABOOT_SIZE), (256 * 4096, 480 << 20))
        self.assertNotIn("wireplumber", text)

    def test_userdata_image_grows_its_root_online(self):
        text = (ROOT / "scripts/mobile/build-phone-userdata.sh").read_text()
        check = (ROOT / "scripts/os/check-arm-disk-image.sh").read_text()
        unit = "ExecStart=/usr/lib/systemd/systemd-growfs /sysroot"
        self.assertIn(unit, text)
        self.assertIn(unit, check)
        self.assertIn("Before=local-fs.target luma-device-layer.service shutdown.target", text)
        self.assertIn('local-fs.target.wants/luma-grow-root.service', text)
        self.assertIn(': >"$deploy/etc/systemd/system/bootloader-update.service"', text)
        self.assertIn("sed -i 's/^enabled=1$/enabled=0/' \"$repo_file\"", text)
        self.assertIn("a non-Luma rpm repository is enabled", check)

    def test_login_screen_keeps_the_handheld_power_defaults(self):
        def keys(name):
            lines = (ROOT / "config/mobile" / name).read_text().splitlines()
            return {line for line in lines if line.startswith(("power-button-action=", "sleep-inactive-", "idle-delay="))}
        greeter = keys("luma-greeter-handheld.dconf")
        self.assertEqual(len(greeter), 4)
        self.assertEqual(greeter, keys("luma-shell-handheld.dconf"))
        spec = (ROOT / "packaging/rpm/luma-fp6-platform.spec").read_text()
        self.assertIn("%{_sysconfdir}/dconf/db/gdm.d/10-luma-handheld", spec)


class SparseImages(unittest.TestCase):
    def run_tool(self, *args):
        subprocess.run([sys.executable, *map(str, args)], check=True)

    def test_kit_sparse_writes_only_its_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bs = 4096
            disk = bytearray(os.urandom(bs * 8))
            (tmp / "generic.raw").write_bytes(disk[:bs * 2] + bytes(bs * 2) + disk[bs * 4:])
            self.run_tool(ROOT / "scripts/mobile/img2simg.py", tmp / "generic.raw", tmp / "generic.sparse")
            fs = os.urandom(bs) + bytes(bs) + os.urandom(bs)
            (tmp / "fs.raw").write_bytes(fs)
            self.run_tool(ROOT / "scripts/mobile/img2simg.py", tmp / "fs.raw", tmp / "kit.sparse", "--at", bs * 3)
            target = tmp / "userdata"
            target.write_bytes(os.urandom(bs * 10))  # a previous install
            self.run_tool(ROOT / "scripts/mobile/simg-apply.py", target, tmp / "generic.sparse", tmp / "kit.sparse")
            got = target.read_bytes()
            want = bytes((tmp / "generic.raw").read_bytes())
            want = want[:bs * 3] + fs + want[bs * 6:]
            self.assertEqual(got[:bs * 8], want)  # zeros overwrite, DONT_CARE keeps the generic image
            fresh = tmp / "fresh"
            self.run_tool(ROOT / "scripts/mobile/simg-apply.py", fresh, tmp / "generic.sparse", tmp / "kit.sparse",
                          "--create", bs * 8)
            self.assertEqual(fresh.read_bytes(), want)


if __name__ == "__main__":
    unittest.main()
