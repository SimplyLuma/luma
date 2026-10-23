#!/usr/bin/env python3
"""Verify a pinned, successful native toolkit artifact before reuse in UI CI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--architecture', required=True, choices=['x86_64', 'aarch64'])
p.add_argument('--output', type=Path, default=Path('build'))
a = p.parse_args()
# Whole-archive digests are GitHub's retained upload identities, not rebuild hashes.
artifacts = {
    'aarch64': (12461641598, '2177ba2b825afca9848ae0c4db58b3c74030ea0668f38e018f83304c872fe168'),
    'x86_64': (12460839503, '03d2060ad144a6e810759098e86355abf145e1577a78560bafba8aec2dffae18'),
}
run = 39857373641
source = '84ccfae4eeaa45a41046f4415b190d2165741f2c'
merge = 'cbc781c98b5455a0a2e3204976a252099ead0037'
repo = 'SimplyLuma/luma'
def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', f'repos/{repo}/{path}']))
record = api(f'actions/runs/{run}')
assert record['conclusion'] == 'success' and record['head_sha'] == source, 'toolkit run identity/result mismatch'
# Provider recipes and inputs must still match the successful source revision.
paths = ['config/desktop/inputs.env', 'scripts/os/build-desktop-toolkit.sh',
         'scripts/packages/build-gtk4.sh', 'scripts/packages/build-libadwaita.sh',
         'scripts/packages/build-luma-developer-platform.sh', 'scripts/packages/build-figtree-fonts.sh',
         'scripts/packages/run-in-rpm-builder.sh', 'scripts/packages/fetch-source-rpm.sh',
         'patches/gtk4', 'patches/libadwaita', 'src/luma-platform',
         'packaging/rpm/luma-developer-platform.spec']
subprocess.run(['git', '-c', f'safe.directory={Path.cwd()}', 'diff', '--exit-code', source, 'HEAD', '--', *paths], check=True)
artifact_id, digest = artifacts[a.architecture]
metadata = api(f'actions/artifacts/{artifact_id}')
assert not metadata['expired'] and metadata['name'] == f'desktop-toolkit-{a.architecture}'
assert metadata['workflow_run']['id'] == run and metadata['digest'] == f'sha256:{digest}'
a.output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryFile() as archive:
    subprocess.run(['gh', 'api', f'repos/{repo}/actions/artifacts/{artifact_id}/zip'], stdout=archive, check=True)
    archive.seek(0)
    assert hashlib.file_digest(archive, 'sha256').hexdigest() == digest, 'toolkit archive checksum mismatch'
    archive.seek(0)
    with zipfile.ZipFile(archive) as z:
        for entry in z.infolist():
            path = Path(entry.filename)
            assert not path.is_absolute() and '..' not in path.parts, 'unsafe archive member'
            assert ((entry.external_attr >> 16) & 0o170000) != 0o120000, 'archive symlink refused'
        z.extractall(a.output)
assert (a.output / 'SOURCE-REVISION').read_text().strip() == merge, 'toolkit source receipt mismatch'
covered = set()
for manifest in a.output.glob('packages/**/SHA256SUMS'):
    for line in manifest.read_text().splitlines():
        expected, name = line.split(maxsplit=1)
        path = (manifest.parent / name.lstrip('*')).resolve()
        assert path.is_relative_to(a.output.resolve()), 'manifest path escapes output'
        assert hashlib.file_digest(path.open('rb'), 'sha256').hexdigest() == expected, str(path)
        covered.add(path)
rpms = {path.resolve() for path in a.output.glob('packages/**/*.rpm')}
assert rpms and rpms == covered, 'uncovered toolkit RPM or missing checksum entry'
print(f'Admitted {len(rpms)} checked {a.architecture} toolkit RPMs from successful run {run}')
