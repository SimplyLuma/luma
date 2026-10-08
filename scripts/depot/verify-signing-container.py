#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Check actual signer custody mounts, not a container name or label alone."""
import argparse
import json
from pathlib import Path
import sys

def check(doc, control, inputs, output, keys, control_sha, input_sha):
    host = doc['HostConfig']
    if host.get('Privileged') or host.get('NetworkMode') != 'none':
        raise ValueError('signer must be unprivileged and networkless')
    if host.get('CapAdd'):
        raise ValueError('signer cannot add capabilities')
    state = doc['State']; pid = state.get('Pid', 0)
    if not state.get('Running') or pid <= 0:
        raise ValueError('signer must have a current running init process')
    proc = Path(f'/proc/{pid}')
    before = (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]
    status = (proc / 'status').read_text().splitlines()
    caps = {line.split(':', 1)[0]: line.split(':', 1)[1].strip()
            for line in status if line.split(':', 1)[0] in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb')}
    if set(caps) != {'CapInh','CapPrm','CapEff','CapBnd','CapAmb'} or any(int(v, 16) for v in caps.values()):
        raise ValueError('signer init retains Linux capabilities')
    oci = json.loads(Path(doc['OCIConfigPath']).read_text())
    if any(oci.get('process', {}).get('capabilities', {}).values()):
        raise ValueError('signer OCI process retains capabilities')
    after = (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]
    if before != after: raise ValueError('signer process changed during verification')
    labels = doc['Config'].get('Labels', {})
    for key, expected in [('role', 'signer'), ('control', control_sha), ('inputs', input_sha)]:
        if labels.get('org.projectluma.depot.' + key) != expected:
            raise ValueError('signer custody label differs from reviewed snapshot')
    expected = {str(control): (control, False), str(inputs): (inputs, False),
                str(keys): (keys, True)}
    expected.update({str(output / part): (output / part, True)
                     for part in ('repo', 'site', 'work', 'tmp', 'releases')})
    if any(m.get('Type') != 'bind' for m in doc['Mounts']) or host.get('Tmpfs'):
        raise ValueError('unexpected signer volume or tmpfs mount')
    hooks = {'PYTHONPATH', 'PYTHONHOME', 'BASH_ENV', 'ENV', 'LD_PRELOAD', 'LD_LIBRARY_PATH'}
    for setting in doc['Config'].get('Env', []):
        name, _, value = setting.partition('=')
        if name in hooks and value:
            raise ValueError('signer inherited an executable environment hook')
    mounts = doc['Mounts']
    if {m['Destination'] for m in mounts} != set(expected):
        raise ValueError('unexpected signer bind mount')
    for mount in mounts:
        source, writable = expected[mount['Destination']]
        if Path(mount['Source']).resolve() != source.resolve() or mount['RW'] != writable:
            raise ValueError('signer source or write mode differs')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('control', 'inputs', 'output', 'keys'): parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--control-sha', required=True)
    parser.add_argument('--inputs-sha', required=True)
    args = parser.parse_args()
    docs = json.load(sys.stdin)
    if len(docs) != 1: raise ValueError('expected exactly one signer container')
    check(docs[0], args.control, args.inputs, args.output, args.keys, args.control_sha, args.inputs_sha)

if __name__ == '__main__': main()
