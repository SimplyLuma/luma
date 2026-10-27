# SPDX-License-Identifier: LGPL-2.1-or-later
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

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
            old = root / 'old'; old.mkdir()
            subprocess.run(['mksquashfs', str(tree), str(old / 'install.img'), '-noappend',
                            '-processors', '1', '-no-progress'], check=True, stdout=subprocess.DEVNULL)
            (old / 'initrd.img').write_bytes(b'original-initramfs-prefix')
            work = root / 'work'; work.mkdir()
            overrides = reuse.refresh_clock(old, work, 2000)
            rebuilt = root / 'rebuilt'
            subprocess.run(['unsquashfs', '-no-progress', '-d', str(rebuilt),
                            str(overrides['/images/install.img'])], check=True, stdout=subprocess.DEVNULL)
            self.assertEqual((rebuilt / 'unrelated').read_text(), 'same bytes')
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
            current = root / 'current'; current.mkdir()
            reuse.verify_at_clock(repo, commit, key, int(time.time()), current)
            early = root / 'early'; early.mkdir()
            with self.assertRaisesRegex(ValueError, 'invalid at retained runtime clock'):
                reuse.verify_at_clock(repo, commit, key, int(time.time()) - 3600, early)
            run('gpgconf', '--homedir', home, '--kill', 'all')

if __name__ == '__main__':
    unittest.main()
