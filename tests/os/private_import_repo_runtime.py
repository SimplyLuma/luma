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
import sys
import tarfile


def load_helper():
    path = Path(__file__).resolve().parents[2] / 'scripts/os/lib/private_import_repo.py'
    spec = importlib.util.spec_from_file_location('private_import', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tiny_oci(root, source):
    root.mkdir(mode=0o700)
    blobs = root / 'blobs/sha256'
    blobs.mkdir(parents=True)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for name, uid, gid, mode in (
            ('.', 0, 0, 0o555), ('etc', 0, 0, 0o755), ('usr', 0, 0, 0o755), ('usr/lib', 0, 0, 0o755),
            ('usr/lib/ostree-boot', 0, 0, 0o755), ('usr/lib/ostree-boot/efi', 0, 0, 0o700),
            ('usr/lib/luma-import-service', 123, 456, 0o750),
        ):
            entry = tarfile.TarInfo(name)
            entry.type = tarfile.DIRTYPE
            entry.uid, entry.gid, entry.mode = uid, gid, mode
            entry.pax_headers = {'SCHILY.xattr.user.luma-import': 'directory',
                                 'SCHILY.xattr.security.selinux': 'system_u:object_r:pesto_exec_t:s0'}
            archive.addfile(entry)
        for name, mode, data in (('state', 0o640, b'preserve service ownership\n'),
                                 ('empty', 0o600, b''), ('unreadable', 0o000, b'private content\n'),
                                 ('setuid', 0o4755, b'setuid content\n')):
            entry = tarfile.TarInfo('usr/lib/luma-import-service/' + name)
            entry.uid, entry.gid, entry.mode = 123, 456, mode
            entry.size = len(data)
            entry.pax_headers = {'SCHILY.xattr.user.luma-import': 'file',
                                 'SCHILY.xattr.user.binary': b'\x00\xffvalue'.decode('utf-8', 'surrogateescape'),
                                 'SCHILY.xattr.security.selinux': 'system_u:object_r:pesto_exec_t:s0'}
            archive.addfile(entry, io.BytesIO(data))
        entry = tarfile.TarInfo('etc/example')
        entry.size, entry.uid, entry.gid, entry.mode = 0, 123, 456, 0o600
        archive.addfile(entry, io.BytesIO())
        entry = tarfile.TarInfo('usr/lib/luma-import-service/link')
        entry.type, entry.linkname = tarfile.SYMTYPE, 'state'
        entry.uid, entry.gid, entry.mode = 567, 678, 0o777
        archive.addfile(entry)
    tar = raw.getvalue()

    source.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(tar)) as archive:
        archive.extractall(source, numeric_owner=True, filter='fully_trusted')
        for entry in archive.getmembers():
            for key, value in entry.pax_headers.items():
                if key.startswith('SCHILY.xattr.') and key != 'SCHILY.xattr.security.selinux':
                    os.setxattr(source / entry.name, key[len('SCHILY.xattr.'):],
                                value.encode('utf-8', 'surrogateescape'), follow_symlinks=False)

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
    parser.add_argument('--repo-mode', choices=('bare-user',), default='bare-user')
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
    source = args.work / 'source'
    tiny_oci(oci, source)
    environment = {**os.environ, 'CONTAINERS_STORAGE_CONF': args.storage_config}
    helper = load_helper()
    bad = shared / 'bad'
    bad.mkdir(mode=0o770)
    bad_commit = import_image(bad, oci, args.image, environment, args.repo_mode)
    bad_rows = metadata(bad, bad_commit)
    assert bad_rows['/usr']['gid'] == 1003, bad_rows
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
    # Execute the maintained capture + overlays + exhaustive verification with
    # actual OCI-imported objects. A separate raw-policy fixture below also
    # exercises a label unknown to this host without installing a host policy.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/os/lib'))
    import repair_import_metadata as repair
    import repair_modes
    repo = repair.open_repo(str(fixed))
    _, directory_rows = repair.tree(repo, fixed_commit)
    policy_tar = args.work / 'raw-policy-fixture.tar'
    with tarfile.open(policy_tar, 'x', format=tarfile.PAX_FORMAT) as archive:
        entry = tarfile.TarInfo('usr/lib/luma-import-service')
        row = directory_rows['/usr/lib/luma-import-service']
        entry.type, entry.uid, entry.gid, entry.mode = tarfile.DIRTYPE, row['uid'], row['gid'], row['mode']
        attrs = {name: bytes.fromhex(value).decode('utf-8', 'surrogateescape') for name, value in row['xattrs'].items()}
        attrs['security.selinux'] = 'system_u:object_r:pesto_exec_t:s0\0'
        entry.pax_headers = {'SCHILY.xattr.' + name: value for name, value in attrs.items()}
        archive.addfile(entry)
    imported_commit = fixed_commit
    fixed_commit = subprocess.check_output(['ostree', '--repo=' + str(fixed), 'commit', '--orphan',
        '--subject=raw policy fixture', '--tree=ref=' + fixed_commit, '--tree=tar=' + str(policy_tar)], text=True).strip()
    manifest, overlay, modes = (args.work / name for name in ('metadata.json', 'metadata.tar', 'modes.txt'))
    repo = repair.open_repo(str(fixed))
    repair.capture(repo, fixed_commit, str(source), 'a' * 64, manifest, overlay, modes)
    mode_tar = args.work / 'modes.tar'
    repaired_modes = repair_modes.write_tar(repo, fixed_commit, repair_modes.read_expected(modes), mode_tar)
    candidate_repo = args.work / 'candidate'
    subprocess.run(['ostree', '--repo=' + str(candidate_repo), 'init', '--mode=archive'], check=True)
    subprocess.run(['ostree', '--repo=' + str(candidate_repo), 'pull-local', '--untrusted', str(fixed), fixed_commit], check=True)
    command = ['ostree', '--repo=' + str(candidate_repo), 'commit', '--orphan', '--subject=metadata regression',
               '--tree=ref=' + fixed_commit]
    if repaired_modes:
        command.append('--tree=tar=' + str(mode_tar))
    command.append('--tree=tar=' + str(overlay))
    candidate = subprocess.check_output(command, text=True).strip()
    report = repair.verify(repair.open_repo(str(candidate_repo)), fixed_commit, candidate, manifest, modes)
    assert not repair_modes.mismatches(repair.open_repo(str(candidate_repo)), candidate, repair_modes.read_expected(modes))
    fixed_rows = metadata(candidate_repo, candidate)
    final_files, final_dirs = repair.tree(repair.open_repo(str(candidate_repo)), candidate)
    assert (final_dirs['/']['uid'], final_dirs['/']['gid'], final_dirs['/']['mode']) == (0, 0, 0o555)
    assert final_dirs['/usr/lib/luma-import-service']['xattrs']['security.selinux'] == b'system_u:object_r:pesto_exec_t:s0\0'.hex()
    for name, mode in (('empty', 0o600), ('unreadable', 0), ('state', 0o640), ('setuid', 0o4755)):
        record = repair.file_record(repair.open_repo(str(candidate_repo)), final_files['/usr/lib/luma-import-service/' + name])
        assert (record['uid'], record['gid'], record['mode']) == (123, 456, mode), record
        assert record['xattrs']['user.binary'] == b'\x00\xffvalue'.hex(), record
    link = repair.file_record(repair.open_repo(str(candidate_repo)), final_files['/usr/lib/luma-import-service/link'])
    assert (link['uid'], link['gid'], link['link_target']) == (567, 678, 'state'), link
    # A real changed ordinary-file object must fail exhaustive verification.
    altered_tar = args.work / 'altered.tar'
    with tarfile.open(altered_tar, 'x') as archive:
        entry = tarfile.TarInfo('usr/lib/luma-import-service/state')
        data = b'unexpected contents'
        entry.size = len(data)
        archive.addfile(entry, io.BytesIO(data))
    altered = subprocess.check_output(['ostree', '--repo=' + str(candidate_repo), 'commit', '--orphan',
        '--subject=deliberate corruption', '--tree=ref=' + candidate, '--tree=tar=' + str(altered_tar)], text=True).strip()
    try:
        repair.verify(repair.open_repo(str(candidate_repo)), fixed_commit, altered, manifest, modes)
    except ValueError:
        pass
    else:
        raise AssertionError('Changed ordinary file was accepted')
    # A changed copied-file source must fail capture before producing outputs.
    unreadable = source / 'usr/lib/luma-import-service/unreadable'
    original = unreadable.read_bytes()
    unreadable.write_bytes(b'changed source')
    try:
        repair.capture(repo, fixed_commit, str(source), 'a' * 64,
                       args.work / 'bad.json', args.work / 'bad.tar', args.work / 'bad-modes.txt')
    except ValueError:
        pass
    else:
        raise AssertionError('Changed copied-file source was accepted')
    finally:
        unreadable.write_bytes(original)
    assert not any((args.work / name).exists() for name in ('bad.json', 'bad.tar', 'bad-modes.txt'))
    subprocess.run(['ostree', '--repo=' + str(candidate_repo), 'fsck'], check=True)
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
    result = {'result': 'PASS_REAL_TINY_OCI_IMPORT_AND_METADATA_REPAIR', 'metadata_verification': report, 'bad': bad_rows, 'fixed': fixed_rows,
              'original_parent_unchanged': True, 'service_ownership_modes_xattrs_preserved': True,
              'existing_unsafe_cache_refused': True, 'unexpected_file_change_refused': True,
              'changed_copied_source_refused_before_outputs': True, 'raw_policy_fixture_parent': imported_commit}
    (args.work / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
