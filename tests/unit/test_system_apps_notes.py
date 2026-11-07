# SPDX-License-Identifier: Apache-2.0
"""system_apps_current.py pr: a pull request that moves a system app's pin or
spec release must add the "Updated <App> to X.Y" improvement fragment the
nightly's notes step requires."""
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOL = ROOT / 'scripts/os/lib/system_apps_current.py'
OLD = 'viola-browser-stable-151.0.7922.72-15.viola8210.native40.creator20261102.1.fc44.x86_64'
NEW = 'viola-browser-stable-151.0.7922.72-15.viola8210.native41.creator20261107.1.fc44.x86_64'
SPEC = '''%global viola_build 8210
%global native_release {native}
Name:           viola-browser-stable
Version:        151.0.7922.72
Release:        15.viola%{{viola_build}}.native%{{native_release}}.creator20261102.1%{{?dist}}
%description
Viola.
'''


class PullRequestNotes(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self._dir.name)
        self.write('config/desktop/system-apps.txt', 'viola-browser-stable  Viola  /nonexistent\n')
        self.write('config/desktop/packages.txt', f'# pins\nluma-tide-0.1.0-2.luma.39.fc44.x86_64\n{OLD}\n')
        self.write('config/desktop/inputs.env', f'LUMA_VIOLA_NEVRA={OLD}\n')
        self.write('packaging/rpm/viola-browser-stable.spec', SPEC.format(native=40))
        self.write('changes/README.md', 'fragments\n')
        self.git('init', '-q')
        self.commit('base')
        self.base = self.git('rev-parse', 'HEAD').strip()

    def tearDown(self):
        self._dir.cleanup()

    def write(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=t', '-c', 'user.email=t@t',
                               '-c', 'commit.gpgsign=false', *args],
                              check=True, capture_output=True, text=True).stdout

    def commit(self, message):
        self.git('add', '-A')
        self.git('commit', '-qm', message)

    def check(self):
        result = subprocess.run([sys.executable, '-B', str(TOOL), 'pr', '--repo', str(self.repo),
                                 '--base', self.base], capture_output=True, text=True)
        return result.returncode, result.stdout + result.stderr

    def bump(self, fragment_type, summary):
        for path in ('config/desktop/packages.txt', 'config/desktop/inputs.env'):
            self.write(path, (self.repo / path).read_text().replace(OLD, NEW))
        self.write('packaging/rpm/viola-browser-stable.spec', SPEC.format(native=41))
        self.write('changes/20261107-viola-site-sign-in.md',
                   f'---\ntype: {fragment_type}\ncomponent: Viola\npackages: [viola-browser-stable]\n'
                   f'highlight: false\n---\nSummary: {summary}\nDetails: x.\n')
        self.commit('bump')

    def test_pin_bump_with_a_fix_fragment_fails(self):
        self.bump('fix', 'Sites that ask for a user name and password can sign you in')
        status, output = self.check()
        self.assertEqual(status, 1, output)
        self.assertIn('FAIL viola-browser-stable: pin in config/desktop/packages.txt', output)
        self.assertIn('Summary: Updated Viola to X.Y', output)

    def test_pin_bump_with_an_update_fragment_passes(self):
        self.bump('improvement', 'Updated Viola to 151.0.7922.72')
        status, output = self.check()
        self.assertEqual(status, 0, output)
        self.assertRegex(output, r'PASS viola-browser-stable: .* is noted in changes/20261107-viola-site-sign-in.md')

    def test_spec_release_macro_alone_needs_a_note(self):
        self.write('packaging/rpm/viola-browser-stable.spec', SPEC.format(native=41))
        self.commit('spec only')
        status, output = self.check()
        self.assertEqual(status, 1, output)
        self.assertIn('FAIL viola-browser-stable: release in packaging/rpm/viola-browser-stable.spec', output)

    def test_editing_an_older_fragment_does_not_count(self):
        self.write('changes/old.md', '---\ntype: improvement\npackages: [viola-browser-stable]\n---\n'
                                     'Summary: Updated Viola to 151.0.7922.72\n')
        self.commit('older fragment')
        self.base = self.git('rev-parse', 'HEAD').strip()
        for path in ('config/desktop/packages.txt', 'config/desktop/inputs.env'):
            self.write(path, (self.repo / path).read_text().replace(OLD, NEW))
        self.write('changes/old.md', (self.repo / 'changes/old.md').read_text() + 'Details: edited.\n')
        self.commit('bump with an edited fragment')
        status, output = self.check()
        self.assertEqual(status, 1, output)

    def test_other_changes_pass(self):
        self.write('config/desktop/packages.txt',
                   (self.repo / 'config/desktop/packages.txt').read_text().replace('2.luma.39', '2.luma.40'))
        self.write('packaging/rpm/viola-browser-stable.spec',
                   SPEC.format(native=40).replace('Viola.\n', 'The Viola browser.\n'))
        self.commit('unrelated')
        status, output = self.check()
        self.assertEqual(status, 0, output)
        self.assertIn('PASS no system app pin or release changed', output)


if __name__ == '__main__':
    unittest.main()
