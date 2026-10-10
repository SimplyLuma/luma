# SPDX-License-Identifier: Apache-2.0
"""Exercise replacement delta delivery and failure ordering in both backends."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

class RemoteSync(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / 'build/repo'
        self.site = self.base / 'build/site'
        self.dest = self.base / 'served'
        self.keys = self.base / 'keys/secrets'
        self.bin = self.base / 'bin'
        for directory in (self.site, self.keys, self.bin):
            directory.mkdir(parents=True)
        for rel, old, new in [('objects/aa/object', b'object', b'object'),
                              ('deltas/aa/pair/superblock', b'old-data', b'new-data'),
                              ('deltas/aa/pair/0', b'old-part', b'new-part'),
                              ('delta-indexes/aa/index', b'old-index', b'new-index'),
                              ('summary', b'old-summary', b'new-summary')]:
            for root, data in ((self.repo, new), (self.dest / 'repo', old)):
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                os.utime(path, (1700000000, 1700000000))
        self.env = dict(os.environ, DEPOT_ROOT=str(self.base / 'build'),
                        DEPOT_KEYS=str(self.base / 'keys'), DEPOT_INSIDE_TOOLS='1',
                        PATH=str(self.bin) + ':' + os.environ['PATH'],
                        SYNC_FIXTURE=str(self.base), REAL_RSYNC=shutil.which('rsync') or '')
        # Adapt destinations to a local fixture; all rsync transfer decisions
        # are made by the real rsync executable, including size/mtime reuse.
        adapter = '''#!/usr/bin/python3
import json,os,pathlib,subprocess,sys
base=pathlib.Path(os.environ['SYNC_FIXTURE']); args=sys.argv[1:]
with (base/'calls').open('a') as f: f.write(json.dumps(args)+'\\n')
if os.environ.get('FAIL_DELTAS') and any('/deltas' in a for a in args): sys.exit(7)
if pathlib.Path(sys.argv[0]).name=='rsync':
    i=args.index('-e'); del args[i:i+2]
    args[-1]=str(base/'served'/args[-1].removeprefix('fixture:'))
    sys.exit(subprocess.call([os.environ['REAL_RSYNC'], *args]))
# Model S3 overwrite semantics: ignore-existing skips even changed bytes.
source=next(a for a in args if a.startswith(str(base/'build')))
target=next(a for a in args if a.startswith('spaces:'))
target=base/'served'/(target.split('/',1)[1] if '/' in target else '')
source=pathlib.Path(source)
members=[source] if source.is_file() else [p for p in source.rglob('*') if p.is_file()]
for p in members:
    rel=pathlib.Path() if source.is_file() else p.relative_to(source)
    q=target/rel; q.parent.mkdir(parents=True,exist_ok=True)
    if '--ignore-existing' not in args or not q.exists(): q.write_bytes(p.read_bytes())
'''
        for name in ('rsync', 'rclone'):
            path = self.bin / name
            path.write_text(adapter)
            path.chmod(0o755)

    def configure(self, backend):
        if backend == 'rsync':
            if not self.env['REAL_RSYNC']:
                self.skipTest('real rsync is required')
            key = self.base / 'publish-key'
            key.write_text('fixture-only')
            config = self.keys / 'dl-origin.env'
            config.write_text(f'DL_RSYNC_DEST=fixture:\nDL_SSH_KEY={key}\n')
        else:
            config = self.keys / 'spaces.env'
            config.write_text('SPACES_ACCESS_KEY=fixture\nSPACES_SECRET_KEY=fixture\nSPACES_REGION=fixture\nSPACES_BUCKET=fixture\n')
        config.chmod(0o600)

    def run_sync(self):
        return subprocess.run(['bash', str(ROOT / 'scripts/depot/sync-remote.sh')],
                              env=self.env, capture_output=True, text=True, timeout=30)

    def check_replacement(self, backend):
        self.configure(backend)
        result = self.run_sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        for rel in ('deltas/aa/pair/superblock', 'deltas/aa/pair/0',
                    'delta-indexes/aa/index', 'summary', 'objects/aa/object'):
            self.assertEqual((self.dest / 'repo' / rel).read_bytes(), (self.repo / rel).read_bytes())
        calls = [json.loads(line) for line in (self.base / 'calls').read_text().splitlines()]
        delta = next(i for i, c in enumerate(calls) if any('/deltas' in a and a.startswith(str(self.repo)) for a in c))
        self.assertIn('--checksum', calls[delta])
        self.assertNotIn('--ignore-existing', calls[delta])
        self.assertTrue(any('summary' in ' '.join(c) for c in calls[delta+1:]))

    def check_failure(self, backend):
        self.configure(backend)
        self.env['FAIL_DELTAS'] = '1'
        self.assertNotEqual(self.run_sync().returncode, 0)
        self.assertEqual((self.dest / 'repo/summary').read_bytes(), b'old-summary')
        self.assertEqual((self.dest / 'repo/delta-indexes/aa/index').read_bytes(), b'old-index')

    def test_rsync_replaces_same_size_same_mtime_generation(self): self.check_replacement('rsync')
    def test_spaces_replaces_generation(self): self.check_replacement('spaces')
    def test_rsync_failure_holds_summary(self): self.check_failure('rsync')
    def test_spaces_failure_holds_summary(self): self.check_failure('spaces')

if __name__ == '__main__': unittest.main()
