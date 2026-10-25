#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Export only pinned signed runtime closures to a new portable build input.

Run in an admitted controller whose source filesystem is read-only. No signing,
repository updates, hard links, history traversal, or publication occurs here.
"""
import argparse
import configparser
import hashlib
import json
import io
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import time

EXTENSIONS = {1: '.filez', 2: '.dirtree', 3: '.dirmeta', 4: '.commit'}
GIB = 1024**3


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def identity(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or path.resolve() != path:
        raise ValueError('Expected canonical regular source: ' + str(path))
    return tuple(getattr(info, key) for key in ('st_dev', 'st_ino', 'st_size', 'st_uid', 'st_gid', 'st_mode', 'st_mtime_ns', 'st_ctime_ns', 'st_nlink'))


def export(repo, site, output, profile, deadline):
    import gi
    gi.require_version('OSTree', '1.0')
    from gi.repository import Gio, OSTree
    if (repo.resolve() != repo or repo.is_symlink() or not repo.is_dir()
            or not (os.statvfs(repo).f_flag & os.ST_RDONLY)):
        raise ValueError('Source must be canonical and mounted read-only')
    parent = output.parent.stat()
    if parent.st_uid != 0 or parent.st_mode & 0o022:
        raise ValueError('Output parent must be administrator-owned and not group/world writable')
    if output.exists() or output.is_symlink() or output.parent.resolve() != output.parent:
        raise ValueError('Fresh output beneath canonical parent required')
    config = configparser.ConfigParser()
    config.read(repo / 'config')
    if config['core']['mode'] != 'archive-z2':
        raise ValueError('Only measured archive-z2 inputs are supported')
    last_health = 0.0
    def health(force=False):
        nonlocal last_health
        now = time.monotonic()
        if now >= deadline:
            raise TimeoutError('Export deadline')
        if not force and now-last_health < 1:
            return
        last_health = now
        fs = os.statvfs(output.parent)
        allocated = sum(path.stat().st_blocks*512 for path in output.iterdir() if path.is_file()) if output.exists() else 0
        if allocated > 8*GIB or fs.f_bavail * fs.f_frsize < 20*GIB + max(0, 8*GIB-allocated):
            raise ValueError('Export8GiB allocation/reserve or20GiB physical floor exceeded')
    def run(*args):
        health()
        return subprocess.check_output(['ostree', '--repo='+str(repo), *args], text=True, timeout=30).strip()
    def heads():
        return {ref: run('rev-parse', ref) for ref in profile['refs']}
    if heads() != profile['refs']:
        raise ValueError('Pinned runtime heads differ')
    source = OSTree.Repo.new(Gio.File.new_for_path(str(repo)))
    source.open(None)
    reachable = set()
    for head in profile['refs'].values():
        health()
        ok, objects = source.traverse_commit(head, 0, None)
        if not ok:
            raise ValueError('Cannot traverse exact commit')
        reachable.update(tuple(key.unpack() if hasattr(key, 'unpack') else key) for key in objects)
        if len(reachable) > profile['reachable_object_count']:
            raise ValueError('Unexpected additional objects')
    if len(reachable) != profile['reachable_object_count']:
        raise ValueError('Reachable object count differs')
    selected = {}
    stored_bytes = 0
    for checksum, kind in reachable:
        health()
        if kind not in EXTENSIONS:
            raise ValueError('Unexpected object type')
        path = repo/'objects'/checksum[:2]/(checksum[2:]+EXTENSIONS[kind])
        stored_bytes += identity(path)[2]
        selected['repo/'+str(path.relative_to(repo))] = path
    if stored_bytes != profile['reachable_stored_bytes']:
        raise ValueError('Measured stored size changed')
    for head in set(profile['refs'].values()):
        detached = repo/'objects'/head[:2]/(head[2:]+'.commitmeta')
        if detached.exists():
            identity(detached)
            selected['repo/'+str(detached.relative_to(repo))] = detached
    for relative, expected in (('summary', profile['summary_sha256']), ('summary.sig', profile['summary_signature_sha256'])):
        path = repo/relative
        identity(path)
        if digest(path) != expected:
            raise ValueError('Original signed summary changed')
        selected['repo/'+relative] = path
    identity(site)
    if digest(site) != profile['flatpakrepo_sha256']:
        raise ValueError('Public descriptor differs')
    selected['site/luma.flatpakrepo'] = site
    pins = {name: identity(path) for name, path in selected.items()}
    output.mkdir(mode=0o700)
    for name in ('system.posix_acl_access', 'system.posix_acl_default'):
        if name in os.listxattr(output):
            os.removexattr(output, name)
    output.chmod(0o700)
    inventory = {}
    archive = output/'runtime-input.tar'
    def add_bytes(stream, name, body, mode=0o644):
        info = tarfile.TarInfo(name)
        info.size, info.mode, info.mtime = len(body), mode, 0
        stream.addfile(info, io.BytesIO(body))
        return dict(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
    class Reader:
        def __init__(self, source):
            self.source, self.sha, self.size = source, hashlib.sha256(), 0
        def read(self, size):
            health()
            chunk = self.source.read(size)
            self.sha.update(chunk)
            self.size += len(chunk)
            return chunk
    with tarfile.open(archive, 'w', format=tarfile.USTAR_FORMAT) as stream:
        for name in ('repo', 'repo/objects', 'repo/tmp', 'repo/state', 'repo/refs',
                     'repo/refs/heads', 'repo/refs/remotes', 'repo/refs/mirrors', 'site'):
            info = tarfile.TarInfo(name)
            info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, 0
            stream.addfile(info)
        config_bytes = b'[core]\nrepo_version=1\nmode=archive-z2\n'
        inventory['repo/config'] = add_bytes(stream, 'repo/config', config_bytes)
        for name, path in sorted(selected.items()):
            health()
            if identity(path) != pins[name]:
                raise ValueError('Source changed before read')
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = pins[name][2], 0o644, 0
            with path.open('rb') as source_file:
                reader = Reader(source_file)
                stream.addfile(info, reader)
            if reader.size != info.size or identity(path) != pins[name]:
                raise ValueError('Source changed while archived')
            inventory[name] = dict(bytes=reader.size, sha256=reader.sha.hexdigest())
        for ref, head in sorted(profile['refs'].items()):
            name = 'repo/refs/heads/'+ref
            inventory[name] = add_bytes(stream, name, (head+'\n').encode())
        body = json.dumps(dict(schema='org.projectluma.depot.runtime-input/v1', profile=profile,
                              files=inventory), sort_keys=True).encode()+b'\n'
        add_bytes(stream, 'INPUTS.json', body)
    if heads() != profile['refs'] or any(identity(selected[name]) != pin for name, pin in pins.items()):
        raise ValueError('Source changed during export')
    with archive.open('ab') as handle:
        handle.flush()
        os.fsync(handle.fileno())
    health(force=True)
    receipt = dict(scope='PORTABLE_ALREADY_SIGNED_BUILD_INPUT_ONLY_NOT_RELEASE', profile=profile,
                   archive_bytes=archive.stat().st_size, archive_sha256=digest(archive),
                   inventory_files=len(inventory), source_pins_unchanged=True)
    # The archive is the durable transport. No per-object fsync storm or copied mirror is needed.
    return receipt


def export_filer(source, output, profile, deadline):
    import yaml
    if source.resolve() != source or source.is_symlink() or not source.is_dir() or not (os.statvfs(source).f_flag & os.ST_RDONLY):
        raise ValueError('Sealed source must be canonical and mounted read-only')
    manifest = source/'org.gnome.Nautilus.yml'
    materials = source/'SOURCE-MANIFEST.json'
    for path in (materials, manifest):
        if identity(path)[2] > 16*1024**2:
            raise ValueError('Unbounded source metadata')
    if digest(materials) != profile['filer_source_manifest_sha256'] or digest(manifest) != profile['filer_manifest_sha256']:
        raise ValueError('Sealed Filer source differs')
    document = yaml.safe_load(manifest.read_text())
    archives = []
    def walk(module):
        for item in module.get('sources', []):
            if isinstance(item, dict) and item.get('type') == 'archive' and 'path' in item:
                name = item['path']
                if Path(name).name != name or not name.startswith('source-') or not name.endswith('.tar'):
                    raise ValueError('Only a sealed sibling source archive is allowed')
                path = source/name
                if identity(path)[2] > 512*1024**2:
                    raise ValueError('Sealed source archive exceeds512MiB')
                if digest(path) != item.get('sha256'):
                    raise ValueError('Sealed source archive differs')
                archives.append(path)
        for child in module.get('modules', []):
            if isinstance(child, dict):
                walk(child)
    for module in document['modules']:
        walk(module)
    if len(archives) != 1:
        raise ValueError('Expected exactly one sealed Filer source archive')
    files = [materials, manifest, *archives]
    pins = {path: identity(path) for path in files}
    archive = output/'filer-input.tar'
    with tarfile.open(archive, 'w', format=tarfile.USTAR_FORMAT) as stream:
        for path in files:
            if time.monotonic() >= deadline:
                raise TimeoutError('Source export deadline')
            info = tarfile.TarInfo(path.name)
            info.size, info.mode, info.mtime = pins[path][2], 0o644, 0
            with path.open('rb') as handle:
                stream.addfile(info, handle)
            if identity(path) != pins[path]:
                raise ValueError('Sealed source changed during read')
    with archive.open('ab') as handle:
        handle.flush()
        os.fsync(handle.fileno())
    return dict(archive_bytes=archive.stat().st_size, archive_sha256=digest(archive),
                files={path.name:dict(bytes=pins[path][2],sha256=digest(path)) for path in files})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--site', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--filer-source', type=Path, required=True)
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text())
    deadline = time.monotonic()+1200
    receipt = export(args.repo, args.site, args.output, profile, deadline)
    receipt['filer'] = export_filer(args.filer_source, args.output, profile, deadline)
    path = args.output/'EXPORTED.json'
    with path.open('x') as handle:
        json.dump(receipt, handle, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
        os.fchmod(handle.fileno(), 0o400)
    fd = os.open(args.output, os.O_DIRECTORY)
    os.fsync(fd)
    os.close(fd)
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
