"""Archive checks must catch failures of services that drop root in early boot."""
import importlib.util
import errno
import os
import posixpath
import struct
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
    files = {'etc/passwd', 'etc/group', 'etc/machine-id', 'usr/share/dbus-1/system.conf'}
    executables = {'usr/bin/dbus-broker', 'usr/bin/dbus-broker-launch', 'usr/bin/NetworkManager'}
    def row(mode, path):
        return (f'{mode} root/root 0 2026-10-23 12:00 squashfs-root/{path}' if squashfs else
                f'{mode} 1 root root 0 Oct 23 12:00 {path}')
    return '\n'.join([*(row('drwxr-xr-x', p) for p in sorted(directories)),
                      *(row('-rw-r--r--', p) for p in sorted(files)),
                      *(row('-rwxr-xr-x', p) for p in sorted(executables))])


def with_public_file(path, mode='-rw-r--r--'):
    text = archive(True)
    entries = access.parse(text)
    parents = []
    parent = posixpath.dirname(path)
    while parent and parent not in entries:
        parents.append(parent)
        parent = posixpath.dirname(parent)
    for parent in reversed(parents):
        text += f'\ndrwxr-xr-x root/root 0 2026-10-23 12:00 squashfs-root/{parent}'
    return text + f'\n{mode} root/root 1 2026-10-23 12:00 squashfs-root/{path}'


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

    def test_machine_id_must_be_present_and_publicly_readable(self):
        original = '-rw-r--r-- 1 root root 0 Oct 23 12:00 etc/machine-id'
        access.check(archive().replace(original, original.replace('-rw-r--r--', '-r--r--r--')))
        with self.assertRaisesRegex(ValueError, 'inaccessible.*etc/machine-id'):
            access.check(archive().replace(original, original.replace('-rw-r--r--', '-rw-rw----')))
        with self.assertRaisesRegex(ValueError, 'lacks required path.*etc/machine-id'):
            access.check(archive().replace(original, ''))

    def test_generated_public_caches_cannot_inherit_private_file_modes(self):
        paths = (*access.PUBLIC_CACHES, 'usr/share/mime/globs2', 'etc/dconf/db/distro',
                 'usr/lib/fontconfig/cache/font-le64.cache-9',
                 'usr/lib64/fontconfig/cache/font-le64.cache-9',
                 'var/cache/fontconfig/font-le64.cache-9')
        for path in paths:
            with self.subTest(path=path):
                access.check(with_public_file(path))
                with self.assertRaisesRegex(ValueError, 'inaccessible'):
                    access.check(with_public_file(path, '-rw-rw----'))

    def test_generated_cache_parent_and_symlink_targets_are_checked(self):
        path = 'usr/share/mime/mime.cache'
        text = with_public_file(path)
        with self.assertRaisesRegex(ValueError, 'inaccessible.*usr/share/mime'):
            access.check(text.replace('drwxr-xr-x root/root 0 2026-10-23 12:00 squashfs-root/usr/share/mime',
                                     'drwxr-x--- root/root 0 2026-10-23 12:00 squashfs-root/usr/share/mime'))
        text = text.replace('-rw-r--r-- root/root 1 2026-10-23 12:00 squashfs-root/'+path,
                            'lrwxrwxrwx root/root 1 2026-10-23 12:00 squashfs-root/'+path+' -> /etc/private-cache')
        text += '\n-rw------- root/root 1 2026-10-23 12:00 squashfs-root/etc/private-cache'
        with self.assertRaisesRegex(ValueError, 'inaccessible.*etc/private-cache'):
            access.check(text)

    def test_unrelated_intentionally_private_files_remain_private(self):
        text = archive(True)
        for path in ('etc/shadow', 'etc/private.key', 'usr/lib/fontconfig/cache/CACHEDIR.TAG'):
            text += f'\n-rw------- root/root 1 2026-10-23 12:00 squashfs-root/{path}'
        access.check(text)


class BuilderTempACL(unittest.TestCase):
    def test_new_scratch_isolates_setgid_named_acl_on_disk_and_tmpfs(self):
        named_user = os.getuid()
        for row in Path('/proc/self/uid_map').read_text().splitlines():
            start, _, count = map(int, row.split())
            if start <= os.getuid()+1 < start+count:
                named_user = os.getuid()+1
                break
        acl = struct.pack('<I', 2) + b''.join(struct.pack('<HHI', tag, permissions, identifier)
            for tag, permissions, identifier in ((1, 7, 0xffffffff), (2, 7, named_user),
                                                (4, 7, 0xffffffff), (16, 7, 0xffffffff),
                                                (32, 0, 0xffffffff)))
        for filesystem in (None, '/dev/shm'):
            with self.subTest(filesystem=filesystem), tempfile.TemporaryDirectory(dir=filesystem) as root:
                parent = Path(root)/'scratch-parent'
                parent.mkdir()
                groups = [gid for gid in os.getgroups() if gid != os.getegid()]
                if os.geteuid() == 0:
                    groups.append(SCRIPT.stat().st_gid)
                for group in groups:
                    try:
                        os.chown(parent, -1, group)
                        break
                    except OSError as error:
                        # A rootless test namespace may expose supplementary
                        # groups without mapping them for chown. The SGID/ACL
                        # case still runs with its mapped group in that case.
                        if error.errno not in (errno.EINVAL, errno.EPERM):
                            raise
                parent.chmod(0o2775)
                os.setxattr(parent, 'system.posix_acl_default', acl)
                cached = parent/'retained-cache'
                cached.write_bytes(b'retained')
                cached.chmod(0o600)
                parent_metadata = (parent.stat().st_mode, parent.stat().st_uid, parent.stat().st_gid,
                                   {key: os.getxattr(parent, key) for key in os.listxattr(parent)})
                cache_metadata = (cached.stat().st_mode, cached.stat().st_ino, cached.read_bytes())
                mask = os.umask(0o022)
                try:
                    before = parent/'old-runtime'
                    before.mkdir(mode=0o755)
                    (before/'machine-id').write_text('\n')
                    self.assertEqual(before.stat().st_mode & 0o7777, 0o2750)
                    self.assertEqual((before/'machine-id').stat().st_mode & 0o777, 0o660)
                    scratch = runtime_temp.create_scratch(parent)
                    self.assertEqual(scratch.parent, parent)
                    self.assertEqual(scratch.stat().st_mode & 0o7777, 0o700)
                    self.assertNotIn('system.posix_acl_default', os.listxattr(scratch))
                    self.assertIn(struct.pack('<HHI', 2, 7, named_user),
                                  os.getxattr(scratch, 'system.posix_acl_access'))
                    after = scratch/'installtree'
                    after.mkdir(mode=0o755)
                    public = after/'machine-id'
                    public.write_text('\n')
                    self.assertEqual(after.stat().st_mode & 0o7777, 0o755)
                    self.assertEqual(public.stat().st_mode & 0o777, 0o644)
                    self.assertEqual(after.stat().st_gid, os.getegid())
                    self.assertEqual(public.stat().st_gid, os.getegid())
                    self.assertNotIn('system.posix_acl_access', os.listxattr(public))
                    self.assertNotEqual(runtime_temp.create_scratch(parent), scratch)
                    self.assertEqual(parent_metadata, (parent.stat().st_mode, parent.stat().st_uid,
                        parent.stat().st_gid, {key: os.getxattr(parent, key) for key in os.listxattr(parent)}))
                    self.assertEqual(cache_metadata, (cached.stat().st_mode, cached.stat().st_ino, cached.read_bytes()))
                finally:
                    os.umask(mask)

    def test_scratch_parent_symlink_is_refused_without_mutation(self):
        with tempfile.TemporaryDirectory() as root:
            parent = Path(root)
            link = parent/'link'
            link.symlink_to(parent)
            with self.assertRaisesRegex(ValueError, 'not a symlink'):
                runtime_temp.create_scratch(link)
            self.assertEqual(list(parent.iterdir()), [link])

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
