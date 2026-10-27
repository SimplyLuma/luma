#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Plan exact, byte-authenticated OSTree object reuse; never qualify media.

Manifests are external custody inputs, not assertions reconstructed from object
names. Production activation additionally needs export-time manifest custody and
an ISO inventory proving that the donor contains exactly its recorded bodies.
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


def config_body(body):
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(body)
    if config.get('core', 'mode', fallback='') != 'archive-z2':
        raise ValueError('payload must be an archive-z2 repository')
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
    if manifest.get('profile') not in ('desktop', 'arm-preview'):
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
        if type(row.get('bytes')) is not int or row['bytes'] < 0 or not HEX.fullmatch(row.get('sha256', '')):
            raise ValueError('invalid metadata body record')
    if metadata['config']['sha256'] != hashlib.sha256(manifest['config'].encode()).hexdigest():
        raise ValueError('config body and metadata pin differ')
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
        if source.stat().st_size != row['bytes'] or digest(source) != row['sha256']:
            raise ValueError('mutable metadata differs from pinned body')
        if path == '.lock' and row['bytes'] != 0:
            raise ValueError('unexpected nonempty repository lock')
    commands = []
    for top in operation['remove_metadata']:
        commands.append(['-rm_r', '/luma/repo/' + top, '--'])
    for path in operation['remove']:
        commands.append(['-rm', '/luma/repo/' + path, '--'])
    for path in operation['replace']:
        source = repository / path
        row = new['objects'][path]
        if source.stat().st_size != row['bytes'] or digest(source) != row['sha256']:
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
