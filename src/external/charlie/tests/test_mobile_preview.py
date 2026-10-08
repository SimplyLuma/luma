# SPDX-License-Identifier: Apache-2.0
"""The development adapter uses the sealed toolkit and installed application."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

source = Path(__file__).resolve().parents[1] / "tools/mobile-preview.py"
spec = importlib.util.spec_from_file_location("charlie_mobile_preview", source)
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


class MobilePreviewTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="charlie-preview-profile-")
        self.addCleanup(self.directory.cleanup)
        self.bundle = Path(self.directory.name)
        sdk = self.bundle / "shared/src/luma-platform/appkit/luma_appkit"
        sdk.mkdir(parents=True)
        (sdk / "__init__.py").touch()
        (self.bundle / "kit/usr/lib64").mkdir(parents=True)
        self.manifest = self.bundle / "manifest.json"
        self.manifest.write_text(json.dumps({"kit_commit": profile.KIT_COMMIT,
                                             "architecture": "aarch64"}))

    def test_source_application_is_not_injected(self):
        env = profile.preview_environment(self.bundle, {"PYTHONPATH": "/stale/source",
                                                      "XDG_DATA_HOME": "/person/data"})
        self.assertEqual(env["PYTHONPATH"], str(self.bundle / "shared/src/luma-platform/appkit"))
        self.assertEqual(env["XDG_DATA_HOME"], "/person/data")
        self.assertEqual(profile.EXECUTABLE, "/usr/bin/org.projectluma.Charlie")
        self.assertEqual(json.loads(self.manifest.read_text())["kit_commit"], profile.KIT_COMMIT)

    def test_unapproved_toolkit_has_no_fallback(self):
        self.manifest.write_text(json.dumps({"kit_commit": "old", "architecture": "aarch64"}))
        with self.assertRaises(ValueError):
            profile.preview_environment(self.bundle, {})

    def test_missing_shared_sdk_has_no_fallback(self):
        (self.bundle / "shared/src/luma-platform/appkit/luma_appkit/__init__.py").unlink()
        with self.assertRaises(ValueError):
            profile.preview_environment(self.bundle, {})
