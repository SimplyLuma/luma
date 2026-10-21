#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Admit exact existing candidate media for a full verification retry."""
from pathlib import Path
import hashlib
import ipaddress
import re
import sys


def codec_endpoint(address, resolved_addresses):
    """Admit a public IPv4 address currently owned by the fixed upstream name.

    This only routes disposable verification VMs around an unhealthy official
    mirror node. It does not select another repository or relax TLS/RPM trust.
    """
    value = ipaddress.IPv4Address(address)
    if (not value.is_global or value.is_multicast or value.is_reserved
            or str(value) != address or address not in set(resolved_addresses)):
        raise ValueError('codec fixture must use a current public address of the official hostname')
    return 'codecs.fedoraproject.org', address


def admit(iso, checksum, channel, ref, commit, version, build, atlas, key):
    iso = Path(iso)
    paths = [iso, Path(str(iso) + '.release'), Path(str(iso) + '.sha256')]
    if not re.fullmatch(r'[0-9a-f]{64}', checksum):
        raise ValueError('an explicit SHA-256 is required')
    if iso.parent.is_symlink() or any(p.is_symlink() or not p.is_file() for p in paths):
        raise ValueError('existing media must be regular files')
    release = {}
    for line in paths[1].read_text().splitlines():
        name, value = line.split('=', 1)
        if name in release:
            raise ValueError('duplicate release field')
        release[name] = value
    expected = dict(channel=channel, ref=ref, commit=commit, version=version,
                    candidate_build=build, atlas_source_revision=atlas,
                    release_key_fingerprint=key, preview_credential='none',
                    test='', test_build='')
    if any(release.get(k) != v for k, v in expected.items()):
        raise ValueError('existing media belong to different inputs or test media')
    if paths[2].read_text().strip() != checksum:
        raise ValueError('checksum sidecar differs from the explicit SHA-256')
    with iso.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != checksum:
            raise ValueError('existing ISO bytes differ from the explicit SHA-256')


if __name__ == '__main__':
    admit(*sys.argv[1:])
