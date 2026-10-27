#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Capture export bodies and reuse authenticated donor ISO objects.

External SHA256 pins bind read-only custody inputs. Full ISO body verification
uses LBA order and avoids extracting or reopening thousands of host files.
Nothing in this module declares media or a release qualified.
"""
import configparser
import hashlib
import json
from pathlib import Path
import re
import shlex
import stat

SCHEMA = 'org.projectluma.iso-payload-bodies/v1'
OBJECT = re.compile(r'objects/[0-9a-f]{2}/[0-9a-f]{62}\.(?:filez|dirtree|dirmeta|commit|commitmeta)\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')
TOP = {'.lock', 'config', 'summary', 'summary.sig', 'refs', 'state', 'tmp', 'extensions'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def attributes(path):
    s = Path(path).stat()
    return dict(mode=stat.S_IMODE(s.st_mode), uid=s.st_uid, gid=s.st_gid)


def directory_inventory(repository):
    import os
    repository = Path(repository)
    result = {'': attributes(repository)}
    for parent, dirs, files in os.walk(repository, followlinks=False):
        for name in dirs:
            path = Path(parent) / name
            if path.is_symlink(): raise ValueError('repository directory symlink')
            result[path.relative_to(repository).as_posix()] = attributes(path)
    return result


def config_body(body):
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(body)
    if config.get('core', 'mode', fallback='') != 'archive-z2':
        raise ValueError('payload must be an archive-z2 repository')
    if any(section != 'core' for section in config.sections()):
        raise ValueError('payload config cannot contain remote/private sections')
    if config.has_option('core', 'parent'):
        raise ValueError('payload cannot depend on core.parent')
    return body


def validate(manifest):
    if manifest.get('schema') != SCHEMA:
        raise ValueError('unsupported body manifest')
    if not HEX.fullmatch(manifest.get('candidate', '')):
        raise ValueError('invalid candidate')
    if not re.fullmatch(r'[0-9a-f]{40}', manifest.get('source_revision', '')):
        raise ValueError('invalid source revision')
    arch = manifest.get('architecture')
    if arch not in ('x86_64', 'aarch64'):
        raise ValueError('invalid architecture')
    if not re.fullmatch(r'luma/1/' + arch + r'/(?:nightly|beta|stable)', manifest.get('ref', '')):
        raise ValueError('unexpected desktop channel')
    if manifest.get('profile') not in ('full', 'arm-preview'):
        raise ValueError('invalid desktop profile')
    config_body(manifest.get('config', ''))
    heads = manifest.get('heads', {})
    if heads.get(manifest['ref']) != manifest['candidate'] or not set(heads) <= {manifest['ref'], 'ostree-metadata'}:
        raise ValueError('extra or incorrect channel/device heads')
    if any(not HEX.fullmatch(c) for c in heads.values()):
        raise ValueError('invalid head checksum')
    objects = manifest.get('objects')
    if not isinstance(objects, dict) or not objects:
        raise ValueError('missing complete object set')
    for path, row in objects.items():
        if not OBJECT.fullmatch(path) or not HEX.fullmatch(row.get('sha256', '')):
            raise ValueError('invalid object path or compressed-body checksum')
        if type(row.get('bytes')) is not int or row['bytes'] < 0:
            raise ValueError('invalid object size')
        if any(type(row.get(k)) is not int or row[k] < 0 for k in ('mode', 'uid', 'gid')) or row['mode'] > 0o7777:
            raise ValueError('invalid outer object attributes')
    tops = manifest.get('metadata_roots')
    if not isinstance(tops, list) or len(set(tops)) != len(tops) or not {'config', 'refs', 'summary', 'summary.sig'} <= set(tops) <= TOP:
        raise ValueError(f'unexpected private/control metadata or incomplete roots: {tops!r}')
    candidate = manifest['candidate']
    for suffix in ('commit', 'commitmeta'):
        if f'objects/{candidate[:2]}/{candidate[2:]}.{suffix}' not in objects:
            raise ValueError('missing candidate or detached signature')
    metadata = manifest.get('metadata_bodies', {})
    allowed_files = {'config', 'summary', 'summary.sig', '.lock'} | {'refs/heads/' + ref for ref in heads}
    if not {'config', 'summary', 'summary.sig'} <= set(metadata) <= allowed_files:
        raise ValueError('unexpected private or unbound mutable metadata')
    for path, row in metadata.items():
        if any(type(row.get(k)) is not int or row[k] < 0 for k in ('mode', 'uid', 'gid')) or row['mode'] > 0o7777:
            raise ValueError('invalid mutable metadata attributes')
        if type(row.get('bytes')) is not int or row['bytes'] < 0 or not HEX.fullmatch(row.get('sha256', '')):
            raise ValueError('invalid metadata body record')
    if metadata['config']['sha256'] != hashlib.sha256(manifest['config'].encode()).hexdigest():
        raise ValueError('config body and metadata pin differ')
    directories = manifest.get('directories', {})
    if '' not in directories or 'objects' not in directories:
        raise ValueError('missing repository directory attributes')
    for name, row in directories.items():
        if name and (name.startswith('/') or '..' in Path(name).parts or name.split('/')[0] not in TOP | {'objects'}):
            raise ValueError('unexpected private directory')
        if name.startswith('objects/') and not re.fullmatch(r'objects/[0-9a-f]{2}', name):
            raise ValueError('unexpected object directory')
        if any(type(row.get(k)) is not int or row[k] < 0 for k in ('mode', 'uid', 'gid')) or row['mode'] > 0o7777:
            raise ValueError('invalid directory attributes')
    return manifest


def load(path, expected_sha256):
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_mode & 0o222:
        raise ValueError('body manifest must be an immutable regular custody file')
    if digest(path) != expected_sha256:
        raise ValueError('body manifest differs from external pin')
    result = validate(json.loads(path.read_text()))
    after = path.lstat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('body manifest changed during read')
    return result


def plan(donor, new):
    validate(donor); validate(new)
    if any(donor[k] != new[k] for k in ('architecture', 'profile', 'ref')):
        raise ValueError('cannot reuse another architecture, profile or channel')
    old, current = donor['objects'], new['objects']
    reuse = sorted(p for p, body in current.items()
                   if not p.endswith('.commitmeta') and old.get(p) == body)
    return dict(schema='org.projectluma.iso-payload-reuse-plan/v1',
                result='PLAN_NOT_MEDIA_OR_QUALIFICATION',
                donor_identity={k: donor[k] for k in ('source_revision', 'candidate', 'architecture', 'profile', 'ref')},
                new_identity={k: new[k] for k in ('source_revision', 'candidate', 'architecture', 'profile', 'ref')},
                reuse=reuse, replace=sorted(set(current) - set(reuse)),
                remove=sorted(set(old) - set(current)),
                remove_metadata=sorted(donor['metadata_roots']),
                replace_metadata=sorted(new['metadata_roots']),
                expected_objects=sorted(current),
                expected_heads=new['heads'], candidate=new['candidate'])


def write_options(repository, donor, new, output):
    """Prepare xorriso arguments after external donor/manifest authentication.

    The caller must verify the full donor ISO SHA and its exact object inventory
    against the donor manifest, and keep this source read-only. This function
    deliberately does not turn unverified manifests into trusted custody.
    """
    repository = Path(repository).resolve(strict=True)
    if any(c in str(repository) for c in ('\n', '\r', '\0')):
        raise ValueError('repository path cannot contain command-file delimiters')
    operation = plan(donor, new)
    if config_body((repository / 'config').read_text()) != new['config']:
        raise ValueError('current config differs from pinned body manifest')
    actual = set()
    for path in (repository / 'objects').rglob('*'):
        if path.is_symlink():
            raise ValueError('object symlinks are not permitted')
        if path.is_file(): actual.add(path.relative_to(repository).as_posix())
    if actual != set(new['objects']):
        raise ValueError('new repository object set differs from pinned inventory')
    heads = {p.relative_to(repository / 'refs/heads').as_posix(): p.read_text().strip()
             for p in (repository / 'refs/heads').rglob('*') if p.is_file()}
    if heads != new['heads']:
        raise ValueError('new repository heads differ from pinned inventory')
    if {p.name for p in repository.iterdir()} != {'objects', *new['metadata_roots']}:
        raise ValueError('new repository has extra private/control entries')
    for top in new['metadata_roots']:
        path = repository / top
        if path.is_symlink() or (path.is_dir() and any(p.is_symlink() for p in path.rglob('*'))):
            raise ValueError('metadata symlinks are not permitted')
    actual_metadata = {p.relative_to(repository).as_posix(): p
                       for top in new['metadata_roots']
                       for p in ([repository / top] if (repository / top).is_file()
                                 else (repository / top).rglob('*')) if p.is_file()}
    if set(actual_metadata) != set(new['metadata_bodies']):
        raise ValueError('mutable metadata inventory differs from pin')
    for path, source in actual_metadata.items():
        row = new['metadata_bodies'][path]
        if source.stat().st_size != row['bytes'] or digest(source) != row['sha256'] or attributes(source) != {k:row[k] for k in ('mode','uid','gid')}:
            raise ValueError('mutable metadata differs from pinned body')
        if path == '.lock' and row['bytes'] != 0:
            raise ValueError('unexpected nonempty repository lock')
    actual_dirs = directory_inventory(repository)
    if actual_dirs != new['directories']: raise ValueError('repository directories differ from pinned inventory')
    commands = []
    for top in operation['remove_metadata']:
        commands.append(['-rm_r', '/luma/repo/' + top, '--'])
    for path in operation['remove']:
        commands.append(['-rm', '/luma/repo/' + path, '--'])
    old_dirs = {d for d in donor['directories'] if d.startswith('objects/')}
    new_dirs = {d for d in new['directories'] if d.startswith('objects/')}
    for path in sorted(old_dirs - new_dirs, reverse=True):
        commands.append(['-rmdir', '/luma/repo/' + path, '--'])
    for path in sorted({'', 'objects'} | new_dirs):
        destination = '/luma/repo' + ('/' + path if path else '')
        row = new['directories'][path]
        if path in new_dirs - old_dirs:
            commands.append(['-mkdir', destination, '--'])
        commands += [['-chmod', format(row['mode'], '04o'), destination, '--'],
                     ['-chown', str(row['uid']), destination, '--'],
                     ['-chgrp', str(row['gid']), destination, '--']]
    for path in operation['replace']:
        source = repository / path
        row = new['objects'][path]
        if source.stat().st_size != row['bytes'] or digest(source) != row['sha256'] or attributes(source) != {k:row[k] for k in ('mode','uid','gid')}:
            raise ValueError('replacement object differs from pinned compressed body')
        commands.append(['-map', str(source), '/luma/repo/' + path])
    for top in operation['replace_metadata']:
        commands.append(['-map', str(repository / top), '/luma/repo/' + top])
    with Path(output).open('x') as stream:
        for command in commands:
            stream.write(shlex.join(command) + '\n')
    return operation


def verify_object_inventory(directory, manifest):
    """Cold/full verification for producing a donor's authenticated ISO receipt.

    This costs a complete read, so do it once while creating the donor evidence,
    not on every reuse. The receipt must also pin the complete ISO's own SHA256.
    """
    validate(manifest)
    directory = Path(directory)
    paths = [p for p in (directory / 'objects').rglob('*') if p.is_file()]
    if any(p.is_symlink() for p in (directory / 'objects').rglob('*')):
        raise ValueError('donor object symlink')
    if {p.relative_to(directory).as_posix() for p in paths} != set(manifest['objects']):
        raise ValueError('donor object set differs from authenticated manifest')
    for path in paths:
        row = manifest['objects'][path.relative_to(directory).as_posix()]
        if path.stat().st_size != row['bytes'] or digest(path) != row['sha256']:
            raise ValueError('donor object compressed body differs')
    return dict(result='VERIFIED_OBJECT_BODIES_NOT_MEDIA_QUALIFICATION', files=len(paths))


INDEX_SCHEMA = 'org.projectluma.export-object-bodies/v1'
RECEIPT_SCHEMA = 'org.projectluma.iso-payload-body-receipt/v1'


def identity(path):
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode):
        raise ValueError('expected regular object body')
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def load_pinned(path, expected, schema):
    path = Path(path)
    before = identity(path)
    if path.stat().st_mode & 0o222 or digest(path) != expected:
        raise ValueError('custody file is writable or differs from external pin')
    doc = json.loads(path.read_text())
    if doc.get('schema') != schema or identity(path) != before:
        raise ValueError('custody schema or file changed')
    return doc


def save_private(path, doc):
    import os
    path = Path(path)
    with path.open('x') as stream:
        json.dump(doc, stream, sort_keys=True)
        stream.write('\n'); stream.flush(); os.fchmod(stream.fileno(), 0o400); os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def capture_index(repository, source_revision, candidate, architecture, profile, ref, previous=None):
    """Capture hot export bodies; unchanged protected inode identities avoid rereads."""
    repository = Path(repository).resolve(strict=True)
    config_body((repository / 'config').read_text())
    old = (previous or {}).get('objects', {})
    objects = {}; read_bytes = 0; reused = 0
    for path in (repository / 'objects').rglob('*'):
        if path.is_dir() and not path.is_symlink(): continue
        name = path.relative_to(repository).as_posix()
        if not OBJECT.fullmatch(name): raise ValueError('unexpected export object path')
        before = identity(path)
        prior = old.get(name, {})
        if prior.get('identity') == before:
            checksum = prior['sha256']; reused += 1
        else:
            checksum = digest(path); read_bytes += before[2]
        if identity(path) != before: raise ValueError('object changed while capturing body index')
        objects[name] = dict(bytes=before[2], sha256=checksum, identity=before)
    doc = dict(schema=INDEX_SCHEMA, source_revision=source_revision, candidate=candidate,
               architecture=architecture, profile=profile, ref=ref, objects=objects,
               actual_body_read_bytes=read_bytes, reused_unchanged_inode_bodies=reused,
               producer_sha256=digest(Path(__file__)), ostree_full_fsck_verified=False)
    # Validate semantic identity with a manifest assembled from actual metadata.
    manifest_from_index(repository, doc)
    return doc


def manifest_from_index(repository, index):
    repository = Path(repository)
    if index.get('schema') != INDEX_SCHEMA: raise ValueError('not an export body index')
    objects = {}
    for p in (repository / 'objects').rglob('*'):
        if p.is_dir() and not p.is_symlink(): continue
        name = p.relative_to(repository).as_posix(); identity(p)
        row = index['objects'].get(name)
        if row is None or p.stat().st_size != row['bytes']:
            raise ValueError('payload object missing from export body custody')
        objects[name] = dict(bytes=row['bytes'], sha256=row['sha256'], **attributes(p))
    metadata = {}
    tops = sorted(p.name for p in repository.iterdir() if p.name != 'objects')
    for top in tops:
        p = repository / top
        for path in ([p] if not p.is_dir() else p.rglob('*')):
            if path.is_dir() and not path.is_symlink(): continue
            before = identity(path)
            metadata[path.relative_to(repository).as_posix()] = dict(bytes=before[2], sha256=digest(path), **attributes(path))
            if identity(path) != before: raise ValueError('metadata changed while capturing')
    heads = {p.relative_to(repository/'refs/heads').as_posix(): p.read_text().strip()
             for p in (repository/'refs/heads').rglob('*') if p.is_file()}
    result = {k: index[k] for k in ('source_revision', 'candidate', 'architecture', 'profile', 'ref')}
    if (repository/'objects').is_symlink(): raise ValueError('object root symlink')
    directories = directory_inventory(repository)
    result.update(schema=SCHEMA, objects=objects, metadata_bodies=metadata, directories=directories,
                  metadata_roots=tops, heads=heads, config=(repository/'config').read_text())
    return validate(result)


def verify_iso_inventory(iso, manifest):
    """Hash actual ISO bodies in LBA order, with no extraction or per-file opens."""
    import subprocess
    validate(manifest); iso = Path(iso); before = identity(iso)
    report = subprocess.check_output(['xorriso', '-indev', str(iso), '-find', '/luma/repo',
                                     '-not', '-type', 'd', '-exec', 'report_sections', '--'],
                                    text=True, stderr=subprocess.PIPE)
    pattern = re.compile(r"File data lba:\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*'(/luma/repo/[^']+)'$")
    sections = {}; expected = {**manifest['metadata_bodies'], **manifest['objects']}
    for line in report.splitlines():
        if not line.startswith('File data lba:'): continue
        match = pattern.fullmatch(line)
        if not match: raise ValueError('unsupported ISO extent report')
        part, start, blocks, size = map(int, match.groups()[:4]); name = match[5][len('/luma/repo/'):]
        if part != 0 or name in sections:
            raise ValueError('multi-section or duplicate payload path is not supported')
        if name not in expected or size != expected[name]['bytes'] or blocks*2048 < size or start*2048+size > before[2]:
            raise ValueError('ISO extent differs from exact expected body inventory')
        sections[name] = (start*2048, size)
    if set(sections) != set(expected): raise ValueError('ISO object/metadata set differs from expected inventory')
    # Reject symlinks/special entries: they cannot silently disappear from a body report.
    other = subprocess.check_output(['xorriso', '-indev', str(iso), '-find', '/luma/repo',
                                     '-not', '-type', 'd', '-not', '-type', 'f', '-exec', 'echo', '--'],
                                    text=True, stderr=subprocess.PIPE)
    if other.strip(): raise ValueError('ISO payload contains a non-regular body')
    listing = subprocess.check_output(['xorriso', '-indev', str(iso), '-find', '/luma/repo',
                                      '-exec', 'lsdl', '--'],
                                     text=True, stderr=subprocess.PIPE)
    listed = set()
    line_pattern = re.compile(r"([-drwxstST]{10})\s+\d+\s+(\S+)\s+(\S+)\s+\d+\s+.*'(/luma/repo(?:/[^']+)?)'$")
    import pwd, grp
    for line in listing.splitlines():
        match = line_pattern.fullmatch(line)
        if not match: raise ValueError('unsupported ISO attribute report: ' + line)
        mode, user, group, path = match.groups(); name = path[len('/luma/repo/'):]
        uid = int(user) if user.isdecimal() else pwd.getpwnam(user).pw_uid
        gid = int(group) if group.isdecimal() else grp.getgrnam(group).gr_gid
        directory = mode.startswith('d')
        name = '' if path == '/luma/repo' else name
        rows = manifest['directories'] if directory else expected
        label = (directory, name)
        if name not in rows or label in listed: raise ValueError('unexpected ISO attribute path')
        row = rows[name]
        if (mode, uid, gid) != (stat.filemode((stat.S_IFDIR if directory else stat.S_IFREG) | row['mode']), row['uid'], row['gid']):
            raise ValueError('ISO outer object permissions or ownership differ')
        listed.add(label)
    if listed != {(False, n) for n in expected} | {(True, n) for n in manifest['directories']}: raise ValueError('missing ISO attribute records')
    last = None; cached = None
    with iso.open('rb') as stream:
        for name, (offset, size) in sorted(sections.items(), key=lambda row: row[1]):
            if (offset, size) != last:
                stream.seek(offset); h = hashlib.sha256(); remaining = size
                while remaining:
                    body = stream.read(min(4*1024**2, remaining))
                    if not body: raise ValueError('short ISO body')
                    h.update(body); remaining -= len(body)
                cached = h.hexdigest(); last = (offset, size)
            if cached != expected[name]['sha256']: raise ValueError('actual ISO compressed body differs from manifest')
    iso_sha = digest(iso)
    if identity(iso) != before: raise ValueError('ISO changed while verifying its bodies')
    return dict(schema=RECEIPT_SCHEMA, result='VERIFIED_PAYLOAD_BODIES_NOT_QUALIFIED',
                iso_sha256=iso_sha, iso_bytes=before[2], manifest=manifest,
                exact_body_inventory_verified=True, actual_body_bytes=sum(v['bytes'] for v in expected.values()))


def authenticated_plan(donor, receipt_path, receipt_sha, repository, index_path, index_sha, output):
    receipt = load_pinned(receipt_path, receipt_sha, RECEIPT_SCHEMA)
    if receipt.get('result') != 'VERIFIED_PAYLOAD_BODIES_NOT_QUALIFIED' or receipt.get('exact_body_inventory_verified') is not True:
        raise ValueError('donor has no actual body verification receipt')
    if Path(donor).stat().st_size != receipt['iso_bytes'] or digest(donor) != receipt['iso_sha256']:
        raise ValueError('donor ISO differs from authenticated body receipt')
    index = load_pinned(index_path, index_sha, INDEX_SCHEMA)
    if index.get('ostree_full_fsck_verified') is not True:
        raise ValueError('export index has no complete OSTree verification')
    manifest = manifest_from_index(repository, index)
    operation = write_options(repository, receipt['manifest'], manifest, output)
    operation.update(donor_receipt_sha256=receipt_sha, export_body_index_sha256=index_sha)
    return manifest, operation


def verify_signed_metadata(repository, ref, commit, key, work):
    import subprocess
    Path(work).mkdir(mode=0o700, parents=True, exist_ok=True)
    client = Path(work) / 'metadata-client'
    if client.exists(): raise ValueError('metadata verification needs a fresh client')
    def run(*args): return subprocess.check_output(list(map(str, args)), stderr=subprocess.PIPE, text=True)
    run('ostree', '--repo='+str(client), 'init', '--mode=archive')
    run('ostree', '--repo='+str(client), 'remote', 'add', '--gpg-import='+str(key),
        '--set=gpg-verify=true', '--set=gpg-verify-summary=true', 'payload', Path(repository).as_uri())
    run('ostree', '--repo='+str(client), 'pull', '--commit-metadata-only', '--depth=0',
        '--disable-static-deltas', 'payload', ref)
    if run('ostree', '--repo='+str(client), 'rev-parse', 'payload:'+ref).strip() != commit:
        raise ValueError('signed repository head differs from candidate')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    for field in ('source-revision', 'candidate', 'architecture', 'profile', 'ref'):
        parser.add_argument('--'+field, required=True)
    parser.add_argument('--previous', type=Path)
    parser.add_argument('--previous-sha256')
    args = parser.parse_args()
    if bool(args.previous) != bool(args.previous_sha256): parser.error('previous index needs an external SHA256 pin')
    previous = load_pinned(args.previous, args.previous_sha256, INDEX_SCHEMA) if args.previous else None
    import subprocess
    subprocess.run(['ostree', '--repo='+str(args.repository), 'fsck', '--all'], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    doc = capture_index(args.repository, args.source_revision, args.candidate, args.architecture, args.profile, args.ref, previous)
    doc['ostree_full_fsck_verified'] = True
    save_private(args.output, doc)
    print(digest(args.output))
