#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Admit the same Fedora source and maintained patch series for Filer's app.

The shared source sealer calls admitted_upstream(); the app build calls prepare.
This deliberately rebuilds Nautilus with /app paths, never repackaging an RPM
binary configured for /usr. The upstream source and extension ABI retain their
GNOME identity and licenses.
"""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

SRPM_SHA256 = 'd3204c26913e958db46ac785c76eed6ab7fb8cb5d6cddd5dfa93e77a96df6411'
SOURCE_SHA256 = 'e1e285efddf42ed30dda5b29f7f8d242dab4bc1409a9054863b367bad4b34d5a'


def admitted_upstream(srpm):
    """Return exact named source bytes from the admitted official source RPM."""
    srpm = Path(srpm)
    if srpm.is_symlink() or not srpm.is_file() or srpm.stat().st_size > 64 * 1024**2:
        raise ValueError('Filer requires a regular bounded upstream source RPM')
    with srpm.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != SRPM_SHA256:
            raise ValueError('Filer upstream source RPM digest mismatch')
    names = ('nautilus-50.2.2.tar.xz', 'default-terminal.patch')
    with tempfile.TemporaryDirectory(prefix='luma-filer-upstream-') as temporary:
        # rpm2cpio receives only the verified file; cpio extracts only these
        # exact archive-root members, never arbitrary package paths/scripts.
        result = subprocess.run(['rpm2cpio', str(srpm.resolve())], check=True,
                                stdout=subprocess.PIPE)
        subprocess.run(['cpio', '-idm', '--quiet', '--no-absolute-filenames',
                        *('./' + name for name in names)],
                       input=result.stdout, cwd=temporary, check=True)
        files = []
        for name in names:
            path = Path(temporary) / name
            if path.is_symlink() or not path.is_file():
                raise ValueError('Official Filer source member missing or unsafe')
            data = path.read_bytes()
            if name.endswith('.tar.xz') and hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
                raise ValueError('Official Nautilus Source0 differs from admitted source')
            files.append(('upstream-filer/' + name, data, 0o644))
        return files


def prepare(root):
    root = root.resolve()
    source = root / 'upstream-filer/nautilus-50.2.2.tar.xz'
    with source.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != SOURCE_SHA256:
            raise ValueError('Nautilus Source0 digest mismatch')
    spec_patch = root / 'packaging/nautilus/filer-spec-complete.patch'
    names = re.findall(r'^\+Patch\d+:\s+(\S+)\s*$', spec_patch.read_text(), re.MULTILINE)
    maintained = sorted((root / 'patches/nautilus').glob('0[0-9][0-9][0-9]-*.patch'))
    admitted = [path.name for path in maintained if 1 <= int(path.name[:4]) <= 154]
    if not names or names != admitted or names[-1] != '0154-filer-native-application-directory.patch':
        raise ValueError('Filer Flatpak and canonical RPM patch series disagree')
    target = root / 'nautilus-50.2.2'
    if target.exists():
        raise ValueError('Filer prepared source already exists')
    with tarfile.open(source) as archive:
        members = archive.getmembers()
        if not members or any(Path(member.name).parts[0] != 'nautilus-50.2.2'
                              for member in members):
            raise ValueError('Unexpected upstream Nautilus archive root')
        archive.extractall(root, members=members, filter='data')
    for patch in [root / 'upstream-filer/default-terminal.patch'] + [
            root / 'patches/nautilus' / name for name in names]:
        subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch)],
                       cwd=target, check=True)
    print(f'Filer canonical source preparation PASS: {len(names)} maintained patches', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    prepare(parser.parse_args().root)
