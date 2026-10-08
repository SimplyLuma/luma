"""Admission checks for the optional emulator compatibility bundle."""
import runpy
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
exporter = runpy.run_path(str(ROOT / "scripts/emulator/export-luma-bundle"))


class CompatibilityBundleTests(unittest.TestCase):
    def test_native_default_has_no_compatibility_engine(self):
        names = {role[2] for role in exporter["read_roles"](ROOT)}
        self.assertFalse(names & {"luma-android-runtime", "waydroid", "wine", "luma-relay"})

    def test_arm_never_receives_x86_runner(self):
        arm = {role[2] for role in exporter["compatibility_roles"]("aarch64")}
        x86 = {role[2] for role in exporter["compatibility_roles"]("x86_64")}
        self.assertIn("luma-android-runtime", arm)
        self.assertIn("luma-relay", arm)
        self.assertNotIn("wine-luma-relay-explorer", arm)
        self.assertIn("wine-luma-relay-explorer", x86)
        self.assertNotIn("luma-relay-fex", arm)

    def test_arm_translation_requires_its_explicit_profile(self):
        arm = {role[2] for role in exporter["compatibility_roles"]("aarch64", arm_windows=True)}
        self.assertIn("luma-relay-fex", arm)
        with self.assertRaises(SystemExit):
            exporter["compatibility_roles"]("x86_64", arm_windows=True)

    def test_software_video_rejects_unpinned_and_symlinked_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / "gralloc.default.so"
            (root / "build-info.json").write_text('{}')
            module.write_bytes(b"unreviewed allocator")
            with self.assertRaisesRegex(SystemExit, "tested source pin"):
                exporter["stage_software_video"](ROOT, root, root / "out")
            module.unlink()
            module.symlink_to(root / "build-info.json")
            with self.assertRaisesRegex(SystemExit, "regular source-built"):
                exporter["stage_software_video"](ROOT, root, root / "out")

    def test_missing_images_refused_before_bundle_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SystemExit):
                exporter["stage_android_images"](ROOT, Path(directory), "aarch64", Path(directory) / "out")

    def test_changed_and_symlinked_archives_refused(self):
        pin = ROOT / "config/android/images-aarch64.env"
        name = next(line.split("=", 1)[1] for line in pin.read_text().splitlines()
                    if line.startswith("LUMA_ANDROID_SYSTEM_FILENAME="))
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            (cache / name).write_bytes(b"incorrect image bytes")
            with self.assertRaises(SystemExit):
                exporter["stage_android_images"](ROOT, cache, "aarch64", cache / "out")
            (cache / name).unlink()
            (cache / name).symlink_to(pin)
            with self.assertRaises(SystemExit):
                exporter["stage_android_images"](ROOT, cache, "aarch64", cache / "out")


if __name__ == "__main__":
    unittest.main()
