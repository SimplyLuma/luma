#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Restore metadata lost by the container importer’s user-mode checkout.

Read ownership, modes and non-SELinux attributes from the pinned, mounted image.
Keep SELinux labels from the raw import: those are computed using the image's
policy, whereas a containers-storage mount has build-host storage labels.
The overlay covers existing directories, symlinks and regular files which
checkout copies: empty files and files without owner-read permission.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import tarfile

import gi
gi.require_version('OSTree', '1.0')
from gi.repository import Gio, OSTree
from repair_modes import open_repo, read_expected, verify as verify_modes

SCHEMA = 'org.projectluma.image-import-metadata/v1'
STORAGE_ATTRIBUTES = ('trusted.overlay.', 'user.overlay.', 'user.fuseoverlayfs.',
                      'trusted.fuseoverlayfs.', 'user.containers.override_stat',
                      'user.ostreemeta')


def tree(repo, revision):
    _, root, _ = repo.read_commit(revision, None)
    root.ensure_resolved()
    files, directories = {}, {}
    pending = [('/', root.tree_get_contents_checksum(), root.tree_get_metadata_checksum())]
    while pending:
        path, contents, metadata = pending.pop()
        _, value = repo.load_variant(OSTree.ObjectType.DIR_META, metadata)
        uid, gid, mode, attrs = value.unpack()
        directories[path] = {'uid': socket.ntohl(uid), 'gid': socket.ntohl(gid),
                             'mode': socket.ntohl(mode) & 0o7777,
                             'xattrs': {bytes(k).rstrip(b'\0').decode(): bytes(v).hex()
                                        for k, v in attrs}}
        _, value = repo.load_variant(OSTree.ObjectType.DIR_TREE, contents)
        entries, children = value.unpack()
        for name, checksum in entries:
            if not name or name in ('.', '..') or '/' in name or len(checksum) != 32:
                raise ValueError('Invalid imported file entry')
            files[path.rstrip('/') + '/' + name] = bytes(checksum).hex()
        for name, child_tree, child_metadata in children:
            if not name or name in ('.', '..') or '/' in name or len(child_tree) != 32 or len(child_metadata) != 32:
                raise ValueError('Invalid imported directory entry')
            pending.append((path.rstrip('/') + '/' + name, bytes(child_tree).hex(),
                            bytes(child_metadata).hex()))
    return files, directories


def attributes(target, *, symlink=False):
    names = os.listxattr(target, follow_symlinks=False) if symlink else os.listxattr(target)
    if any(name.startswith(STORAGE_ATTRIBUTES) for name in names):
        raise ValueError('Storage implementation attribute in mounted image')
    return {name: (os.getxattr(target, name, follow_symlinks=False) if symlink else os.getxattr(target, name)).hex()
            for name in names if name != 'security.selinux'}


def identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_size, info.st_ctime_ns


def sensitive_mode(path, mode, modes):
    excluded = ('/proc', '/sys', '/dev', '/run', '/tmp', '/var', '/sysroot', '/boot')
    if any(path == top or path.startswith(top + '/') for top in excluded):
        return
    if mode & 0o7000 or not mode & 0o400:
        if '\n' in path or '\r' in path:
            raise ValueError('Sensitive-mode path cannot be represented in the mode inventory')
        modes[path] = mode


def image_directory(base, path, files, copied_files, modes, expected_files):
    source = '/etc' + path[len('/usr/etc'):] if path == '/usr/etc' or path.startswith('/usr/etc/') else path
    descriptor = os.dup(base)
    try:
        for part in source.split('/'):
            if not part:
                continue
            if part in ('.', '..'):
                raise ValueError('Invalid image directory path: ' + source)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        before = os.fstat(descriptor)
        result = {'uid': before.st_uid, 'gid': before.st_gid, 'mode': stat.S_IMODE(before.st_mode),
                  'xattrs': attributes(descriptor)}
        sensitive_mode(path, result['mode'], modes)
        seen = set()
        with os.scandir(descriptor) as children:
            for child in children:
                target = path.rstrip('/') + '/' + child.name
                if target not in files:
                    continue
                seen.add(target)
                info = child.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    name = '/proc/self/fd/' + str(descriptor) + '/' + child.name
                    record = {'kind': 'symlink', 'uid': info.st_uid, 'gid': info.st_gid,
                              'mode': stat.S_IMODE(info.st_mode), 'xattrs': attributes(name, symlink=True),
                              'link_target': os.readlink(child.name, dir_fd=descriptor)}
                    if identity(info) != identity(os.stat(child.name, dir_fd=descriptor, follow_symlinks=False)):
                        raise ValueError('Image symlink changed: ' + target)
                    copied_files[target] = record
                elif stat.S_ISREG(info.st_mode):
                    sensitive_mode(target, stat.S_IMODE(info.st_mode), modes)
                    if info.st_size and info.st_mode & stat.S_IRUSR:
                        continue
                    fd = os.open(child.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
                    try:
                        original = os.fstat(fd)
                        if identity(info) != identity(original):
                            raise ValueError('Image file changed before capture: ' + target)
                        digest = hashlib.sha256()
                        while chunk := os.read(fd, 1024 * 1024):
                            digest.update(chunk)
                        record = {'kind': 'regular', 'uid': original.st_uid, 'gid': original.st_gid,
                                  'mode': stat.S_IMODE(original.st_mode), 'size': original.st_size,
                                  'xattrs': attributes(fd), 'content_sha256': digest.hexdigest()}
                        if identity(original) != identity(os.fstat(fd)):
                            raise ValueError('Image file changed during capture: ' + target)
                        copied_files[target] = record
                    finally:
                        os.close(fd)
                else:
                    raise ValueError('Unsupported retained image file type: ' + target)
        if seen != expected_files:
            raise ValueError('Imported file paths do not match the pinned image directory: ' + path)
        if identity(before) != identity(os.fstat(descriptor)):
            raise ValueError('Image directory changed while collecting metadata: ' + source)
        return result
    finally:
        os.close(descriptor)


class InputReader:
    def __init__(self, stream):
        self.stream = stream

    def read(self, size):
        # Compressed Gio streams may return fewer bytes than requested even
        # before EOF. tarfile expects read(size) to fill that request.
        chunks = []
        remaining = size
        while remaining:
            data = self.stream.read_bytes(remaining, None).get_data()
            if not data:
                break
            chunks.append(data)
            remaining -= len(data)
        return b''.join(chunks)


def file_record(repo, checksum):
    _, stream, info, attrs = repo.load_file(checksum, None)
    record = {'uid': info.get_attribute_uint32('unix::uid'), 'gid': info.get_attribute_uint32('unix::gid'),
              'mode': info.get_attribute_uint32('unix::mode') & 0o7777,
              'xattrs': {bytes(k).rstrip(b'\0').decode(): bytes(v).hex() for k, v in (attrs.unpack() if attrs is not None else [])}}
    if info.get_file_type() == Gio.FileType.REGULAR:
        digest = hashlib.sha256()
        while chunk := stream.read_bytes(1024 * 1024, None).get_data():
            digest.update(chunk)
        record.update(kind='regular', size=info.get_size(), content_sha256=digest.hexdigest())
    elif info.get_file_type() == Gio.FileType.SYMBOLIC_LINK:
        record.update(kind='symlink', link_target=info.get_symlink_target())
    else:
        raise ValueError('Unsupported imported file type')
    return record


def capture(repo, revision, image_root, image_id, manifest, output, mode_output):
    if os.geteuid() != 0 or not re.fullmatch('[a-f0-9]{64}', revision) or not re.fullmatch('[a-f0-9]{64}', image_id):
        raise ValueError('Require root and exact image/raw import identities')
    files, raw = tree(repo, revision)
    expected, copied_files, modes = {}, {}, {}
    files_by_directory = {path: set() for path in raw}
    for path in files:
        files_by_directory[path.rpartition('/')[0] or '/'].add(path)
    base = os.open(image_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for path, imported in raw.items():
            record = image_directory(base, path, files, copied_files, modes, files_by_directory[path])
            if 'security.selinux' in imported['xattrs']:
                record['xattrs']['security.selinux'] = imported['xattrs']['security.selinux']
            expected[path] = record
    finally:
        os.close(base)
    for path, record in copied_files.items():
        original = file_record(repo, files[path])
        keys = ('kind', 'link_target') if record['kind'] == 'symlink' else ('kind', 'size', 'content_sha256')
        if any(original.get(key) != record[key] for key in keys):
            raise ValueError('Imported content/type differs from the pinned image: ' + path)
        if 'security.selinux' in original['xattrs']:
            record['xattrs']['security.selinux'] = original['xattrs']['security.selinux']
    # Collect everything successfully before creating any outputs or success receipt.
    with tarfile.open(output, 'x', format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(expected.keys() | copied_files.keys(), key=lambda name: (name.count('/'), name)):
            record = expected[path] if path in expected else copied_files[path]
            entry = tarfile.TarInfo(path.lstrip('/') or '.')
            entry.uid, entry.gid, entry.mode = record['uid'], record['gid'], record['mode']
            entry.mtime = 0
            entry.uname = entry.gname = ''
            entry.pax_headers = {'SCHILY.xattr.' + name: bytes.fromhex(value).decode('utf-8', 'surrogateescape')
                                 for name, value in record['xattrs'].items()}
            if path in expected:
                entry.type = tarfile.DIRTYPE
                archive.addfile(entry)
            elif record['kind'] == 'symlink':
                entry.type = tarfile.SYMTYPE
                entry.linkname = record['link_target']
                archive.addfile(entry)
            else:
                entry.type, entry.size = tarfile.REGTYPE, record['size']
                _, stream, _, _ = repo.load_file(files[path], None)
                archive.addfile(entry, InputReader(stream))
    with open(output, 'rb') as stream:
        os.fsync(stream.fileno())
        tar_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    with open(mode_output, 'x') as stream:
        for path, mode in sorted(modes.items()):
            stream.write(f'{mode:o} {path}\n')
        stream.flush(); os.fsync(stream.fileno())
    document = {'schema': SCHEMA, 'raw_import': revision, 'image_id': image_id,
                'metadata_overlay_sha256': tar_sha, 'directory_count': len(expected),
                'directories': expected, 'copied_files': copied_files,
                'selinux_source': 'raw-import-image-policy'}
    with open(manifest, 'x') as stream:
        json.dump(document, stream, sort_keys=True); stream.write('\n')
        stream.flush(); os.fsync(stream.fileno())
    return {'directories': len(expected), 'copied_files': len(copied_files)}


def verify(repo, old, new, manifest, modes):
    document = json.loads(Path(manifest).read_bytes())
    if document.get('schema') != SCHEMA or document.get('raw_import') != old:
        raise ValueError('Directory metadata manifest does not describe this raw import')
    old_files, old_dirs = tree(repo, old)
    new_files, new_dirs = tree(repo, new)
    expected = document['directories']
    if old_files.keys() != new_files.keys() or old_dirs.keys() != new_dirs.keys() or old_dirs.keys() != expected.keys():
        raise ValueError('Repair added or removed paths or changed directory types')
    if document['directory_count'] != len(expected) or new_dirs != expected:
        raise ValueError('Repaired directory metadata differs from the original image manifest')
    for path in old_dirs:
        if old_dirs[path]['xattrs'].get('security.selinux') != new_dirs[path]['xattrs'].get('security.selinux'):
            raise ValueError('Repair changed an image-policy SELinux directory label: ' + path)
    sensitive = read_expected(modes)
    if any(path not in old_files and path not in old_dirs for _, path in sensitive):
        raise ValueError('A sensitive-mode path is missing from the imported tree')
    copied_files = document['copied_files']
    if not copied_files.keys() <= old_files.keys():
        raise ValueError('Metadata repair introduces a file path')
    for path, expected_file in copied_files.items():
        before, after = file_record(repo, old_files[path]), file_record(repo, new_files[path])
        keys = ('kind', 'link_target') if expected_file['kind'] == 'symlink' else ('kind', 'size', 'content_sha256')
        if after != expected_file or any(before.get(key) != after.get(key) for key in keys):
            raise ValueError('File metadata differs from the image, or contents changed: ' + path)
        if before['xattrs'].get('security.selinux') != after['xattrs'].get('security.selinux'):
            raise ValueError('File policy SELinux label changed: ' + path)
    file_modes = [(mode, path) for mode, path in sensitive if path in old_files and path not in copied_files]
    allowed = {path for _, path in file_modes} | copied_files.keys()
    for path in old_files:
        if path not in allowed and old_files[path] != new_files[path]:
            raise ValueError('Repair changed an unlisted non-directory object: ' + path)
    problems = verify_modes(repo, old, new, file_modes)
    if problems:
        raise ValueError('\n'.join(problems))
    return {'result': 'PASS_EXACT_DIRECTORY_AND_FILE_METADATA', 'directories': len(expected),
            'copied_files': len(copied_files),
            'unchanged_non_directory_objects': len(old_files) - len(allowed),
            'verified_sensitive_file_modes': len(file_modes)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    collect = commands.add_parser('capture')
    for name in ('repo', 'commit', 'image_root', 'image_id', 'manifest', 'tar', 'modes'):
        collect.add_argument(name)
    check = commands.add_parser('verify')
    for name in ('repo', 'old', 'new', 'manifest', 'modes'):
        check.add_argument(name)
    args = parser.parse_args()
    repo = open_repo(args.repo)
    if args.action == 'capture':
        print(json.dumps({'metadata_captured': capture(repo, args.commit, args.image_root,
                                                          args.image_id, args.manifest, args.tar, args.modes)}))
    else:
        print(json.dumps(verify(repo, args.old, args.new, args.manifest, args.modes)))


if __name__ == '__main__':
    main()
