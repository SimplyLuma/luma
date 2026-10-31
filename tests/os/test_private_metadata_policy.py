# SPDX-License-Identifier: Apache-2.0
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/os/lib'))
from private_metadata_policy import DEFAULT, normalize

class PrivateMetadataTest(unittest.TestCase):
    def record(self, mode):
        return {'uid': 0, 'gid': 0, 'mode': mode, 'content_sha256': 'unchanged',
                'xattrs': {'system.posix_acl_default': DEFAULT, 'system.posix_acl_access': DEFAULT,
                           'security.selinux': '00', 'user.preserved': 'abcd'}}
    def test_rpm_directory_modes_are_authoritative(self):
        for path, mode in (('/', 0o555), ('/usr', 0o755), ('/usr/etc/private', 0o750)):
            fixed = normalize(path, self.record(0o2770), {path: mode}, directory=True)
            self.assertEqual(fixed['mode'], mode)
            self.assertEqual(fixed['xattrs'], {'security.selinux': '00', 'user.preserved': 'abcd'})
    def test_generated_private_directory_stays_private(self):
        self.assertEqual(normalize('/usr/etc/selinux/active', self.record(0o2700), {}, True)['mode'], 0o700)
    def test_generated_public_data_directory_is_traversable(self):
        self.assertEqual(normalize('/usr/share/luma/data', self.record(0o2750), {}, True)['mode'], 0o755)
    def test_private_file_never_gains_permissions(self):
        record = self.record(0o600)
        self.assertEqual(normalize('/usr/share/luma/private', record, {}), record)
    def test_packaged_private_file_stays_private(self):
        record = self.record(0o640)
        self.assertEqual(normalize('/usr/etc/secret', record, {'/usr/etc/secret': 0o640}), record)
    def test_only_known_public_file_permissions_recovered(self):
        record = self.record(0o640)
        fixed = normalize('/usr/lib/os-release', record, {'/usr/lib/os-release': 0o644})
        self.assertEqual(fixed['mode'], 0o644)
        self.assertEqual(fixed['content_sha256'], record['content_sha256'])
        self.assertEqual(fixed['uid'], record['uid'])
        self.assertEqual(fixed['xattrs']['security.selinux'], '00')
    def test_special_file_modes_are_preserved(self):
        record = self.record(0o4111)
        self.assertEqual(normalize('/usr/bin/sudo', record, {'/usr/bin/sudo': 0o4755}), record)
    def test_named_access_acl_is_preserved(self):
        record = self.record(0o2770)
        record['xattrs']['system.posix_acl_access'] = '0200000001000700ffffffff02000400e803000004000700ffffffff10000700ffffffff20000000ffffffff'
        self.assertEqual(normalize('/usr/share/custom', record, {}, True), record)
    def test_unrelated_acl_is_preserved(self):
        record = self.record(0o2770)
        record['xattrs']['system.posix_acl_default'] = 'unrelated'
        self.assertEqual(normalize('/usr/share/custom', record, {}, True), record)
