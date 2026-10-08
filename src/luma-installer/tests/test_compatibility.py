import unittest
from pathlib import Path
from unittest.mock import patch
from luma_installer.compatibility import dispatch_compatibility
from luma_installer.errors import InstallerError


class CompatibilityDispatch(unittest.TestCase):
    def test_missing_backends_do_not_launch_anything(self):
        for name in ("sample.APK", "sample.exe"):
            with self.subTest(name=name), patch.object(Path, "is_file", return_value=False), patch("luma_installer.compatibility.subprocess.Popen") as launch:
                with self.assertRaisesRegex(InstallerError, "not been run or installed"):
                    dispatch_compatibility(Path(name))
                launch.assert_not_called()

    def test_dispatch_uses_native_review_process_and_literal_filename(self):
        for suffix, target in (("apk", "luma-android-installer"), ("exe", "luma-relay-installer")):
            path = Path("/tmp/space ; package." + suffix)
            with patch.object(Path, "is_file", return_value=True), patch("luma_installer.compatibility.subprocess.Popen") as launch:
                self.assertTrue(dispatch_compatibility(path))
                launch.assert_called_once_with(["/usr/bin/" + target, str(path)], start_new_session=True, close_fds=True)

    def test_spawn_failure_is_presentable(self):
        with patch.object(Path, "is_file", return_value=True), patch("luma_installer.compatibility.subprocess.Popen", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(InstallerError, "could not start"):
                dispatch_compatibility(Path("/tmp/test.exe"))

    def test_native_package_stays_in_installer(self):
        with patch("luma_installer.compatibility.subprocess.Popen") as launch:
            self.assertFalse(dispatch_compatibility(Path("/tmp/test.rpm")))
            launch.assert_not_called()
