# SPDX-License-Identifier: Apache-2.0
"""The FP6 kernel owns its Android GPU admission, and it cannot drift."""
import hashlib
import importlib.util
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

DEVICE = Path(__file__).resolve().parents[1]
ROOT = DEVICE.parents[2]
TOOL = ROOT / "scripts" / "kernel" / "fp6-android-gpu-admission.py"
spec = importlib.util.spec_from_file_location("fp6_android_gpu_admission", TOOL)
admission = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admission)

GPU_PATCH = """\
--- a/drivers/gpu/drm/msm/gmu.c
+++ b/drivers/gpu/drm/msm/gmu.c
@@ -1 +1 @@
-rate 200
+rate 650
"""
SENSOR_PATCH = """\
--- a/drivers/iio/sensor.c
+++ b/drivers/iio/sensor.c
@@ -1 +1 @@
-sensor 1
+sensor 2
"""
DEVICE_TOML = """\
[kernel]
tag = "luma-fp6-test-1"
release = "1"
config = "c"
dtb = "qcom/x.dtb"
patches = [{patches}]

[kernel.android_gpu]
required_patches = ["patches/gpu.patch"]
rejected_patches = []
qualified_gpu_inputs = ""
qualified_notes_sha256 = ""
kernel_inputs = ""
admitted = false
"""


def elf_with_notes(notes: bytes) -> bytes:
    strtab = b"\0.notes\0.shstrtab\0"
    data_offset = 64
    strtab_offset = data_offset + len(notes)
    shoff = strtab_offset + len(strtab)
    header = b"\x7fELF" + bytes([2, 1, 1]) + bytes(9)
    header += struct.pack("<HHIQQQIHHHHHH", 2, 183, 1, 0, 0, shoff, 0, 64, 0, 0, 64, 3, 2)

    def section(name, kind, offset, size):
        return struct.pack("<IIQQQQIIQQ", name, kind, 0, 0, offset, size, 0, 0, 1, 0)

    sections = section(0, 0, 0, 0) + section(1, 7, data_offset, len(notes)) + section(8, 3, strtab_offset, len(strtab))
    return header + notes + strtab + sections


class Fp6AndroidGpuAdmission(unittest.TestCase):
    def test_record_matches_the_kernel_inputs(self):
        # Changing the tag, config, fragments or patches needs `stamp`, which
        # rewrites `admitted` in the same diff.
        self.assertEqual(admission.check(), [])

    def test_current_kernel_lacks_the_gmu_fixes(self):
        kernel, record = admission.load()
        _, admitted, reasons = admission.expected(kernel, record)
        self.assertEqual(admitted, record["admitted"])
        for patch in record["required_patches"]:
            if patch not in kernel.get("patches", []):
                self.assertFalse(admitted)

    def test_runtime_ships_no_hand_edited_list(self):
        self.assertFalse((ROOT / "config" / "android" / "hardware" / "fairphone-fp6-kernel-notes.sha256").exists())
        spec_text = (ROOT / "packaging" / "rpm" / "kernel-modules-luma-fp6.spec").read_text()
        self.assertIn("fairphone-fp6-kernel-notes.d/%{kver}.sha256", spec_text)
        build = (ROOT / "scripts" / "kernel" / "build-fp6.sh").read_text()
        self.assertIn("fp6-android-gpu-admission.py", build)

    def test_notes_digest_is_the_notes_section(self):
        with tempfile.TemporaryDirectory() as directory:
            vmlinux = Path(directory) / "vmlinux"
            vmlinux.write_bytes(elf_with_notes(b"\x04\0\0\0build-id-notes"))
            self.assertEqual(admission.kernel_notes_sha256(vmlinux),
                             hashlib.sha256(b"\x04\0\0\0build-id-notes").hexdigest())

    @unittest.skipUnless(shutil.which("patch"), "needs patch(1)")
    def test_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "patches").mkdir()
            (root / "patches" / "gpu.patch").write_text(GPU_PATCH)
            (root / "patches" / "sensor.patch").write_text(SENSOR_PATCH)
            device = root / "device.toml"
            source = root / "src"
            (source / "drivers" / "gpu" / "drm" / "msm").mkdir(parents=True)
            (source / "drivers" / "gpu" / "drm" / "msm" / "gmu.c").write_text("rate 650\n")
            vmlinux = root / "vmlinux"
            vmlinux.write_bytes(elf_with_notes(b"kernel one"))
            output = root / "admission.sha256"

            # Inputs changed (record never stamped): CI and the build both fail.
            device.write_text(DEVICE_TOML.format(patches='"patches/gpu.patch"'))
            self.assertTrue(admission.check(device, root))
            with self.assertRaises(admission.AdmissionError):
                admission.emit(source, vmlinux, output, device, root)

            # Stamped but never qualified: built, no entry.
            admitted, _ = admission.stamp(device, root)
            self.assertFalse(admitted)
            self.assertEqual(admission.check(device, root), [])
            self.assertEqual(admission.emit(source, vmlinux, output, device, root)[0], False)
            self.assertNotRegex(output.read_text(), r"(?m)^[0-9a-f]{64}")

            # Qualified: the build writes the entry for the kernel it built.
            kernel, record = admission.load(device)
            manifest = root / "manifest.env"
            manifest.write_text(
                f"KERNEL_INPUTS={admission.kernel_inputs(kernel, root)}\n"
                f"GPU_INPUTS={admission.gpu_inputs(kernel, root)}\n"
                f"KERNEL_NOTES_SHA256={'a' * 64}\n"
            )
            self.assertTrue(admission.qualify(manifest, device, root)[0])
            digest = hashlib.sha256(b"kernel one").hexdigest()
            self.assertEqual(admission.emit(source, vmlinux, output, device, root), (True, digest))
            self.assertRegex(output.read_text(), rf"(?m)^{digest}  1$")

            # A non-GPU rebuild stays admitted once restamped.
            text = device.read_text().replace('\npatches = ["patches/gpu.patch"]',
                                             '\npatches = ["patches/gpu.patch", "patches/sensor.patch"]')
            device.write_text(text)
            self.assertTrue(admission.check(device, root))
            self.assertTrue(admission.stamp(device, root)[0])

            # A GPU input change (new tag) falls out until qualified again.
            device.write_text(device.read_text().replace('tag = "luma-fp6-test-1"', 'tag = "luma-fp6-test-2"'))
            self.assertFalse(admission.stamp(device, root)[0])

            # A tree that does not really carry the required patch is refused.
            device.write_text(device.read_text().replace('tag = "luma-fp6-test-2"', 'tag = "luma-fp6-test-1"'))
            self.assertTrue(admission.stamp(device, root)[0])
            (source / "drivers" / "gpu" / "drm" / "msm" / "gmu.c").write_text("rate 200\n")
            with self.assertRaises(admission.AdmissionError):
                admission.emit(source, vmlinux, output, device, root)


if __name__ == "__main__":
    unittest.main()
