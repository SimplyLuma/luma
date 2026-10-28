#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Export a stopped installation as a generalized, compressed QCOW2 template.

Run as root in a private mount namespace inside a bounded build worker. All
changes go to a new raw copy; the qualified source disk remains untouched.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


def run(args, **kwargs):
    print('+', *map(str, args), flush=True)
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def build(args):
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=args.resume_raw)
    assert os.geteuid() == 0, 'Use a bounded root build worker'
    assert source.is_file() and source.suffix == '.qcow2'
    assert subprocess.run(['fuser', str(source)], capture_output=True).returncode == 1
    assert shutil.disk_usage(output).free > 50 * 1024**3
    raw = output / 'staging.raw'
    if args.resume_raw:
        info=json.loads(subprocess.check_output(['qemu-img','info','--output=json',source],text=True))
        assert raw.is_file() and raw.stat().st_size==info['virtual-size']
        assert not (output/'BUILD.json').exists(), 'Never replace an exported image'
        assert subprocess.run(['fuser',str(raw)],capture_output=True).returncode==1
    else:
        run(['qemu-img', 'convert', '-p', '-f', 'qcow2', '-O', 'raw', source, raw])
    # Read GPT from the file directly. Loop partition nodes can lag behind
    # --partscan in a busy worker; a bounded partition slice needs no udev scan.
    table=json.loads(subprocess.check_output(['sfdisk','--json',raw],text=True))['partitiontable']
    root_partition=max(table['partitions'],key=lambda p:p['size'])
    sector=table.get('sectorsize',512)
    assert root_partition['size']*sector>8*1024**3
    mount = output / 'root'
    mount.mkdir(exist_ok=args.resume_raw)
    assert not os.path.ismount(mount) and not any(mount.iterdir()), 'Mount directory must be empty'
    loop = subprocess.check_output(['losetup','--find','--show','--offset',str(root_partition['start']*sector),
        '--sizelimit',str(root_partition['size']*sector),str(raw)], text=True).strip()
    mounted = False
    home_mount = None
    var_bind = None
    try:
        partition=loop
        assert subprocess.check_output(['blkid','-p','-s','TYPE','-o','value',partition],text=True).strip()=='btrfs'
        run(['mount', '-o', 'subvolid=5', partition, mount]); mounted = True
        deployments = list(mount.glob('**/ostree/deploy/*/deploy/*.0'))
        assert len(deployments) == 1, deployments
        deployment = deployments[0]
        system_root = deployment.parents[4]
        var = deployment.parents[1] / 'var'
        assert (var / 'lib').is_dir(), var
        # Atomic Fedora may keep /var/home in its own Btrfs subvolume.
        # Mount that actual home before removing qualification-only user data.
        for line in (deployment / 'etc/fstab').read_text().splitlines():
            fields = line.split()
            if not fields or fields[0].startswith('#') or len(fields) < 4:continue
            if fields[1] in ('/home', '/var/home'):
                assert fields[2] == 'btrfs', 'Unsupported separate home filesystem'
                subvolume = next((o for o in fields[3].split(',') if o.startswith(('subvol=', 'subvolid='))), None)
                assert subvolume and re.fullmatch(r'subvol(?:id)?=[A-Za-z0-9_./-]+', subvolume)
                uuid = subprocess.check_output(['blkid', '-s', 'UUID', '-o', 'value', partition], text=True).strip()
                assert fields[0] == 'UUID=' + uuid, 'Home must belong to this private image'
                home_mount = var / 'home'
                home_mount.mkdir(exist_ok=True)
                run(['mount', '-t', 'btrfs', '-o', subvolume, partition, home_mount])

        def remove(path):
            assert path.parent.resolve().is_relative_to(mount)
            if path.is_symlink() or path.is_file():path.unlink()
            elif path.is_dir():shutil.rmtree(path)

        def write(relative, text, mode=0o644):
            path = deployment / relative
            assert path.parent.resolve().is_relative_to(mount)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + '.try-new')
            temporary.write_text(text); temporary.chmod(mode); temporary.replace(path)

        def link(relative, value):
            path = deployment / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            remove(path);path.symlink_to(value)

        write('etc/try-luma/firstboot.py', Path(__file__).with_name('prepared-firstboot.py').read_text(), 0o755)
        write('etc/systemd/system/try-luma-seed.service', '''[Unit]
Description=Prepare this private Try Luma workspace
After=local-fs.target
Before=sshd.service gdm.service display-manager.service
ConditionVirtualization=vm
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /etc/try-luma/firstboot.py
TimeoutStartSec=300
StandardOutput=journal+console
StandardError=journal+console
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
''')
        link('etc/systemd/system/multi-user.target.wants/try-luma-seed.service', '../try-luma-seed.service')
        link('etc/systemd/system/multi-user.target.wants/sshd.service', '/usr/lib/systemd/system/sshd.service')
        write('etc/systemd/system/sshd.service.d/90-try-luma.conf', '[Unit]\nRequires=try-luma-seed.service\nAfter=try-luma-seed.service\n')
        write('etc/systemd/system/gdm.service.d/90-try-luma.conf', '[Unit]\nRequires=try-luma-seed.service\nAfter=try-luma-seed.service\n')
        link('etc/systemd/system/systemd-firstboot.service', '/dev/null')
        write('etc/gdm/custom.conf', '[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin=luma\n')
        write('etc/machine-id', '', 0o444)
        write('etc/luma-tester-target', 'desktop\n')
        # Previews render in software; keep the shell's real blur and
        # translucent materials instead of its no-GPU fallback surfaces.
        write('etc/environment.d/60-try-luma-materials.conf', 'LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1\n')
        write('etc/try-luma/image.json', json.dumps(dict(schema=1, arch=args.arch,
            protocol='luma-unified-v1', source_iso_sha256=args.iso_sha256)) + '\n')
        for key in (deployment / 'etc/ssh').glob('ssh_host_*'):remove(key)
        for relative in ('lib/try-luma', 'lib/cloud', 'lib/systemd/random-seed', 'lib/systemd/coredump',
                         'log/journal', 'home/luma', 'roothome/.ssh', 'roothome/.bash_history'):
            remove(var / relative)
        home = var / 'home/luma/.config'
        home.mkdir(parents=True)
        (home / 'gnome-initial-setup-done').write_text('yes\n')
        account = next(line.split(':') for line in (deployment / 'etc/passwd').read_text().splitlines() if line.startswith('luma:'))
        # Export a complete user installation instead of leaving its first
        # creation to competing shell/app-discovery processes at login.
        user_repo = home.parent / '.local/share/flatpak/repo'
        user_repo.mkdir(parents=True)
        run(['ostree', '--repo=' + str(user_repo), 'init', '--mode=bare-user-only'])
        for path in (home.parent, *home.parent.rglob('*')):
            os.chown(path, int(account[2]), int(account[3]))
        for key in ('secret_key', 'timestamps', 'seen-bssids'):
            remove(var / 'lib/NetworkManager' / key)
        dbus = var / 'lib/dbus/machine-id'
        remove(dbus);dbus.parent.mkdir(parents=True, exist_ok=True);dbus.symlink_to('/etc/machine-id')
        policy = sorted((deployment / 'etc/selinux/targeted/policy').glob('policy.*'))[-1]
        contexts = deployment / 'etc/selinux/targeted/contexts/files/file_contexts'
        # Relabel only paths this exporter changed. Unrelated target-policy
        # types (for example newer snapd types) need not exist in the host policy.
        changed=['etc/try-luma','etc/gdm/custom.conf','etc/machine-id','etc/luma-tester-target',
            'etc/environment.d/60-try-luma-materials.conf',
            'etc/systemd/system/try-luma-seed.service',
            'etc/systemd/system/multi-user.target.wants/try-luma-seed.service',
            'etc/systemd/system/multi-user.target.wants/sshd.service',
            'etc/systemd/system/sshd.service.d','etc/systemd/system/gdm.service.d',
            'etc/systemd/system/systemd-firstboot.service']
        run(['setfiles', '-F', '-c', policy, '-r', deployment, contexts,
             *[deployment / name for name in changed]])
        var_bind = deployment / 'var'
        assert not var_bind.is_symlink(), 'Unexpected OSTree var layout'
        var_bind.mkdir(exist_ok=True)
        run(['mount', '--rbind', var, var_bind])
        run(['setfiles', '-F', '-c', policy, '-r', deployment, contexts,
             var_bind / 'home', var_bind / 'lib/dbus'])
        run(['umount', '-R', var_bind]);var_bind = None
        run(['sync', '-f', mount])
        run(['fstrim', '-v', mount])
    finally:
        if var_bind is not None:run(['umount', '-R', var_bind])
        if home_mount is not None:run(['umount', home_mount])
        if mounted:run(['umount', mount])
        run(['losetup', '-d', loop])
    image = output / ('try-luma-' + args.arch + '.qcow2')
    run(['qemu-img', 'convert', '-p', '-f', 'raw', '-O', 'qcow2', '-c', '-m', '2', raw, image])
    run(['qemu-img', 'check', image])
    info = json.loads(subprocess.check_output(['qemu-img', 'info', '--output=json', image], text=True))
    assert not info.get('backing-filename') and not info.get('snapshots')
    sha = hashlib.file_digest(image.open('rb'), 'sha256').hexdigest()
    receipt = dict(schema=1, qualified=False, arch=args.arch, source_iso_sha256=args.iso_sha256,
        sha256=sha, bytes=image.stat().st_size, guest_protocol='luma-unified-v1', standalone=True)
    (output / 'BUILD.json').write_text(json.dumps(receipt, indent=2) + '\n')
    raw.unlink()
    for path in output.iterdir():os.chown(path, args.owner, args.group)
    os.chown(output,args.owner,args.group)
    image.chmod(0o444)
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--arch', choices=['x86_64', 'aarch64'], required=True)
    parser.add_argument('--iso-sha256', required=True)
    parser.add_argument('--owner', type=int, default=os.getuid())
    parser.add_argument('--group', type=int, default=os.getgid())
    parser.add_argument('--resume-raw', action='store_true',help='Resume this owned export after its raw copy completed')
    build(parser.parse_args())
