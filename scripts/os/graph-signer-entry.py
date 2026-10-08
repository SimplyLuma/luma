#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Verify administrator-sealed graph control before any credential/key use.

The systemd unit pins both the manifest and the trusted tools image ID. Nothing
from a builder checkout or its environment is imported or executed here.
"""
import importlib.util
import os
from pathlib import Path
import re
import stat
import sys


def main():
    if len(sys.argv) != 6 or os.getuid() != 0:
        raise ValueError('usage: graph-signer-entry CONTROL MANIFEST IMAGE ROOT CHANNEL (root only)')
    root, digest, image, pipeline, channel = sys.argv[1:]
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
    environment = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
                   'HOME': '/root', 'PYTHONDONTWRITEBYTECODE': '1',
                   'LUMA_OS_ROOT': pipeline, 'TMPDIR': pipeline + '/tmp',
                   'LUMA_OS_GRAPH_TOOLS_IMAGE': image}
    os.execve('/usr/bin/bash', ['bash', str(root / 'scripts/os/sign-update-graph.sh'),
                              '--channel', channel, '--no-sync'], environment)


if __name__ == '__main__':
    main()
