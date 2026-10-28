#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Import the launcher's private NoCloud seed into a prepared Luma VM.

This deliberately accepts only the existing luma user's public key and the
requested interface. It does not execute cloud-config commands or create users.
"""
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import tempfile


def seed_values(text):
    if not text.startswith('#cloud-config\n'):
        raise ValueError('Expected the launcher NoCloud configuration')
    data = json.loads(text.split('\n', 1)[1])
    users = data.get('users')
    if not isinstance(users, list) or len(users) != 1 or users[0].get('name') != 'luma':
        raise ValueError('The seed must configure only the luma account')
    keys = users[0].get('ssh_authorized_keys')
    if not isinstance(keys, list) or len(keys) != 1 or not isinstance(keys[0], str):
        raise ValueError('Expected one private workspace public key')
    key = keys[0].strip()
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/]+={0,3}(?: [^\r\n]+)?', key):
        raise ValueError('Invalid workspace public key')
    target = 'desktop'
    for item in data.get('write_files', []):
        if item.get('path') != '/etc/luma-tester-target':
            raise ValueError('Unsupported seed file')
        target = item.get('content', '').strip()
    if target not in ('desktop', 'tablet', 'mobile'):
        raise ValueError('Invalid interface')
    return key, target


def main():
    seed = next((p for name in ('cidata', 'CIDATA')
                 if (p := Path('/dev/disk/by-label') / name).exists()), None)
    if seed is None:
        raise RuntimeError('The private workspace seed is missing')
    with tempfile.TemporaryDirectory(prefix='try-luma-seed-', dir='/run') as mount:
        subprocess.run(['mount', '-o', 'ro,nosuid,nodev,noexec', str(seed), mount], check=True)
        try:
            path = Path(mount) / 'user-data'
            if path.stat().st_size > 65536:
                raise ValueError('Seed exceeds its limit')
            key, target = seed_values(path.read_text())
        finally:
            subprocess.run(['umount', mount], check=True)
    user = pwd.getpwnam('luma')
    directory = Path(user.pw_dir) / '.ssh'
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    os.chown(directory, user.pw_uid, user.pw_gid)
    receipt = Path('/var/lib/try-luma/seed.json')
    try:
        old = json.loads(receipt.read_text()).get('key', '')
    except (OSError, ValueError):
        old = ''
    authorized = directory / 'authorized_keys'
    lines = authorized.read_text().splitlines() if authorized.exists() else []
    lines = [line for line in lines if line != old and line != key]
    temporary = directory / 'authorized_keys.new'
    temporary.write_text('\n'.join([key, *lines]) + '\n')
    temporary.chmod(0o600)
    os.chown(temporary, user.pw_uid, user.pw_gid)
    temporary.replace(authorized)
    subprocess.run(['restorecon', '-RF', str(directory)], check=True)
    Path('/etc/luma-tester-target').write_text(target + '\n')
    receipt.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    receipt.write_text(json.dumps(dict(key=key, target=target,
        fingerprint=hashlib.sha256(key.encode()).hexdigest())) + '\n')
    receipt.chmod(0o600)


if __name__ == '__main__':
    main()
