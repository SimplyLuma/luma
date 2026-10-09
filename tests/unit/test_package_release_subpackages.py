# SPDX-License-Identifier: Apache-2.0
"""The release preflight rejects shared RPM pins the main RPM cannot install."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class DesktopSharedPackagePins(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'config', self.root / 'config')
        for name in ('packaging', 'patches', 'scripts', 'changes'):
            if (ROOT / name).exists():
                (self.root / name).symlink_to(ROOT / name, target_is_directory=True)

    def run_contract(self):
        return subprocess.run(
            ['bash', str(ROOT / 'tests/smoke/package-release-contract.sh'), str(self.root)],
            text=True, capture_output=True, check=False)

    def make_shared_pin_stale(self, package):
        path = self.root / 'config/desktop/packages.txt'
        lines = path.read_text().splitlines()
        for index, line in enumerate(lines):
            if line.startswith(package + '-'):
                version, release_arch = line[len(package) + 1:].split('-', 1)
                architecture = release_arch.rsplit('.', 1)[1]
                lines[index] = f'{package}-{version}-1.luma.1.fc44.{architecture}'
                path.write_text('\n'.join(lines) + '\n')
                return
        self.fail(f'No shared package pin for {package}')

    def test_current_main_and_shared_packages_pass(self):
        result = self.run_contract()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_previous_mutter_common_is_rejected(self):
        self.make_shared_pin_stale('mutter-common')
        result = self.run_contract()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('mutter-common', result.stdout)
        self.assertIn('patches/mutter/0000-luma-fedora-spec.patch', result.stdout)

    def test_previous_settings_filesystem_is_rejected(self):
        self.make_shared_pin_stale('gnome-control-center-filesystem')
        result = self.run_contract()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('gnome-control-center-filesystem', result.stdout)
        self.assertIn('patches/gnome-control-center/0000-luma-fedora-spec.patch', result.stdout)


if __name__ == '__main__':
    unittest.main()
