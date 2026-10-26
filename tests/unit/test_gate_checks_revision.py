# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("gate_checks_revision", ROOT / "scripts/os/lib/gate_checks_revision.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class GateChecksRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.write("tests/os/gate/browser-opens.js", "original check\n")
        self.write("src/app.py", "production code\n")
        self.source = self.commit()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], stderr=subprocess.DEVNULL).decode().strip()

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def commit(self):
        self.git("add", "--all")
        self.git("commit", "-qm", "Fixture")
        return self.git("rev-parse", "HEAD")

    def test_original_source_is_allowed(self):
        result = MODULE.admit(self.repo, self.source, self.source)
        self.assertEqual(result["changed_paths"], [])

    def test_fixture_only_descendant_records_exact_identity(self):
        self.write("tests/os/gate/browser-opens.js", "corrected fixture setup\n")
        checks = self.commit()
        result = MODULE.admit(self.repo, self.source, checks)
        self.assertEqual(result["source_revision"], self.source)
        self.assertEqual(result["checks_revision"], checks)
        self.assertEqual(result["changed_paths"], ["tests/os/gate/browser-opens.js"])
        self.assertFalse(result["candidate_payload_changed"])
        self.assertFalse(result["phase_reuse"])

    def test_keyring_fixture_closure_is_allowed(self):
        paths = ['scripts/os/verify-media.sh', 'tests/os/gate/browser-opens.sh',
                 'tests/os/gate/browser-fixture-keyring.py',
                 'tests/unit/test_browser_fixture_keyring.py']
        for path in paths: self.write(path, 'fixture setup\n')
        result = MODULE.admit(self.repo, self.source, self.commit())
        self.assertEqual(result['changed_paths'], sorted(paths))

    def test_keyring_fixture_does_not_admit_unrelated_ci(self):
        self.write('tests/os/gate/browser-fixture-keyring.py', 'fixture setup\n')
        self.write('.github/workflows/native-desktop.yml', 'changed CI\n')
        with self.assertRaisesRegex(ValueError, 'outside gate fixtures'):
            MODULE.admit(self.repo, self.source, self.commit())

    def test_product_change_rejected(self):
        self.write("src/app.py", "different production code\n")
        with self.assertRaisesRegex(ValueError, "outside gate fixtures"):
            MODULE.admit(self.repo, self.source, self.commit())

    def test_desktop_smoke_fixture_correction_is_allowed(self):
        self.write("tests/smoke/desktop.sh", "architecture-aware fixture\n")
        checks = self.commit()
        result = MODULE.admit(self.repo, self.source, checks)
        self.assertEqual(result["changed_paths"], ["tests/smoke/desktop.sh"])
        self.assertFalse(result["candidate_payload_changed"])
        self.assertFalse(result["phase_reuse"])

    def test_desktop_smoke_cannot_hide_changed_product_pins(self):
        self.write("tests/smoke/desktop.sh", "architecture-aware fixture\n")
        self.write("config/desktop/inputs.env", "different product pins\n")
        with self.assertRaisesRegex(ValueError, "outside gate fixtures"):
            MODULE.admit(self.repo, self.source, self.commit())

    def test_other_check_cannot_change(self):
        self.write("tests/os/image-contract.sh", "different image assertion\n")
        with self.assertRaisesRegex(ValueError, "outside gate fixtures"):
            MODULE.admit(self.repo, self.source, self.commit())

    def test_product_rename_cannot_hide_behind_fixture_path(self):
        self.git("mv", "src/app.py", "tests/os/gate/admin-prompt.sh")
        with self.assertRaisesRegex(ValueError, "outside gate fixtures"):
            MODULE.admit(self.repo, self.source, self.commit())

    def test_dirty_tracked_checkout_rejected(self):
        self.write("src/app.py", "uncommitted\n")
        with self.assertRaisesRegex(ValueError, "tracked changes"):
            MODULE.admit(self.repo, self.source, self.source)

    def test_driver_must_be_exact_checks_revision(self):
        self.write("tests/os/gate/browser-opens.js", "new fixture\n")
        self.commit()
        with self.assertRaisesRegex(ValueError, "exact checks revision"):
            MODULE.admit(self.repo, self.source, self.source)

    def test_revision_must_be_full_commit_not_ref(self):
        for revision in ("HEAD", self.source[:12], "--help", self.source + "\n"):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                MODULE.admit(self.repo, self.source, revision)

    def test_ancestor_checks_cannot_replace_newer_source(self):
        self.write("tests/os/gate/browser-opens.js", "new fixture\n")
        newer = self.commit()
        self.git("checkout", "--detach", self.source)
        with self.assertRaisesRegex(ValueError, "descend"):
            MODULE.admit(self.repo, newer, self.source)


if __name__ == "__main__":
    unittest.main()
