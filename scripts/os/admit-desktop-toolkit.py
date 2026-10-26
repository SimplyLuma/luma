#!/usr/bin/env python3
"""Verify a pinned, successful native toolkit artifact before reuse in UI CI."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
import zipfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--architecture', required=True, choices=['x86_64', 'aarch64'])
p.add_argument('--output', type=Path, default=Path('build'))
p.add_argument('--providers-only', action='store_true', help='Reuse only unchanged GTK, Adwaita and fonts before rebuilding the platform')
p.add_argument('--run-id', type=int, help='Admit a successful toolkit cohort from this exact source revision')
a = p.parse_args()
# Temporary bring-up bootstrap: expired artifacts fail closed and require a
# new successful matching toolkit cohort with refreshed run/archive receipts.
# Whole-archive digests are GitHub's retained upload identities, not rebuild hashes.
artifacts = {
    'aarch64': (12693178186, '43ddb07adc40940f95d727a8ecfed26c3ba75aa9b6d458d88fae88b13fbf4ae6'),
    'x86_64': (12692698325, '9bd2b7b60dac0a5d64aca1022001bd45fe8ff0268e311eb4003940936839e83f'),
}
run = 40408751360
source = '6863ccf3dd549a4d555cb14f8e9e1e8d77249561'
merge = source
repo = 'SimplyLuma/luma'
# Apply the same verified source and consumed-pin checks to the pinned whole
# toolkit and explicitly selected runs. Unrelated application pins may advance.
if not a.providers_only:
    a.run_id = a.run_id or run
def api(path):
    for attempt in range(3):
        try:
            return json.loads(subprocess.check_output(['gh', 'api', f'repos/{repo}/{path}'], timeout=90))
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
if a.run_id:
    assert not a.providers_only, 'select one admission mode'
    run = a.run_id
record = api(f'actions/runs/{run}')
if a.run_id:
    source = record['head_sha']
    assert len(source) == 40 and all(c in '0123456789abcdef' for c in source)
    merge = source
    subprocess.run(['git', '-c', f'safe.directory={Path.cwd()}', 'merge-base', '--is-ancestor', source, 'HEAD'], check=True)
assert record['conclusion'] == 'success' and record['head_sha'] == source, 'toolkit run identity/result mismatch'
if a.run_id:
    assert record['path'] == '.github/workflows/desktop-toolkit.yml' and record['name'] == 'Native desktop toolkit'
# Provider recipes and inputs must still match the successful source revision.
paths = ['config/desktop/inputs.env', 'scripts/os/build-desktop-toolkit.sh',
         'scripts/packages/build-gtk4.sh', 'scripts/packages/build-libadwaita.sh',
         'scripts/packages/build-luma-developer-platform.sh', 'scripts/packages/build-figtree-fonts.sh',
         'scripts/packages/run-in-rpm-builder.sh', 'scripts/packages/fetch-source-rpm.sh',
         'patches/gtk4', 'patches/libadwaita', 'src/luma-platform',
         'packaging/rpm/luma-developer-platform.spec', 'scripts/fonts',
         'packaging/fontconfig', 'packaging/rpm/google-figtree-fonts.spec',
         'assets/icon-theme/Prairie/symbolic/actions/lumaui-*.svg', 'LICENSE.md', 'scripts/packages/spec-release.sh',
         'tests/toolkit-native']
if a.run_id:
    paths.remove('config/desktop/inputs.env')
if a.providers_only:
    paths = ['scripts/packages/build-gtk4.sh', 'scripts/packages/build-libadwaita.sh',
             'scripts/packages/build-figtree-fonts.sh', 'scripts/packages/run-in-rpm-builder.sh',
             'scripts/packages/fetch-source-rpm.sh', 'scripts/packages/spec-release.sh',
             'patches/gtk4', 'patches/libadwaita', 'scripts/fonts', 'packaging/fontconfig',
             'packaging/rpm/google-figtree-fonts.spec', 'LICENSE.md']
if a.providers_only or a.run_id:
    def values(body):
        return dict(line.split('=', 1) for line in body.splitlines() if line and not line.startswith('#') and '=' in line)
    prior = values(subprocess.check_output(['git', '-c', f'safe.directory={Path.cwd()}', 'show', f'{source}:config/desktop/inputs.env'], text=True))
    current = values(Path('config/desktop/inputs.env').read_text())
    keys = [key for key in prior if key.startswith(('GTK4_', 'LIBADWAITA_', 'FIGTREE_', 'FEDORA_RPM_BUILD_CONTAINER') + (() if a.providers_only else ('LUMA_DEVELOPER_PLATFORM_',)))]
    assert keys and all(prior[key] == current.get(key) for key in keys), 'toolkit provider pin changed'
subprocess.run(['git', '-c', f'safe.directory={Path.cwd()}', 'diff', '--exit-code', source, 'HEAD', '--', *paths], check=True)
if a.run_id:
    candidates = api(f'actions/runs/{run}/artifacts?per_page=100')['artifacts']
    selected = [row for row in candidates if row['name'] == f'desktop-toolkit-{a.architecture}']
    assert len(selected) == 1, 'ambiguous toolkit artifact'
    artifact_id = selected[0]['id']
    digest = selected[0]['digest'].removeprefix('sha256:')
    assert len(digest) == 64 and all(c in '0123456789abcdef' for c in digest)
else:
    artifact_id, digest = artifacts[a.architecture]
metadata = api(f'actions/artifacts/{artifact_id}')
assert not metadata['expired'], 'bootstrap toolkit expired; build/check a new toolkit cohort and refresh its pinned receipts'
assert metadata['name'] == f'desktop-toolkit-{a.architecture}'
assert metadata['workflow_run']['id'] == run and metadata['digest'] == f'sha256:{digest}'
a.output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryFile() as archive:
    for attempt in range(3):
        archive.seek(0)
        archive.truncate()
        try:
            subprocess.run(['gh', 'api', f'repos/{repo}/actions/artifacts/{artifact_id}/zip'], stdout=archive, check=True, timeout=300)
            break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
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
if a.providers_only:
    import shutil
    platform = a.output / 'packages/luma-developer-platform'
    if platform.exists():
        shutil.rmtree(platform)
    (a.output / 'SOURCE-REVISION').unlink()
    (a.output / 'REUSED-TOOLKIT-PROVIDERS.json').write_text(json.dumps({'run': run, 'source': source, 'receipt_source': merge, 'artifact_id': artifact_id, 'archive_sha256': digest, 'platform_reused': False}, indent=2) + '\n')
print(f'Admitted {len(rpms)} checked {a.architecture} toolkit RPMs from successful run {run}')
