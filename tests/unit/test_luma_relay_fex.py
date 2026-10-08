"""Admission, isolation and interruption tests for the optional ARM backend."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from luma_relay import fex
from luma_relay.errors import RelayError
from luma_relay.package import WindowsPackage, host_supports
from luma_relay.sandbox import sandbox_command


class FexTests(unittest.TestCase):
    def test_translation_is_explicit_and_cannot_admit_arm_pe(self):
        from dataclasses import replace
        package = WindowsPackage("/tmp/a.exe", "a.exe", 1024, "0" * 64,
                                 "exe", "x86_64", "open", False, False)
        self.assertFalse(host_supports(package, "aarch64")[0])
        self.assertTrue(host_supports(package, "aarch64", fex_available=True)[0])
        self.assertFalse(host_supports(replace(package, processor="arm64"),
                                      "aarch64", fex_available=True)[0])

    @mock.patch("luma_relay.fex.platform.machine", return_value="aarch64")
    @mock.patch("luma_relay.fex.os.sysconf", return_value=16384)
    def test_non_4k_kernel_is_refused(self, *_):
        self.assertFalse(fex.available())

    def test_bad_image_and_failed_extraction_never_publish_a_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "image.erofs"
            image.write_bytes(b"admitted test image")
            expected = hashlib.sha256(image.read_bytes()).hexdigest()
            profile = root / "fex.json"
            profile.write_text(json.dumps({"rootfs_sha256": "0" * 64}))
            with mock.patch.object(fex, "PROFILE", profile), \
                 mock.patch.object(fex, "IMAGE", image), \
                 mock.patch.object(fex, "available", return_value=True), \
                 mock.patch.object(fex, "_trusted_file"), \
                 mock.patch.object(fex, "cache_home", return_value=root / "cache"), \
                 mock.patch.object(fex.subprocess, "run") as run:
                with self.assertRaises(RelayError):
                    fex.prepare_rootfs()
                run.assert_not_called()
                profile.write_text(json.dumps({"rootfs_sha256": expected}))
                run.return_value.returncode = 1
                with self.assertRaises(RelayError):
                    fex.prepare_rootfs()
                self.assertFalse((root / "cache/luma-relay/fex" / expected).exists())
                self.assertFalse(list((root / "cache/luma-relay/fex").glob(".extract-*")))

    def test_rootfs_symlinks_are_not_trusted_as_package_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "link"
            path.symlink_to("/etc/passwd")
            with self.assertRaises(RelayError):
                fex._trusted_file(path)

    @mock.patch("luma_relay.fex.available", return_value=True)
    @mock.patch("luma_relay.fex.prepare_rootfs", return_value=Path("/cache/fex/admitted"))
    @mock.patch("luma_relay.sandbox.shutil.which", return_value="/usr/bin/bwrap")
    def test_translator_stays_inside_capsule_with_read_only_runtime(self, *_):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "installer.exe"
            source.touch()
            with mock.patch("luma_relay.sandbox.relay_bridge_bindings",
                            return_value=[(Path("/usr/lib/bridge.dll"), Path("/relay/bridge.dll"))]):
                argv, env = sandbox_command(root, ["/usr/bin/wine", "/source/input"],
                                            source=source, network=False)
            self.assertIn(["--ro-bind", "/cache/fex/admitted", "/relay-rootfs"],
                          [argv[i:i+3] for i in range(len(argv))])
            self.assertIn(["--ro-bind", str(source), "/source/input"],
                          [argv[i:i+3] for i in range(len(argv))])
            self.assertEqual(argv[argv.index("--") + 1], "/usr/bin/FEXInterpreter")
            self.assertNotIn("--share-net", argv)
            self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", env)
            self.assertEqual(env["HOME"], "/home/luma")
            self.assertEqual(env["FEX_SERVERSOCKETPATH"], "/relay/run/fex-server.sock")
            self.assertNotIn("/dev/fuse", argv)


if __name__ == "__main__":
    unittest.main()
