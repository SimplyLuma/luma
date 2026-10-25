# SPDX-License-Identifier: Apache-2.0
"""Exercise source/header/payload checks with an actual isolated noarch RPM."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('native_noarch', ROOT / 'scripts/os/check-native-noarch-package.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ActualNoarchPackage(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / 'build/packages/luma-desktop-launcher-policy'
        self.directory.mkdir(parents=True)
        self.spec = self.root / 'fixture.spec'
        self.spec.write_text('''Name: luma-desktop-launcher-policy
Version: 0.0.1
Release: 1
Summary: Isolated package checker fixture
License: MIT
BuildArch: noarch
%description
Private checker fixture; never installed or executed.
%install
mkdir -p %{buildroot}/usr/share/luma %{buildroot}/usr/libexec
printf 'fixture\\n' > %{buildroot}/usr/share/luma/system-flatpak-replacements.txt
printf 'fixture\\n' > %{buildroot}/usr/libexec/luma-flatpak-replacements
%files
/usr/share/luma/system-flatpak-replacements.txt
/usr/libexec/luma-flatpak-replacements
''')
        subprocess.run(['rpmbuild', '-bb', '--nodeps', '--define', f'_topdir {self.directory}',
                        '--define', '_build_id_links none', str(self.spec)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.rpm = next(self.directory.glob('RPMS/**/*.rpm'))
        self.checksum()

    def checksum(self):
        import hashlib
        digest = hashlib.sha256(self.rpm.read_bytes()).hexdigest()
        (self.directory / 'SHA256SUMS').write_text(f'{digest}  {self.rpm.relative_to(self.directory)}\n')

    def test_real_package_header_and_payload_pass(self):
        module.check(self.root, self.spec)

    def test_actual_header_does_not_match_new_source_identity(self):
        self.spec.write_text(self.spec.read_text().replace('Version: 0.0.1', 'Version: 0.0.2'))
        with self.assertRaisesRegex(ValueError, 'current source spec'):
            module.check(self.root, self.spec)

    def test_modified_real_archive_is_refused(self):
        with self.rpm.open('ab') as stream:
            stream.write(b'changed-after-checksum')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            module.check(self.root, self.spec)


class AdditionalNoarchPayloads(unittest.TestCase):
    def test_real_shell_state_and_icon_packages_require_their_shipped_payload(self):
        import hashlib
        for name in ('luma-shell-state', 'prairie-icon-theme'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                directory = root / 'build/packages' / name
                directory.mkdir(parents=True)
                required = sorted(module.REQUIRED[name])
                spec = root / 'fixture.spec'
                install = '\n'.join(
                    'mkdir -p %{buildroot}' + str(Path(path).parent) + '\n' +
                    "printf 'fixture\\n' > %{buildroot}" + path for path in required)
                spec.write_text(f"""Name: {name}
Version: 0.0.1
Release: 1
Summary: Isolated checker payload fixture
License: MIT
BuildArch: noarch
%description
Private fixture; never installed or executed.
%install
{install}
%files
""" + '\n'.join(required) + '\n')
                subprocess.run(['rpmbuild', '-bb', '--nodeps', '--define', f'_topdir {directory}',
                                '--define', '_build_id_links none', str(spec)], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                rpm = next(directory.glob('RPMS/**/*.rpm'))
                digest = hashlib.sha256(rpm.read_bytes()).hexdigest()
                (directory / 'SHA256SUMS').write_text(f'{digest}  {rpm.relative_to(directory)}\n')
                module.check(root, spec)
                # Actual payload is queried again; adding a missing required file
                # cannot be satisfied by the valid NEVRA/checksum alone.
                from unittest.mock import patch
                with patch.dict(module.REQUIRED, {name: set(required) | {'/usr/share/missing'}}):
                    with self.assertRaisesRegex(ValueError, 'Missing shipped files'):
                        module.check(root, spec)


if __name__ == '__main__':
    unittest.main()
