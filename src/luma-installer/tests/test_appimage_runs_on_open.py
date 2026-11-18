import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from luma_installer import workflow


class AddedAppImageTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_DATA_HOME": self.home.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.records = Path(self.home.name) / "luma/installer/applications"
        self.records.mkdir(parents=True)

    def add(self, application_id, sha256, *, unpacked=True, kind="appimage"):
        root = Path(self.home.name) / "appimages" / application_id / "AppDir"
        root.mkdir(parents=True)
        if unpacked:
            (root / "AppRun").write_text("#!/bin/sh\n")
        (self.records / f"{application_id}.json").write_text(json.dumps(
            {"format": kind, "sha256": sha256, "root": str(root), "command": "/app/AppRun"}))

    def test_same_file_finds_its_added_copy(self):
        self.add("appimage-tool-aaaa", "a" * 64)
        self.add("appimage-other-bbbb", "b" * 64)
        record = workflow.added_appimage("a" * 64)
        self.assertEqual(record["application_id"], "appimage-tool-aaaa")

    def test_new_file_is_added_first(self):
        self.add("appimage-tool-aaaa", "a" * 64)
        self.assertIsNone(workflow.added_appimage("c" * 64))

    def test_missing_private_copy_is_added_again(self):
        self.add("appimage-tool-aaaa", "a" * 64, unpacked=False)
        self.assertIsNone(workflow.added_appimage("a" * 64))

    def test_other_formats_with_the_same_digest_are_not_reused(self):
        self.add("portable-tool-aaaa", "a" * 64, kind="portable")
        self.assertIsNone(workflow.added_appimage("a" * 64))


if __name__ == "__main__":
    unittest.main()
