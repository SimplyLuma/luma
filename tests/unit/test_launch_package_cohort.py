# SPDX-License-Identifier: Apache-2.0
"""Build metadata must produce the exact app-launch cohort the images request."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


def inputs():
    return dict(line.split('=', 1) for line in (ROOT / 'config/desktop/inputs.env').read_text().splitlines()
                if line and not line.startswith('#') and '=' in line)


class LaunchPackageCohortTests(unittest.TestCase):
    def test_native_frontends_and_platform_specs_match_image_pins(self):
        pins = inputs()
        for package, alias in (
            ('luma-developer-platform', 'LUMA_DEVELOPER_PLATFORM_AARCH64_NEVRA'),
            ('prairie-core-apps', 'PRAIRIE_CORE_APPS_NEVRA'),
            ('luma-monitor', 'LUMA_MONITOR_NEVRA'),
            ('luma-terminal', 'LUMA_TERMINAL_NEVRA'),
            ('luma-tide', 'LUMA_TIDE_NEVRA'),
        ):
            with self.subTest(package=package):
                spec = (ROOT / 'packaging/rpm' / (package + '.spec')).read_text()
                version = re.search(r'^Version:\s+(\S+)', spec, re.M)[1]
                release = re.search(r'^Release:\s+(\S+)', spec, re.M)[1].replace('%{?dist}', '')
                self.assertTrue(pins[alias].startswith(f'{package}-{version}-{release}.fc44.'), pins[alias])

    def test_shell_patch_and_all_architecture_pins_match_builder_release(self):
        pins = inputs()
        patch = (ROOT / 'patches/gnome-shell/0000-luma-fedora-spec.patch').read_text()
        release = re.search(r'^\+Release:\s+(\S+)', patch, re.M)[1].replace('%{?dist}', '')
        self.assertEqual(release, pins['GNOME_SHELL_LUMA_RELEASE'])
        for alias in ('GNOME_SHELL_NEVRA', 'GNOME_SHELL_AARCH64_NEVRA', 'GNOME_SHELL_COMMON_NEVRA'):
            self.assertIn('-' + release + '.fc44.', pins[alias])


if __name__ == '__main__':
    unittest.main()
