#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Verify administrator-sealed graph control before any credential/key use.

The systemd unit pins both the manifest and the trusted tools image ID. Nothing
from a builder checkout or its environment is imported or executed here.
"""
import argparse
import hashlib
import json
import importlib.util
import os
from pathlib import Path
import re
import stat
import sys


def selection(control, architecture=None, profile='full', hardware_target=''):
    spec=importlib.util.spec_from_file_location('entry_graph_bucket',control/'scripts/os/lib/graph_bucket.py')
    buckets=importlib.util.module_from_spec(spec);spec.loader.exec_module(buckets)
    if architecture is None:
        architecture=buckets.sibling('devices').device(hardware_target)['arch'] if hardware_target else 'x86_64'
    bucket=buckets.GraphBucket(architecture,hardware_target)
    spec=importlib.util.spec_from_file_location('entry_desktop_target',control/'scripts/os/lib/desktop_target.py')
    targets=importlib.util.module_from_spec(spec);spec.loader.exec_module(targets)
    targets.profile(control,architecture,profile)
    return bucket


def pinned_source(path,expected):
    if not re.fullmatch('[a-f0-9]{64}',expected or ''):
        raise ValueError('Explicit graph source needs its administrator-pinned SHA256')
    if not path.is_absolute():raise ValueError('Graph source must be absolute')
    for member in (path,*path.parents):
        entry=member.lstat()
        if entry.st_uid or entry.st_mode&0o022 or stat.S_ISLNK(entry.st_mode):
            raise ValueError('Graph source has an unsafe administrator-owned path')
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
        raise ValueError('Pinned graph source changed')
    return json.loads(path.read_text())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('control','digest','image','pipeline','channel'):parser.add_argument(name)
    parser.add_argument('--arch',choices=('x86_64','aarch64'))
    parser.add_argument('--profile',default='full')
    parser.add_argument('--hardware-target',default='')
    parser.add_argument('--source',type=Path)
    parser.add_argument('--source-sha256')
    args=parser.parse_args()
    if os.getuid()!=0:raise ValueError('Graph signer entry is root only')
    root,digest,image,pipeline,channel=args.control,args.digest,args.image,args.pipeline,args.channel
    if not re.fullmatch(r'[a-f0-9]{64}', digest) or not re.fullmatch(r'[a-f0-9]{64}', image):
        raise ValueError('unsealed graph signer identity')
    if channel not in ('beta', 'nightly', 'stable'):
        raise ValueError('unsupported graph channel')
    root = Path(root)
    for directory in (root, *root.parents):
        entry = directory.lstat()
        if not stat.S_ISDIR(entry.st_mode) or entry.st_uid or entry.st_mode & 0o022:
            raise ValueError('graph control has an unsafe directory owner/type')
    helper = root / 'scripts/depot/seal-signing-control.py'
    for member in (root / 'scripts', root / 'scripts/depot', helper):
        entry = member.lstat()
        if entry.st_uid or entry.st_mode & 0o022 or stat.S_ISLNK(entry.st_mode):
            raise ValueError('graph verifier has an unsafe owner/type')
    # These fixed, administrator-owned entry and verifier files are outside all
    # builder mounts. The complete snapshot is checked before source execution.
    spec = importlib.util.spec_from_file_location('graph_control', helper)
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    verifier.verify(root, digest)
    if not Path(pipeline).is_absolute():
        raise ValueError('pipeline root must be absolute')
    bucket=selection(root,args.arch,args.profile,args.hardware_target)
    if bool(args.source)!=bool(args.source_sha256):raise ValueError('Source path and its SHA256 must be supplied together')
    if bucket.hardware_target and args.source is None:
        raise ValueError('Hardware signing requires an explicitly pinned source; generic Hub policy is not device policy')
    if args.source:bucket.validate_graph(pinned_source(args.source,args.source_sha256),channel)
    environment = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
                   'HOME': '/root', 'PYTHONDONTWRITEBYTECODE': '1',
                   'LUMA_OS_ROOT': pipeline, 'TMPDIR': pipeline + '/tmp',
                   'LUMA_OS_GRAPH_TOOLS_IMAGE': image,
                   'LUMA_TARGET_ARCHITECTURE':bucket.architecture,'LUMA_OS_PROFILE':args.profile}
    command=['bash',str(root/'scripts/os/sign-update-graph.sh'),'--arch',bucket.architecture,
             '--profile',args.profile,'--channel',channel,'--no-sync']
    if bucket.hardware_target:command+=['--hardware-target',bucket.hardware_target]
    if args.source:command+=['--source',str(args.source)]
    os.execve('/usr/bin/bash',command,environment)


if __name__ == '__main__':
    main()
