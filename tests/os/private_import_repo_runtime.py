#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Opt-in tiny real OCI importer regression; no OS image is built or published.

Run as root in bounded admission with --image and --storage-config pointing to
an existing qualified importer image/store. --work must be a fresh owned path.
"""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile


def load_helper():
    path = Path(__file__).resolve().parents[2] / 'scripts/os/lib/private_import_repo.py'
    spec = importlib.util.spec_from_file_location('private_import', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tiny_oci(root):
    root.mkdir(mode=0o700)
    blobs = root / 'blobs/sha256'
    blobs.mkdir(parents=True)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for name, uid, gid, mode in (
            ('usr', 0, 0, 0o755), ('usr/lib', 0, 0, 0o755),
            ('usr/lib/ostree-boot', 0, 0, 0o755), ('usr/lib/ostree-boot/efi', 0, 0, 0o700),
            ('usr/lib/luma-import-service', 123, 456, 0o750),
        ):
            entry = tarfile.TarInfo(name)
            entry.type = tarfile.DIRTYPE
            entry.uid, entry.gid, entry.mode = uid, gid, mode
            entry.pax_headers = {'SCHILY.xattr.user.luma-import': 'directory'}
            archive.addfile(entry)
        entry = tarfile.TarInfo('usr/lib/luma-import-service/state')
        entry.uid, entry.gid, entry.mode = 123, 456, 0o640
        data = b'preserve service ownership\n'
        entry.size = len(data)
        entry.pax_headers = {'SCHILY.xattr.user.luma-import': 'file'}
        archive.addfile(entry, io.BytesIO(data))
    tar = raw.getvalue()

    def blob(data, kind):
        digest = hashlib.sha256(data).hexdigest()
        (blobs / digest).write_bytes(data)
        return {'mediaType': kind, 'digest': 'sha256:' + digest, 'size': len(data)}

    layer = blob(gzip.compress(tar, mtime=0), 'application/vnd.oci.image.layer.v1.tar+gzip')
    config = blob(json.dumps({'architecture': 'arm64', 'os': 'linux',
        'config': {'Labels': {'ostree.bootable': 'true'}},
        'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + hashlib.sha256(tar).hexdigest()]}}).encode(),
        'application/vnd.oci.image.config.v1+json')
    manifest = blob(json.dumps({'schemaVersion': 2, 'config': config, 'layers': [layer]}).encode(),
                    'application/vnd.oci.image.manifest.v1+json')
    manifest['annotations'] = {'org.opencontainers.image.ref.name': 'luma'}
    (root / 'index.json').write_text(json.dumps({'schemaVersion': 2, 'manifests': [manifest]}))
    (root / 'oci-layout').write_text('{"imageLayoutVersion":"1.0.0"}\n')


def import_image(repo, oci, image, environment, mode):
    subprocess.run(['ostree', '--repo=' + str(repo), 'init', '--mode=' + mode], check=True)
    result = subprocess.run(['podman', 'run', '--rm', '--pull=never', '--net=none', '--privileged',
        '--cgroups=disabled', '--security-opt', 'label=disable',
        '--volume', str(oci) + ':/run/luma-import/oci:ro',
        '--volume', str(repo) + ':/run/luma-import/repo',
        '--entrypoint', '/usr/bin/ostree', image, 'container', 'image', 'pull',
        '/run/luma-import/repo', 'ostree-unverified-image:oci:/run/luma-import/oci:luma'],
        env=environment, capture_output=True, text=True, timeout=150)
    (repo.parent / (repo.name + '-import.log')).write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    matches = re.findall(r'(?:Wrote:|No changes in) .* => ([a-f0-9]{64})', result.stdout)
    if len(matches) != 1:
        raise ValueError('No unique actual import merge commit: ' + result.stdout)
    return matches[0]


def metadata(path, commit):
    import gi
    gi.require_version('OSTree', '1.0')
    from gi.repository import Gio, OSTree
    repo = OSTree.Repo.new(Gio.File.new_for_path(str(path)))
    repo.open(None)
    _, root, _ = repo.read_commit(commit, None)
    rows = {}
    for name in ('/usr', '/usr/lib/ostree-boot/efi', '/usr/lib/luma-import-service',
                 '/usr/lib/luma-import-service/state'):
        node = root.resolve_relative_path(name.lstrip('/'))
        info = node.query_info('standard::type,unix::uid,unix::gid,unix::mode',
                               Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
        node.ensure_resolved()
        if info.get_file_type() == Gio.FileType.DIRECTORY:
            _, meta = repo.load_variant(OSTree.ObjectType.DIR_META, node.tree_get_metadata_checksum())
            attrs = meta.get_child_value(3)
        else:
            _, stream, _, attrs = repo.load_file(node.get_checksum(), None)
        rows[name] = {'uid': info.get_attribute_uint32('unix::uid'),
                      'gid': info.get_attribute_uint32('unix::gid'),
                      'mode': info.get_attribute_uint32('unix::mode') & 0o7777,
                      'xattrs': {bytes(k).rstrip(b'\0').decode(): bytes(v).hex()
                                 for k, v in attrs.unpack()}}
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--repo-mode', choices=('bare-user', 'bare'), default='bare')
    parser.add_argument('--storage-config', required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    assert os.getuid() == 0 and args.work.is_absolute() and not args.work.exists()
    args.work.mkdir(mode=0o700)
    for attr in ('system.posix_acl_default', 'system.posix_acl_access'):
        if attr in os.listxattr(args.work):
            os.removexattr(args.work, attr)
    os.chown(args.work, 0, 0)
    os.chmod(args.work, 0o700)
    shared = args.work / 'shared'
    shared.mkdir(mode=0o700)
    os.chown(shared, 0, 1003)
    os.chmod(shared, 0o2770)
    subprocess.run(['setfacl', '-m', 'd:u::rwx,d:g::rwx,d:m::rwx,d:o::---', str(shared)], check=True)
    parent_before = (shared.stat().st_mode, shared.stat().st_uid, shared.stat().st_gid,
                     {x: os.getxattr(shared, x) for x in os.listxattr(shared)})
    oci = args.work / 'oci'
    tiny_oci(oci)
    environment = {**os.environ, 'CONTAINERS_STORAGE_CONF': args.storage_config}
    helper = load_helper()
    bad = shared / 'bad'
    bad.mkdir(mode=0o770)
    bad_commit = import_image(bad, oci, args.image, environment, args.repo_mode)
    bad_rows = metadata(bad, bad_commit)
    if args.repo_mode == 'bare-user':
        assert bad_rows['/usr']['gid'] == 1003, bad_rows
    else:
        assert bad_rows['/usr']['gid'] == 0, bad_rows
        assert (bad_rows['/usr/lib/luma-import-service']['uid'], bad_rows['/usr/lib/luma-import-service']['gid']) == (123, 456)
        assert 'system.posix_acl_default' in bad_rows['/usr']['xattrs'], bad_rows
    for existing in (bad, shared):
        try:
            helper.ensure_private(existing)
        except ValueError:
            pass
        else:
            raise AssertionError('Unsafe existing cache was adopted')
    fixed = shared / 'fixed'
    helper.ensure_private(fixed)
    helper.ensure_private(fixed)  # Exact private cache admission is repeatable.
    fixed_commit = import_image(fixed, oci, args.image, environment, args.repo_mode)
    fixed_rows = metadata(fixed, fixed_commit)
    expected = {'/usr': (0, 0, 0o755), '/usr/lib/ostree-boot/efi': (0, 0, 0o700),
                '/usr/lib/luma-import-service': (123, 456, 0o750),
                '/usr/lib/luma-import-service/state': (123, 456, 0o640)}
    for path, (uid, gid, mode) in expected.items():
        row = fixed_rows[path]
        assert (row['uid'], row['gid'], row['mode']) == (uid, gid, mode), (path, row)
        assert not any(key.startswith('system.posix_acl_') for key in row['xattrs']), row
        assert row['xattrs'].get('user.luma-import') == ('file' if path.endswith('/state') else 'directory').encode().hex(), row
    assert parent_before == (shared.stat().st_mode, shared.stat().st_uid, shared.stat().st_gid,
                            {x: os.getxattr(shared, x) for x in os.listxattr(shared)})
    result = {'result': 'PASS_REAL_TINY_OCI_IMPORT', 'bad': bad_rows, 'fixed': fixed_rows,
              'original_parent_unchanged': True, 'service_ownership_modes_xattrs_preserved': True,
              'existing_unsafe_cache_refused': True}
    (args.work / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
