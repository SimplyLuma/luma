# SPDX-License-Identifier: Apache-2.0
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from luma_android.apk import ApkValidationError, inspect_apk


def apk(abi=None):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        if abi:
            archive.writestr(f"lib/{abi}/library.so", b"library")
    return content.getvalue()


class PackageShapeTests(unittest.TestCase):
    def test_mislabeled_apkm_uses_nested_base_and_preserves_actual_abi(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "download.apk"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("info.json", "{}")
                archive.writestr("base.apk", apk())
                archive.writestr("split_config.arm64_v8a.apk", apk("arm64-v8a"))
            result = inspect_apk(path, 1024 * 1024)
            self.assertEqual(result.package_format, "apkm")
            self.assertEqual(result.apk_entries, ("base.apk", "split_config.arm64_v8a.apk"))
            self.assertEqual(result.native_abis, ("arm64-v8a",))

    def test_single_apk_with_bundle_suffix_keeps_single_payload_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "download.apkm"
            path.write_bytes(apk("x86_64"))
            result = inspect_apk(path, 1024 * 1024)
            self.assertEqual(result.package_format, "apk")
            self.assertEqual(result.apk_entries, ())
            self.assertEqual(result.native_abis, ("x86_64",))

    def test_unrelated_archive_does_not_become_an_android_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "download.apk"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("readme.txt", "not an Android app")
            with self.assertRaises(ApkValidationError):
                inspect_apk(path, 1024 * 1024)

    def test_mislabeled_bundle_still_rejects_invalid_nested_apks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "download.apk"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("base.apk", b"not an APK")
            with self.assertRaises(ApkValidationError):
                inspect_apk(path, 1024 * 1024)
