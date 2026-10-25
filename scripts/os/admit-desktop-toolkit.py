#!/usr/bin/env python3
"""Verify a pinned, successful native toolkit artifact before reuse in UI CI."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--architecture', required=True, choices=['x86_64', 'aarch64'])
p.add_argument('--output', type=Path, default=Path('build'))
a = p.parse_args()
# Temporary bring-up bootstrap: expired artifacts fail closed and require a
# new successful matching toolkit cohort with refreshed run/archive receipts.
# Whole-archive digests are GitHub's retained upload identities, not rebuild hashes.
artifacts = {
    'aarch64': (12583195962, '4173a2a9d0146390f36abd31e581133237b128a3d503b65bbd2e7dd34237c02f'),
    'x86_64': (12582657109, '6be2904c26659f41ffadcf749b3dca32a74738405d4df42988133e1472bb60bd'),
}
run = 40150227413
source = '4d8d714d5a34bc20a1eb46135f2e1316d958d15e'
merge = '84d0d83661b7987b0765f508c45f6473f068da25'
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
         'packaging/rpm/luma-developer-platform.spec', 'scripts/fonts',
         'packaging/fontconfig', 'packaging/rpm/google-figtree-fonts.spec',
         'assets/icon-theme/Prairie', 'LICENSE.md', 'scripts/packages/spec-release.sh',
         'tests/toolkit-native']
subprocess.run(['git', '-c', f'safe.directory={Path.cwd()}', 'diff', '--exit-code', source, 'HEAD', '--', *paths], check=True)
artifact_id, digest = artifacts[a.architecture]
metadata = api(f'actions/artifacts/{artifact_id}')
assert not metadata['expired'], 'bootstrap toolkit expired; build/check a new toolkit cohort and refresh its pinned receipts'
assert metadata['name'] == f'desktop-toolkit-{a.architecture}'
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
        # The original upload glob also retained build work directories. These
        # are covered by the archive digest but are not admitted providers.
        for entry in z.infolist():
            if not any(part.startswith('work.') for part in Path(entry.filename).parts):
                z.extract(entry, a.output)
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
