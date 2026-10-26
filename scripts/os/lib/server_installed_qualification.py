#!/usr/bin/env python3
"""Explicit server TCG custody for the unchanged installed-app regression suite.

This module checks evidence, not simulated guest success. Callers must supply
independently pinned final release inputs; no historical Mac/build defaults exist.
Preparation never launches a VM or changes candidate/feed content.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import stat
import socket
import subprocess
import time
import struct
import uuid
import shlex
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

PROVIDER = 'server-qemu-tcg-aarch64/v2'
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


def regular_bytes(path: Path, *, owner_uid=None, immutable=False, maximum_bytes=64 * 1024**2):
    path = Path(path)
    require(path.is_absolute(), 'Proof path must be absolute')
    # Reject symlink traversal, including parents; resolve() alone would hide it.
    for part in (path, *path.parents):
        require(not part.is_symlink(), 'Symlink proof path: ' + str(part))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode), 'Proof is not regular: ' + str(path))
        require(before.st_size <= maximum_bytes, 'Proof exceeds bounded input size')
        if owner_uid is not None:
            require(before.st_uid == owner_uid, 'Wrong proof owner: ' + str(path))
        if immutable:
            require(before.st_mode & 0o222 == 0, 'Writable receipt: ' + str(path))
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            body = stream.read(maximum_bytes + 1)
            require(len(body) <= maximum_bytes, 'Proof grew past bounded input size')
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
                'signed_heads_sha256', 'native_headers_sha256', 'app_baseline_sha256'):
        valid_hash(expected.get(key), 'Missing independent ' + key)
    validate_producer_binding(expected.get('producer_binding'))
    require(expected.get('qmp_transport') in {'direct-unix-qmp/v1','libvirt-system-qmp/v1'},
            'Explicit server QMP transport required')
    if expected['qmp_transport']=='libvirt-system-qmp/v1':
        valid_hash(expected.get('domain_definition_sha256'), 'Independent installer domain XML required')



def validate_producer_binding(binding):
    require(isinstance(binding, dict), 'Independent producer binding required')
    roots = binding.get('root_units')
    require(isinstance(roots, dict) and roots, 'Exact root unit/cgroup bindings required')
    for unit, cgroup in roots.items():
        require(re.fullmatch(r'luma-arm-desktop-[a-zA-Z0-9_.@-]+\.service', unit), 'Wrong owned root unit')
        require(cgroup == '/luma.slice/luma-arm.slice/luma-arm-desktop.slice/' + unit,
                'Root producer cgroup must exactly match owned unit')
    require(re.fullmatch(r'dev-arm-server-[a-zA-Z0-9_.-]+\.service', binding.get('normal_unit', '')),
            'Exact normal admission unit required')
    require(binding.get('normal_cgroup') == '/user.slice/user-1003.slice/user@1003.service/app.slice/' +
            binding['normal_unit'], 'Wrong normal admission cgroup')
    require(type(binding.get('qemu_uid')) is int and binding['qemu_uid'] > 0 and
            type(binding.get('qemu_gid')) is int and binding['qemu_gid'] > 0, 'Explicit non-root QEMU owner required')
    require(re.fullmatch(r'luma-arm-desktop-[a-zA-Z0-9_.-]+', binding.get('domain_name', '')),
            'Exact owned libvirt domain name required')
    require(re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', binding.get('domain_uuid', '')),
            'Exact libvirt UUID required')


def validate_closed_observation(consumers, binding, root_outcomes):
    validate_producer_binding(binding)
    require(consumers.get('qemu_pids') == [] and consumers.get('open_fds') == [], 'Live VM/disk consumers remain')
    require(consumers.get('domain_name') == binding['domain_name'] and
            consumers.get('domain_uuid') == binding['domain_uuid'] and
            consumers.get('domain_state') == 'shut off', 'Actual domain UUID/state differs')
    units = consumers.get('units')
    require(isinstance(units, list) and len(units) == len(binding['root_units']) and
            {x.get('Id') for x in units} == set(binding['root_units']), 'Unrelated or missing root units')
    require(set(root_outcomes) == set(binding['root_units']), 'Missing exact root outcome receipts')
    for unit in units:
        name = unit['Id']; expected_cg = binding['root_units'][name]
        require(unit.get('ActiveState') == 'inactive' and unit.get('MainPID') == '0' and
                unit.get('ControlGroup') in ('', expected_cg) and
                unit.get('LoadState') in ('loaded', 'not-found'), 'Root unit is not actually inactive')
        if unit['LoadState'] == 'loaded':
            require(unit.get('Result') == 'success' and unit.get('ExecMainStatus') == '0', 'Root unit did not succeed')
        outcome = root_outcomes[name]
        require(outcome.get('unit') == name and outcome.get('cgroup') == expected_cg and
                type(outcome.get('exit_status')) is int and outcome['exit_status'] == 0,
                'Exact root producer outcome failed')
    normal = consumers.get('normal_unit_state', {})
    require(normal.get('Id') == binding['normal_unit'] and normal.get('MainPID') == '0' and
            normal.get('ActiveState') == 'inactive' and normal.get('ControlGroup') in ('', binding['normal_cgroup']),
            'Normal admission unit has not closed')
    groups = consumers.get('cgroups')
    expected_groups = set(binding['root_units'].values()) | {binding['normal_cgroup']}
    require(isinstance(groups, dict) and set(groups) == expected_groups, 'Wrong producer cgroup scan inventory')
    states = {x['Id']: x for x in units};states[binding['normal_unit']] = normal
    for name, cg in list(binding['root_units'].items()) + [(binding['normal_unit'], binding['normal_cgroup'])]:
        observed = groups[cg]
        require(observed.get('pids') == [] and type(observed.get('exists')) is bool, 'Producer cgroup is populated')
        if not observed['exists']:
            require(states[name]['ControlGroup'] == '', 'Missing cgroup without collected inactive unit')

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
    body = regular_bytes(path, owner_uid=owner_uid, immutable=True, maximum_bytes=4 * 1024**2)
    p = json.loads(body)
    reject_mac(p)
    for key in IDENTITY_KEYS:
        require(p.get(key) == expected[key], 'Prepared identity differs: ' + key)
    require(p.get('provider') == PROVIDER, 'Explicit server provider required')
    require(p.get('producer_binding') == expected['producer_binding'], 'Independent producer identity differs')
    require(p.get('qmp_transport') == expected['qmp_transport'], 'Independent QMP transport differs')
    require(p.get('result') == 'PREPARED_ONLY', 'Preparation cannot claim execution')
    require(p.get('guest_architecture') == 'aarch64' and
            p.get('host_architecture') == 'x86_64' and p.get('accelerator') == 'tcg' and
            p.get('qemu_machine') == 'virt', 'Wrong server architecture/accelerator')
    for key in ('static_proof_sha256', 'checks_preparation_sha256', 'checks_manifest_sha256',
                'signed_heads_sha256', 'native_headers_sha256', 'app_baseline_sha256'):
        require(p.get(key) == expected[key], 'Independent input differs: ' + key)
    proofs = read_proofs(p, fixture, owner_uid)
    for key, name in (('static_proof_sha256', 'static-proof.json'),
                      ('checks_preparation_sha256', 'checks-preparation.json'),
                      ('checks_manifest_sha256', 'checks-manifest.json'),
                      ('signed_heads_sha256', 'signed-heads.json'),
                      ('native_headers_sha256', 'native-headers.json'),
                      ('app_baseline_sha256', 'app-baseline.json')):
        require(p['proof_hashes'].get(name) == p[key], 'Missing pinned input proof: ' + name)
    manifest = json.loads(proofs['checks-manifest.json'])
    require(set(manifest) == set(CHECK_FILES), 'Guest checker capsule incomplete')
    for name, digest in manifest.items():
        require(p['proof_hashes'].get('checks/' + name) == digest, 'Guest checker differs: ' + name)
    heads = json.loads(proofs['signed-heads.json'])
    baseline = json.loads(proofs['app-baseline.json'])
    validate_heads(heads, baseline)
    headers = json.loads(proofs['native-headers.json'])
    require(set(headers) == {'luma-terminal', 'luma-application-installer'}, 'Native headers incomplete')
    for value in headers.values():
        valid_hash(value['header_sha256'])
        require(isinstance(value.get('nevra'), str) and value['nevra'], 'Missing RPM NEVRA')
    invocation = json.loads(proofs['qemu-invocation.json'])
    validate_invocation(invocation, p)
    require(p.get('fixture_root') == str(fixture), 'Prepared fixture root differs')
    executable_names={'qemu','firmware'}
    if p['qmp_transport']=='libvirt-system-qmp/v1':
        executable_names|={'libvirt_cli','firmware_variables'}
        require(p.get('domain_definition_sha256')==expected['domain_definition_sha256'] and
                p['proof_hashes'].get('domain-installer.xml')==expected['domain_definition_sha256'],
                'Prepared modern libvirt definition differs')
        require(p.get('installer_boot_inputs',{})==expected.get('installer_boot_inputs',{}),
                'Independent extracted/injected installer inputs differ')
        require(p.get('installer_cmdline')==expected.get('installer_cmdline') and
                p.get('installer_stage2')==expected.get('installer_stage2'), 'Independent installer boot intent differs')
        for value in p.get('installer_boot_inputs',{}).values():
            valid_hash(value['sha256'])
            require(file_sha256(value['path'])==value['sha256'], 'Retained installer input changed')
        validate_libvirt_definition(proofs['domain-installer.xml'],p)
    require(set(p['executables'])==executable_names, 'Hypervisor input inventory differs')
    for name in executable_names:
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


def validate_app_baseline(baseline):
    """Validate independently pinned current-source policy, never infer from heads."""
    require(isinstance(baseline, dict) and set(baseline) == {'schema', 'applications'} and
            baseline['schema'] == 'org.projectluma.os-app-baseline/v1',
            'Wrong current-source app baseline schema')
    apps = baseline['applications']
    require(isinstance(apps, list) and apps, 'Nonempty app baseline required')
    # Reverse-DNS Flatpak names: three or more nonempty ASCII components.
    # A dash is supported only in the final application component.
    pattern = r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\.[A-Za-z_][A-Za-z0-9_-]*'
    require(all(isinstance(app, str) and len(app) <= 255 and re.fullmatch(pattern, app)
                for app in apps), 'Invalid baseline application ID')
    require(len(apps) == len(set(apps)), 'Duplicate baseline application ID')
    return frozenset(apps)


def validate_heads(heads, baseline):
    apps = validate_app_baseline(baseline)
    runtimes = {'runtime/org.projectluma.Platform/aarch64/44',
                'runtime/org.projectluma.Platform.Locale/aarch64/44',
                'runtime/org.projectluma.Platform.GL.default/aarch64/44'}
    expected_refs = {'app/' + app + '/aarch64/beta' for app in apps} | runtimes
    require(isinstance(heads, dict) and set(heads) == expected_refs,
            'Signed heads differ from exact pinned baseline and runtime refs')
    for digest in heads.values():
        valid_hash(digest)
    for app, _ in BEHAVIORS:
        require('app/' + app + '/aarch64/beta' in heads, 'Missing behavioral app')


def validate_invocation(argv, prepared):
    if prepared['qmp_transport']=='libvirt-system-qmp/v1':
        require(isinstance(argv,dict) and argv.get('transport')=='libvirt-system-qmp/v1',
                'Explicit planned libvirt invocation required')
        executable=prepared['executables']['libvirt_cli']['path']
        require(argv.get('domain_definition_sha256')==prepared['domain_definition_sha256'],
                'Planned invocation has wrong domain definition')
        require(argv.get('define_argv')==[executable,'--connect','qemu:///system','define',
                str(Path(prepared['fixture_root'])/'domain-installer.xml')], 'Unreviewed libvirt define invocation')
        require(argv.get('start_argv')==[executable,'--connect','qemu:///system','start',
                prepared['producer_binding']['domain_name'],'--paused'], 'Must pause exact defined domain before guest execution')
        return
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



def validate_installer_boot_intent(kernel,initrd,cmdline,p):
    inputs=p.get('installer_boot_inputs',{})
    require(set(inputs)=={'kernel','initrd','kickstart'} and kernel==inputs['kernel']['path'] and
            initrd==inputs['initrd']['path'], 'Unpinned direct installer kernel/initrd/kickstart')
    ks=inputs['kickstart'].get('initrd_path')
    require(isinstance(cmdline,str) and cmdline==p.get('installer_cmdline') and
            isinstance(ks,str) and Path(ks).is_absolute() and '..' not in Path(ks).parts,
            'Exact intended installer command line/embedded KS path required')
    arguments=shlex.split(cmdline)
    require([arg for arg in arguments if arg.startswith('inst.ks=')]==['inst.ks=file:'+ks],
            'Installer command line points at another kickstart')
    stage2=p.get('installer_stage2')
    require(isinstance(stage2,str) and stage2.startswith('inst.stage2=hd:LABEL=') and
            [arg for arg in arguments if arg.startswith('inst.stage2=')]==[stage2],
            'Installer stage2 must name the independently pinned final ISO label')


def validate_libvirt_definition(body,p):
    """Validate source-pinned planned/live modern libvirt XML, never infer argv."""
    root=ET.fromstring(body)
    require(root.get('type')=='qemu' and root.findtext('name')==p['producer_binding']['domain_name'] and
            root.findtext('uuid')==p['producer_binding']['domain_uuid'], 'Wrong explicit libvirt domain')
    os_type=root.find('os/type')
    require(os_type is not None and os_type.get('arch')=='aarch64' and
            re.fullmatch(r'virt(?:-[0-9]+\.[0-9]+)?',os_type.get('machine','')), 'Wrong libvirt guest/machine')
    require(root.findtext('devices/emulator')==p['executables']['qemu']['path'], 'Changed XML emulator')
    loader=root.find('os/loader');nvram=root.find('os/nvram')
    require(loader is not None and loader.text==p['executables']['firmware']['path'] and
            loader.get('readonly')=='yes' and loader.get('type')=='pflash', 'Changed read-only UEFI code')
    require(nvram is not None and nvram.get('template')==p['executables']['firmware_variables']['path'] and
            Path(nvram.text).parent==Path(p['target_disk_path']).parent, 'NVRAM must be new owned VM state from pinned template')
    require(root.findtext('resource/partition')=='/luma/arm/desktop', 'Wrong owned libvirt resource partition')
    kernel=root.findtext('os/kernel');initrd=root.findtext('os/initrd')
    if kernel or initrd:
        validate_installer_boot_intent(kernel,initrd,root.findtext('os/cmdline'),p)
    hard=root.find('memtune/hard_limit')
    factors={'KiB':1024,'MiB':1024**2,'GiB':1024**3,'bytes':1}
    require(hard is not None and hard.get('unit','KiB') in factors and
            int(hard.text)*factors[hard.get('unit','KiB')]==8*1024**3, 'Final VM hard8GiB bound required')
    require(root.findall('devices/hostdev')==[], 'Physical host device in generic server fixture')
    disks=root.findall('devices/disk')
    target=[d for d in disks if d.get('device')=='disk']
    media=[d for d in disks if d.get('device')=='cdrom']
    require(len(disks)==2 and len(target)==len(media)==1, 'Only fresh target and exact final ISO allowed')
    target_source=target[0].find('source');target_driver=target[0].find('driver')
    media_source=media[0].find('source');media_driver=media[0].find('driver')
    require(target_source is not None and target_driver is not None and target_source.get('file')==p['target_disk_path'] and
            target_driver.get('type')=='qcow2', 'Changed XML target disk')
    require(media_source is not None and media_driver is not None and media_source.get('file')==p['iso_path'] and
            media[0].find('readonly') is not None and media_driver.get('type')=='raw',
            'Exact final ISO must remain attached read-only')
    return root


def validate_observed_qemu_argv(argv,p):
    """Validate final observed block graph and any directly booted installer intent."""
    validate_qemu_block_graph(argv,p)
    if any(flag in argv for flag in ('-kernel','-initrd','-append')):
        def option(name):
            require(argv.count(name)==1 and argv.index(name)+1<len(argv), 'Missing actual '+name)
            return argv[argv.index(name)+1]
        validate_installer_boot_intent(option('-kernel'),option('-initrd'),option('-append'),p)


def validate_qemu_block_graph(argv,p):
    """Bind real -blockdev or -drive argv observed at this paused QEMU birth."""
    require(isinstance(argv,list) and argv and argv[0]==p['executables']['qemu']['path'] and
            all(isinstance(x,str) for x in argv), 'Actual QEMU argv differs')
    def option(name):
        require(argv.count(name)==1 and argv.index(name)+1<len(argv), 'Missing actual '+name)
        return argv[argv.index(name)+1]
    require(option('-accel').split(',')[0]=='tcg' and
            re.fullmatch(r'virt(?:-[0-9]+\.[0-9]+)?',option('-machine').split(',')[0]),
            'Observed accelerator/machine differs')
    require('-enable-kvm' not in argv, 'Unexpected actual KVM accelerator')
    nodes={}
    for i,arg in enumerate(argv[:-1]):
        if arg=='-blockdev':
            node=json.loads(argv[i+1])
            require(isinstance(node,dict) and isinstance(node.get('node-name'),str) and
                    node['node-name'] not in nodes, 'Duplicate/malformed actual block node')
            nodes[node['node-name']]=node
    drives=[argv[i+1] for i,arg in enumerate(argv[:-1]) if arg=='-drive']
    attached=set()
    for index,arg in enumerate(argv[:-1]):
        if arg=='-device':
            value=argv[index+1]
            if value.startswith('{'):device=json.loads(value)
            else:device=dict(part.split('=',1) for part in value.split(',') if '=' in part)
            if isinstance(device.get('drive'),str):attached.add(device['drive'])
    def readonly(node):
        value=node.get('read-only',False)
        require(type(value) is bool, 'Malformed actual read-only property')
        return value
    def source_file(node,path):
        if node.get('driver')=='file':return node if node.get('filename')==path else None
        source=nodes.get(node.get('file'))
        return source if source and source.get('driver')=='file' and source.get('filename')==path else None
    def bound_file(path,read_only,fmt):
        for name,node in nodes.items():
            if name not in attached:continue
            source=source_file(node,path)
            if source is None:continue
            # libvirt represents a raw CD directly with the read-only file BDS.
            if fmt=='raw' and node.get('driver')=='file' and read_only and readonly(node):return True
            if node.get('driver')==fmt and readonly(node) is read_only:
                # file protocol nodes commonly use auto-read-only:true without
                # explicit read-only:false. The attached format node controls
                # guest writes; live query-block independently checks effective ro.
                if read_only or not readonly(source):return True
        for drive in drives:
            parts=drive.split(',')
            if 'file='+path in parts and 'format='+fmt in parts:
                if read_only:return 'readonly=on' in parts or 'read-only=on' in parts
                return 'readonly=on' not in parts and 'read-only=on' not in parts
        return False
    require(bound_file(p['iso_path'],True,'raw'), 'Actual argv lacks exact attached read-only ISO block graph')
    require(bound_file(p['target_disk_path'],False,'qcow2'), 'Actual argv lacks exact attached writable target block graph')
    firmware=p['executables']['firmware']['path']
    machine_options=dict(part.split('=',1) for part in option('-machine').split(',') if '=' in part)
    pflash={value for key,value in machine_options.items() if key.startswith('pflash')}
    firmware_ok=any(name in pflash and readonly(node) and source_file(node,firmware) is not None
                    for name,node in nodes.items())
    firmware_ok=firmware_ok or any('file='+firmware in d.split(',') and 'if=pflash' in d.split(',') and
                                 'readonly=on' in d.split(',') for d in drives)
    require(firmware_ok, 'Actual argv lacks attached pinned read-only firmware')


def validate_live_blocks(blocks,p):
    """Exact source/RO/format checks for effective QMP media, including raw file CD."""
    require(isinstance(blocks,list), 'Malformed actual query-block result')
    for path,formats,readonly in ((p['iso_path'],{'raw','file'},True),
                                  (p['target_disk_path'],{'qcow2'},False)):
        matches=[row['inserted'] for row in blocks if row.get('inserted',{}).get('file')==path]
        require(len(matches)==1, 'Actual server block device missing/duplicated: '+path)
        inserted=matches[0]
        require(inserted.get('drv') in formats and inserted.get('ro') is readonly,
                'Actual effective block format/read-only differs: '+path)
        if not readonly:
            image=inserted.get('image',{})
            require(image.get('virtual-size')==VIRTUAL_BYTES and not image.get('backing-filename'),
                    'Actual target size/backing differs')


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



def validate_launch_transition(p, original, transition):
    """Pin actual libvirt runtime ownership/labels before any guest code runs."""
    binding = p['producer_binding']
    require(transition.get('phase') == 'paused-before-guest-execution' and
            transition.get('domain_name') == binding['domain_name'] and
            transition.get('domain_uuid') == binding['domain_uuid'], 'Wrong pre-execution launch binding')
    require(transition.get('before_disk_identity') == original, 'Launch did not begin with restored original identity')
    live = transition.get('live_disk_identity', {})
    require(set(live) == set(original), 'Incomplete live disk identity')
    for key in ('device', 'inode', 'mode'):
        require(live[key] == original[key], 'Launch replaced inode/mode: ' + key)
    require(live['uid'] == binding['qemu_uid'] and live['gid'] == binding['qemu_gid'], 'Unexpected live QEMU owner')
    require(type(transition.get('qemu_pid')) is int and transition['qemu_pid'] > 1 and
            type(transition.get('qemu_start_ticks')) is int and transition['qemu_start_ticks'] > 0,
            'Actual launch PID birth required')
    if p['qmp_transport']=='libvirt-system-qmp/v1':
        validate_libvirt_definition(transition['live_domain_xml'],p)
        validate_observed_qemu_argv(transition.get('qemu_invocation'),p)
    root = ET.fromstring(transition['live_domain_xml'])
    require(root.get('type') == 'qemu' and root.findtext('name') == binding['domain_name'] and
            root.findtext('uuid') == binding['domain_uuid'] and
            root.find('os/type').get('arch') == 'aarch64', 'Live domain XML identity differs')
    disks = [row for row in root.findall('devices/disk') if row.get('device') == 'disk']
    require(len(disks) == 1 and disks[0].find('source').get('file') == p['target_disk_path'] and
            disks[0].find('driver').get('type') == 'qcow2', 'Actual live disk XML differs')
    require(root.findall('devices/hostdev') == [], 'Unexpected physical host device')
    label = root.find("seclabel[@model='selinux']")
    dac = root.find("seclabel[@model='dac']")
    require(label is not None and label.get('type') == 'dynamic' and label.get('relabel') == 'yes',
            'Actual dynamic SELinux XML label required')
    imagelabel = label.findtext('imagelabel')
    processlabel = label.findtext('label')
    require(isinstance(imagelabel, str) and isinstance(processlabel, str) and
            imagelabel.startswith('system_u:object_r:svirt_image_t:s0:c') and
            processlabel.startswith('system_u:system_r:svirt_tcg_t:s0:c') and
            processlabel.split(':', 3)[3] == imagelabel.split(':', 3)[3], 'Wrong actual TCG process/image MCS')
    require(dac is not None and dac.findtext('label') == '+' + str(binding['qemu_uid']) + ':+' + str(binding['qemu_gid']),
            'Actual DAC process label differs')
    original_attrs = original['xattrs']; actual_attrs = live['xattrs']
    trusted = {'trusted.libvirt.security.' + suffix for suffix in
               ('dac', 'ref_dac', 'ref_selinux', 'selinux', 'timestamp_dac', 'timestamp_selinux')}
    require(not (trusted & set(original_attrs)), 'Fresh disk has stale libvirt security tracking')
    require(set(actual_attrs) == set(original_attrs) | trusted, 'Unexpected runtime attribute inventory')
    for name, value in original_attrs.items():
        if name != 'security.selinux':
            require(actual_attrs[name] == value, 'Unexpected changed runtime attribute: ' + name)
    require(bytes.fromhex(actual_attrs['security.selinux']).rstrip(b'\0').decode() == imagelabel,
            'Disk MCS differs from exact launch XML')
    def decoded(name):return bytes.fromhex(actual_attrs['trusted.libvirt.security.' + name]).decode()
    require(decoded('dac') == '+' + str(original['uid']) + ':+' + str(original['gid']) and
            decoded('ref_dac') == decoded('ref_selinux') == '1' and
            decoded('selinux') == bytes.fromhex(original_attrs['security.selinux']).rstrip(b'\0').decode(),
            'Original owner/label tracking differs')
    timestamp = decoded('timestamp_dac')
    require(timestamp.isdecimal() and decoded('timestamp_selinux') == timestamp,
            'Actual launch security timestamps differ')
    return live


def validate_launch_history(p, original, installed, proofs):
    history = json.loads(proofs['launch-history.json'])
    require(isinstance(history, list) and history, 'Actual per-launch history required')
    births = set(); last = None
    for index, record in enumerate(history):
        name = record['transition_file']
        require(name in proofs and hashlib.sha256(proofs[name]).hexdigest() == record['transition_sha256'],
                'Missing immutable launch transition')
        transition = json.loads(proofs[name])
        live = validate_launch_transition(p, original, transition)
        birth = (transition['qemu_pid'], transition['qemu_start_ticks'])
        require(birth not in births, 'Duplicate QEMU birth relabeled as another launch');births.add(birth)
        if index < len(history) - 1:
            closed_name = record['closure_file']
            require(closed_name in proofs and hashlib.sha256(proofs[closed_name]).hexdigest() == record['closure_sha256'],
                    'Missing stopped-interval identity proof')
            closure = json.loads(proofs[closed_name])
            require(closure.get('transition_sha256') == record['transition_sha256'] and
                    closure.get('restored_disk_identity') == original and
                    closure.get('qemu_pid') == birth[0] and closure.get('qemu_start_ticks') == birth[1] and
                    closure.get('process_absent') is True and closure.get('no_open_fds') is True,
                    'Stopped launch did not fully restore original identity')
        last = record
    require(installed.get('active_launch_sha256') == last['transition_sha256'], 'Installed boot bound to wrong active launch')
    return live, transition

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
        body = regular_bytes(path, owner_uid=owner_uid, immutable=True, maximum_bytes=4 * 1024**2)
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
    live_identity, active_transition = validate_launch_history(p, created['disk_identity'], installed, ip)
    require(installed.get('disk_identity') == live_identity, 'Installed disk differs from actual boot launch')
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
    require(q.get('disk_identity') == live_identity and q.get('unsigned_modifications') == [] and
            q.get('active_launch_sha256') == installed['active_launch_sha256'],
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
    last_closure = json.loads(cp['final-launch-closure.json'])
    require(last_closure.get('transition_sha256') == installed['active_launch_sha256'] and
            last_closure.get('restored_disk_identity') == created['disk_identity'] and
            last_closure.get('qemu_pid') == active_transition['qemu_pid'] and
            last_closure.get('qemu_start_ticks') == active_transition['qemu_start_ticks'] and
            last_closure.get('process_absent') is True and last_closure.get('no_open_fds') is True,
            'Final launch did not fully restore original disk identity')
    outcome = json.loads(cp['normal-wrapper-outcome.json'])
    require(type(outcome.get('exit_status')) is int and outcome['exit_status'] == 0 and
            outcome.get('reason') == 'command exited' and
            isinstance(outcome.get('finished_utc'), str) and outcome['finished_utc'],
            'Normal admitted wrapper did not finish successfully')
    job_record = json.loads(cp['normal-job-record.json'])
    require(job_record.get('unit') == p['producer_binding']['normal_unit'] and
            job_record.get('cgroup') == p['producer_binding']['normal_cgroup'] and
            job_record.get('uid') == 1003 and job_record.get('memory_gib') == 10 and
            job_record.get('disk_device') == p['iso_identity']['device'] and
            type(job_record.get('disk_gib')) is int and
            job_record['disk_gib'] * 1024**3 >= p['planned_growth_bytes'],
            'Normal producer admission does not match exact lifecycle/storage growth')
    consumers = json.loads(cp['closed-consumers.json'])
    validate_closed_observation(consumers, p['producer_binding'], json.loads(cp['root-wrapper-outcomes.json']))
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
    signed_heads, native_headers, app_baseline (current source baseline JSON),
    qemu, firmware, qemu_invocation (JSON file),
    target_disk_path, and planned_growth_bytes. expected.app_baseline_sha256
    independently pins authoritative source baseline bytes, never derived from
    the signed head inventory. Admission remains caller-owned.
    Returns preparation hash; a failed preparation is retained, never overwritten.
    """
    validate_expected(expected)
    require('app_baseline' in inputs, 'Independent current-source app baseline input required')
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
        ('native-headers.json', 'native_headers', 'native_headers_sha256'),
        ('app-baseline.json', 'app_baseline', 'app_baseline_sha256')):
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
    executable_names=('qemu','firmware')
    if expected['qmp_transport']=='libvirt-system-qmp/v1':
        copy_input(inputs['domain_definition'],'domain-installer.xml',expected['domain_definition_sha256'])
        executable_names+=('libvirt_cli','firmware_variables')
    prepared = {key: expected[key] for key in IDENTITY_KEYS}
    prepared.update({key: expected[key] for key in (
        'static_proof_sha256', 'checks_preparation_sha256', 'checks_manifest_sha256',
        'signed_heads_sha256', 'native_headers_sha256', 'app_baseline_sha256')})
    if expected['qmp_transport']=='libvirt-system-qmp/v1':
        prepared['domain_definition_sha256']=expected['domain_definition_sha256']
        prepared['installer_boot_inputs']=expected.get('installer_boot_inputs',{})
        prepared['installer_cmdline']=expected.get('installer_cmdline')
        prepared['installer_stage2']=expected.get('installer_stage2')
    prepared.update(fixture_root=str(fixture),qmp_transport=expected['qmp_transport'],producer_binding=expected['producer_binding'], result='PREPARED_ONLY', provider=PROVIDER, guest_architecture='aarch64',
                    host_architecture='x86_64', accelerator='tcg', qemu_machine='virt',
                    iso_path=str(iso), iso_identity=identity(iso), iso_readonly_binding=True,
                    target_disk_path=str(Path(inputs['target_disk_path'])),
                    minimum_free_bytes=FLOOR_BYTES,
                    planned_growth_bytes=inputs['planned_growth_bytes'],
                    observed_free_bytes=free.f_bavail * free.f_frsize,
                    executables={name: {'path': str(Path(inputs[name])),
                                        'sha256': file_sha256(inputs[name])}
                                 for name in executable_names}, proof_hashes=proofs)
    require(Path(prepared['target_disk_path']).is_absolute(), 'Absolute target disk path required')
    validate_invocation(json.loads((fixture / 'qemu-invocation.json').read_text()), prepared)
    prepared_hash = immutable_json(fixture / 'PREPARED.json', prepared)
    validate_prepared(fixture, expected, owner_uid=os.getuid())
    return prepared_hash




def prepare_checker_capsule(baseline: Path, preparation: Path, original_hashes: dict,
                            destination: Path, source_repository: Path,
                            original_source_revision: str, final_source_revision: str):
    """Copy reviewed checks; change ONLY emitted source-identity metadata.

    Every embedded module hash is independently checked against its exact Git
    blob at the final source revision. A changed module/preamble is refused for
    separate review, never silently rewritten. No guest result/PASS is reused.
    """
    require(re.fullmatch('[0-9a-f]{40}', final_source_revision), 'Invalid final source revision')
    require(set(original_hashes) == set(CHECK_FILES), 'Original checker manifest incomplete')
    destination = Path(destination)
    require(not destination.exists(), 'Fresh checker capsule destination required')
    original = {}
    for name, digest in original_hashes.items():
        body = regular_bytes(Path(baseline) / name)
        require(hashlib.sha256(body).hexdigest() == digest, 'Original checker bytes differ: ' + name)
        original[name] = body
    preamble = json.loads(regular_bytes(Path(preparation)))
    audit = preamble['preamble_hash_audit']
    rows = {(row['check'], row['module']): row for row in audit}
    require(len(rows) == len(audit), 'Duplicate source preamble audit')
    embedded = {}
    for name, body in original.items():
        tree = ast.parse(body.decode())
        for node in tree.body:
            if not isinstance(node, ast.For) or not isinstance(node.iter, ast.Call):
                continue
            expression = node.iter.func
            if not isinstance(expression, ast.Attribute) or expression.attr != 'items' or not isinstance(expression.value, ast.Dict):
                continue
            mapping = expression.value
            pairs = [(key.value, value.value) for key, value in zip(mapping.keys, mapping.values)
                     if isinstance(key, ast.Constant) and isinstance(key.value, str) and
                     isinstance(value, ast.Constant) and isinstance(value.value, str) and
                     re.fullmatch('[0-9a-f]{64}', value.value)]
            for module, digest in pairs:
                require((name, module) not in embedded, 'Duplicate embedded module anchor')
                embedded[(name, module)] = digest
    require(set(embedded) == set(rows), 'Actual embedded source hashes differ from audit inventory')
    actual_audit = []
    for key, digest in embedded.items():
        rel = Path(rows[key]['source_path'])
        require(not rel.is_absolute() and '..' not in rel.parts, 'Unsafe authoritative source path')
        blob = subprocess.check_output(['git', '-C', str(source_repository), 'show',
                                        final_source_revision + ':' + str(rel)],
                                       stdin=subprocess.DEVNULL, timeout=30)
        actual = hashlib.sha256(blob).hexdigest()
        require(actual == digest, 'Final source module changed; checker review required: ' + str(key))
        actual_audit.append(dict(check=key[0], module=key[1], source_path=str(rel),
                                 expected_sha256=digest, authoritative_sha256=actual))
    driver = original['run-guest-checks.py'].decode()
    tree = ast.parse(driver)
    constants = [value for node in ast.walk(tree) if isinstance(node, ast.Dict)
                 for key, value in zip(node.keys, node.values)
                 if isinstance(key, ast.Constant) and key.value == 'source_revision' and
                 isinstance(value, ast.Constant) and value.value == original_source_revision]
    require(len(constants) == 1, 'Original driver source identity anchor differs')
    node = constants[0]
    lines = driver.splitlines(keepends=True)
    require(node.lineno == node.end_lineno, 'Unexpected multiline source metadata')
    offset = sum(len(line) for line in lines[:node.lineno - 1])
    start, end = offset + node.col_offset, offset + node.end_col_offset
    literal = driver[start:end]
    replacement = repr(final_source_revision)
    adapted = driver[:start] + replacement + driver[end:]
    require(adapted[:start] + literal + adapted[start + len(replacement):] == driver,
            'Source metadata adaptation changed guest behavior')
    ast.parse(adapted)
    destination.mkdir(mode=0o700)
    manifest = {}
    for name, body in original.items():
        prepared = adapted.encode() if name == 'run-guest-checks.py' else body
        fd = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(prepared);stream.flush();os.fsync(fd)
        finally:
            os.close(fd)
        manifest[name] = hashlib.sha256(prepared).hexdigest()
    proof = dict(result='PREPARED_ONLY_NOT_GUEST_EXECUTED',
                 original_source_revision=original_source_revision,
                 source_revision=final_source_revision, original_hashes=original_hashes,
                 prepared_hashes=manifest, preamble_hash_audit=actual_audit,
                 changed_only_emitted_source_identity=True,
                 guest_behavior_and_assertions_preserved=True)
    immutable_json(destination / 'PREPARATION.json', proof)
    immutable_json(destination / 'MANIFEST.json', manifest)
    return proof

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
    require(parent.st_dev == Path(p['iso_path']).stat().st_dev, 'VM disk must share the admitted ISO filesystem')
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


def observe_disk_consumers(disk: Path, binding: dict, root_outcomes: dict):
    """Real privileged closure scan bound to PREPARED producer/domain identities.

    A collected cgroup is valid only with its exact inactive unit, MainPID0 and
    blank ControlGroup. Independent recorded root/normal outcomes remain needed.
    """
    require(os.getuid() == 0, 'Full /proc consumer scan requires release administrator')
    validate_producer_binding(binding)
    target = identity(disk)
    open_fds, qemu_pids = [], []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():continue
        try:
            command = (proc / 'cmdline').read_bytes().split(b'\0')
            if any(os.fsencode(str(disk)) in arg for arg in command) and command and b'qemu' in command[0]:
                qemu_pids.append(int(proc.name))
            for fd in (proc / 'fd').iterdir():
                try:observed = fd.stat()
                except FileNotFoundError:continue
                if (observed.st_dev, observed.st_ino) == (target['device'], target['inode']):
                    open_fds.append({'pid': int(proc.name), 'fd': int(fd.name)})
        except (FileNotFoundError, ProcessLookupError):continue
    def unit_state(name, user=False):
        command = ['systemctl'] + (['--user'] if user else []) + ['show', name]
        if user:
            command = ['runuser', '-u', 'luma-dev-2', '--', 'env', 'XDG_RUNTIME_DIR=/run/user/1003',
                       'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1003/bus'] + command
        for prop in ('Id', 'MainPID', 'ControlGroup', 'LoadState', 'ActiveState', 'Result', 'ExecMainStatus'):
            command += ['-p', prop]
        answer = subprocess.run(command, text=True, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        record = dict(line.split('=', 1) for line in answer.stdout.splitlines() if '=' in line)
        require(answer.returncode == 0 or record.get('LoadState') == 'not-found', 'Unit state observation failed')
        require(record.get('Id') == name, 'Observed another unit')
        return record
    units = [unit_state(name) for name in binding['root_units']]
    normal = unit_state(binding['normal_unit'], user=True)
    groups = {}
    for cg in set(binding['root_units'].values()) | {binding['normal_cgroup']}:
        path = Path('/sys/fs/cgroup' + cg)
        if path.exists():
            require(not path.is_symlink(), 'Symlink producer cgroup')
            pids = [int(pid) for procs in path.rglob('cgroup.procs') for pid in procs.read_text().split()]
            groups[cg] = {'exists': True, 'pids': sorted(set(pids))}
        else:
            groups[cg] = {'exists': False, 'pids': []}
    def virsh(*args):
        return subprocess.check_output(['virsh', '--connect', 'qemu:///system', *args],
                                       text=True, stdin=subprocess.DEVNULL, timeout=30).strip()
    observed = dict(qemu_pids=qemu_pids, open_fds=open_fds, units=units,
                    normal_unit_state=normal, cgroups=groups,
                    domain_name=binding['domain_name'], domain_uuid=virsh('domuuid', binding['domain_name']),
                    domain_state=virsh('domstate', binding['domain_name']), scanned_disk_identity=target)
    validate_closed_observation(observed, binding, root_outcomes)
    return observed


class ServerInputBridge:
    """Actual local QMP input/screenshot bridge, bound to one observed QEMU birth.

    This is an operator adapter only. It never runs guest assertions, changes
    check deadlines, synthesizes mapped windows or calls a Mac/tunnel helper.
    """
    def __init__(self, qmp_socket: Path, pid: int, start_ticks: int,
                 invocation: list[str], *, peer_uid: int, capture_directory=None, timeout=30):
        require(type(pid) is int and pid > 1, 'Invalid QEMU PID')
        require(type(peer_uid) is int and peer_uid >= 0, 'Pinned QEMU peer UID required')
        self.peer_uid = peer_uid
        self.capture_directory = Path(capture_directory) if capture_directory is not None else None
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
        deadline = time.monotonic() + self.timeout
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(self.timeout); conn.connect(str(self.socket_path))
            peer_pid, peer_uid, _ = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            require((peer_pid, peer_uid) == (self.pid, self.peer_uid), 'QMP socket is not the pinned QEMU process/UID')
            pending = bytearray()
            total_bytes = 0
            events = 0
            def read_message():
                nonlocal total_bytes, events
                while b'\n' not in pending:
                    remaining = deadline - time.monotonic()
                    require(remaining > 0, 'Absolute QMP deadline exceeded')
                    conn.settimeout(remaining)
                    piece = conn.recv(65536)
                    require(piece, 'QMP socket closed before complete response')
                    total_bytes += len(piece)
                    require(total_bytes <= 1024**2, 'QMP response budget exceeded')
                    pending.extend(piece)
                line, _, rest = pending.partition(b'\n')
                pending[:] = rest
                events += 1
                require(events <= 64 and time.monotonic() < deadline, 'QMP event/deadline budget exceeded')
                return json.loads(line)
            require('QMP' in read_message(), 'Invalid QMP greeting')
            def call(name, args):
                conn.sendall((json.dumps({'execute': name, 'arguments': args, 'id': name}) + '\n').encode())
                while True:
                    message = read_message()
                    if message.get('id') == name:
                        require('error' not in message, 'QMP operation failed: ' + str(message))
                        return message['return']
            call('qmp_capabilities', {})
            return call(command, arguments or {})

    def snapshot(self, path: Path):
        path = Path(path)
        require(path.is_absolute() and not path.exists(), 'Exclusive screenshot output required')
        directory = self.capture_directory
        require(directory is not None and directory.is_absolute(), 'Explicit QEMU-owned capture directory required')
        for part in (directory, *directory.parents):
            require(not part.is_symlink(), 'Symlink capture directory')
        before = directory.stat()
        require(stat.S_ISDIR(before.st_mode) and before.st_uid == self.peer_uid and
                stat.S_IMODE(before.st_mode) == 0o700, 'Capture directory is not private to pinned QEMU UID')
        source = directory / ('frame-' + uuid.uuid4().hex + '.png')
        require(not source.exists(), 'Unexpected capture path collision')
        self.execute('screendump', {'filename': str(source), 'format': 'png'})
        body = regular_bytes(source, owner_uid=self.peer_uid, maximum_bytes=16 * 1024**2)
        require(body.startswith(b'\x89PNG\r\n\x1a\n') and len(body) >= 24, 'Actual QMP screenshot missing')
        after = directory.stat()
        require((before.st_dev, before.st_ino, before.st_uid, stat.S_IMODE(before.st_mode)) ==
                (after.st_dev, after.st_ino, after.st_uid, stat.S_IMODE(after.st_mode)), 'Capture directory changed')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(body);stream.flush();os.fsync(fd)
        finally:
            os.close(fd)
        require(file_sha256(path) == hashlib.sha256(body).hexdigest(), 'Copied actual frame differs')
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


LIBVIRT_CHILD = r"""
import json,os,sys,libvirt,libvirt_qemu
connection=libvirt.open('qemu:///system')
try:
 domain=connection.lookupByUUIDString(sys.argv[1])
 assert domain.name()==sys.argv[2] and domain.isActive()
 action=sys.argv[3]
 if action=='monitor':
  response=libvirt_qemu.qemuMonitorCommand(domain,sys.argv[4],0)
  assert len(response.encode())<=1024**2
  print(response)
 else:
  assert action=='screenshot'
  target=sys.argv[4];stream=connection.newStream(0);count=0
  fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  try:
   domain.screenshot(stream,0,0)
   with os.fdopen(fd,'wb',closefd=False) as output:
    def sink(_stream,data,_opaque):
     global count
     count+=len(data);assert count<=16*1024**2
     output.write(data);return len(data)
    stream.recvAll(sink,None);stream.finish();output.flush();os.fsync(fd)
  except BaseException:
   try:stream.abort()
   except libvirt.libvirtError:pass
   raise
  finally:os.close(fd)
finally:connection.close()
"""


class ServerLibvirtInputBridge(ServerInputBridge):
    """Actual UUID-bound system libvirt QMP/screenshot stream, no raw socket race.

    libvirt owns its primary monitor. A bounded isolated child uses the trusted
    local qemu:///system API and exact domain UUID/name; PID birth/argv and live
    QEMU UID are independently rechecked before and after every operation.
    No root-only evidence directory is made writable to QEMU.
    """
    def __init__(self, domain_name, domain_uuid, pid, start_ticks, invocation, *, peer_uid, timeout=30):
        require(re.fullmatch(r'luma-arm-desktop-[a-zA-Z0-9_.-]+', domain_name), 'Unowned libvirt domain')
        require(re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', domain_uuid), 'Invalid domain UUID')
        require(type(pid) is int and pid>1 and type(start_ticks) is int and start_ticks>0 and
                type(peer_uid) is int and peer_uid>0, 'Exact non-root QEMU PID/birth/UID required')
        self.domain_name,self.domain_uuid=domain_name,domain_uuid
        self.pid,self.start_ticks,self.invocation=pid,start_ticks,invocation
        self.peer_uid,self.timeout=peer_uid,timeout
        self.runtime_pid_identity=None
        self.check_process()

    def check_process(self):
        super().check_process()
        status = dict(line.split(':',1) for line in (Path('/proc')/str(self.pid)/'status').read_text().splitlines() if ':' in line)
        require([int(value) for value in status['Uid'].split()] == [self.peer_uid]*4,
                'Actual libvirt QEMU process UID differs')
        self.bind_runtime_pid()

    def bind_runtime_pid(self):
        path=Path('/run/libvirt/qemu')/(self.domain_name+'.pid')
        before=identity(path)
        require(before['uid']==0 and before['mode']&0o022==0, 'Untrusted libvirt runtime PID file')
        body=regular_bytes(path,owner_uid=0,maximum_bytes=64)
        require(body.strip().isdigit() and int(body.strip())==self.pid, 'Domain UUID/name is not the pinned QEMU PID')
        require(identity(path)==before, 'Runtime PID identity changed while observing')
        if self.runtime_pid_identity is None:self.runtime_pid_identity=before
        else:require(self.runtime_pid_identity==before, 'Runtime domain PID file was replaced')

    def child(self, action, argument):
        self.check_process()
        require(action in {'monitor','screenshot'}, 'Unreviewed libvirt transport action')
        result=subprocess.run(['/usr/bin/python3','-B','-I','-c',LIBVIRT_CHILD,self.domain_uuid,self.domain_name,action,argument],
                              stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              timeout=self.timeout,check=True)
        require(len(result.stdout)<=1024**2 and len(result.stderr)<=65536, 'libvirt response exceeds budget')
        self.check_process()
        return result.stdout

    def execute(self, command, arguments=None):
        require(command in {'query-status','query-block','input-send-event','send-key'},
                'Unreviewed libvirt monitor command')
        request=json.dumps({'execute':command,'arguments':arguments or {}})
        response=json.loads(self.child('monitor',request))
        require('error' not in response and 'return' in response, 'Actual libvirt QMP operation failed')
        return response['return']

    def snapshot(self,path):
        path=Path(path)
        require(path.is_absolute() and not path.exists(), 'Exclusive private screenshot output required')
        self.child('screenshot',str(path))
        body=regular_bytes(path,owner_uid=os.getuid(),maximum_bytes=16*1024**2)
        require(body.startswith(b'\x89PNG\r\n\x1a\n') and len(body)>=24, 'Actual libvirt PNG stream missing')
        return struct.unpack('>II',body[16:24])

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
