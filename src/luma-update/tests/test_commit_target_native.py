# SPDX-License-Identifier: Apache-2.0
"""Read real OSTree commit metadata in an isolated unsigned fixture repository.

This tests metadata decoding, not signature trust or live staging. The sysroot
lookup is redirected to the private fixture; the host repository is untouched.
"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    import gi
    gi.require_version("OSTree", "1.0")
    from gi.repository import Gio, GLib, OSTree
    from luma_update.rpmostree import RpmOstree
except (ImportError, ValueError):
    OSTree = None


@unittest.skipUnless(OSTree is not None, "native OSTree is unavailable")
class NativeCommitMetadata(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "repo"
        self.tree = self.root / "tree"
        self.tree.mkdir()
        self.repo = OSTree.Repo.new(Gio.File.new_for_path(str(self.path)))
        self.repo.create(OSTree.RepoMode.BARE_USER, None)
        repo = self.repo
        class FixtureSysroot:
            def load(self, _): return True
            def get_repo(self, _): return True, repo
        self.redirect = patch.object(OSTree.Sysroot, "new_default", return_value=FixtureSysroot())
        self.redirect.start()
        self.addCleanup(self.redirect.stop)

    def commit(self, *metadata):
        values = {}
        for option in metadata:
            key, value = option.split("=", 1)[1].split("=", 1)
            values[key] = GLib.Variant("s", value) if option.startswith("--add-metadata-string=") else GLib.Variant.parse(None, value, None, None)
        self.repo.prepare_transaction(None)
        tree = OSTree.MutableTree.new()
        self.repo.write_directory_to_mtree(Gio.File.new_for_path(str(self.tree)), tree, None, None)
        ok, root = self.repo.write_mtree(tree, None)
        self.assertTrue(ok)
        ok, checksum = self.repo.write_commit(None, "fixture", None,
                                             GLib.Variant("a{sv}", values), root, None)
        self.assertTrue(ok)
        self.repo.transaction_set_ref(None, "fixture", checksum)
        self.repo.commit_transaction(None)
        return checksum

    def test_reads_exact_immutable_commit_not_current_ref(self):
        target = self.commit("--add-metadata-string=org.projectluma.hardware-target=fairphone,fp6",
                             "--add-metadata-string=org.projectluma.architecture=aarch64")
        self.commit("--add-metadata-string=org.projectluma.hardware-target=generic",
                    "--add-metadata-string=org.projectluma.architecture=x86_64")
        self.assertEqual(RpmOstree().commit_target_metadata(target), {
            "org.projectluma.hardware-target": "fairphone,fp6",
            "org.projectluma.architecture": "aarch64"})

    def test_wrong_variant_types_are_not_coerced(self):
        target = self.commit("--add-metadata=org.projectluma.hardware-target=true",
                             "--add-metadata=org.projectluma.architecture=42")
        self.assertEqual(RpmOstree().commit_target_metadata(target), {
            "org.projectluma.hardware-target": True, "org.projectluma.architecture": 42})

    def test_missing_metadata_is_not_inferred_from_ref(self):
        self.assertEqual(RpmOstree().commit_target_metadata(self.commit()), {})

    def test_generic_explicit_wrong_type_is_not_legacy_keyless(self):
        from luma_update.hardware import require_commit_target
        revision = self.commit("--add-metadata=org.projectluma.architecture=42")
        with self.assertRaises(ValueError):
            require_commit_target(RpmOstree().commit_target_metadata(revision), "", "x86_64")
