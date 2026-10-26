# SPDX-License-Identifier: Apache-2.0
"""Persistent VM preferences, disk checkpoints and explicit host integrations."""
from __future__ import annotations
import json
import os
import platform
from pathlib import Path
import shlex
import subprocess
import time
import uuid
from datetime import datetime, timezone

from .core import TesterError, run

DEFAULTS = dict(memory=4096, cpus=4, disk_gib=32, graphics='auto', audio=True,
                microphone=False, agent_ssh=False, boot_iso=True, folders=[], usb=[], device=None)


def load(root):
    try:
        value = json.loads((Path(root) / 'vm-settings.json').read_text())
        return validate(dict(DEFAULTS, **value))
    except (OSError, ValueError, TypeError, TesterError):
        return dict(DEFAULTS, folders=[], usb=[])


def validate(settings):
    result = dict(settings)
    for key, low, high in [('memory', 1024, 65536), ('cpus', 1, 32), ('disk_gib', 8, 256)]:
        if type(result.get(key)) is not int or not low <= result[key] <= high:
            raise TesterError(f'Invalid VM {key}: choose {low}–{high}.')
    if result.get('graphics') not in ('auto', 'virgl', 'software'):
        raise TesterError('Unknown graphics setting.')
    for key in ('audio', 'microphone', 'agent_ssh', 'boot_iso'):
        if type(result.get(key)) is not bool:
            raise TesterError('Invalid ' + key + ' switch.')
    # QEMU exposes recording through its duplex codec; it has no input-only HDA codec.
    if result['microphone']:result['audio']=True
    if result.get('device') is not None:
        from .device_profiles import validate as validate_device
        result['device']=validate_device(result['device'])
    for key in ('folders', 'usb'):
        if not isinstance(result.get(key), list) or len(result[key]) > 8:
            raise TesterError('Choose at most eight ' + key + '.')
    for folder in result['folders']:
        if (not isinstance(folder, dict) or set(folder) != {'path', 'writable'}
                or not isinstance(folder['path'], str) or type(folder['writable']) is not bool):
            raise TesterError('Invalid shared folder.')
        path = Path(folder['path']).expanduser().resolve()
        if path in (Path('/'), Path.home()):
            raise TesterError('Choose a specific folder, rather than your home or root.')
        folder['path'] = str(path)
    for device in result['usb']:
        if (not isinstance(device, dict) or set(device) != {'bus', 'address', 'name'}
                or not isinstance(device['name'], str)
                or any(type(device.get(k)) is not int or not 1 <= device[k] <= 255 for k in ('bus', 'address'))):
            raise TesterError('Invalid USB device.')
    return result


def atomic_json(path, value):
    path = Path(path);path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex)
    try:
        with temporary.open('x') as stream:
            temporary.chmod(0o600)
            json.dump(value, stream, indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save(root, settings):
    result = validate(dict(DEFAULTS, **settings))
    atomic_json(Path(root) / 'vm-settings.json', result)
    return result


def devices(settings):
    """QEMU options, never shell fragments. Folder paths use QEMU's comma escaping."""
    args = []
    if settings['audio'] or settings['microphone']:
        runtime = Path(os.environ.get('XDG_RUNTIME_DIR', '/nonexistent'))
        backend = 'coreaudio' if platform.system() == 'Darwin' else 'pipewire' if (runtime / 'pipewire-0').is_socket() else 'none'
        args += ['-audiodev', backend + ',id=sound0', '-device', 'ich9-intel-hda',
                 '-device', ('hda-duplex' if settings['microphone'] else 'hda-output') + ',audiodev=sound0']
    for i, folder in enumerate(settings['folders']):
        path = Path(folder['path']).resolve()
        if not path.is_dir():
            raise TesterError('Shared folder is no longer available: ' + str(path))
        value = f'local,id=shared{i},path={str(path).replace(",", ",,")},security_model=mapped-xattr'
        if not folder['writable']:
            value += ',readonly=on'
        args += ['-fsdev', value, '-device', f'virtio-9p-pci,fsdev=shared{i},mount_tag=luma{i}']
    if settings['usb']:
        args += ['-device', 'qemu-xhci,id=usb']
    for i, device in enumerate(settings['usb']):
        node = Path(f'/dev/bus/usb/{device["bus"]:03}/{device["address"]:03}')
        current=next((d for d in usb_devices() if (d['bus'],d['address'],d['name']) == (device['bus'],device['address'],device['name'])),None)
        if current and current['mounted']:
            raise TesterError('Eject this drive on your computer before sharing: '+device['name'])
        matching=current is not None
        if not matching or not os.access(node, os.R_OK | os.W_OK):
            raise TesterError('USB device is disconnected or needs host permission: ' + device['name'])
        args += ['-device', f'usb-host,hostbus={device["bus"]},hostaddr={device["address"]},id=hostusb{i}']
    return args


def usb_devices():
    from .host_devices import usb_devices as inventory
    return inventory()


def cameras():
    return [item for item in usb_devices() if item['kind']=='camera']


def checkpoints(session):
    if not session.disk.exists():
        return []
    if session.status() != 'stopped':
        for block in session.qmp('query-block'):
            inserted=block.get('inserted',{})
            if inserted.get('node-name')=='os':
                return inserted.get('image',{}).get('snapshots',[])
        return []
    info = json.loads(subprocess.check_output(['qemu-img', 'info', '--output=json', str(session.disk)]))
    return info.get('snapshots', [])


def checkpoint(session, name):
    name = name.strip()
    if not 1 <= len(name) <= 80 or any(ord(c) < 32 for c in name):
        raise TesterError('Name the snapshot using 1–80 characters.')
    status=session.status()
    if status in ('running','paused'):
        if any(item['name']==name for item in checkpoints(session)):
            raise TesterError('A snapshot with that name already exists.')
        # Disk checkpoint: a brief pause gives the atomic disk operation a
        # stable boundary. RAM and shared host folders are not captured.
        if status=='running':session.qmp('stop')
        try:
            session.qmp('blockdev-snapshot-internal-sync', {'device':'os','name':name}, timeout=120)
        finally:
            if status=='running':session.qmp('cont')
        return checkpoints(session)
    with session.lock():
        if session.status() != 'stopped' or not session.disk.exists():
            raise TesterError('This workspace is not ready for a disk snapshot.')
        if any(item['name'] == name for item in checkpoints(session)):
            raise TesterError('A snapshot with that name already exists.')
        run(['qemu-img', 'snapshot', '-c', name, str(session.disk)], timeout=120)
    return checkpoints(session)


def restore(session, name):
    with session.lock():
        if session.status() != 'stopped':
            raise TesterError('Shut down the guest before restoring a snapshot.')
        if not any(item['name'] == name for item in checkpoints(session)):
            raise TesterError('This snapshot is no longer available.')
        run(['qemu-img', 'snapshot', '-a', name, str(session.disk)], timeout=120)


def configure_guest(session, settings, mount_folders=True):
    """Host control SSH stays private; the optional agent gets a separate revocable key."""
    key = session.directory / 'agent-key'
    if key.exists():key.chmod(0o600)
    if settings['agent_ssh'] and not key.exists():
        run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], timeout=15)
    if key.exists():
        key.chmod(0o600)
        public = Path(str(key) + '.pub').read_text().strip()
        script = '''import os,sys
from pathlib import Path
key=sys.stdin.read().strip()
p=Path.home()/'.ssh/authorized_keys'
lines=p.read_text().splitlines()
identity=' '.join(key.split()[:2])
lines=[line for line in lines if ' '.join(line.split()[:2])!=identity]
if sys.argv[1]=='on':lines.append(key)
temp=p.with_suffix('.tester-new');temp.write_text('\\n'.join(lines)+'\\n');temp.chmod(0o600);temp.replace(p)
'''
        run(session.ssh(['python3', '-c', script, 'on' if settings['agent_ssh'] else 'off']),
            input=public, text=True, timeout=15, stdout=subprocess.DEVNULL)
    for i, folder in enumerate(settings['folders'] if mount_folders else []):
        target = '/mnt/shared/' + str(i + 1)
        raw = '/mnt/luma-shared-raw/' + str(i + 1)
        # Translate ownership inside the guest, without chmod/chown on the
        # host's project. Host and guest user IDs need not match.
        install = 'command -v bindfs >/dev/null || dnf5 -y --setopt=install_weak_deps=False install bindfs'
        run(session.ssh(['sudo', 'sh', '-c', install]), timeout=120, stdout=subprocess.DEVNULL)
        run(session.ssh(['sudo', 'mkdir', '-p', raw, target]), timeout=15)
        # mountpoint makes repeated configuration idempotent.
        script = 'mountpoint -q "$1" || mount -t 9p -o "$2" "$3" "$1"'
        options = 'trans=virtio,version=9p2000.L,msize=262144' + ('' if folder['writable'] else ',ro')
        run(session.ssh(['sudo', 'sh', '-c', script, 'mount-shared', raw, options, 'luma' + str(i)]), timeout=15)
        bind = ['bindfs', '--force-user=' + session.manifest['user'], '--force-group=' + session.manifest['user'],
                '--perms=u+rwX', '--create-as-mounter', '-o', 'allow_other' + ('' if folder['writable'] else ',ro'), raw, target]
        run(session.ssh(['sudo', 'sh', '-c', 'mountpoint -q "$1" || { shift; exec "$@"; }',
                        'bind-shared', target, *bind]), timeout=20)


def repair_legacy_renderer(session):
    """Upgrade only the known old simulator launcher, before exposing its screen.

    New images select by negotiated VirtIO features themselves. A dedicated
    greetd drop-in repairs existing disks without replacing them or user code.
    """
    if session.manifest['target'] != 'mobile':
        return False
    mode=json.loads((session.directory/'graphics.json').read_text())['mode']
    legacy='cbe4b7a2ef3918f997beaa2d9fd9eedabb4c010fd0020231b9872961e5ddfdca'
    script='''import hashlib,json,sys
from pathlib import Path
identity=Path('/etc/luma/tester-image')
if not identity.exists() or 'LUMA_PHYSICAL_DEVICE=false' not in identity.read_text():
 print('false');raise SystemExit()
launcher=Path('/usr/libexec/luma-tester-mobile-session')
if not launcher.exists():print('false');raise SystemExit()
body=launcher.read_bytes()
current=b'VIRTIO_GPU_F_VIRGL' in body
if not current and hashlib.sha256(body).hexdigest()!=sys.argv[2]:
 print('false');raise SystemExit()
p=Path('/etc/systemd/system/greetd.service.d/60-luma-tester-renderer.conf')
prefix='# Managed by Luma Tester\\n'
expected=prefix+'[Service]\\nEnvironment=WLR_RENDERER=pixman\\n' if sys.argv[1]=='software' and not current else None
previous=p.read_text() if p.exists() else None
if previous and not previous.startswith(prefix):print('false');raise SystemExit()
if previous==expected:print('false');raise SystemExit()
if expected is None:p.unlink(missing_ok=True)
else:
 p.parent.mkdir(parents=True,exist_ok=True);p.write_text(expected);p.chmod(0o644)
print('true')
'''
    changed=subprocess.check_output(session.ssh(['sudo','python3','-c',script,mode,legacy]),text=True,timeout=20).strip()
    if changed!='true':
        return False
    old=subprocess.check_output(session.ssh(['cat','/proc/sys/kernel/random/boot_id']),text=True,timeout=10).strip()
    # SSH may disconnect before systemctl returns. Verify a new boot below.
    result=subprocess.run(session.ssh(['sudo','systemctl','--no-block','reboot']),
                          capture_output=True,text=True,timeout=15)
    if result.returncode not in (0,255):
        raise TesterError('The simulator could not restart: '+result.stderr.strip())
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        time.sleep(2)
        value=subprocess.run(session.ssh(['cat','/proc/sys/kernel/random/boot_id']),capture_output=True,text=True,timeout=10)
        if value.returncode==0 and value.stdout.strip()!=old:
            session.wait_graphical(120)
            return True
    raise TesterError('The saved simulator did not restart after its display update.')


def ssh_connection(session):
    """Connection details for the optional user key; never expose key contents."""
    if not session.manifest.get('cloud_init',True):
        raise TesterError('Installation media has no managed SSH account. Use a developer image for SSH access.')
    if not load(session.root)['agent_ssh'] or not (session.directory / 'agent-key').exists():
        raise TesterError('Enable SSH access and start Luma to connect.')
    (session.directory/'agent-key').chmod(0o600)
    port = json.loads((session.directory / 'connection.json').read_text())['port']
    command = ['ssh', '-F', '/dev/null', '-i', str(session.directory / 'agent-key'), '-p', str(port),
               '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'UserKnownHostsFile=' + str(session.directory / 'known_hosts'),
               session.manifest['user'] + '@127.0.0.1']
    return dict(host='127.0.0.1', port=port, user=session.manifest['user'], command=shlex.join(command))


def agent_prompt(session, manifest_path, executable='luma-tester'):
    settings = load(session.root)
    connection = ssh_connection(session)
    text = f'''Work in this Luma Tester {session.manifest['target']} guest.
Build: {session.manifest['build']}
Guest architecture: {session.manifest['arch']}. Host architecture and guest architecture are independent.
Connect locally using this existing private key (never print or copy its contents):
{connection['command']}

The guest disk persists across shutdown and reopening. Use the Tester sidebar to
save a named disk snapshot before risky changes. Keep application work in the
guest user's home or an explicitly shared folder. Do not replace the OS image.
Snapshots cover the guest disk, not shared host folders. Use version control
for shared host projects.
The guest includes luma-sdk; inspect `luma-sdk --help` and the installed platform
documentation for app builds. For native apps, run them in the guest user's
graphical session using `systemd-run --user --collect --unit=app-preview COMMAND`.
Use the app's own development watcher or restart its preview service after edits.
'''
    try:
        requested=json.loads((session.directory/'experience.json').read_text())['experience']
    except (OSError,ValueError,KeyError,TypeError):requested=session.manifest['target']
    if requested in ('tablet','tv','smart-device'):
        from .experiences import LABELS
        text+='\nRequested interface: '+LABELS[requested]+'. The OS may still use its current Desktop interface while mode support is developed.\nThe request is available in /sys/firmware/qemu_fw_cfg/by_name/opt/luma/experience/raw when fw_cfg sysfs is enabled.\n'
    if session.manifest['target']=='mobile':
        text+='\nDevice state: /run/user/$(id -u)/luma-tester/device.json (pixels, UI scale, orientation, posture, hinge, regions and keyboard). Watch this file for live transitions. Hinge-aware app layouts must consume its regions; the guest does not yet supply a standard fold sensor API.\n'
    for i, folder in enumerate(settings['folders']):
        text += f'\nShared folder {i+1}: {folder["path"]} → /mnt/shared/{i+1} ({"read/write" if folder["writable"] else "read only"}).'
    launcher = [executable] + (['--appimage-extract-and-run'] if executable.endswith('.AppImage') else [])
    text += '\n\nCLI for status and screenshots:\n' + shlex.join(launcher + ['--state', str(session.root)])
    text += '\nManifest: ' + shlex.quote(str(manifest_path)) + '\n'
    return text


def ensure_mobile_display(session):
    """Install a checksummed simulator update before revealing a fresh boot.

    This never runs in response to a live resize, nor against physical/custom
    images. Existing private disks and developer projects remain in place.
    """
    from .core import digest
    directory=os.environ.get('LUMA_TESTER_GUEST_UPDATES')
    if not directory or session.manifest['target']!='mobile' or session.manifest['arch']!='x86_64':
        return False
    known={'bd88d4af0c5e1749cf30ef87f98551d2697bfca60f9a42130211a6853faa54e9',
           'e4403e122bff15084d7b63c06209a9b88cf1d71a960c9f4eb9c19a74b1558dab'}
    if session.manifest['image']['sha256'] not in known:return False
    metadata=Path(directory)/'mobile-display.json'
    if not metadata.is_file():return False
    manifest=json.loads(metadata.read_text())
    if manifest.get('schema')!=1 or manifest.get('arch')!='x86_64':
        raise TesterError('Invalid simulator display update.')
    filename=manifest['file']
    if Path(filename).name!=filename:raise TesterError('Invalid simulator update path.')
    package=Path(directory)/filename
    if package.stat().st_size!=manifest['bytes'] or digest(package)!=manifest['sha256']:
        raise TesterError('Simulator display update checksum mismatch.')
    probe="""from pathlib import Path
p=Path('/etc/luma/tester-image')
assert p.is_file() and 'LUMA_PHYSICAL_DEVICE=false' in p.read_text()
p=Path('/etc/luma/tester-display-modes')
launcher=Path('/usr/libexec/luma-tester-mobile-session')
configured=launcher.exists() and 'export LUMA_TESTER_DYNAMIC_MODES=1' in launcher.read_text()
print(p.read_text().strip() if p.exists() and configured else '')
"""
    previous=subprocess.check_output(session.ssh(['python3','-c',probe]),text=True,timeout=15).strip()
    if previous==manifest['sha256']:return False
    script=r"""import hashlib,os,subprocess,sys,tempfile
from pathlib import Path
payload=sys.stdin.buffer.read()
assert hashlib.sha256(payload).hexdigest()==sys.argv[1]
path=Path('/etc/systemd/system/greetd.service.d/70-luma-tester-device.conf')
prefix='# Managed by Luma Tester\n'
assert not path.exists() or path.read_text().startswith(prefix.rstrip())
launcher=Path('/usr/libexec/luma-tester-mobile-session')
body=launcher.read_text()
entry='exec /opt/luma/phosh/bin/phosh-session'
assert body.count(entry)==1, 'Unrecognized simulator session launcher'
with tempfile.NamedTemporaryFile(prefix='luma-tester-display-',suffix='.rpm',delete=False) as f:
 name=f.name;f.write(payload)
try:
 header=subprocess.check_output(['rpm','-qp','--qf','%{NAME}|%{ARCH}',name],text=True)
 assert header=='phoc|x86_64'
 subprocess.run(['dnf5','-y','--disablerepo=*','install',name],check=True,timeout=120)
finally:os.unlink(name)
if 'export LUMA_TESTER_DYNAMIC_MODES=1' not in body:
 launcher.write_text(body.replace(entry,'export LUMA_TESTER_DYNAMIC_MODES=1\n'+entry,1))
path.unlink(missing_ok=True)
Path('/etc/luma/tester-display-modes').write_text(sys.argv[1]+'\n')
"""
    run(session.ssh(['sudo','python3','-c',script,manifest['sha256']]),input=package.read_bytes(),timeout=150,stdout=subprocess.DEVNULL)
    old=subprocess.check_output(session.ssh(['cat','/proc/sys/kernel/random/boot_id']),text=True,timeout=10).strip()
    result=subprocess.run(session.ssh(['sudo','systemctl','--no-block','reboot']),capture_output=True,text=True,timeout=15)
    if result.returncode not in (0,255):raise TesterError('Could not restart the simulator after its display update.')
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        time.sleep(2)
        result=subprocess.run(session.ssh(['cat','/proc/sys/kernel/random/boot_id']),capture_output=True,text=True,timeout=10)
        if result.returncode==0 and result.stdout.strip()!=old:
            session.wait_graphical(120);return True
    raise TesterError('Simulator display update was installed; the guest has not restarted yet.')
