#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the production checksum publisher with non-writable prior exports."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

repo = Path(__file__).resolve().parents[2]
helper = repo/'scripts/android/write-checksums.sh'
assert os.geteuid() != 0, 'Permission controls require an ordinary user'
with tempfile.TemporaryDirectory() as scratch:
    root=Path(scratch);output=root/'output';output.mkdir()
    (output/'system.img').write_bytes(b'synthetic system')
    (output/'vendor.img').write_bytes(b'synthetic vendor')
    manifest=output/'SHA256SUMS';manifest.write_text('previous manifest');manifest.chmod(0o444)
    def publish(*files):
        return subprocess.run(['bash','-c','source "$1"; shift; luma_android_write_checksums "$@"',
            'checksum-fixture',str(helper),str(output),*files],capture_output=True,text=True)
    result=publish();assert result.returncode==0,result.stderr
    for line in manifest.read_text().splitlines():
        digest,name=line.split('  ',1)
        assert digest==hashlib.sha256((output/name).read_bytes()).hexdigest()
    assert len(manifest.read_text().splitlines())==2
    victim=root/'unrelated';victim.write_text('do not change');victim.chmod(0o444)
    manifest.unlink();manifest.symlink_to(victim)
    result=publish('system.img','vendor.img');assert result.returncode==0,result.stderr
    assert not manifest.is_symlink() and victim.read_text()=='do not change'
    previous=manifest.read_bytes()
    (output/'unreadable').write_text('denied');(output/'unreadable').chmod(0)
    result=publish();assert result.returncode!=0
    assert manifest.read_bytes()==previous and not list(output.glob('.luma-checksums-*'))
    (output/'unreadable').chmod(0o600)
print('PASS readonly prior manifest, exact hashes, explicit image list, symlink-target preservation and failed-read atomicity')
