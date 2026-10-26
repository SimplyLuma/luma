#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Assemble the owner's private Surface input directory from completed artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def assemble(packages, firmware, output):
    if output.exists():
        raise ValueError('Hardware output already exists')
    lock = ROOT / 'config/kernel/surface-pro12-7.3.json'
    receipt = json.loads((packages / 'PACKAGES.json').read_text())
    if receipt['source_lock_sha256'] != sha(lock):
        raise ValueError('Kernel package uses another source lock')
    rpms = [r for r in receipt['files'] if r['file'].startswith('RPMS/')]
    if len(rpms) != 1:
        raise ValueError('Expected exactly one binary device kernel package')
    rpm = packages / rpms[0]['file']
    if sha(rpm) != rpms[0]['sha256']:
        raise ValueError('Kernel package changed')
    output.mkdir(parents=True)
    (output / 'rpms').mkdir()
    shutil.copyfile(rpm, output / 'rpms' / rpm.name)
    # Device-specific firmware and DSP payloads only. Retain the common ARM
    # image's current generic Adreno firmware rather than downgrading it.
    for src, relative in [
        (firmware/'lib/firmware/qcom/x1p42100', 'usr/lib/firmware/qcom/x1p42100'),
        (firmware/'usr/share/qcom/x1p42100', 'usr/share/qcom/x1p42100'),
    ]:
        shutil.copytree(src, output/'rootfs'/relative)
    topology = firmware/'lib/firmware/qcom/x1e80100/X1P42100-Microsoft-Surface-Pro-12in-tplg.bin'
    if not topology.is_file():
        raise ValueError('Surface AudioReach topology must be built before assembly')
    destination = output/'rootfs/usr/lib/firmware/qcom/x1e80100'/topology.name
    destination.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(topology,destination)
    rows=[]
    for path in sorted(p for p in output.rglob('*') if p.is_file()):
        row={'path':str(path.relative_to(output)), 'sha256':sha(path)}
        if row['path'].startswith('rpms/'):
            row['header_sha256']=subprocess.check_output(['rpm','-qp','--nosignature','--qf','%{SHA256HEADER}',str(path)],text=True)
        rows.append(row)
    manifest={'schema':'org.projectluma.hardware-inputs/v1','scope':'owner-personal-test-only',
              'device':'microsoft/sp12in','source_lock_sha256':sha(lock),
              'kernel_release':json.loads(lock.read_text())['release'],'files':rows}
    (output/'hardware-inputs.json').write_text(json.dumps(manifest,indent=2)+'\n')
    subprocess.run(['python3',str(ROOT/'scripts/os/lib/hardware_inputs.py'),
                    '--inputs',str(output),'--device','microsoft/sp12in'],check=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('packages','firmware','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();assemble(a.packages.resolve(),a.firmware.resolve(),a.output.absolute())
