#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Seal publisher control separately from builder-writable artifacts.

Run by the release administrator, outside all builder bind mounts. The returned
manifest digest must be supplied explicitly when creating/using a signer.
"""
import argparse
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

MANIFEST = 'SIGNING-CONTROL.json'
FICLONE = 0x40049409
REFLINK_UNSUPPORTED = frozenset({errno.EXDEV, errno.EOPNOTSUPP,
                               errno.ENOTTY, errno.EINVAL})

def copy_snapshot_bytes(origin, output):
    """Copy into a fresh inode, admitting only exact independently hashed bytes.

    A supported reflink shares storage extents, never the source inode. The
    existing caller's source race guards still apply. Filesystems that cannot
    clone use the same bounded streaming path; other ioctl failures are errors.
    """
    cloned = False
    try:
        fcntl.ioctl(output.fileno(), FICLONE, origin.fileno())
        cloned = True
    except OSError as error:
        if error.errno not in REFLINK_UNSUPPORTED:
            raise
        output.seek(0)
        output.truncate(0)
    digest = hashlib.sha256()
    while block := origin.read(1024 * 1024):
        if not cloned:
            output.write(block)
        digest.update(block)
    output.flush()
    os.fsync(output.fileno())
    return digest.hexdigest()

def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def protected(path):
    path = Path(os.path.abspath(path))
    for parent in (path, *path.parents):
        st = parent.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
            raise ValueError(f'publisher path must be a root-owned non-writable directory: {parent}')
    return path

def verify(root, digest, schema='org.projectluma.signing-control/v1'):
    root = protected(root)
    content = (root / MANIFEST).read_bytes()
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError('publisher control manifest digest changed')
    doc = json.loads(content)
    if doc.get('schema') != schema:
        raise ValueError('unsupported publisher control manifest')
    actual = set()
    actual_directories = {}
    for path in root.rglob('*'):
        st = path.lstat()
        if st.st_uid != 0 or st.st_mode & 0o022 or stat.S_ISLNK(st.st_mode):
            raise ValueError(f'unsafe publisher control member: {path}')
        if stat.S_ISDIR(st.st_mode):
            actual_directories[path.relative_to(root).as_posix()] = stat.S_IMODE(st.st_mode)
            continue
        if not stat.S_ISREG(st.st_mode): raise ValueError('nonregular publisher member')
        rel = path.relative_to(root).as_posix()
        if rel == MANIFEST: continue
        actual.add(rel)
        record = doc['files'].get(rel)
        if record is None or record != {'sha256': file_hash(path),
                                        'mode': stat.S_IMODE(st.st_mode), 'bytes': st.st_size}:
            raise ValueError(f'publisher control member changed: {rel}')
    if actual != set(doc['files']): raise ValueError('publisher control member set changed')
    if actual_directories != doc.get('directories'):
        raise ValueError('publisher snapshot directory set or modes changed')
    return doc

def seal(source, target, inputs=False, graph=False):
    target = Path(os.path.abspath(target))
    protected(target.parent)
    if target.exists(): raise ValueError('refusing to replace sealed publisher control')
    target.mkdir(mode=0o700)
    selected = (list((source / 'scripts/os').rglob('*')) + [
        source / 'scripts/depot/seal-signing-control.py',
        source / 'config/os/release.env', source / 'config/desktop/inputs.env',
        source / 'image/os-tools/Containerfile']) if graph else list(source.rglob('*')) if inputs else list((source / 'scripts/depot').rglob('*')) + [
        source / 'config/desktop/inputs.env',
        source / 'packaging/flatpak/apps/titles.json',
        source / 'assets/icon-theme/Prairie/scalable/apps/org.projectluma.Depot.svg',
        source / 'src/luma-installer/luma_installer/__init__.py',
        source / 'src/luma-installer/luma_installer/depot_permissions.py']
    records = {}
    for path in sorted(selected):
        if '__pycache__' in path.parts: continue
        st = path.lstat()
        if stat.S_ISDIR(st.st_mode):
            # OSTree opens empty tmp/ and refs directories even for read-only
            # operations. They are part of the usable producer artifact.
            (target / path.relative_to(source)).mkdir(parents=True, exist_ok=True, mode=0o700)
            continue
        if not stat.S_ISREG(st.st_mode): raise ValueError(f'nonregular publisher source: {path}')
        rel = path.relative_to(source)
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Exact bytes are sealed now; the signer never imports builder modules.
        with path.open('rb') as origin, dest.open('xb') as output:
            before = os.fstat(origin.fileno())
            if (before.st_dev, before.st_ino) != (st.st_dev, st.st_ino):
                raise ValueError('source changed before snapshot')
            digest = copy_snapshot_bytes(origin, output)
            after = os.fstat(origin.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('source changed during snapshot')
        if file_hash(path) != digest:
            raise ValueError('source bytes changed after snapshot')
        if file_hash(dest) != digest:
            raise ValueError('destination bytes differ from source snapshot')
        if (dest.stat().st_dev, dest.stat().st_ino) == (st.st_dev, st.st_ino):
            raise ValueError('sealed destination shares the source inode')
        mode = 0o500 if st.st_mode & 0o111 else 0o400
        dest.chmod(mode)
        records[rel.as_posix()] = {'sha256': digest, 'mode': mode, 'bytes': before.st_size}
    schema = 'org.projectluma.signing-inputs/v1' if inputs else 'org.projectluma.signing-control/v1'
    directories = {}
    for directory in target.rglob('*'):
        if directory.is_dir():
            directory.chmod(0o500)
            directories[directory.relative_to(target).as_posix()] = 0o500
    data = (json.dumps({'schema': schema, 'files': records, 'directories': directories},
                       sort_keys=True, indent=2) + '\n').encode()
    (target / MANIFEST).write_bytes(data)
    (target / MANIFEST).chmod(0o400)
    for path in target.rglob('*'):
        if path.is_dir(): path.chmod(0o500)
    target.chmod(0o500)
    digest = hashlib.sha256(data).hexdigest()
    verify(target, digest, schema)
    return digest

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('seal', 'verify', 'seal-inputs', 'verify-inputs', 'seal-graph'))
    parser.add_argument('root', type=Path)
    parser.add_argument('value', help='destination path for seal; pinned manifest SHA256 for verify')
    args = parser.parse_args()
    if args.operation in ('seal', 'seal-inputs', 'seal-graph'):
        print(seal(args.root.resolve(), args.value, args.operation == 'seal-inputs', args.operation == 'seal-graph'))
    else:
        verify(args.root, args.value, 'org.projectluma.signing-inputs/v1' if args.operation == 'verify-inputs' else 'org.projectluma.signing-control/v1')
        print('publisher snapshot verified')

if __name__ == '__main__': main()
