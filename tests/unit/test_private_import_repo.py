# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/os/lib/private_import_repo.py"
SPEC = importlib.util.spec_from_file_location("private_import_repo", MODULE_PATH)
assert SPEC and SPEC.loader
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class PrivateImportAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.parent = pathlib.Path(self.directory.name)
        self.cache = self.parent / "import"
        self.cache.mkdir()
        (self.cache / "objects").write_text("cached\n")
        receipt = {"device": 1, "inode": 2}
        (self.cache / helper.MARKER).write_text(json.dumps(receipt))
        self.addCleanup(self.directory.cleanup)
        patch = mock.patch.object(helper.os, "getuid", return_value=0)
        patch.start()
        self.addCleanup(patch.stop)

    def refuse_then_create(self, path):
        if path.exists():
            raise ValueError("Existing private import creation receipt differs; refuse cached metadata")
        path.mkdir()
        return {"path": str(path)}

    def test_a_refused_cache_is_refused_by_default(self):
        with mock.patch.object(helper, "ensure_private", side_effect=self.refuse_then_create):
            with self.assertRaises(ValueError):
                helper.admit(self.cache)
        self.assertEqual((self.cache / "objects").read_text(), "cached\n")
        self.assertEqual(sorted(p.name for p in self.parent.iterdir()), ["import"])

    def test_a_refused_cache_moves_aside_unread_and_a_fresh_one_is_created(self):
        with mock.patch.object(helper, "ensure_private", side_effect=self.refuse_then_create):
            receipt = helper.admit(self.cache, quarantine_refused=True)
        self.assertEqual(receipt, {"path": str(self.cache)})
        self.assertEqual(list(self.cache.iterdir()), [])
        aside = [p for p in self.parent.iterdir() if p.name.startswith("import.quarantine-")]
        self.assertEqual(len(aside), 1)
        self.assertEqual((aside[0] / "objects").read_text(), "cached\n")

    def test_parent_checks_are_never_quarantined_away(self):
        with mock.patch.object(helper.os, "getuid", return_value=1000):
            with self.assertRaises(ValueError):
                helper.admit(self.cache, quarantine_refused=True)
        self.assertTrue((self.cache / "objects").exists())

    def test_a_missing_cache_is_created_without_quarantine(self):
        fresh = self.parent / "fresh"
        with mock.patch.object(helper, "ensure_private", side_effect=self.refuse_then_create):
            helper.admit(fresh, quarantine_refused=True)
        self.assertFalse(any(p.name.startswith("fresh.quarantine-") for p in self.parent.iterdir()))


if __name__ == "__main__":
    unittest.main()
