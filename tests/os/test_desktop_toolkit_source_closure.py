# SPDX-License-Identifier: Apache-2.0
"""Exercise the actual admission pathspec against real isolated Git histories."""
import ast
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ActualToolkitSourceClosure(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((ROOT / 'scripts/os/admit-desktop-toolkit.py').read_text())
        self.paths = next(ast.literal_eval(node.value) for node in tree.body
                          if isinstance(node, ast.Assign) and
                          any(isinstance(target, ast.Name) and target.id == 'paths'
                              for target in node.targets))
        self.assertIn('assets/icon-theme/Prairie/symbolic/actions/lumaui-*.svg', self.paths)
        self.assertNotIn('assets/icon-theme/Prairie', self.paths)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git('init', '-q')
        for path in ('assets/icon-theme/Prairie/symbolic/actions/lumaui-first.svg',
                     'assets/icon-theme/Prairie/application-manifest.json',
                     'assets/icon-theme/Prairie/scalable/apps/example.svg',
                     'scripts/packages/build-luma-developer-platform.sh',
                     'src/luma-platform/NOTICE', 'LICENSE.md', 'config/desktop/inputs.env'):
            self.write(path, 'original\n')
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'isolated source fixture')
        self.base = self.git('rev-parse', 'HEAD').stdout.strip()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def guarded_diff(self):
        self.git('add', '-A')
        # The index is the prospective new source tree; same Git pathspecs as
        # the production base-to-HEAD guard, with no shell wildcard expansion.
        return subprocess.run(['git', '-c', f'safe.directory={self.root}', '-C', str(self.root),
                               'diff', '--cached', '--exit-code', self.base, '--', *self.paths],
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode

    def test_unconsumed_manifest_and_app_artwork_do_not_rebuild_toolkit(self):
        self.write('assets/icon-theme/Prairie/application-manifest.json', 'export metadata\n')
        self.write('assets/icon-theme/Prairie/scalable/apps/example.svg', 'app art\n')
        self.assertEqual(self.guarded_diff(), 0)

    def test_modified_consumed_glyph_is_refused(self):
        self.write('assets/icon-theme/Prairie/symbolic/actions/lumaui-first.svg', 'changed\n')
        self.assertEqual(self.guarded_diff(), 1)

    def test_added_consumed_glyph_is_refused(self):
        self.write('assets/icon-theme/Prairie/symbolic/actions/lumaui-added.svg', 'new\n')
        self.assertEqual(self.guarded_diff(), 1)

    def test_deleted_consumed_glyph_is_refused(self):
        (self.root / 'assets/icon-theme/Prairie/symbolic/actions/lumaui-first.svg').unlink()
        self.assertEqual(self.guarded_diff(), 1)

    def test_recipe_notice_license_and_main_inputs_remain_guarded(self):
        for path in ('scripts/packages/build-luma-developer-platform.sh',
                     'src/luma-platform/NOTICE', 'LICENSE.md', 'config/desktop/inputs.env'):
            with self.subTest(path=path):
                self.git('reset', '--hard', '-q', self.base)
                self.write(path, 'changed\n')
                self.assertEqual(self.guarded_diff(), 1)


if __name__ == '__main__':
    unittest.main()
