# SPDX-License-Identifier: Apache-2.0
"""Package files open in Valet, by one desktop id, without the chooser."""
import configparser
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / 'src/luma-desktop-launcher-policy'
VALET = 'io.luma.Valet.desktop'
ALIASES = ('org.projectluma.ApplicationInstaller.desktop', 'io.luma.Install.desktop')
# Every package type Valet installs: RPM, DEB, AppImage, Flatpak bundle,
# .flatpakref, .flatpakrepo, Snap, APK, Luma's Android package set, Windows.
PACKAGE_TYPES = {
    'application/x-rpm', 'application/vnd.debian.binary-package', 'application/vnd.appimage',
    'application/vnd.flatpak', 'application/vnd.flatpak.ref', 'application/vnd.flatpak.repo',
    'application/vnd.snap', 'application/vnd.android.package-archive',
    'application/vnd.projectluma.android-package-set', 'application/x-ms-dos-executable',
    'application/x-msdownload', 'application/x-msi', 'application/vnd.microsoft.portable-executable',
}


def group(path, name):
    parser = configparser.ConfigParser(interpolation=None, strict=False, delimiters=('=',))
    parser.optionxform = str
    parser.read(path, encoding='utf-8')
    return dict(parser[name]) if parser.has_section(name) else {}


def defaults(path):
    return {key: value.split(';')[0] for key, value in group(path, 'Default Applications').items()}


class ValetOpensPackagesTest(unittest.TestCase):
    def test_open_directly_lists_exactly_the_package_types(self):
        listed = group(POLICY / 'open-directly.list', 'Open Directly')
        self.assertEqual(set(listed), PACKAGE_TYPES)
        self.assertEqual(set(listed.values()), {VALET})

    def test_valet_handles_every_listed_type(self):
        data = ROOT / 'src/luma-installer/data'
        valet = group(data / VALET, 'Desktop Entry')
        self.assertLessEqual(PACKAGE_TYPES, set(valet['MimeType'].split(';')))
        self.assertNotEqual(valet.get('NoDisplay'), 'true')
        for name in ALIASES:
            with self.subTest(alias=name):
                self.assertEqual(group(data / name, 'Desktop Entry').get('NoDisplay'), 'true')

    def test_shipped_defaults_name_valet(self):
        # /etc/xdg/gnome-mimeapps.list (launcher policy) is what a Luma
        # session reads first; every package type names Valet there, the
        # handler open-directly.list trusts.
        gnome = defaults(POLICY / 'gnome-mimeapps.list')
        for mime in sorted(PACKAGE_TYPES):
            with self.subTest(type=mime):
                self.assertEqual(gnome.get(mime), VALET)
        # The lower-ranked /etc/xdg/mimeapps.list may still name an alias of
        # Valet, never another application.
        system = defaults(ROOT / 'src/prairie-core/data/luma-mimeapps.list')
        for mime in sorted(PACKAGE_TYPES & set(system)):
            with self.subTest(type=mime):
                self.assertIn(system[mime], (VALET,) + ALIASES)

    def test_policy_is_installed_where_the_portal_reads_it(self):
        patch = (ROOT / 'patches/xdg-desktop-portal/'
                 '0001-open-uri-open-package-files-in-the-system-installer.patch').read_text()
        self.assertIn('#define LUMA_OPEN_DIRECTLY_POLICY "/usr/share/luma/open-directly.list"', patch)
        self.assertIn('#define LUMA_OPEN_DIRECTLY_GROUP "Open Directly"', patch)
        self.assertIn('#define LUMA_SYSTEM_APPLICATIONS_DIR "/usr/share/applications/"', patch)
        spec = (ROOT / 'packaging/rpm/luma-desktop-launcher-policy.spec').read_text()
        self.assertIn('install -D -m 0644 open-directly.list %{buildroot}%{_datadir}/luma/open-directly.list', spec)
        self.assertIn('\n%{_datadir}/luma/open-directly.list\n', spec)

    def test_portal_patch_is_built_and_pinned(self):
        spec = (ROOT / 'patches/xdg-desktop-portal/0000-luma-fedora-spec.patch').read_text()
        self.assertIn('+Patch1001: 0001-open-uri-open-package-files-in-the-system-installer.patch', spec)
        self.assertIn('+Patch1002: 0002-request-run-disconnect-off-the-main-loop.patch', spec)
        self.assertIn('+Patch1003: 0003-open-uri-open-files-in-their-chosen-default.patch', spec)
        inputs = dict(line.split('=', 1) for line in
                      (ROOT / 'config/desktop/inputs.env').read_text().splitlines()
                      if '=' in line and not line.startswith('#'))
        release = inputs['XDG_DESKTOP_PORTAL_LUMA_RELEASE']
        self.assertIn(f'+Release: %autorelease -e {release.split(".", 1)[1]}', spec)
        nevra = inputs['XDG_DESKTOP_PORTAL_NEVRA']
        self.assertEqual(nevra, f'xdg-desktop-portal-{inputs["XDG_DESKTOP_PORTAL_VERSION"]}-{release}.fc44.x86_64')
        self.assertIn(nevra, (ROOT / 'config/desktop/packages.txt').read_text().splitlines())


if __name__ == '__main__':
    unittest.main()
