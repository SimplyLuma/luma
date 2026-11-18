# SPDX-License-Identifier: Apache-2.0
"""luma_verify_firmware_store checks /var/lib/luma/device-firmware against the
device's own firmware.manifest, anchored by digest in the initramfs."""
import hashlib
import importlib.util
import os
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/mobile/fp6-native-init-functions.sh"
TOOL = ROOT / "scripts/mobile/common/firmware-store-anchor.py"
SHELL = os.environ.get("LUMA_TEST_SH", "bash").split()
GENERIC = "/etc/luma-firmware-manifest.sha256"
LEGACY = "/etc/luma-fp6-firmware-manifest.sha256"
ROOTS = 'case "$root" in /|/sysroot) ;; *) return 1 ;; esac'
# The test is not root: report owner 0:0 for its own files, and safe root-owned
# directories above its temporary directory.
STAT = """
stat() {
	case "$3" in
		"$T"|"$T"/*) command stat "$@" | sed 's/^[0-9]*:[0-9]*:/0:0:/' ;;
		*) printf '%s\\n' "$2" | sed 's/%u/0/; s/%g/0/; s/%a/755/; s/%h/1/' ;;
	esac
}
"""

SPEC = importlib.util.spec_from_file_location("firmware_store_anchor", TOOL)
anchor_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(anchor_tool)


def manifest(files, header=None, links=()):
    rows = [f"H\t{len(files) if header is None else header}\t-\t-\t-", "D\t755\t-\tqcom\t-"]
    for name, data in sorted(files.items()):
        rows.append(f"F\t644\t{hashlib.sha256(data).hexdigest()}\tqcom/{name}\t-")
    for name, target in links:
        rows.append(f"L\t777\t-\tqcom/{name}\t{target}")
    return ("\n".join(rows) + "\n").encode()


class FirmwareStore(unittest.TestCase):
    FILES = {"a.mbn": b"a", "b.mbn": b"bb", "c.jsn": b"ccc"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(os.path.realpath(self.tmp.name))
        self.root = self.t / "sysroot"
        self.store = self.root / "var/lib/luma/device-firmware"
        (self.t / "etc").mkdir(mode=0o755)
        os.chmod(self.t / "etc", 0o755)
        text = SCRIPT.read_text()
        self.assertEqual(text.count(ROOTS), 1)
        text = text.replace(ROOTS, ROOTS.replace("/sysroot)", f"/sysroot|{self.root})"))
        text = text.replace(GENERIC, str(self.t) + GENERIC).replace(LEGACY, str(self.t) + LEGACY)
        (self.t / "init_functions.sh").write_text(text)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, data, files=None):
        for directory in (self.root, *reversed(self.store.parents[:4]), self.store, self.store / "qcom"):
            directory.mkdir(exist_ok=True)
            os.chmod(directory, 0o755)
        for name, content in (self.FILES if files is None else files).items():
            path = self.store / "qcom" / name
            path.write_bytes(content)
            os.chmod(path, 0o644)
        path = self.store / "luma-firmware.manifest"
        path.write_bytes(data)
        os.chmod(path, 0o644)

    def anchor(self, name, data):
        path = Path(str(self.t) + name)
        path.write_text(hashlib.sha256(data).hexdigest() + "\n")
        os.chmod(path, 0o644)

    def verify(self):
        script = f". {self.t}/init_functions.sh\n{STAT}\nluma_verify_firmware_store {self.root}\n"
        return subprocess.run([*SHELL, "-c", script], capture_output=True, text=True,
                              env={**os.environ, "T": str(self.t)}).returncode

    def test_device_manifest_with_the_generic_anchor(self):
        data = manifest(self.FILES)
        self.build(data)
        self.anchor(GENERIC, data)
        self.assertEqual(self.verify(), 0)

    def test_legacy_fp6_anchor_when_there_is_no_generic_one(self):
        data = manifest(self.FILES)
        self.build(data)
        self.anchor(LEGACY, data)
        self.assertEqual(self.verify(), 0)

    def test_generic_anchor_wins_over_the_legacy_one(self):
        data = manifest(self.FILES)
        self.build(data)
        self.anchor(LEGACY, data)
        self.anchor(GENERIC, b"another manifest")
        self.assertNotEqual(self.verify(), 0)

    def test_no_anchor_fails(self):
        self.build(manifest(self.FILES))
        self.assertNotEqual(self.verify(), 0)

    def test_symlinks_are_listed_rows(self):
        data = manifest(self.FILES, links=[("d.mbn", "a.mbn")])
        self.build(data)
        os.symlink("a.mbn", self.store / "qcom/d.mbn")
        self.anchor(GENERIC, data)
        self.assertEqual(self.verify(), 0)

    def test_count_must_match_the_header(self):
        for header in (2, 4, "03", "", "x", 1234567):
            with self.subTest(header=header):
                data = manifest(self.FILES, header=header)
                self.build(data)
                self.anchor(GENERIC, data)
                self.assertNotEqual(self.verify(), 0)

    def test_changed_extra_or_missing_files_fail(self):
        data = manifest(self.FILES)
        self.anchor(GENERIC, data)
        self.build(data, {**self.FILES, "b.mbn": b"BB"})
        self.assertNotEqual(self.verify(), 0)
        self.build(data, {**self.FILES, "b.mbn": b"bb", "extra.bin": b"x"})
        self.assertNotEqual(self.verify(), 0)
        (self.store / "qcom/extra.bin").unlink()
        self.assertEqual(self.verify(), 0)
        (self.store / "qcom/c.jsn").unlink()
        self.assertNotEqual(self.verify(), 0)

    def test_writable_file_fails(self):
        data = manifest(self.FILES)
        self.build(data)
        self.anchor(GENERIC, data)
        os.chmod(self.store / "qcom/a.mbn", 0o664)
        self.assertNotEqual(self.verify(), 0)


class DeviceDeclaration(unittest.TestCase):
    def test_no_device_count_in_the_check(self):
        text = SCRIPT.read_text()
        start = text.index("luma_verify_firmware_store() {")
        body = text[start:text.index("\n}\n", start)]
        self.assertNotIn("267", body)

    def test_fp6_anchor_is_its_manifest(self):
        device = ROOT / "devices/fairphone/fp6"
        firmware = tomllib.loads((device / "device.toml").read_text())["firmware"]
        self.assertEqual(anchor_tool.anchor(device), firmware["manifest_sha256"])
        header = (device / "firmware.manifest").read_text().split("\n", 1)[0]
        self.assertEqual(header, "H\t267\t-\t-\t-")
        self.assertEqual(firmware["files"], 267)

    def test_every_device_has_an_anchor(self):
        for toml in sorted((ROOT / "devices").glob("*/*/device.toml")):
            with self.subTest(device=toml.parent.name):
                anchor_tool.anchor(toml.parent)

    def test_tool_refuses_a_count_that_disagrees(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = Path(tmp)
            data = manifest(FirmwareStore.FILES)
            (device / "firmware.manifest").write_bytes(data)
            toml = ('[firmware]\nmanifest = "firmware.manifest"\n'
                    f'manifest_sha256 = "{hashlib.sha256(data).hexdigest()}"\nfiles = {{}}\n')
            (device / "device.toml").write_text(toml.format(3))
            out = device / "anchor"
            result = subprocess.run(["python3", str(TOOL), str(device), str(out)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(out.read_text(), hashlib.sha256(data).hexdigest() + "\n")
            self.assertEqual(out.stat().st_mode & 0o777, 0o644)
            (device / "device.toml").write_text(toml.format(4))
            with self.assertRaises(ValueError):
                anchor_tool.anchor(device)
            data = manifest(FirmwareStore.FILES, header=4)
            (device / "firmware.manifest").write_bytes(data)
            (device / "device.toml").write_text(toml.replace(
                hashlib.sha256(manifest(FirmwareStore.FILES)).hexdigest(),
                hashlib.sha256(data).hexdigest()).format(4))
            with self.assertRaises(ValueError):
                anchor_tool.anchor(device)


if __name__ == "__main__":
    unittest.main()
