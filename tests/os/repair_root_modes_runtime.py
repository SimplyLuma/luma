#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Tiny actual OSTree root mode repair; no image build or release."""
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/os/lib'))
import repair_modes as repair
with tempfile.TemporaryDirectory(prefix='luma-root-mode-') as temp:
    root = Path(temp)
    repo = root / 'repo'
    subprocess.run(['ostree', '--repo='+str(repo), 'init', '--mode=archive'], check=True)
    original_tar = root/'original.tar'
    with tarfile.open(original_tar, 'w') as tar:
        entry = tarfile.TarInfo('.')
        entry.type, entry.mode = tarfile.DIRTYPE, 0o755
        tar.addfile(entry)
        entry = tarfile.TarInfo('body')
        entry.mode, entry.size = 0o644, 4
        tar.addfile(entry, io.BytesIO(b'body'))
    def commit(*trees):
        return subprocess.check_output(['ostree', '--repo='+str(repo), 'commit', '--orphan', '--subject=root-mode', *trees], text=True).strip()
    old = commit('--tree=tar='+str(original_tar))
    expected = [(0o2555, '/')]
    changes = repair.write_tar(repair.open_repo(str(repo)), old, expected, str(root/'repair.tar'))
    assert changes == [('/', 0o755, 0o2555)], changes
    new = commit('--tree=ref='+old, '--tree=tar='+str(root/'repair.tar'))
    assert not repair.verify(repair.open_repo(str(repo)), old, new, expected)
    assert not repair.mismatches(repair.open_repo(str(repo)), new, expected)
    print('PASS actual OSTree root directory lookup, tar overlay and metadata verification')
