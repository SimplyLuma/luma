"""Archive checks must catch failures of services that drop root in early boot."""
import importlib.util
import os
import tempfile
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[2]/'scripts/install/atlas-iso/verify-runtime-access.py'
spec = importlib.util.spec_from_file_location('runtime_access', SCRIPT)
access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(access)
temp_spec = importlib.util.spec_from_file_location("runtime_temp", SCRIPT.with_name("prepare-runtime-temp.py"))
runtime_temp = importlib.util.module_from_spec(temp_spec)
temp_spec.loader.exec_module(runtime_temp)


def archive(squashfs=False):
    directories = {'usr', 'usr/bin', 'usr/lib', 'usr/lib64', 'usr/share', 'usr/share/dbus-1',
                   'usr/share/dbus-1/system.d', 'etc', 'etc/dbus-1'}
    files = {'etc/passwd', 'etc/group', 'usr/share/dbus-1/system.conf'}
    executables = {'usr/bin/dbus-broker', 'usr/bin/dbus-broker-launch', 'usr/bin/NetworkManager'}
    def row(mode, path):
        return (f'{mode} root/root 0 2026-10-23 12:00 squashfs-root/{path}' if squashfs else
                f'{mode} 1 root root 0 Oct 23 12:00 {path}')
    return '\n'.join([*(row('drwxr-xr-x', p) for p in sorted(directories)),
                      *(row('-rw-r--r--', p) for p in sorted(files)),
                      *(row('-rwxr-xr-x', p) for p in sorted(executables))])


class RuntimeAccess(unittest.TestCase):
    def test_both_real_tool_listing_formats_are_accepted(self):
        for squashfs in (False, True):
            with self.subTest(squashfs=squashfs): access.check(archive(squashfs))

    def test_masked_library_parent_blocks_an_otherwise_executable_broker(self):
        text = archive().replace('drwxr-xr-x 1 root root 0 Oct 23 12:00 usr/lib64',
                                 'drwxr-x--- 1 root root 0 Oct 23 12:00 usr/lib64')
        with self.assertRaisesRegex(ValueError, 'inaccessible.*usr/lib64'): access.check(text)

    def test_private_identity_database_is_rejected(self):
        text = archive().replace('-rw-r--r-- 1 root root 0 Oct 23 12:00 etc/passwd',
                                 '-rw-rw---- 1 root root 0 Oct 23 12:00 etc/passwd')
        with self.assertRaisesRegex(ValueError, 'inaccessible.*etc/passwd'): access.check(text)

    def test_symlink_is_checked_at_its_target(self):
        text = archive().replace('drwxr-xr-x 1 root root 0 Oct 23 12:00 usr/lib\n',
                                 'lrwxrwxrwx 1 root root 5 Oct 23 12:00 usr/lib -> lib64\n')
        access.check(text)
        with self.assertRaises(ValueError): access.check(text.replace('-> lib64', '-> ../../outside'))

    def test_unrelated_paths_with_spaces_keep_their_full_identity(self):
        text = archive(True)+'\n-rw-r--r-- root/root 1 2026-10-23 12:00 squashfs-root/usr/share/doc/One package/LICENSE.txt\n-rw-r--r-- root/root 1 2026-10-23 12:00 squashfs-root/usr/share/doc/Other package/LICENSE.txt'
        access.check(text)

    def test_missing_bus_binary_and_duplicate_entries_are_rejected(self):
        lines = archive().splitlines()
        with self.assertRaisesRegex(ValueError, 'lacks required path'):
            access.check('\n'.join(line for line in lines if not line.endswith('usr/bin/dbus-broker')))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            access.check(archive()+'\n'+lines[0])


class BuilderTempACL(unittest.TestCase):
    def test_default_acl_does_not_mask_new_archive_files_after_cleanup(self):
        acl = bytes.fromhex('0200000001000700ffffffff04000700ffffffff10000700ffffffff20000000ffffffff')
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'builder-temp'
            path.mkdir(mode=0o1777)
            path.chmod(0o1777)
            os.setxattr(path, 'system.posix_acl_default', acl)
            mask = os.umask(0o022)
            try:
                before = path/'before'; before.mkdir(mode=0o755)
                self.assertEqual(before.stat().st_mode & 0o7777, 0o750)
                runtime_temp.clear_defaults((path,))
                after = path/'after'; after.mkdir(mode=0o755)
                (after/'public').write_text('public')
                self.assertEqual(after.stat().st_mode & 0o7777, 0o755)
                self.assertEqual((after/'public').stat().st_mode & 0o7777, 0o644)
                self.assertEqual(path.stat().st_mode & 0o7777, 0o1777)
            finally:
                os.umask(mask)

    def test_non_sticky_directory_is_refused_without_mutation(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            before = path.stat()
            with self.assertRaisesRegex(ValueError, 'sticky public directory'):
                runtime_temp.clear_defaults((path,))
            self.assertEqual(path.stat().st_mode, before.st_mode)


if __name__ == '__main__': unittest.main()
