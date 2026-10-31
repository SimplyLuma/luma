#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Check the selected signed mirror's transport bytes before native CI intake."""
import argparse
import configparser
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
from urllib.parse import urlsplit

SOURCE = 'bdaf62fd08989b790decfac941a6947f8f72273a'
REFS = {'app/com.rhyme.viola/aarch64/beta',
        'runtime/org.projectluma.Platform/aarch64/44',
        'runtime/org.projectluma.Platform.GL.default/aarch64/44',
        'runtime/org.projectluma.Platform.Locale/aarch64/44'}
DIRECTORIES = {'repo', 'repo/objects', 'repo/tmp', 'repo/state', 'repo/refs',
               'repo/refs/heads', 'repo/refs/remotes', 'repo/refs/mirrors', 'site'}
LIMIT = 8 * 1024**3


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate(archive, destination, expected_sha):
    assert re.fullmatch('[0-9a-f]{64}', expected_sha)
    assert archive.is_file() and not archive.is_symlink() and archive.stat().st_size <= LIMIT
    assert digest(archive) == expected_sha, 'Signed input archive digest differs'
    assert not destination.exists()
    destination.mkdir(mode=0o755)
    files = {}; directories = set(); total = 0
    with tarfile.open(archive, 'r:') as tar:
        for member in tar:
            name = member.name.rstrip('/') if member.isdir() else member.name
            path = PurePosixPath(name)
            assert name and str(path) == name and not path.is_absolute() and '..' not in path.parts
            assert all(32 <= ord(char) < 127 for char in name)
            if member.isdir():
                assert name in DIRECTORIES and name not in directories and member.size == 0
                directories.add(name)
                (destination / name).mkdir(parents=True, exist_ok=True)
                continue
            assert member.isfile() and not member.issparse() and name not in files
            assert 0 <= member.size <= LIMIT and len(files) < 100000
            total += member.size
            assert total <= LIMIT
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, target.open('xb') as output:
                hasher = hashlib.sha256(); size = 0
                while chunk := source.read(1024**2):
                    output.write(chunk); hasher.update(chunk); size += len(chunk)
            assert size == member.size
            target.chmod(0o444)
            files[name] = {'bytes': size, 'sha256': hasher.hexdigest()}
    assert (destination / 'INPUTS.json').stat().st_size <= 32 * 1024**2
    document = json.loads((destination / 'INPUTS.json').read_bytes())
    assert document['schema'] == 'org.projectluma.depot.runtime-input/v1'
    profile = document['profile']
    assert profile['source'] == SOURCE and profile['architecture'] == 'aarch64'
    assert set(profile['refs']) == REFS
    assert all(re.fullmatch('[0-9a-f]{64}', commit) for commit in profile['refs'].values())
    assert document['files'] == {key: value for key, value in files.items() if key != 'INPUTS.json'}
    ordinary = {'repo/config', 'repo/summary', 'repo/summary.sig', 'site/luma.flatpakrepo'}
    ordinary |= {'repo/refs/heads/' + ref for ref in REFS}
    objects = []
    for name, row in document['files'].items():
        if name in ordinary:
            continue
        assert re.fullmatch(r'repo/objects/[0-9a-f]{2}/[0-9a-f]{62}\.(filez|dirtree|dirmeta|commit|commitmeta)', name), name
        if not name.endswith('.commitmeta'):
            objects.append(row)
    assert len(objects) == profile['reachable_object_count']
    assert sum(row['bytes'] for row in objects) == profile['reachable_stored_bytes']
    for name, key in [('repo/summary', 'summary_sha256'),
                      ('repo/summary.sig', 'summary_signature_sha256'),
                      ('site/luma.flatpakrepo', 'flatpakrepo_sha256')]:
        assert digest(destination / name) == profile[key]
    for ref, commit in profile['refs'].items():
        assert (destination / 'repo/refs/heads' / ref).read_text() == commit + '\n'
    config = configparser.ConfigParser(interpolation=None)
    config.read(destination / 'repo/config')
    assert config.sections() == ['core']
    assert dict(config['core']) == {'repo_version': '1', 'mode': 'archive-z2'}
    # Signature verification occurs through normal Flatpak/libostree installation.
    return {'scope': 'SIGNED_MIRROR_TRANSPORT_BYTES_NOT_RUNTIME_ACCEPTANCE',
            'archive_sha256': expected_sha, 'profile': profile, 'files': len(files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('sha256')
    parser.add_argument('--url', required=True)
    args = parser.parse_args()
    url = urlsplit(args.url)
    assert url.scheme == 'https' and url.hostname == 'dl.simplyluma.com'
    assert not url.username and not url.password and url.port is None and not url.query and not url.fragment
    assert url.path == '/inputs/depot-build/' + args.sha256 + '/runtime-input.tar'
    print(json.dumps(validate(args.archive, args.destination, args.sha256), sort_keys=True))


if __name__ == '__main__':
    main()
