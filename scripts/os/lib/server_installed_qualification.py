#!/usr/bin/env python3
"""Explicit server TCG custody for the unchanged installed-app regression suite.

This module checks evidence, not simulated guest success. Callers must supply
independently pinned final release inputs; no historical Mac/build defaults exist.
Preparation never launches a VM or changes candidate/feed content.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import socket
import subprocess
from collections import Counter
from pathlib import Path

PROVIDER = 'server-qemu-tcg-aarch64-ssd/v1'
VIRTUAL_BYTES = 48 * 1024**3
FLOOR_BYTES = 20 * 1024**3
LAUNCHERS = ('org.projectluma.Tide', 'org.projectluma.Messages', 'org.projectluma.Ari')
BEHAVIORS = {
    ('org.projectluma.Tide', 'installed_startup_runtime.py'),
    ('org.projectluma.Tide', 'search_panel_runtime.py'),
    ('org.projectluma.Tide', 'artwork_hues_runtime.py'),
    ('org.projectluma.Messages', 'messages_draft_images_runtime.py'),
    ('org.projectluma.Ari', 'installed-native-and-signed-catalogue-service'),
    ('org.gnome.Nautilus', 'filer-space'),
    ('org.gnome.Nautilus', 'actual-sidebar-Applications-speed-columns-search-retirement'),
    ('org.gnome.Nautilus', 'signed-Filer-to-native-Terminal-real-shell-directory'),
    *((app, 'installed-launcher') for app in LAUNCHERS),
}
CHECK_FILES = (
    'run-guest-checks.py', 'installed_startup_runtime.py', 'search_panel_runtime.py',
    'artwork_hues_runtime.py', 'messages_draft_images_runtime.py',
    'installed_host_service.py', 'filer-flatpak-space.py',
    'filer-applications-installed.py', 'filer-terminal-boundary.py',
)
IDENTITY_KEYS = ('build_id', 'source_revision', 'profile', 'candidate',
                 'iso_sha256', 'iso_bytes', 'fixture_token')
MAC_KEYS = {'native_mac_vm', 'native_staging_sha256', 'native_attempt',
            'MAC-STAGED.json', 'volume_uuid', 'remote_task_directory',
            'mac-external-apfs-ssd', 'hvf', 'apfs'}


class QualificationError(ValueError):
    pass


def require(value, message):
    if not value:
        raise QualificationError(message)


def valid_hash(value, label='SHA256'):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value), label)
    return value


def regular_bytes(path: Path, *, owner_uid=None, immutable=False):
    path = Path(path)
    require(path.is_absolute(), 'Proof path must be absolute')
    # Reject symlink traversal, including parents; resolve() alone would hide it.
    for part in (path, *path.parents):
        require(not part.is_symlink(), 'Symlink proof path: ' + str(part))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode), 'Proof is not regular: ' + str(path))
        if owner_uid is not None:
            require(before.st_uid == owner_uid, 'Wrong proof owner: ' + str(path))
        if immutable:
            require(before.st_mode & 0o222 == 0, 'Writable receipt: ' + str(path))
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            body = stream.read()
        after = os.fstat(fd)
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                 before.st_ctime_ns) == (after.st_dev, after.st_ino, after.st_size,
                 after.st_mtime_ns, after.st_ctime_ns), 'Proof changed while reading')
        return body
    finally:
        os.close(fd)


def file_sha256(path):
    """Bounded-memory complete hash; reject changing files and symlink traversal."""
    path = Path(path)
    for part in (path, *path.parents):
        require(not part.is_symlink(), 'Symlink input: ' + str(part))
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode), 'Input must be regular')
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = os.fstat(stream.fileno())
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                 before.st_ctime_ns) == (after.st_dev, after.st_ino, after.st_size,
                 after.st_mtime_ns, after.st_ctime_ns), 'Input changed during hashing')
    return digest


def identity(path):
    path = Path(path)
    require(not path.is_symlink(), 'Symlink identity')
    s = path.stat()
    require(stat.S_ISREG(s.st_mode), 'Disk identity must be regular')
    return dict(device=s.st_dev, inode=s.st_ino, uid=s.st_uid, gid=s.st_gid,
                mode=stat.S_IMODE(s.st_mode),
                xattrs={name: os.getxattr(path, name).hex()
                        for name in sorted(os.listxattr(path))})


def reject_mac(value):
    if isinstance(value, dict):
        for key, child in value.items():
            require(key not in MAC_KEYS, 'Historical Mac field in server evidence: ' + key)
            reject_mac(child)
    elif isinstance(value, list):
        for child in value:
            reject_mac(child)
    elif isinstance(value, str):
        require(value.lower() not in MAC_KEYS, 'Historical Mac provider value')


def validate_expected(expected):
    require(set(IDENTITY_KEYS) <= expected.keys(), 'Missing independent release identities')
    require(re.fullmatch(r'[0-9]{8}\.[0-9]+', expected['build_id']), 'Invalid build')
    require(re.fullmatch('[0-9a-f]{40}', expected['source_revision']), 'Invalid source')
    valid_hash(expected['candidate'], 'Invalid candidate')
    valid_hash(expected['iso_sha256'], 'Invalid ISO')
    require(type(expected['iso_bytes']) is int and expected['iso_bytes'] > 0, 'Invalid ISO bytes')
    require(expected['profile'] == 'arm-preview', 'Wrong desktop profile')
    require(re.fullmatch('[a-zA-Z0-9_.-]+', expected['fixture_token']), 'Invalid fixture token')
    for key in ('static_proof_sha256', 'checks_preparation_sha256', 'checks_manifest_sha256',
                'signed_heads_sha256', 'native_headers_sha256'):
        valid_hash(expected.get(key), 'Missing independent ' + key)


def immutable_json(path, data):
    body = (json.dumps(data, indent=2, sort_keys=True) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(body)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    return hashlib.sha256(body).hexdigest()


def read_proofs(receipt, fixture, owner_uid):
    """All evidence is inside one fixture; relative names cannot escape it."""
    proofs = receipt.get('proof_hashes')
    require(isinstance(proofs, dict) and proofs, 'Missing proof closure')
    result = {}
    for name, digest in proofs.items():
        rel = Path(name)
        require(not rel.is_absolute() and rel.parts and '..' not in rel.parts,
                'Proof must be a safe fixture-relative path')
        valid_hash(digest)
        path = fixture / rel
        body = regular_bytes(path, owner_uid=owner_uid, immutable=True)
        require(hashlib.sha256(body).hexdigest() == digest, 'Changed proof: ' + name)
        result[name] = body
    return result


def validate_prepared(fixture, expected, *, owner_uid=0):
    fixture = Path(fixture)
    validate_expected(expected)
    path = fixture / 'PREPARED.json'
    body = regular_bytes(path, owner_uid=owner_uid, immutable=True)
    p = json.loads(body)
    reject_mac(p)
    for key in IDENTITY_KEYS:
        require(p.get(key) == expected[key], 'Prepared identity differs: ' + key)
    require(p.get('provider') == PROVIDER, 'Explicit server provider required')
    require(p.get('result') == 'PREPARED_ONLY', 'Preparation cannot claim execution')
    require(p.get('guest_architecture') == 'aarch64' and
            p.get('host_architecture') == 'x86_64' and p.get('accelerator') == 'tcg' and
            p.get('qemu_machine') == 'virt', 'Wrong server architecture/accelerator')
    for key in ('static_proof_sha256', 'checks_preparation_sha256', 'checks_manifest_sha256',
                'signed_heads_sha256', 'native_headers_sha256'):
        require(p.get(key) == expected[key], 'Independent input differs: ' + key)
    proofs = read_proofs(p, fixture, owner_uid)
    for key, name in (('static_proof_sha256', 'static-proof.json'),
                      ('checks_preparation_sha256', 'checks-preparation.json'),
                      ('checks_manifest_sha256', 'checks-manifest.json'),
                      ('signed_heads_sha256', 'signed-heads.json'),
                      ('native_headers_sha256', 'native-headers.json')):
        require(p['proof_hashes'].get(name) == p[key], 'Missing pinned input proof: ' + name)
    manifest = json.loads(proofs['checks-manifest.json'])
    require(set(manifest) == set(CHECK_FILES), 'Guest checker capsule incomplete')
    for name, digest in manifest.items():
        require(p['proof_hashes'].get('checks/' + name) == digest, 'Guest checker differs: ' + name)
    heads = json.loads(proofs['signed-heads.json'])
    validate_heads(heads)
    headers = json.loads(proofs['native-headers.json'])
    require(set(headers) == {'luma-terminal', 'luma-application-installer'}, 'Native headers incomplete')
    for value in headers.values():
        valid_hash(value['header_sha256'])
        require(isinstance(value.get('nevra'), str) and value['nevra'], 'Missing RPM NEVRA')
    invocation = json.loads(proofs['qemu-invocation.json'])
    validate_invocation(invocation, p)
    for name in ('qemu', 'firmware'):
        value = p['executables'][name]
        valid_hash(value['sha256'])
        require(file_sha256(value['path']) == value['sha256'], 'Changed host binary: ' + name)
    iso = Path(p['iso_path'])
    require(iso.stat().st_size == p['iso_bytes'] and file_sha256(iso) == p['iso_sha256'],
            'Final ISO whole-body differs')
    require(identity(iso) == p['iso_identity'], 'ISO input identity differs')
    require(p['iso_readonly_binding'] is True, 'ISO must have a read-only VM binding')
    require(p['minimum_free_bytes'] >= FLOOR_BYTES, 'Storage reserve below mandatory floor')
    return p, hashlib.sha256(body).hexdigest(), heads


def validate_heads(heads):
    require(isinstance(heads, dict) and len(heads) == 27, 'Exactly 27 signed heads required')
    require(len([ref for ref in heads if ref.startswith('app/')]) == 24, 'Exactly 24 app heads required')
    runtimes = {'runtime/org.projectluma.Platform/aarch64/44',
                'runtime/org.projectluma.Platform.Locale/aarch64/44',
                'runtime/org.projectluma.Platform.GL.default/aarch64/44'}
    require({r for r in heads if r.startswith('runtime/')} == runtimes, 'Wrong signed runtime heads')
    for ref, digest in heads.items():
        parts = ref.split('/')
        require(len(parts) == 4 and parts[2] == 'aarch64' and
                ((parts[0] == 'app' and parts[3] == 'beta') or ref in runtimes), 'Wrong signed ref')
        valid_hash(digest)
    for app, _ in BEHAVIORS:
        require('app/' + app + '/aarch64/beta' in heads, 'Missing behavioral app')


def validate_invocation(argv, prepared):
    require(isinstance(argv, list) and argv and all(isinstance(s, str) for s in argv), 'Invalid invocation')
    require(argv[0] == prepared['executables']['qemu']['path'], 'QEMU invocation mismatch')
    def option(name):
        require(argv.count(name) == 1 and argv.index(name) + 1 < len(argv), 'Missing/duplicate ' + name)
        return argv[argv.index(name) + 1]
    require(option('-accel').split(',')[0] == 'tcg', 'TCG required')
    require(option('-machine').split(',')[0] == 'virt', 'virt machine required')
    require('-enable-kvm' not in argv, 'Wrong accelerator')
    drives = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == '-drive']
    require(any('file=' + prepared['iso_path'] in d.split(',') and 'readonly=on' in d.split(',')
                for d in drives), 'Actual invocation lacks exact read-only ISO')
    firmware = prepared['executables']['firmware']['path']
    bios = argv[argv.index('-bios') + 1] if argv.count('-bios') == 1 else None
    require(bios == firmware or any('file=' + firmware in d.split(',') and
            'if=pflash' in d.split(',') and 'readonly=on' in d.split(',') for d in drives),
            'Actual invocation lacks pinned read-only firmware')
    require(any('file=' + prepared['target_disk_path'] in d.split(',') and
            'format=qcow2' in d.split(',') for d in drives), 'Actual invocation lacks exact target disk')


def validate_rows(q, heads):
    require(q.get('result') == 'PASS' and q.get('architecture') == 'aarch64', 'Guest did not pass')
    rows = q.get('results')
    require(isinstance(rows, list) and len(rows) == 11, 'All 11 actual result rows required')
    actual = Counter((row.get('app_id'), row.get('check')) for row in rows)
    require(actual == Counter(BEHAVIORS), 'Missing/duplicate behavioral category or launcher')
    for row in rows:
        require(type(row.get('exit_status')) is int and row['exit_status'] == 0 and
                row.get('actual_flatpak_sandbox') is True and row.get('permission_overrides') is False,
                'Failed or altered sandbox behavior')
        ref = 'app/' + row['app_id'] + '/aarch64/beta'
        require(row.get('ref') == ref and row.get('commit') == heads[ref], 'Wrong actual installed app head')
        if row['check'] == 'installed-launcher':
            require(row.get('mapped_window') is True, 'Launcher did not map')
        if row['check'] != 'installed-native-and-signed-catalogue-service':
            valid_hash(row.get('log_sha256'), 'Missing actual checker log')


def validate_server_qualification(fixture: Path, expected: dict, *, owner_uid=0):
    """Verify the complete closed producer chain; never promote or modify proof."""
    fixture = Path(fixture)
    p, prior_hash, heads = validate_prepared(fixture, expected, owner_uid=owner_uid)
    all_proofs = {str(fixture / name): digest for name, digest in p['proof_hashes'].items()}
    all_proofs[str(fixture / 'PREPARED.json')] = prior_hash
    chain = {}
    for name, result in (('CREATED', 'CREATED_ONLY'), ('INSTALLED', 'PASS_INSTALLED'),
                         ('QUALIFIED', 'PASS'), ('CLOSED', 'PASS_CLOSED')):
        path = fixture / (name + '.json')
        body = regular_bytes(path, owner_uid=owner_uid, immutable=True)
        receipt = json.loads(body)
        reject_mac(receipt)
        require(receipt.get('provider') == PROVIDER and receipt.get('result') == result,
                'Wrong provider/stage: ' + name)
        for key in IDENTITY_KEYS:
            require(receipt.get(key) == p[key], 'Receipt identity differs: ' + key)
        require(receipt.get('previous_receipt_sha256') == prior_hash, 'Broken receipt chain: ' + name)
        proofs = read_proofs(receipt, fixture, owner_uid)
        all_proofs.update({str(fixture / key): digest for key, digest in receipt['proof_hashes'].items()})
        prior_hash = hashlib.sha256(body).hexdigest()
        all_proofs[str(path)] = prior_hash
        chain[name] = (receipt, proofs)
    created = chain['CREATED'][0]
    require(created.get('exclusively_created') is True and created.get('allocated_data_bytes') == 0,
            'Fresh exclusive zero data extent proof required')
    disk_info = json.loads(chain['CREATED'][1]['created-qemu-img-info.json'])
    require(disk_info.get('format') == 'qcow2' and disk_info.get('virtual-size') == VIRTUAL_BYTES and
            not disk_info.get('backing-filename') and not disk_info.get('dirty-flag'), 'Not a fresh standalone 48GiB disk')
    require(created.get('disk_path') == p['target_disk_path'], 'Changed created disk path')
    require(set(created.get('disk_identity', {})) == {'device', 'inode', 'uid', 'gid', 'mode', 'xattrs'}, 'Incomplete created disk identity')
    extents = json.loads(chain['CREATED'][1]['created-qemu-img-map.json'])
    require(isinstance(extents, list) and extents and all(x.get('data') is False for x in extents), 'Initial disk contains allocated guest data')
    installed, ip = chain['INSTALLED']
    require(installed.get('disk_identity') == created['disk_identity'], 'Installed disk identity differs')
    require(installed.get('installation_origin') == 'fresh-final-luma-iso', 'Actual final Luma medium required')
    require(installed.get('unsigned_modifications') == [], 'Unsigned diagnostic overlays not allowed')
    transcript = ip['installer.log'].decode(errors='replace')
    require('Installation complete' in transcript and 'Anaconda' in transcript, 'Missing actual installer completion')
    status = json.loads(ip['installed-rpm-ostree-status.json'])
    booted = [row for row in status['deployments'] if row.get('booted') is True]
    require(len(booted) == 1 and booted[0]['checksum'] == p['candidate'], 'Installed boot candidate differs')
    boot = json.loads(ip['installed-boot.json'])
    require(boot.get('uname_machine') == 'aarch64' and boot.get('graphical_target') == 'active' and
            boot.get('efi_boot') is True, 'Installed ARM EFI graphical boot not proven')
    q, qp = chain['QUALIFIED']
    require(q.get('disk_identity') == created['disk_identity'] and q.get('unsigned_modifications') == [],
            'Qualified disk changed or modified unsigned')
    result = json.loads(qp['guest-result.json'])
    require(result.get('source_revision') == p['source_revision'], 'Guest result source differs')
    validate_rows(result, heads)
    dispatch = json.loads(qp['driver-outcome.json'])
    require(dispatch.get('returncode') == 0 and dispatch.get('timed_out') is False and
            dispatch.get('results_present') is True, 'Normal driver outcome missing')
    runtime = json.loads(qp['runtime-heads.json'])
    require(runtime == {k: v for k, v in heads.items() if k.startswith('runtime/')}, 'Installed runtime head mismatch')
    require(json.loads(qp['native-host-headers.json']) == json.loads(regular_bytes(fixture / 'native-headers.json')),
            'Actual installed native headers mismatch')
    for row in result['results']:
        if 'log_sha256' in row:
            require(row['log_sha256'] in q['proof_hashes'].values(), 'Actual result log absent from proof closure')
    closed, cp = chain['CLOSED']
    require(closed.get('disk_identity') == created['disk_identity'], 'Closed source disk identity differs')
    outcome = json.loads(cp['normal-wrapper-outcome.json'])
    require(outcome.get('returncode') == 0 and outcome.get('result') == 'finished' and
            outcome.get('signal') is None, 'Normal admitted wrapper did not finish successfully')
    consumers = json.loads(cp['closed-consumers.json'])
    require(consumers.get('qemu_pids') == [] and consumers.get('open_fds') == [] and
            consumers.get('cgroup_pids') == [] and consumers.get('domain_state') == 'shut off',
            'Owned VM/producer still has consumers')
    require(consumers.get('units') and all(x.get('ActiveState') == 'inactive' and
            x.get('Result') == 'success' and x.get('ExecMainStatus') == '0' and
            re.fullmatch(r'luma-arm-desktop-[a-zA-Z0-9_.@-]+\.service', x.get('Id', ''))
            for x in consumers['units']), 'Root units not successfully closed')
    require(str(consumers.get('scanned_cgroup', '')).startswith(
            '/sys/fs/cgroup/luma.slice/luma-arm.slice/luma-arm-desktop.slice/'),
            'Missing exact owned producer cgroup scan')
    require(consumers.get('scanned_disk_identity') == created['disk_identity'], 'No-FD scan disk identity differs')
    archive = Path(closed['archive_path'])
    require(identity(archive) == closed['archive_identity'], 'Archived disk metadata differs')
    require(file_sha256(archive) == closed['archive_sha256'] and archive.stat().st_size == closed['archive_bytes'],
            'Archived full disk body differs')
    require(closed.get('source_disk_sha256') == closed['archive_sha256'] and
            closed.get('source_disk_bytes') == closed['archive_bytes'],
            'Complete source/archive body custody differs')
    for key in ('uid', 'gid', 'mode', 'xattrs'):
        require(closed['archive_identity'][key] == closed['disk_identity'][key],
                'Source/archive disk metadata custody differs: ' + key)
    require(closed.get('source_input_sha256') == p['iso_sha256'], 'Closed ISO input changed')
    require(cp['original-domain.xml'] == cp['restored-domain.xml'], 'Original XML not restored')
    return {'provider': PROVIDER, 'qualified_metadata': q, 'proof_hashes': all_proofs,
            'closed_receipt_sha256': prior_hash, 'architecture': 'aarch64',
            'physical_hardware_coverage': False}



def prepare_fixture(fixture: Path, expected: dict, inputs: dict):
    """Freeze real inputs exclusively; never infer new release IDs from old proof.

    inputs contains iso, static_proof, checks_preparation, checks_directory,
    signed_heads, native_headers, qemu, firmware, qemu_invocation (JSON file),
    target_disk_path, and planned_growth_bytes. Admission remains caller-owned.
    Returns preparation hash; a failed preparation is retained, never overwritten.
    """
    validate_expected(expected)
    fixture = Path(fixture)
    require(fixture.is_absolute() and not fixture.exists(), 'Fresh fixture path required')
    iso = Path(inputs['iso'])
    require(iso.is_absolute() and iso.stat().st_size == expected['iso_bytes'] and
            file_sha256(iso) == expected['iso_sha256'], 'Final ISO body differs')
    require(type(inputs['planned_growth_bytes']) is int and inputs['planned_growth_bytes'] > 0,
            'Explicit positive remaining-growth budget required')
    free = os.statvfs(iso.parent)
    require(free.f_bavail * free.f_frsize >= FLOOR_BYTES + inputs['planned_growth_bytes'],
            'Insufficient real SSD reserve plus growth')
    fixture.mkdir(mode=0o700)
    (fixture / 'checks').mkdir(mode=0o700)
    proofs = {}
    def copy_input(source, name, expected_digest=None):
        data = regular_bytes(Path(source))
        digest = hashlib.sha256(data).hexdigest()
        if expected_digest is not None:
            require(digest == expected_digest, 'Independent frozen input differs: ' + name)
        target = fixture / name
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(data); stream.flush(); os.fsync(fd)
        finally:
            os.close(fd)
        proofs[name] = digest
        return data
    for name, key, digest_key in (
        ('static-proof.json', 'static_proof', 'static_proof_sha256'),
        ('checks-preparation.json', 'checks_preparation', 'checks_preparation_sha256'),
        ('signed-heads.json', 'signed_heads', 'signed_heads_sha256'),
        ('native-headers.json', 'native_headers', 'native_headers_sha256')):
        copy_input(inputs[key], name, expected[digest_key])
    checks = Path(inputs['checks_directory'])
    manifest = {}
    for name in CHECK_FILES:
        copy_input(checks / name, 'checks/' + name)
        manifest[name] = proofs['checks/' + name]
    # The contract independently pins this exact canonical manifest encoding.
    digest = immutable_json(fixture / 'checks-manifest.json', manifest)
    require(digest == expected['checks_manifest_sha256'], 'Independent checker capsule differs')
    proofs['checks-manifest.json'] = digest
    copy_input(inputs['qemu_invocation'], 'qemu-invocation.json')
    prepared = {key: expected[key] for key in IDENTITY_KEYS}
    prepared.update({key: expected[key] for key in (
        'static_proof_sha256', 'checks_preparation_sha256', 'checks_manifest_sha256',
        'signed_heads_sha256', 'native_headers_sha256')})
    prepared.update(result='PREPARED_ONLY', provider=PROVIDER, guest_architecture='aarch64',
                    host_architecture='x86_64', accelerator='tcg', qemu_machine='virt',
                    iso_path=str(iso), iso_identity=identity(iso), iso_readonly_binding=True,
                    target_disk_path=str(Path(inputs['target_disk_path'])),
                    minimum_free_bytes=FLOOR_BYTES,
                    planned_growth_bytes=inputs['planned_growth_bytes'],
                    observed_free_bytes=free.f_bavail * free.f_frsize,
                    executables={name: {'path': str(Path(inputs[name])),
                                        'sha256': file_sha256(inputs[name])}
                                 for name in ('qemu', 'firmware')}, proof_hashes=proofs)
    require(Path(prepared['target_disk_path']).is_absolute(), 'Absolute target disk path required')
    validate_invocation(json.loads((fixture / 'qemu-invocation.json').read_text()), prepared)
    prepared_hash = immutable_json(fixture / 'PREPARED.json', prepared)
    validate_prepared(fixture, expected, owner_uid=os.getuid())
    return prepared_hash



def write_stage(fixture, expected, stage, result, previous_stage, proofs, **fields):
    """Exclusively freeze observed phase outputs; no producer success inference."""
    previous = regular_bytes(fixture / (previous_stage + '.json'),
                             owner_uid=os.getuid(), immutable=True)
    hashes = {}
    for name, body in proofs.items():
        rel = Path(name)
        require(not rel.is_absolute() and rel.parts and '..' not in rel.parts,
                'Unsafe phase proof path')
        target = fixture / rel
        require(target.parent == fixture, 'Phase proof must be directly inside fixture')
        data = body if isinstance(body, bytes) else encoded_json(body)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(data); stream.flush(); os.fsync(fd)
        finally:
            os.close(fd)
        hashes[name] = hashlib.sha256(data).hexdigest()
    receipt = {key: expected[key] for key in IDENTITY_KEYS}
    receipt.update(provider=PROVIDER, result=result,
                   previous_receipt_sha256=hashlib.sha256(previous).hexdigest(),
                   proof_hashes=hashes, **fields)
    immutable_json(fixture / (stage + '.json'), receipt)
    return receipt


def encoded_json(data):
    return (json.dumps(data, indent=2, sort_keys=True) + '\n').encode()


def create_fresh_disk(fixture: Path, expected: dict, qemu_img: Path,
                      qemu_img_sha256: str):
    """Actually create/observe a fresh 48GiB qcow2 under the admitted lifecycle.

    Leaves any partial disk/proofs on error. Caller supplies a separately pinned
    qemu-img binary and owns resource admission; this never starts a VM.
    """
    fixture = Path(fixture)
    p, _, _ = validate_prepared(fixture, expected, owner_uid=os.getuid())
    valid_hash(qemu_img_sha256)
    require(file_sha256(qemu_img) == qemu_img_sha256, 'Changed qemu-img executable')
    target = Path(p['target_disk_path'])
    parent = target.parent.stat()
    require(parent.st_uid == os.getuid() and stat.S_IMODE(parent.st_mode) & 0o077 == 0,
            'Fresh disk parent must be private to producer')
    require(parent.st_dev == Path(p['iso_path']).stat().st_dev, 'VM disk must share admitted MAIN SSD lifecycle')
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    # Only this exclusively created empty inode may be initialized by qemu-img.
    initial = identity(target)
    subprocess.run([str(qemu_img), 'create', '-f', 'qcow2', str(target), str(VIRTUAL_BYTES)],
                   check=True, stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
    require(identity(target) == initial, 'qemu-img replaced reserved disk identity')
    info = json.loads(subprocess.check_output([str(qemu_img), 'info', '--output=json', str(target)],
                                             stdin=subprocess.DEVNULL, timeout=60))
    extents = json.loads(subprocess.check_output([str(qemu_img), 'map', '--output=json', str(target)],
                                                stdin=subprocess.DEVNULL, timeout=60))
    require(info.get('format') == 'qcow2' and info.get('virtual-size') == VIRTUAL_BYTES and
            not info.get('backing-filename') and not info.get('dirty-flag'), 'Fresh qcow2 validation failed')
    require(extents and all(row.get('data') is False for row in extents), 'Fresh qcow2 has guest data')
    return write_stage(fixture, expected, 'CREATED', 'CREATED_ONLY', 'PREPARED', {
        'created-qemu-img-info.json': info, 'created-qemu-img-map.json': extents,
        'created-qemu-img-executable.json': {'path': str(qemu_img), 'sha256': qemu_img_sha256},
    }, exclusively_created=True, allocated_data_bytes=0,
        disk_identity=identity(target), disk_path=str(target))


def observe_disk_consumers(disk: Path, cgroup: Path, unit_names: list[str], domain_state: str):
    """Real privileged closure scan. Errors fail closed; no empty fake defaults.

    Call after the normally admitted producer has actually exited. Archive and
    original XML byte comparison are separately mandatory for CLOSED evidence.
    """
    require(os.getuid() == 0, 'Full /proc consumer scan requires release administrator')
    target = identity(disk)
    require(str(cgroup).startswith('/sys/fs/cgroup/luma.slice/luma-arm.slice/luma-arm-desktop.slice/'),
            'Only the exact owned ARM cgroup may be scanned')
    require(cgroup.is_dir() and not cgroup.is_symlink(), 'Owned cgroup missing')
    cg_pids = []
    for path in cgroup.rglob('cgroup.procs'):
        cg_pids += [int(pid) for pid in path.read_text().split()]
    open_fds = []
    qemu_pids = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            command = (proc / 'cmdline').read_bytes().split(b'\0')
            if any(os.fsencode(str(disk)) in arg for arg in command) and command and b'qemu' in command[0]:
                qemu_pids.append(int(proc.name))
            for fd in (proc / 'fd').iterdir():
                try:
                    observed = fd.stat()
                except FileNotFoundError:
                    continue
                if (observed.st_dev, observed.st_ino) == (target['device'], target['inode']):
                    open_fds.append({'pid': int(proc.name), 'fd': int(fd.name)})
        except (FileNotFoundError, ProcessLookupError):
            continue
    units = []
    require(unit_names and len(unit_names) == len(set(unit_names)), 'Explicit unique owned units required')
    for unit in unit_names:
        require(re.fullmatch(r'luma-arm-desktop-[a-zA-Z0-9_.@-]+\.service', unit), 'Unowned closure unit')
        output = subprocess.check_output(['systemctl', 'show', unit, '-p', 'Id', '-p', 'ActiveState',
                                          '-p', 'Result', '-p', 'ExecMainStatus'],
                                         text=True, stdin=subprocess.DEVNULL, timeout=30)
        record = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
        require(record.get('Id') == unit and record.get('ActiveState') == 'inactive' and
                record.get('Result') == 'success' and record.get('ExecMainStatus') == '0',
                'Actual root unit is not successfully closed')
        units.append(record)
    observed = dict(qemu_pids=qemu_pids, open_fds=open_fds, cgroup_pids=sorted(set(cg_pids)),
                    domain_state=domain_state, units=units, scanned_disk_identity=target,
                    scanned_cgroup=str(cgroup))
    require(domain_state == 'shut off' and not qemu_pids and not open_fds and not cg_pids,
            'Actual VM/disk consumers remain')
    return observed

class ServerInputBridge:
    """Actual local QMP input/screenshot bridge, bound to one observed QEMU birth.

    This is an operator adapter only. It never runs guest assertions, changes
    check deadlines, synthesizes mapped windows or calls a Mac/tunnel helper.
    """
    def __init__(self, qmp_socket: Path, pid: int, start_ticks: int,
                 invocation: list[str], timeout=30):
        require(type(pid) is int and pid > 1, 'Invalid QEMU PID')
        self.pid, self.start_ticks, self.invocation = pid, start_ticks, invocation
        self.socket_path, self.timeout = Path(qmp_socket), timeout
        self.check_process()
        require(stat.S_ISSOCK(self.socket_path.lstat().st_mode), 'QMP is not a real local socket')

    def check_process(self):
        process = Path('/proc') / str(self.pid)
        body = (process / 'stat').read_text()
        ticks = int(body[body.rindex(')') + 2:].split()[19])
        require(ticks == self.start_ticks, 'QEMU PID birth changed')
        argv = (process / 'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
        require([arg.decode() for arg in argv] == self.invocation, 'QEMU invocation changed')
        require(str((process / 'exe').resolve()) == self.invocation[0], 'QEMU executable differs')

    def execute(self, command, arguments=None):
        require(command in {'query-status', 'query-block', 'screendump', 'input-send-event', 'send-key'},
                'Unreviewed QMP bridge action')
        self.check_process()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(self.timeout); conn.connect(str(self.socket_path))
            stream = conn.makefile('rwb')
            require('QMP' in json.loads(stream.readline()), 'Invalid QMP greeting')
            def call(name, args):
                stream.write((json.dumps({'execute': name, 'arguments': args, 'id': name}) + '\n').encode())
                stream.flush()
                while True:
                    message = json.loads(stream.readline())
                    if message.get('id') == name:
                        require('error' not in message, 'QMP operation failed: ' + str(message))
                        return message['return']
            call('qmp_capabilities', {})
            return call(command, arguments or {})

    def snapshot(self, path: Path):
        path = Path(path)
        require(path.is_absolute() and not path.exists(), 'Exclusive screenshot output required')
        self.execute('screendump', {'filename': str(path), 'format': 'png'})
        body = regular_bytes(path)
        require(body.startswith(b'\x89PNG\r\n\x1a\n'), 'Actual QMP screenshot missing')
        import struct
        return struct.unpack('>II', body[16:24])

    def key(self, key):
        require(key in {'SPACE', 'ESC'}, 'Invalid original guest key request')
        code = {'SPACE': 'spc', 'ESC': 'esc'}[key]
        self.execute('send-key', {'keys': [{'type': 'qcode', 'data': code}], 'hold-time': 80})

    def click(self, x, y, width, height):
        require(all(type(v) is int for v in (x, y, width, height)) and
                0 <= x < width and 0 <= y < height and width > 1 and height > 1,
                'Hardware click outside actual display')
        move = [{'type': 'abs', 'data': {'axis': axis, 'value': value}}
                for axis, value in (('x', x * 32767 // (width - 1)), ('y', y * 32767 // (height - 1)))]
        self.execute('input-send-event', {'events': move + [
            {'type': 'btn', 'data': {'button': 'left', 'down': True}}]})
        import time
        time.sleep(.08)
        self.execute('input-send-event', {'events': [
            {'type': 'btn', 'data': {'button': 'left', 'down': False}}]})

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', type=Path)
    parser.add_argument('--prepare-inputs', type=Path, help='freeze preparation only; never launch a VM')
    parser.add_argument('--expected', type=Path, required=True,
                        help='independently pinned final release inputs, not derived from the fixture')
    args = parser.parse_args()
    expected = json.loads(args.expected.read_text())
    if args.prepare_inputs:
        result = {'result': 'PREPARED_ONLY', 'prepared_sha256': prepare_fixture(
            args.fixture, expected, json.loads(args.prepare_inputs.read_text()))}
    else:
        result = validate_server_qualification(args.fixture, expected)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
