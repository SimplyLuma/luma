# SPDX-License-Identifier: Apache-2.0
"""Ask RPM itself to parse all production Shell patch declarations."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ShellPatchNumbers(unittest.TestCase):
    def declarations(self):
        patch = ROOT / 'patches/gnome-shell/0000-luma-fedora-spec.patch'
        return [line[1:] for line in patch.read_text().splitlines()
                if line.startswith('+Patch')]

    def parse(self, declarations):
        with tempfile.TemporaryDirectory() as directory:
            spec = Path(directory) / 'shell-patch-ids.spec'
            spec.write_text('Name: shell-patch-ids\nVersion: 1\nRelease: 1\n'
                            'Summary: Shell patch declaration fixture\nLicense: MIT\n'
                            + '\n'.join(declarations)
                            + '\n%description\nPatch declaration parser fixture.\n%files\n')
            return subprocess.run(['rpmspec', '--parse', str(spec)],
                                  text=True, capture_output=True)

    def test_production_patch_numbers_parse_and_keep_both_features(self):
        declarations = self.declarations()
        for payload in ('0280-retire-standalone-displays.patch',
                        '0280-drawer-artwork-and-grid-alignment.patch'):
            self.assertEqual(sum(line.split(':', 1)[1].strip() == payload
                                 for line in declarations), 1)
        parsed = self.parse(declarations)
        self.assertEqual(parsed.returncode, 0, parsed.stderr)

    def test_real_rpm_parser_rejects_duplicate_patch_identity(self):
        declarations = self.declarations()
        declarations.append(declarations[-1].split(':', 1)[0]
                            + ': duplicate-identity.patch')
        parsed = self.parse(declarations)
        self.assertNotEqual(parsed.returncode, 0)
        self.assertIn('defined multiple times', parsed.stderr)


if __name__ == '__main__':
    unittest.main()
