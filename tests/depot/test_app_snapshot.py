# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("snapshot", Path(__file__).resolve().parents[2] / "scripts/depot/validate-app-snapshot.py")
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)
APP = "org.gnome.Nautilus"
REF = f"app/{APP}/x86_64/beta"
DEBUG = f"runtime/{APP}.Debug/x86_64/beta"
BASE = f"[Application]\nname={APP}\n"
DECL = f"[Extension {APP}.Debug]\ndirectory=lib/debug\nautodelete=true\nno-autodownload=true\n[Build]\nbuilt-extensions={APP}.Debug;\n"
EXT = f"[Runtime]\nname={APP}.Debug\n[ExtensionOf]\nref={REF}\n"

class SnapshotTests(unittest.TestCase):
    def validate(self, refs=None, app=BASE+DECL, debug=EXT):
        contents = {REF: ("a"*64, app), DEBUG: ("b"*64, debug)}
        return snapshot.validate(APP, "x86_64", refs or [REF, DEBUG], contents.__getitem__)
    def test_exact_real_builder_companion(self):
        self.assertEqual(self.validate(), [{"ref": REF, "source_commit": "a"*64}, {"ref": DEBUG, "source_commit": "b"*64}])
    def test_app_without_debug_and_catalogue(self):
        self.assertEqual(len(self.validate([REF, "appstream/x86_64", "appstream2/x86_64"], BASE)), 1)
    def test_foreign_wrong_arch_branch_and_other_runtime(self):
        for ref in (DEBUG.replace(APP, "org.evil.Other"), DEBUG.replace("x86_64", "aarch64"), DEBUG.replace("beta", "stable"), "runtime/org.projectluma.Sdk/x86_64/44", f"runtime/{APP}.Locale/x86_64/beta"):
            with self.subTest(ref=ref), self.assertRaises(ValueError): self.validate([REF, ref])
    def test_duplicate_multiple_app_and_unmaintained_branch(self):
        for refs in ([REF, REF], [REF, REF.replace("beta", "stable")], [REF.replace("beta", "future")], [REF.replace("beta", "extra/beta")]):
            with self.subTest(refs=refs), self.assertRaises(ValueError): self.validate(refs)
    def test_missing_changed_or_injected_debug_declaration(self):
        for app in (BASE, BASE+DECL.replace("lib/debug", "lib/office"), BASE+DECL.replace("true", "false", 1), BASE+DECL.replace("[Build]", "extra=unsafe\n[Build]")):
            with self.subTest(app=app), self.assertRaises(ValueError): self.validate(app=app)
    def test_absent_duplicate_or_foreign_built_extension(self):
        for app in (BASE+DECL.replace(f"{APP}.Debug;", ""), BASE+DECL.replace(f"{APP}.Debug;", f"{APP}.Debug;{APP}.Debug;"), BASE+DECL.replace(f"built-extensions={APP}.Debug", "built-extensions=org.evil.Other.Debug")):
            with self.subTest(app=app), self.assertRaises(ValueError): self.validate(app=app)
    def test_extension_of_and_runtime_identity_are_exact(self):
        for debug in (EXT.replace(REF, REF.replace("beta", "stable")), EXT.replace(f"name={APP}.Debug", "name=org.evil.Other.Debug"), EXT+"[Context]\nfilesystems=host;\n", EXT.replace("ref=", "foreign=1\nref=")):
            with self.subTest(debug=debug), self.assertRaises(ValueError): self.validate(debug=debug)
    def test_metadata_duplicates_oversize_and_bad_app_identity(self):
        for app in (BASE+BASE, BASE.replace(APP, "org.evil.Other"), BASE+"#"*1048576):
            with self.subTest(app=app), self.assertRaises((ValueError, snapshot.configparser.Error)): self.validate(app=app)
    def test_bad_commit_and_excessive_refs(self):
        with self.assertRaises(ValueError): snapshot.validate(APP,"x86_64",[REF],lambda _: ("g"*64, BASE))
        with self.assertRaises(ValueError): snapshot.validate(APP,"x86_64",[REF]*257,lambda _: ("a"*64, BASE))

if __name__ == "__main__": unittest.main()
