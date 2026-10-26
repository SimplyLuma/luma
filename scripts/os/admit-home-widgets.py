#!/usr/bin/env python3
"""Reuse successful native Home widget jobs with exact source and archive identity."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import zipfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--architecture', required=True, choices=['x86_64', 'aarch64'])
a = p.parse_args()
repo = 'SimplyLuma/luma'
run = 40354455921
source = '09a56d1e51b3421c3c91df2fd337faf30988ebcf'
identities = {
    'x86_64': (12666070556, '2b81a0d9f563decafc0a80bb17781acf3a80fb3c49f0003c4b2cca770d2b2e09'),
    'aarch64': (12665686413, 'c41ba93a94c97a4c5567377d1ac00fab78dff089ba6c5a82d1d22e43954b2680'),
}
def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', f'repos/{repo}/{path}']))
record = api(f'actions/runs/{run}')
assert record['head_sha'] == source and record['path'] == '.github/workflows/desktop-components.yml'
jobs = api(f'actions/runs/{run}/jobs?per_page=100')['jobs']
assert any('home-widgets' in job['name'] and a.architecture in job['name'] and job['conclusion'] == 'success' for job in jobs)
# Other failed jobs in this matrix do not invalidate this completed native producer.
paths = ['src/luma-home-widgets', 'scripts/packages/build-luma-home-widgets.sh',
         'scripts/packages/run-in-rpm-builder.sh', 'scripts/developer/generate-home-widget-styles.py',
         'config/shared/design-tokens.d/home-widgets.json', 'packaging/rpm/luma-home-widgets.spec']
subprocess.run(['git', '-c', f'safe.directory={Path.cwd()}', 'diff', '--exit-code', source, 'HEAD', '--', *paths], check=True)
artifact, digest = identities[a.architecture]
metadata = api(f'actions/artifacts/{artifact}')
assert not metadata['expired'] and metadata['workflow_run']['id'] == run
assert metadata['name'] == f'desktop-home-widgets-{a.architecture}' and metadata['digest'] == 'sha256:' + digest
with tempfile.TemporaryDirectory() as temporary:
    directory = Path(temporary)
    archive = directory / 'artifact.zip'
    with archive.open('wb') as stream:
        subprocess.run(['gh', 'api', f'repos/{repo}/actions/artifacts/{artifact}/zip'], stdout=stream, check=True)
    with archive.open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == digest
    with zipfile.ZipFile(archive) as zipped:
        for entry in zipped.infolist():
            path = Path(entry.filename)
            assert not path.is_absolute() and '..' not in path.parts and stat.S_IFMT(entry.external_attr >> 16) != stat.S_IFLNK
        zipped.extractall(directory / 'extracted')
    extracted = directory / 'extracted'
    receipts = list(extracted.rglob('SOURCE-REVISION'))
    assert len(receipts) == 1 and receipts[0].read_text().strip() == source
    native = list(extracted.glob(f'packages/luma-home-widgets/RPMS/{a.architecture}/luma-home-widgets-*.rpm'))
    assert len(native) == 1
    identity = subprocess.check_output(['rpm', '-qp', '--nosignature', '--qf', '%{NEVRA}', str(native[0])], text=True)
    assert identity == f'luma-home-widgets-0.1.0-1.luma.1.fc44.{a.architecture}'
    output = Path('build/packages/luma-home-widgets')
    assert not output.exists(), 'refuse to overwrite another provider'
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(extracted / 'packages/luma-home-widgets', output)
    Path('build/HOME-WIDGETS-PROVIDER.json').write_text(json.dumps({'run': run, 'source': source, 'artifact_id': artifact, 'archive_sha256': digest, 'architecture': a.architecture}, indent=2) + '\n')
print(f'Admitted checked {a.architecture} Home widgets from native job in run {run}')
