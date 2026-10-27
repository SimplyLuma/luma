# SPDX-License-Identifier: LGPL-2.1-or-later
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts/install/atlas-iso' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

wrapper = module('mkksiso_wrapper', 'run-mkksiso.py')
reuse = module('runtime_reuse', 'reuse-installer-runtime.py')

class RuntimeReuse(unittest.TestCase):
    def test_normal_command_unchanged(self):
        command = ['xorriso', '-indev', 'donor', '-report_el_torito', 'plain']
        self.assertEqual(command, wrapper.replace_payload_command(command))

    def test_replacement_removes_only_old_repo_and_maps_real_new_repo(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'luma').mkdir()
            (root / 'repo').mkdir()
            (root / 'luma/repo').symlink_to(root / 'repo', target_is_directory=True)
            command = ['xorriso', '-indev', 'old.iso', '-outdev', 'new.iso',
                       '-boot_image', 'any', 'replay', '-map', str(root / 'luma'), 'luma']
            actual = wrapper.replace_payload_command(command)
            self.assertEqual(actual[-6:], ['-rm_r', '/luma/repo', '--', '-map',
                                          str(root / 'repo'), '/luma/repo'])
            self.assertNotIn(str(root / 'luma'), actual)

    def test_ambiguous_map_rejected(self):
        with self.assertRaises(ValueError):
            wrapper.replace_payload_command(['xorriso', '-indev', 'old', '-outdev', 'new'])

    def test_runtime_source_diff_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
            git('init', '-q')
            git('config', 'user.name', 'fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            path = root / 'config/install/atlas/runtime-kernel.env'
            path.parent.mkdir(parents=True)
            path.write_text('kernel=one\n')
            git('add', '.'); git('commit', '-qm', 'base')
            old = git('rev-parse', 'HEAD')
            reuse.runtime_source_proof(root, old, old)
            path.write_text('kernel=two\n')
            git('commit', '-qam', 'kernel changed')
            with self.assertRaisesRegex(ValueError, 'runtime source changed'):
                reuse.runtime_source_proof(root, old, git('rev-parse', 'HEAD'))

    def test_explicit_target_discinfo_rejects_wrong_or_missing_architecture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.discinfo'
            for arch in ('x86_64', 'aarch64'):
                path.write_text('epoch\nLuma\n' + arch + '\n')
                wrapper.check_target_discinfo(path, arch)
                other = 'aarch64' if arch == 'x86_64' else 'x86_64'
                with self.assertRaisesRegex(ValueError, 'target architecture'):
                    wrapper.check_target_discinfo(path, other)
            path.write_text('short\n')
            with self.assertRaises(ValueError):
                wrapper.check_target_discinfo(path, 'aarch64')
            path.unlink()
            with self.assertRaisesRegex(ValueError, 'require .discinfo'):
                wrapper.check_target_discinfo(path, 'aarch64')

    def test_actual_pe_header_machine_and_bounds(self):
        import struct
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'boot.efi'
            for arch, machine in (('x86_64', 0x8664), ('aarch64', 0xaa64)):
                body = bytearray(160)
                body[:2] = b'MZ'
                struct.pack_into('<I', body, 0x3c, 128)
                body[128:132] = b'PE\0\0'
                struct.pack_into('<H', body, 132, machine)
                path.write_bytes(body)
                self.assertEqual(reuse.pe_machine(path, arch), machine)
                other = 'aarch64' if arch == 'x86_64' else 'x86_64'
                with self.assertRaisesRegex(ValueError, 'target architecture'):
                    reuse.pe_machine(path, other)
                path.write_bytes(body[:130])
                with self.assertRaisesRegex(ValueError, 'PE header'):
                    reuse.pe_machine(path, arch)
            path.write_bytes(b'short')
            with self.assertRaisesRegex(ValueError, 'DOS header'):
                reuse.pe_machine(path, 'aarch64')

    def test_treeinfo_validation_and_replacement(self):
        body = '[checksums]\nimages/install.img = sha256:' + 'a'*64 + '\n'
        self.assertEqual(reuse.update_treeinfo(body, {'/images/install.img': None}), body)
        updated = reuse.update_treeinfo(body, {'/images/install.img': 'b'*64})
        self.assertIn('sha256:' + 'b'*64, updated)
        self.assertEqual(updated.count('sha256:'), 1)
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            reuse.update_treeinfo(body + body, {'/images/install.img': None})
        # Optional unrelated Lorax entries remain byte-for-byte intact.
        self.assertEqual(reuse.update_treeinfo(body, {'/images/pxeboot/initrd.img': 'c'*64}), body)

    def test_tool_preflight_reports_all_missing_before_work(self):
        with patch.object(reuse.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'mksquashfs, cpio'):
                reuse.preflight_tools(True)
            with self.assertRaises(ValueError) as error:
                reuse.preflight_tools(False)
            self.assertNotIn('mksquashfs', str(error.exception))
        for value in ('0', '-1'):
            with self.assertRaises(reuse.argparse.ArgumentTypeError):
                reuse.positive_integer(value)

    def test_clock_refresh_changes_only_two_stage2_fields_and_appends_initramfs(self):
        import gzip
        import os
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree = root / 'tree'
            (tree / 'usr/lib').mkdir(parents=True)
            (tree / 'usr/share/luma-installer-atlas').mkdir(parents=True)
            clock = tree / 'usr/lib/clock-epoch'
            clock.touch(); os.utime(clock, (1000, 1000))
            (tree / 'usr/share/luma-installer-atlas/media.env').write_text('clock_epoch=1000\nvolid=RETAINED\n')
            (tree / 'unrelated').write_text('same bytes')
            os.setxattr(tree / 'unrelated', 'user.runtime-fixture', b'preserved')
            old = root / 'old'; old.mkdir()
            # An unprivileged fixture can preserve user xattrs, but cannot
            # restore security.selinux. Production retains every namespace.
            subprocess.run(['mksquashfs', str(tree), str(old / 'install.img'), '-noappend',
                            '-processors', '1', '-no-progress', '-xattrs-include', '^user\\.'],
                           check=True, stdout=subprocess.DEVNULL)
            (old / 'initrd.img').write_bytes(b'original-initramfs-prefix')
            work = root / 'work'; work.mkdir()
            with patch.object(reuse, 'run', wraps=reuse.run) as commands:
                overrides = reuse.refresh_clock(old, work, 2000)
            squash = next(call.args for call in commands.call_args_list if call.args[0] == 'mksquashfs')
            self.assertEqual(squash[squash.index('-processors') + 1], '2')
            self.assertEqual(squash[squash.index('-mem') + 1], '512M')
            rebuilt = root / 'rebuilt'
            subprocess.run(['unsquashfs', '-no-progress', '-xattrs-include', '^user\\.',
                            '-d', str(rebuilt),
                            str(overrides['/images/install.img'])], check=True, stdout=subprocess.DEVNULL)
            self.assertEqual((rebuilt / 'unrelated').read_text(), 'same bytes')
            self.assertEqual(os.getxattr(rebuilt / 'unrelated', 'user.runtime-fixture'), b'preserved')
            self.assertEqual(int((rebuilt / 'usr/lib/clock-epoch').stat().st_mtime), 2000)
            self.assertEqual((rebuilt / 'usr/share/luma-installer-atlas/media.env').read_text(),
                             'clock_epoch=2000\nvolid=RETAINED\n')
            initrd = overrides['/images/pxeboot/initrd.img'].read_bytes()
            self.assertTrue(initrd.startswith(b'original-initramfs-prefix'))
            archive = gzip.decompress(initrd[len(b'original-initramfs-prefix'):])
            extract = root / 'cpio'; extract.mkdir()
            subprocess.run(['cpio', '-idm', '--quiet'], input=archive, cwd=extract, check=True)
            self.assertEqual(int((extract / 'usr/lib/clock-epoch').stat().st_mtime), 2000)

    def test_real_signed_commit_rejects_old_rtc(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / 'gnupg'; home.mkdir(mode=0o700)
            def run(*args, **kwargs):
                return subprocess.check_output(list(map(str, args)), stderr=subprocess.DEVNULL, **kwargs)
            run('gpg', '--batch', '--homedir', home, '--pinentry-mode', 'loopback',
                '--passphrase', '', '--quick-generate-key', 'fixture@example.invalid', 'ed25519', 'sign', '1d')
            key = root / 'public.gpg'
            key.write_bytes(run('gpg', '--homedir', home, '--export'))
            repo = root / 'repo'
            run('ostree', f'--repo={repo}', 'init', '--mode=archive-z2')
            tree = root / 'tree'; tree.mkdir(); (tree / 'hello').write_text('fixture')
            commit = run('ostree', f'--repo={repo}', 'commit', '-b', 'test', f'--tree=dir={tree}',
                         '--gpg-sign=fixture@example.invalid', f'--gpg-homedir={home}').decode().strip()
            current = root / ('deep-release-control-' * 8) / 'current'
            current.mkdir(parents=True)
            self.assertGreater(len(str(current / 'gpg')), 108)
            reuse.verify_at_clock(repo, commit, key, int(time.time()), current)
            early = root / 'early'; early.mkdir()
            with self.assertRaisesRegex(ValueError, 'invalid at retained runtime clock'):
                reuse.verify_at_clock(repo, commit, key, int(time.time()) - 3600, early)
            run('gpgconf', '--homedir', home, '--kill', 'all')

if __name__ == '__main__':
    unittest.main()
