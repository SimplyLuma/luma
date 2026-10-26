# SPDX-License-Identifier: Apache-2.0
"""tools/check-devices.py accepts the real tree and rejects each broken rule."""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("check_devices", ROOT / "tools" / "check-devices.py")
check_devices = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_devices)


@unittest.skipIf(check_devices.jsonschema is None, "jsonschema is not installed")
class DeviceTree(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for name in ("devices", "socs"):
            shutil.copytree(ROOT / name, self.root / name,
                            ignore=shutil.ignore_patterns("__pycache__"))
        (self.root / ".github").mkdir()
        shutil.copy(ROOT / ".github" / "CODEOWNERS", self.root / ".github" / "CODEOWNERS")
        for name in ("scripts/mobile/fp6-native-init-functions.sh", "config/mobile/fp6-ostree-source.json"):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / name, self.root / name)
        for name in ("config/kernel", "patches/linux-surface-pro12"):
            shutil.copytree(ROOT / name, self.root / name)
        self.fp6 = self.root / "devices" / "fairphone" / "fp6"

    def tearDown(self):
        self.temporary.cleanup()

    def errors(self):
        checker = check_devices.Checker(self.root)
        checker.run()
        return checker.errors

    def edit(self, path, old, new):
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1))

    def assertRejected(self, needle):
        errors = self.errors()
        self.assertTrue(any(needle in e for e in errors), f"{needle!r} not in {errors}")

    def test_real_tree_is_valid(self):
        self.assertEqual(self.errors(), [])

    def test_userspace_requirements_reject_dnf_options(self):
        surface = self.root / 'devices/microsoft/sp12in/device.toml'
        self.edit(surface, 'alsa-ucm >= 1.2.16.1', '--disableexcludes=all')
        self.assertRejected('does not match')

    def test_userspace_requirements_reject_extra_commands(self):
        surface = self.root / 'devices/microsoft/sp12in/device.toml'
        self.edit(surface, 'alsa-ucm >= 1.2.16.1', 'alsa-ucm-conf; reboot')
        self.assertRejected('does not match')

    def test_unknown_manifest_key(self):
        self.edit(self.fp6 / "device.toml", 'tier = "supported"', 'tier = "supported"\ncolour = "green"')
        self.assertRejected("Additional properties are not allowed")

    def test_unknown_tier(self):
        self.edit(self.fp6 / "device.toml", 'tier = "supported"', 'tier = "official"')
        self.assertRejected("is not one of")

    def test_supported_device_needs_a_release(self):
        text = (self.fp6 / "device.toml").read_text()
        (self.fp6 / "device.toml").write_text(text.split("[[releases]]")[0])
        self.assertRejected("'releases' is a required property")

    def test_firmware_blob_is_refused(self):
        (self.fp6 / "adsp.mbn").write_bytes(b"\x7fELF")
        self.assertRejected("binary, archive or key file type")
        self.assertRejected("unexpected ['adsp.mbn']")

    def test_large_or_binary_file_is_refused(self):
        (self.fp6 / "tests" / "test_fairphone_fp6_data.py").write_bytes(b"x = 1\n\0")
        self.assertRejected("NUL bytes")

    def test_imei_shaped_number_is_refused(self):
        # A synthetic 15-digit number with a valid Luhn check digit.
        body = "00000000000001"
        number = next(body + d for d in "0123456789" if check_devices.luhn_valid(body + d))
        self.edit(self.fp6 / "device.toml", 'year = 2025', f'year = 2025\n# {number}')
        self.assertRejected("IMEI")

    def test_iccid_shaped_number_is_refused(self):
        self.edit(self.fp6 / "boot.toml", 'schema = 1', 'schema = 1\n# 89' + "0" * 17)
        self.assertRejected("ICCID")

    def test_firmware_manifest_hash_is_enforced(self):
        manifest = self.fp6 / "firmware.manifest"
        manifest.write_text(manifest.read_text() + "D\t755\t-\textra\t-\n")
        self.assertRejected("differs from firmware.manifest_sha256")

    def test_firmware_alias_cannot_escape_the_store(self):
        manifest = self.fp6 / "firmware.manifest"
        manifest.write_text(manifest.read_text() + "L\t777\t-\tescape\t../../etc/passwd\n")
        self.assertRejected("malformed 'L' row")

    def test_initramfs_script_must_match_the_recorded_hash(self):
        script = self.root / "scripts/mobile/fp6-native-init-functions.sh"
        script.write_text(script.read_text() + "# changed\n")
        self.assertRejected("differs from initramfs.init_functions_sha256")

    def test_initramfs_ostree_package_must_match_its_source_pins(self):
        self.edit(self.fp6 / "device.toml", "1.luma.2.fc44", "1.luma.9.fc44")
        self.assertRejected("initramfs.ostree_package does not match")

    def test_selinux_must_stay_enforcing(self):
        self.edit(self.fp6 / "boot.toml", '  "enforcing=1",\n', '')
        self.assertRejected("must contain 'enforcing=1'")

    def test_forbidden_argument_is_refused(self):
        self.edit(self.fp6 / "boot.toml", '"enforcing=1",', '"enforcing=1",\n  "selinux=0",')
        self.assertRejected("forbidden 'selinux=0'")

    def test_guide_may_not_erase_the_modem_store(self):
        self.edit(self.fp6 / "INSTALL.md", "fastboot flash dtbo_a", "fastboot erase modemst1\nfastboot flash dtbo_a")
        self.assertRejected("preserved partition 'modemst1'")

    def test_unknown_soc(self):
        self.edit(self.fp6 / "device.toml", 'soc = "qcom/milos"', 'soc = "qcom/sm8250"')
        self.assertRejected("has no socs/ directory")

    def test_path_and_manifest_must_agree(self):
        self.edit(self.fp6 / "device.toml", 'codename = "fp6"', 'codename = "fp5"')
        self.assertRejected("must match the directory path")

    def test_kernel_tag_names_the_device_and_release(self):
        self.edit(self.fp6 / "device.toml", 'tag = "luma-fp6-', 'tag = "luma-fp5-')
        self.assertRejected("kernel.tag must start with 'luma-fp6-'")

    def test_every_device_needs_an_owner(self):
        self.edit(self.root / ".github" / "CODEOWNERS", "/devices/fairphone/fp6/ @nmcmil", "")
        self.assertRejected("no entry for /devices/fairphone/fp6/")

    def test_device_needs_tests(self):
        shutil.rmtree(self.fp6 / "tests")
        self.assertRejected("missing ['tests']")


if __name__ == "__main__":
    unittest.main()
