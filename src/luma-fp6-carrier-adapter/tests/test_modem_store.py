# SPDX-License-Identifier: Apache-2.0
"""Run the real modem-store seeder and rmtfs backend chooser with only physical paths projected."""
import hashlib, json, os, pathlib, subprocess, sys, tempfile, unittest

HERE = pathlib.Path(__file__).parent
SCRIPTS = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parents[1] / 'scripts/mobile'
sys.argv = sys.argv[:1]
MIB = 1024 * 1024
LABELS = {'modemst1': 'modem_fs1', 'modemst2': 'modem_fs2', 'fsc': 'modem_fsc', 'fsg': 'modem_fsg', 'study': 'modem_study'}


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


class ModemStore(unittest.TestCase):
    def seed(self, sizes=None, blank_fsg=False, rmtfs_active=False, existing_store=False, blank_modemst=False, store_manifest=None,
             store_files=()):
        sizes = sizes or {'modemst1': 10 * MIB, 'modemst2': 10 * MIB, 'fsc': 128 * 1024, 'fsg': 10 * MIB, 'study': 2 * MIB}
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        r = pathlib.Path(td.name)
        parts, bins, var = r / 'by-partlabel', r / 'bin', r / 'var/lib/luma'
        for d in (parts, bins, var):
            d.mkdir(parents=True)
        for label, size in sizes.items():
            body = b'\0' * size if (label == 'fsg' and blank_fsg) or (label.startswith('modemst') and blank_modemst) else os.urandom(size)
            (parts / label).write_bytes(body)
        (r / 'model').write_text('The Fairphone (Gen. 6)\0')
        if existing_store:
            (var / 'rmtfs').mkdir()
            if store_manifest is not None:
                (var / 'rmtfs/luma-fp6-modem-store.json').write_text(store_manifest)
            for name in store_files:
                (var / 'rmtfs' / name).write_bytes(b'x')
        for name, body in {'id': 'echo 0', 'restorecon': 'exit 0',
                           'blockdev': 'stat -c %s "$2"',
                           'systemctl': 'exit ' + ('0' if rmtfs_active else '3')}.items():
            p = bins / name
            p.write_text('#!/bin/bash\n' + body + '\n')
            p.chmod(0o755)
        script = (SCRIPTS / 'luma-fp6-modem-store').read_text()
        self.state = r / 'run/luma/fp6-modem-state.json'
        for old, new in {'/run/luma/fp6-modem-state.json': str(self.state),
                         '/var/lib/luma/rmtfs.new': str(var / 'rmtfs.new'), '/var/lib/luma/rmtfs': str(var / 'rmtfs'),
                         '/dev/disk/by-partlabel': str(parts), '/sys/firmware/devicetree/base/model': str(r / 'model'),
                         '[[ -b $device ]]': '[[ -f $device ]]'}.items():
            self.assertIn(old, script)
            script = script.replace(old, new)
        helper = r / 'helper'
        helper.write_text(script)
        before = {label: sha(parts / label) for label in sizes}
        x = subprocess.run(['bash', str(helper)], env=dict(os.environ, PATH=str(bins) + ':/usr/bin:/bin'),
                           capture_output=True, text=True, timeout=60)
        after = {label: sha(parts / label) for label in sizes}
        self.assertEqual(before, after, 'a modem partition changed')
        return x.returncode, x.stdout + x.stderr, var, before

    def test_seeds_exact_copies_and_manifest(self):
        code, out, var, digests = self.seed()
        self.assertEqual(code, 0, out)
        self.assertIn('state=seeded partitions_written=0', out)
        self.assertEqual(json.loads(self.state.read_text()), {'state': 'ok'})
        store = var / 'rmtfs'
        self.assertFalse((var / 'rmtfs.new').exists())
        self.assertEqual(oct(store.stat().st_mode & 0o777), '0o700')
        for label, file in LABELS.items():
            self.assertEqual(sha(store / file), digests[label])
            self.assertEqual(oct((store / file).stat().st_mode & 0o777), '0o600')
        self.assertEqual((store / 'modem_tunning').read_bytes(), b'')
        manifest = json.loads((store / 'luma-fp6-modem-store.json').read_text())
        self.assertFalse(manifest['partitions_written'])
        self.assertEqual({k: v['sha256'] for k, v in manifest['sources'].items()}, digests)

    def test_blank_fsg_refused_without_store(self):
        code, out, var, _ = self.seed(blank_fsg=True)
        self.assertNotEqual(code, 0)
        self.assertIn('reason=fsg_blank', out)
        self.assertFalse((var / 'rmtfs').exists() or (var / 'rmtfs.new').exists())

    def test_wrong_modemst_size_refused(self):
        code, out, var, _ = self.seed(sizes={'modemst1': 8 * MIB, 'modemst2': 10 * MIB, 'fsc': 128 * 1024, 'fsg': 10 * MIB, 'study': MIB})
        self.assertNotEqual(code, 0)
        self.assertIn('reason=partition_size_modemst1', out)
        self.assertFalse((var / 'rmtfs').exists())

    def test_running_rmtfs_refused(self):
        code, out, var, _ = self.seed(rmtfs_active=True)
        self.assertNotEqual(code, 0)
        self.assertIn('reason=rmtfs_active', out)

    def test_blank_modemst_defers_seeding(self):
        code, out, var, _ = self.seed(blank_modemst=True)
        self.assertEqual(code, 0, out)
        self.assertIn('state=deferred reason=modemst_blank', out)
        self.assertEqual(json.loads(self.state.read_text()), {'state': 'efs-missing'})
        self.assertFalse((var / 'rmtfs').exists() or (var / 'rmtfs.new').exists())

    ZERO = hashlib.sha256(b'\0' * 10 * MIB).hexdigest()
    BLANK_MANIFEST = json.dumps({'sources': {'modemst1': {'sha256': ZERO}, 'modemst2': {'sha256': ZERO}}}, separators=(',', ':'))

    def test_blank_seed_retired_after_android_repair(self):
        code, out, var, digests = self.seed(existing_store=True, store_manifest=self.BLANK_MANIFEST)
        self.assertEqual(code, 0, out)
        self.assertIn('state=retired-blank-store', out)
        self.assertIn('state=seeded', out)
        self.assertEqual(sha(var / 'rmtfs/modem_fs1'), digests['modemst1'])
        self.assertEqual(len(list(var.glob('rmtfs.blank-*'))), 1)

    def test_blank_seed_kept_while_partitions_still_blank(self):
        code, out, var, _ = self.seed(existing_store=True, store_manifest=self.BLANK_MANIFEST, blank_modemst=True)
        self.assertEqual(code, 0, out)
        self.assertIn('state=present', out)
        self.assertEqual(list(var.glob('rmtfs.blank-*')), [])
        # A store seeded from blank modemst has no EFS (no IMEI), even when complete.
        self.assertEqual(json.loads(self.state.read_text()), {'state': 'efs-missing'})

    STORE_FILES = tuple(LABELS.values()) + ('modem_tunning',)
    REAL_MANIFEST = json.dumps({'sources': {'modemst1': {'sha256': 'a' * 64}, 'modemst2': {'sha256': 'b' * 64}}}, separators=(',', ':'))

    def test_complete_real_store_is_healthy_with_blank_modemst(self):
        # The normal installed state: seeded on the fastboot first boot, then
        # the bootloader's TAR refresh blanked modemst.
        code, out, var, _ = self.seed(existing_store=True, store_manifest=self.REAL_MANIFEST,
                                      store_files=self.STORE_FILES, blank_modemst=True)
        self.assertEqual(code, 0, out)
        self.assertIn('state=present', out)
        self.assertEqual(json.loads(self.state.read_text()), {'state': 'ok'})

    def test_incomplete_store_with_blank_modemst_is_efs_missing(self):
        code, out, var, _ = self.seed(existing_store=True, store_manifest=self.REAL_MANIFEST,
                                      store_files=('modem_fs1', 'modem_fs2'), blank_modemst=True)
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(self.state.read_text()), {'state': 'efs-missing'})

    def test_existing_store_untouched(self):
        code, out, var, _ = self.seed(existing_store=True)
        self.assertEqual(code, 0, out)
        self.assertIn('state=present', out)
        self.assertEqual(list((var / 'rmtfs').iterdir()), [])
        # Incomplete store, real modemst: rmtfs serves the partitions read-only.
        self.assertEqual(json.loads(self.state.read_text()), {'state': 'ok'})


class BackendChooser(unittest.TestCase):
    def choose(self, files):
        with tempfile.TemporaryDirectory() as td:
            r = pathlib.Path(td)
            store = r / 'rmtfs'
            if files is not None:
                store.mkdir()
                for f in files:
                    (store / f).write_bytes(b'x')
            fake = r / 'rmtfs-bin'
            fake.write_text('#!/bin/bash\necho "$@"\n')
            fake.chmod(0o755)
            script = (SCRIPTS / 'luma-fp6-rmtfs').read_text()
            script = script.replace('/var/lib/luma/rmtfs', str(store)).replace('/usr/bin/rmtfs', str(fake))
            helper = r / 'helper'
            helper.write_text(script)
            return subprocess.run(['bash', str(helper)], capture_output=True, text=True, timeout=10).stdout.strip()

    def test_complete_store_uses_file_backend(self):
        out = self.choose(list(LABELS.values()) + ['modem_tunning'])
        self.assertTrue(out.startswith('-o ') and out.endswith(' -s'), out)

    def test_missing_or_incomplete_store_stays_read_only(self):
        self.assertEqual(self.choose(None), '-r -P -s')
        self.assertEqual(self.choose(['modem_fs1', 'modem_fs2']), '-r -P -s')


if __name__ == '__main__':
    unittest.main()
