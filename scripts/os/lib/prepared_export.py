#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bind a keyless native export to the exact build before release signing."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess

SCHEMA = 'org.projectluma.prepared-os-export/v1'
FILES = ('build.env', 'provenance.json', 'sbom.spdx.json', 'packages-installed.tsv',
         'image-import-metadata.json', 'image-modes.txt', 'commit-metadata.args')
BUILD_KEYS = frozenset('LUMA_BUILD_ID LUMA_OS_ARCH LUMA_OS_REF_PREFIX LUMA_OS_PROFILE '
    'LUMA_OS_CAPABILITIES_SHA256 LUMA_OS_GRAPH_PREFIX LUMA_OS_HUB_GRAPH_URL_TEMPLATE '
    'LUMA_OS_VERSION LUMA_OS_CHANNEL LUMA_NIGHTLY_DATE LUMA_DISPLAY_NAME '
    'LUMA_SOURCE_REVISION LUMA_SOURCE_DIRTY LUMA_SOURCE_SNAPSHOT_SHA256 '
    'LUMA_RELEASE_CHECKS_SHA256 LUMA_IMAGE_TAG LUMA_IMAGE_ID LUMA_BASE_IMAGE '
    'LUMA_BUILD_STARTED LUMA_BUILD_COMPLETED'.split())

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for body in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(body)
    return h.hexdigest()

def build_env(path):
    values = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if not separator or key not in BUILD_KEYS or key in values:
            raise ValueError('Unexpected or duplicate native build field: ' + key)
        values[key] = value
    return values


def require_private_inputs(directory):
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Signing input directory must be root owned and not group/other writable')
    for name in (*FILES, 'export-prepared.json'):
        info = (directory / name).lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Signing inputs must be private regular root-owned files: ' + name)


def validate_files(directory):
    values = build_env(directory / 'build.env')
    if (values.get('LUMA_SOURCE_DIRTY') != 'false'
            or values.get('LUMA_SOURCE_SNAPSHOT_SHA256')
            or values.get('LUMA_RELEASE_CHECKS_SHA256')):
        raise ValueError('Remote release signing requires clean committed source')
    if values.get('LUMA_OS_ARCH') not in {'x86_64', 'aarch64'}:
        raise ValueError('Unsupported architecture')
    for key, pattern in [('LUMA_BUILD_ID', r'[0-9]{8}\.[0-9]+'),
                         ('LUMA_SOURCE_REVISION', r'[0-9a-f]{40}'),
                         ('LUMA_IMAGE_ID', r'[0-9a-f]{64}')]:
        if not re.fullmatch(pattern, values.get(key, '')):
            raise ValueError('Invalid build identity: ' + key)
    provenance = json.loads((directory / 'provenance.json').read_bytes())
    expected = {'build_id': values['LUMA_BUILD_ID'], 'architecture': values['LUMA_OS_ARCH'],
                'version': values['LUMA_OS_VERSION'], 'channel': values['LUMA_OS_CHANNEL']}
    if any(provenance.get(key) != value for key, value in expected.items()):
        raise ValueError('Provenance differs from build identity')
    if provenance.get('source') != {'revision': values['LUMA_SOURCE_REVISION'], 'dirty': False}:
        raise ValueError('Provenance source differs')
    if provenance.get('image', {}).get('id') != values['LUMA_IMAGE_ID']:
        raise ValueError('Provenance image differs')
    if provenance.get('installed_packages', {}).get('inventory_sha256') != sha(directory / 'packages-installed.tsv'):
        raise ValueError('Package inventory differs from provenance')
    if provenance.get('sbom', {}).get('sha256') != sha(directory / 'sbom.spdx.json'):
        raise ValueError('SBOM differs from provenance')
    manifest = json.loads((directory / 'image-import-metadata.json').read_bytes())
    if manifest.get('image_id') != values['LUMA_IMAGE_ID']:
        raise ValueError('Import metadata names another image')
    return values, manifest

def receipt(directory, candidate, tree, parent, ref, kernel, root):
    values, manifest = validate_files(directory)
    for value in (candidate, tree, root):
        if not re.fullmatch(r'[0-9a-f]{64}', value):
            raise ValueError('Invalid OSTree identity')
    if parent and not re.fullmatch(r'[0-9a-f]{64}', parent):
        raise ValueError('Invalid parent')
    if ref != values['LUMA_OS_REF_PREFIX'] + '/' + values['LUMA_OS_CHANNEL']:
        raise ValueError('Prepared ref differs from build architecture/channel')
    if manifest.get('raw_import') != tree:
        raise ValueError('Import manifest names another tree')
    if not re.fullmatch(r'[A-Za-z0-9_.+~-]+', kernel):
        raise ValueError('Invalid kernel version')
    return {'schema': SCHEMA, 'result': 'PREPARED_UNSIGNED_NOT_RELEASE_QUALIFIED',
            'build_id': values['LUMA_BUILD_ID'], 'architecture': values['LUMA_OS_ARCH'],
            'source_revision': values['LUMA_SOURCE_REVISION'], 'image_id': values['LUMA_IMAGE_ID'],
            'candidate': candidate, 'tree_commit': tree, 'parent': parent, 'ref': ref,
            'kernel': kernel, 'root_dirtree': root,
            'files': {name: sha(directory / name) for name in FILES}}

def verify(directory, digest, source, image, parent):
    path = directory / 'export-prepared.json'
    if not re.fullmatch(r'[0-9a-f]{64}', digest) or sha(path) != digest:
        raise ValueError('Prepared receipt differs from authenticated native receipt')
    document = json.loads(path.read_bytes())
    expected = receipt(directory, *(document[name] for name in
        ('candidate', 'tree_commit', 'parent', 'ref', 'kernel', 'root_dirtree')))
    if document != expected:
        raise ValueError('Prepared receipt or bound build files changed')
    if document['source_revision'] != source or document['image_id'] != image:
        raise ValueError('Prepared export differs from the selected source/image')
    if document['parent'] != parent:
        raise ValueError('Published channel parent changed after native preparation')
    return document


def verify_commit(repository, directory, document):
    import gi
    gi.require_version('OSTree', '1.0')
    from gi.repository import Gio, OSTree
    repo = OSTree.Repo.new(Gio.File.new_for_path(str(repository)))
    repo.open(None)
    ok, commit, _state = repo.load_commit(document['candidate'])
    if not ok:
        raise ValueError('Prepared candidate is unavailable')
    metadata = commit.get_child_value(0).unpack()
    values, _manifest = validate_files(directory)
    expected = {
        'version': values['LUMA_OS_VERSION'],
        'org.projectluma.build-id': values['LUMA_BUILD_ID'],
        'org.projectluma.build-version': values['LUMA_OS_VERSION'],
        'org.projectluma.channel': values['LUMA_OS_CHANNEL'],
        'org.projectluma.source-revision': values['LUMA_SOURCE_REVISION'],
        'org.projectluma.source-dirty': 'false',
        'org.projectluma.architecture': values['LUMA_OS_ARCH'],
        'org.projectluma.base-image-digest': values['LUMA_BASE_IMAGE'].split('@')[-1],
        'org.projectluma.provenance-sha256': sha(directory / 'provenance.json'),
        'org.projectluma.sbom-sha256': sha(directory / 'sbom.spdx.json'),
        'org.projectluma.image-tree': document['tree_commit'],
        'ostree.ref-binding': [document['ref']],
    }
    if any(key in metadata for key in ('org.projectluma.source-snapshot-sha256',
                                      'org.projectluma.release-checks-sha256')):
        raise ValueError('A private snapshot cannot acquire a clean release identity')
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise ValueError('Candidate metadata differs from the authenticated native build')
    actual_parent = bytes(commit.get_child_value(1).unpack()).hex()
    if actual_parent != document['parent']:
        raise ValueError('Candidate commit has a different parent')
    rows = []
    for line in (directory / 'packages-installed.tsv').read_text().splitlines():
        fields = line.split('\t')
        if len(fields) >= 5 and fields[0] != 'gpg-pubkey':
            name, epoch, version, release, arch = fields[:5]
            rows.append((name, str(int(epoch or 0)), version, release, arch))
    rows.sort(key=lambda row: (row[0].encode(), row[4].encode(), int(row[1]), row[2], row[3]))
    if not rows or metadata.get('rpmostree.rpmdb.pkglist') != rows:
        raise ValueError('Candidate package inventory differs from the native build')
    return {'result': 'PASS_NATIVE_CANDIDATE_METADATA', 'candidate': document['candidate']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    capture = sub.add_parser('capture')
    capture.add_argument('--build-dir', type=Path, required=True)
    for name in ('candidate', 'tree-commit', 'parent', 'ref', 'kernel', 'root-dirtree'):
        capture.add_argument('--' + name, required=True)
    check = sub.add_parser('verify')
    check.add_argument('--build-dir', type=Path, required=True)
    for name in ('receipt-sha256', 'source-revision', 'image-id', 'parent'):
        check.add_argument('--' + name, required=True)
    check.add_argument('--env-output', type=Path, required=True)
    check.add_argument('--repo', type=Path)
    args = parser.parse_args()
    if args.command == 'capture':
        document = receipt(args.build_dir, args.candidate, args.tree_commit,
                           args.parent, args.ref, args.kernel, args.root_dirtree)
        path = args.build_dir / 'export-prepared.json'
        with path.open('x') as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write('\n')
        print(json.dumps({'result': document['result'], 'receipt_sha256': sha(path)}))
    else:
        document = verify(args.build_dir, args.receipt_sha256, args.source_revision,
                          args.image_id, args.parent)
        if args.repo is not None:
            verify_commit(args.repo, args.build_dir, document)
        with args.env_output.open('x') as stream:
            for key, name in [('REF', 'ref'), ('TREE_COMMIT', 'tree_commit'),
                              ('CANDIDATE', 'candidate'), ('PARENT', 'parent'),
                              ('ROOT_DIRTREE', 'root_dirtree'), ('KERNEL', 'kernel')]:
                stream.write('LUMA_EXPORT_' + key + '=' + document[name] + '\n')
        print(json.dumps({'result': 'PASS_AUTHENTICATED_PREPARED_EXPORT', 'candidate': document['candidate']}))

if __name__ == '__main__':
    main()
