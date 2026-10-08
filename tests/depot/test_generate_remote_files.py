# SPDX-License-Identifier: Apache-2.0
"""Unit tests for scripts/depot/generate-remote-files.py: withdrawn apps.

Needs the ostree CLI (the Depot tools container has it); skipped without it.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/depot/generate-remote-files.py"


def ostree(repo, *args):
    return subprocess.run(["ostree", f"--repo={repo}", *args], check=True,
                          capture_output=True, text=True).stdout


@unittest.skipUnless(shutil.which("ostree"), "needs the ostree CLI")
class WithdrawnApps(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = self.tmp / "repo"
        ostree(self.repo, "init", "--mode=archive-z2")
        # This isolated fixture writes only a few KiB. Reserve an absolute
        # 16 MB, so a large build volume's percentage reserve does not make
        # the withdrawal tests fail before they exercise any application.
        ostree(self.repo, "config", "set", "core.min-free-space-size", "16MB")
        tree = self.tmp / "tree"
        (tree / "files").mkdir(parents=True)
        (tree / "files/hello").write_text("hello\n")
        (tree / "metadata").write_text("[Application]\nname=x\n")
        for app, extra in (("org.projectluma.Notes", []),
                           ("org.projectluma.StickyNotes", [])):
            ostree(self.repo, "commit", f"--branch=app/{app}/x86_64/beta",
                   f"--tree=dir={tree}", "--subject=build", *extra)
        # Withdrawal: a new commit on the same ref that carries the reason.
        ostree(self.repo, "commit", "--branch=app/org.projectluma.StickyNotes/x86_64/beta",
               f"--tree=dir={tree}", "--subject=end of life",
               "--add-metadata-string=ostree.endoflife=Withdrawn for now")
        self.key = self.tmp / "key.gpg"
        self.key.write_bytes(b"not a real key")
        self.site = self.tmp / "site"
        (self.site / "apps").mkdir(parents=True)
        # A descriptor left over from before the withdrawal.
        (self.site / "apps/org.projectluma.StickyNotes.beta.flatpakref").write_text("old\n")

    def run_script(self):
        return subprocess.run([sys.executable, str(SCRIPT), "--repo", str(self.repo),
                               "--site", str(self.site), "--gpg-key", str(self.key)],
                              check=True, capture_output=True, text=True).stdout

    def test_withdrawn_app_gets_no_descriptor_and_no_index_entry(self):
        summary = json.loads(self.run_script().strip().splitlines()[-1])
        self.assertEqual(summary["end_of_life"], ["org.projectluma.StickyNotes//beta"])
        apps = sorted(p.name for p in (self.site / "apps").glob("*.flatpakref"))
        self.assertEqual(apps, ["org.projectluma.Notes.beta.flatpakref", "org.projectluma.Notes.flatpakref"])
        index = json.loads((self.site / "apps/index.json").read_text())
        self.assertEqual([a["app_id"] for a in index["apps"]], ["org.projectluma.Notes"])

    def test_beta_is_default_and_nightly_requires_explicit_descriptor(self):
        tree = self.tmp / 'tree'
        for branch in ('stable', 'nightly'):
            ostree(self.repo, 'commit', f'--branch=app/org.projectluma.Notes/x86_64/{branch}',
                   f'--tree=dir={tree}', '--subject=other channel')
        self.run_script()
        self.assertIn('DefaultBranch=beta', (self.site/'luma.flatpakrepo').read_text())
        self.assertIn('Branch=beta', (self.site/'apps/org.projectluma.Notes.flatpakref').read_text())
        for branch in ('stable','beta','nightly'):
            text = (self.site/f'apps/org.projectluma.Notes.{branch}.flatpakref').read_text()
            self.assertIn(f'Branch={branch}', text)
        ostree(self.repo, 'commit', '--branch=app/org.projectluma.Notes/x86_64/beta',
               f'--tree=dir={tree}', '--subject=withdraw beta', '--add-metadata-string=ostree.endoflife=Withdrawn')
        self.run_script()
        self.assertFalse((self.site/'apps/org.projectluma.Notes.flatpakref').exists())
        self.assertTrue((self.site/'apps/org.projectluma.Notes.nightly.flatpakref').is_file())

    def test_ref_is_kept_so_installed_copies_learn_it_ended(self):
        self.run_script()
        self.assertIn("app/org.projectluma.StickyNotes/x86_64/beta", ostree(self.repo, "refs").split())


if __name__ == "__main__":
    unittest.main()
