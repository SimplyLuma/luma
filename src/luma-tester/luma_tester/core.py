# SPDX-License-Identifier: Apache-2.0
"""Local QEMU runtime. No GTK dependency; the CLI and GUI share this implementation."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import signal
import shutil
from . import host
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, build_opener


class TesterError(Exception):
    pass


def run(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], check=True, **kwargs)


def digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_manifest(path):
    """Manifests are data, never QEMU arguments or executable setup hooks."""
    return validate_manifest(json.loads(Path(path).read_text()))


def validate_manifest(m):
    """Validate local and catalog profiles using the same boot contract."""
    if not isinstance(m, dict):
        raise TesterError('Manifest must be a JSON object.')
    for name in ('id', 'target', 'arch', 'boot', 'build', 'user'):
        if not isinstance(m.get(name), str):
            raise TesterError('Manifest ' + name + ' must be a string.')
    if type(m.get('schema')) is not int or m['schema'] != 1 or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}', m.get('id', '')):
        raise TesterError('Expected schema 1 and a short lowercase image id.')
    if m.get('target') not in ('mobile', 'desktop') or m.get('arch') not in ('x86_64', 'aarch64'):
        raise TesterError('Unsupported target or architecture.')
    if not isinstance(m.get('build'), str) or not m['build'].strip():
        raise TesterError('The guest build identity is required.')
    if m.get('boot') not in ('bios', 'uefi') or (m['arch'] == 'aarch64' and m['boot'] != 'uefi'):
        raise TesterError('ARM guests require UEFI; x86 guests accept bios or uefi.')
    if m.get('media','disk') not in ('disk','iso'):
        raise TesterError('Choose a developer disk or installation ISO.')
    if m.get('cloud_init') is not (m.get('media','disk') == 'disk'):
        raise TesterError('Developer disks require cloud-init; installation ISOs do not.')
    if 'guest_protocol' in m and (m['guest_protocol'] != 'luma-unified-v1' or m.get('media', 'disk') != 'disk'):
        raise TesterError('Unsupported prepared guest protocol.')
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', m.get('user', '')):
        raise TesterError('Invalid guest user.')
    if 'display' in m:
        display = m['display']
        if (m['target'] != 'mobile' or not isinstance(display, dict)
                or set(display) != {'width', 'height', 'scale'}
                or any(type(display.get(key)) is not int or not 320 <= display[key] <= 2160
                       for key in ('width', 'height'))
                or type(display.get('scale')) not in (int, float)
                or not 1 <= display['scale'] <= 4):
            raise TesterError('Mobile display requires bounded dimensions and UI scale.')
    if 'capabilities' in m:
        capabilities = m['capabilities']
        if (not isinstance(capabilities, dict)
                or set(capabilities) - {'home', 'dynamic_display'}
                or any(type(value) is not bool for value in capabilities.values())):
            raise TesterError('Invalid guest control capabilities.')
    image = m.get('image', {})
    if not isinstance(image, dict):
        raise TesterError('Image definition must be an object.')
    if not isinstance(image.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', image['sha256']):
        raise TesterError('A SHA-256 image digest is required.')
    if type(image.get('bytes')) is not int or not 0 < image['bytes'] <= 64 * 1024**3:
        raise TesterError('Image size must be between 1 byte and 64 GiB.')
    if set(image) != {'url', 'sha256', 'bytes'}:
        raise TesterError('Image must specify only url, sha256 and bytes.')
    if not isinstance(image['url'], str):
        raise TesterError('Image URL must be a string.')
    parsed = urlparse(image['url'])
    if parsed.scheme == 'file':
        if parsed.netloc or not parsed.path.startswith('/'):
            raise TesterError('Local image URLs must be file:///absolute/path.')
    elif parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise TesterError('Images require HTTPS or a local file URL.')
    if 'artifact_id' in m and (not isinstance(m['artifact_id'], str) or
            not re.fullmatch(r'os/(nightly|beta|stable)/[A-Za-z0-9._-]+/(x86_64|aarch64)', m['artifact_id'])):
        raise TesterError('Invalid Hub artifact identity.')
    return m


def data_root(state=None):
    if not state and not os.environ.get('LUMA_TESTER_HOME') and host.windows():
        return (Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'Luma Tester').resolve()
    if not state and not os.environ.get('LUMA_TESTER_HOME') and platform.system() == 'Darwin':
        return (Path.home() / 'Library/Application Support/Luma Tester').resolve()
    return Path(state or os.environ.get('LUMA_TESTER_HOME') or
                Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'luma-tester').resolve()


class HTTPSRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        if urlparse(request.full_url).scheme == 'https' and urlparse(new_url).scheme != 'https':
            raise TesterError('Refusing a download redirected away from HTTPS.')
        return super().redirect_request(request, response, code, message, headers, new_url)


class Session:
    def __init__(self, manifest, state=None):
        self.manifest = read_manifest(manifest)
        self.experience = self.manifest['target']
        self.root = data_root(state)
        self.directory = self.root / 'sessions' / self.manifest['id']
        # UNIX socket paths have a small platform limit. Keep them outside the data path.
        runtime = (data_root() / 'runtime' if host.windows() else
            Path(os.environ.get('XDG_RUNTIME_DIR', f'/tmp/luma-tester-{os.getuid()}')))
        try:host.private_directory(runtime)
        except RuntimeError as exc:raise TesterError(str(exc)) from exc
        token = hashlib.sha256(str(self.directory).encode()).hexdigest()[:16]
        self.runtime = runtime / ('luma-test-' + token)
        try:host.private_directory(self.runtime)
        except RuntimeError as exc:raise TesterError(str(exc)) from exc
        self.pipe_name = 'luma-tester-' + token
        self.qmp_path = self.runtime / 'qmp'
        self.display_path = self.runtime / 'display'
        self.base = self.root / 'images' / (self.manifest['image']['sha256'] + ('.iso' if self.manifest.get('media') == 'iso' else '.qcow2'))
        self.key = self.directory / 'developer-key'

    @property
    def disk(self):
        from . import provision
        return self.directory / ('workspace.qcow2' if provision.ready(self) else 'disk.qcow2')

    @contextlib.contextmanager
    def lock(self):
        with open(self.runtime / 'lock', 'a+b') as stream:
            try:
                host.lock(stream)
            except OSError as exc:
                raise TesterError('This session is already running or being prepared.') from exc
            if host.windows() and self.status() != 'stopped':
                raise TesterError('This session is already running.')
            yield stream

    def tools(self):
        required = ['qemu-img', 'qemu-system-' + self.manifest['arch']]
        if self.manifest['cloud_init']:
            required += ['ssh-keygen', 'ssh']
            if not host.windows():required += ['xorriso']
        elif self.manifest.get('media')=='iso':
            from . import provision
            if provision.approved(self):
                required += ['ssh-keygen','ssh']
                if not host.windows():required += ['xorriso']
        return {x: shutil.which(x) for x in required}

    def prepare(self, report=print, progress=None):
        with self.lock():
            self._prepare(report, progress)

    def _prepare(self, report, progress=None):
        from .transfers import TransferReporter
        transfer = TransferReporter(self.manifest['image']['bytes'], report, progress)
        missing = [k for k, v in self.tools().items() if not v]
        if missing:
            raise TesterError('Install required tools: ' + ', '.join(missing))
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.base.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        saved = self.directory / 'manifest.json'
        if saved.exists():
            previous = json.loads(saved.read_text())
            # A portable directory/AppImage can move; bytes and boot/profile
            # identity stay pinned even when the verified image URL changes.
            previous['image']['url'] = self.manifest['image']['url']
            if previous != self.manifest:
                raise TesterError('This id already belongs to another image. Use a new manifest id.')
        # Pin identity before any disk/key mutation, including interrupted first preparation.
        saved.write_text(json.dumps(self.manifest, indent=2) + '\n')
        from . import provision
        if provision.ready(self):
            # A completed installation is self-contained. Reopening saved work
            # must not redownload installation media after cache cleanup.
            self.validate_disk(self.disk)
            report('Your saved Luma workspace is ready.')
            return
        image = self.manifest['image']
        verified=False
        # Only published, explicitly associated artifacts contact the Hub.
        # Private/local images never create attribution tickets.
        if (not self.base.exists() and self.manifest.get('artifact_id')
                and urlparse(image['url']).scheme == 'https'):
            from .downloads import download
            artifact = {'artifact_id': self.manifest['artifact_id'], 'url': image['url'],
                        'sha256': image['sha256'], 'size_bytes': image['bytes']}
            if shutil.disk_usage(self.root).free < image['bytes'] + 2 * 1024**3:
                raise TesterError('Insufficient space for image plus 2 GiB working reserve.')
            purpose = 'first_download'
            for previous_path in (self.root / 'sessions').glob('*/manifest.json'):
                try:
                    previous = json.loads(previous_path.read_text())
                    if (previous.get('target') == self.manifest['target']
                            and previous.get('arch') == self.manifest['arch']
                            and previous['image']['sha256'] != image['sha256']):
                        purpose = 'update'
                        break
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            download(artifact, self.base, experience={'smart-device':'home'}.get(self.experience,self.experience), purpose=purpose, report=report, progress=progress)
            try:
                self.validate_image(self.base)
            except Exception:
                self.base.unlink(missing_ok=True)
                raise
            self.base.chmod(0o444)
            verified=True
        if not self.base.exists():
            if shutil.disk_usage(self.root).free < image['bytes'] + 2 * 1024**3:
                raise TesterError('Insufficient space for image plus 2 GiB working reserve.')
            phase = 'copying' if urlparse(image['url']).scheme == 'file' else 'downloading'
            transfer.update('connecting', 0, force=True)
            # Unique temporary paths allow two different sessions to share this cache safely.
            output = tempfile.NamedTemporaryFile(dir=self.base.parent, delete=False)
            partial = Path(output.name)
            try:
                with output:
                    with build_opener(HTTPSRedirects).open(image['url'], timeout=30) as source:
                        if urlparse(image['url']).scheme == 'https' and urlparse(source.url).scheme != 'https':
                            raise TesterError('Refusing a download redirected away from HTTPS.')
                        count = 0
                        checksum=hashlib.sha256()
                        transfer.update(phase, 0, force=True)
                        read = getattr(source, 'read1', source.read)
                        while chunk := read(64 * 1024):
                            count += len(chunk)
                            if count > image['bytes']:
                                raise TesterError('Download exceeds declared image size.')
                            output.write(chunk)
                            checksum.update(chunk)
                            transfer.update(phase, count)
                    transfer.update(phase, count, force=True)
                    output.flush()
                # Windows cannot replace or remove a file while our writer is
                # open. Close it before validation, promotion, or failure cleanup.
                transfer.update('verifying', count, force=True)
                if count != image['bytes'] or checksum.hexdigest() != image['sha256']:
                    raise TesterError('Guest image size or SHA-256 mismatch.')
                self.validate_image(partial)
                partial.chmod(0o444)
                partial.replace(self.base)
                verified=True
                transfer.update('ready', count, force=True)
            finally:
                if partial.exists():
                    partial.chmod(0o600)
                    partial.unlink()
        # Verify cached inputs too. Do not run a different image under a trusted build label.
        if self.base.stat().st_size != image['bytes'] or (not verified and transfer.digest(self.base) != image['sha256']):
            raise TesterError('Cached image changed; remove only that cache file and prepare again.')
        from . import vm
        capacity = vm.load(self.root)['disk_gib'] * 1024**3
        if not self.disk.exists():
            if self.manifest.get('media') == 'iso':
                run(['qemu-img','create','-q','-f','qcow2',self.disk,str(capacity)])
            else:
                run(['qemu-img', 'create', '-q', '-f', 'qcow2', '-F', 'qcow2', '-b', self.base, self.disk])
        info = json.loads(subprocess.check_output(['qemu-img', 'info', '--output=json', str(self.disk)]))
        if info['virtual-size'] < capacity:
            run(['qemu-img', 'resize', '-q', str(self.disk), str(capacity)])
        self.disk_capacity = max(info['virtual-size'], capacity)
        if not self.manifest['cloud_init']:
            report('Installation media and persistent disk are ready.')
            return
        if not self.key.exists():
            run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', self.key])
        # Shared filesystems can inherit ACL masks that SSH refuses for keys.
        self.key.chmod(0o600)
        if not (self.directory / 'seed.iso').exists():
            user = self.manifest['user']
            config = {'users': [{'name': user, 'groups': ['wheel'], 'shell': '/bin/bash',
                                 'lock_passwd': True, 'sudo': ['ALL=(ALL) NOPASSWD:ALL'],
                                 'ssh_authorized_keys': [Path(str(self.key) + '.pub').read_text().strip()]}],
                      'ssh_pwauth': False, 'disable_root': True,
                      'write_files': [{'path': '/etc/luma-tester-target', 'permissions': '0644',
                                       'content': (self.experience if self.manifest.get('guest_protocol') == 'luma-unified-v1'
                                                   else self.manifest['target']) + '\n'}]}
            # cloud-init accepts JSON as a YAML subset.
            (self.directory / 'user-data').write_text('#cloud-config\n' + json.dumps(config))
            (self.directory / 'meta-data').write_text(json.dumps({'instance-id': self.manifest['id']}))
            if host.windows():
                from .seed import create
                create(self.directory)
            else:
                run(['xorriso', '-as', 'mkisofs', '-quiet', '-o', self.directory / 'seed.iso.new',
                     '-V', 'cidata', '-J', '-r', self.directory / 'user-data', self.directory / 'meta-data'])
            (self.directory / 'seed.iso.new').replace(self.directory / 'seed.iso')
        report('Guest image and private developer session are ready.')

    def validate_image(self, path):
        if self.manifest.get('media') == 'iso':
            from .experiences import validate_iso
            validate_iso(path)
            return
        self.validate_disk(path)

    @staticmethod
    def validate_disk(path):
        info = json.loads(subprocess.check_output(['qemu-img', 'info', '--output=json', str(path)]))
        details = info.get('format-specific', {}).get('data', {})
        if (info['format'] != 'qcow2' or info.get('backing-filename') or
                info.get('data-file') or details.get('data-file') or
                info.get('encrypted') or details.get('encrypt')):
            raise TesterError('Image must be a standalone, unencrypted qcow2 disk.')
        if info.get('virtual-size', 0) > 256 * 1024**3:
            raise TesterError('Guest virtual disk exceeds 256 GiB.')

    def firmware(self, override=None):
        if self.manifest['boot'] == 'bios':
            return None
        bundled=Path(os.environ.get('LUMA_TESTER_FIRMWARE_DIR','/nonexistent'))
        choices = ([override] if override else []) + [str(bundled / ('aarch64/QEMU_EFI.fd' if self.manifest['arch']=='aarch64' else 'ovmf/OVMF_CODE.fd'))] + (
            ['/usr/share/edk2/aarch64/QEMU_EFI.fd', '/usr/share/AAVMF/AAVMF_CODE.fd']
            if self.manifest['arch'] == 'aarch64' else
            ['/usr/share/edk2/ovmf/OVMF_CODE.fd', '/usr/share/OVMF/OVMF_CODE.fd'])
        for candidate in choices:
            if Path(candidate).is_file():
                return str(Path(candidate).resolve())
        raise TesterError('UEFI firmware missing; install edk2 or supply --firmware PATH.')

    @staticmethod
    def render_device(requested=None):
        candidates = [Path(requested)] if requested else sorted(Path('/dev/dri').glob('renderD*'))
        for path in candidates:
            path = path.resolve()
            if re.fullmatch(r'/dev/dri/renderD[0-9]+', str(path)) and path.is_char_device() and os.access(path, os.R_OK | os.W_OK):
                return str(path)
        if requested:
            raise TesterError('The selected GPU render device is not accessible.')
        return None

    def command(self, port=2228, memory=None, cpus=None, headless=False, firmware=None, software=False, embedded=False,
                graphics=None, render_node=None, experience=None, provisioning=False, disk_path=None):
        from . import vm
        from . import experiences
        from . import provision
        installed = not provisioning and provision.ready(self)
        experience = experience or self.manifest['target']
        if not experiences.compatible(experience,self.manifest):
            raise TesterError('This image cannot boot the selected experience.')
        settings = vm.load(self.root)
        memory = settings['memory'] if memory is None else memory
        cpus = settings['cpus'] if cpus is None else cpus
        graphics = settings['graphics'] if graphics is None else graphics
        if not 1024 <= memory <= 65536 or not 1 <= cpus <= 32 or not 1024 <= port <= 65535:
            raise TesterError('Use memory 1024–65536 MiB, CPUs 1–32, SSH port 1024–65535.')
        if graphics not in ('auto', 'virgl', 'software'):
            raise TesterError('Choose graphics auto, virgl or software.')
        render = self.render_device(render_node) if graphics != 'software' else None
        accelerated = graphics == 'virgl' or (graphics == 'auto' and not headless and render is not None)
        if accelerated and render is None:
            raise TesterError('Accelerated graphics needs an accessible GPU render device.')
        display = ('egl-headless,rendernode=' + render if accelerated and (embedded or headless) else
                   'gtk,gl=on,zoom-to-fit=on' if accelerated else
                   'none' if headless or embedded else 'sdl' if host.windows() else 'cocoa' if platform.system() == 'Darwin' else 'gtk,zoom-to-fit=on')
        gpu = 'virtio-gpu-gl-pci' if accelerated else 'virtio-gpu-pci'
        arch = self.manifest['arch']
        native = platform.machine() in ({'aarch64', 'arm64'} if arch == 'aarch64' else {'x86_64', 'AMD64'})
        kvm = native and not software and platform.system() == 'Linux' and os.access('/dev/kvm', os.R_OK | os.W_OK)
        hvf = native and not software and platform.system() == 'Darwin'
        whpx = native and not software and host.whpx_available()
        accelerator = 'whpx' if whpx else 'hvf' if hvf else 'kvm' if kvm else 'tcg,thread=multi'
        width, height = (432, 960) if experience == 'mobile' else (1280, 800) if experience in ('tablet','smart-device') else (1920, 1080)
        if experience == 'mobile' and self.manifest.get('display'):
            width, height = (self.manifest['display'][key] for key in ('width', 'height'))
        if experience == 'mobile' and (self.manifest.get('media') == 'iso' or
                                     self.manifest.get('guest_protocol') == 'luma-unified-v1'):
            # ARM EDK2 needs an 80-column console; 432px can assert before GRUB.
            width, height = 864, 1920
        argv = ['qemu-system-' + arch, '-name', 'Luma ' + experiences.LABELS[experience],
                '-machine', 'virt,gic-version=3' if arch == 'aarch64' else 'q35',
                '-accel', accelerator, '-cpu', 'host' if kvm or hvf else 'max',
                '-m', str(memory), '-smp', str(cpus), '-nic',
                f'user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:{port}-:22',
                '-vga', 'none', '-device', f'{gpu},edid=on,xres={width},yres={height}',
                '-device', 'virtio-keyboard-pci', '-device', 'virtio-tablet-pci',
                '-device', 'virtio-rng-pci', '-qmp', f'unix:{self.qmp_path},server=on,wait=off',
                '-monitor', 'none', '-serial', 'file:' + str(self.directory / 'console.log'),
                '-display', display]
        if os.environ.get('LUMA_TESTER_QEMU_DATA_DIR'):
            argv += ['-L', os.environ['LUMA_TESTER_QEMU_DATA_DIR']]
        if arch=='aarch64':
            position=argv.index('-nic')
            argv[position:position+2]=['-netdev',f'user,id=network0,hostfwd=tcp:127.0.0.1:{port}-:22','-device','virtio-net-pci,netdev=network0,romfile=']
        argv += ['-fw_cfg','name=opt/luma/experience,string='+experiences.FLAGS.get(experience,experience)]
        if self.manifest['target'] == 'mobile' or (experience == 'mobile' and
                self.manifest.get('guest_protocol') == 'luma-unified-v1'):
            argv += ['-device', 'virtio-multitouch-pci']
        if embedded:
            if host.windows():
                import secrets
                self.windows_display = dict(port=host.free_port(), password=secrets.token_hex(4))
                argv += ['-vnc', '127.0.0.1:'+str(self.windows_display['port']-5900)+',password=on', '-S']
            else:argv += ['-vnc', 'unix:' + str(self.display_path)]
        disks=[('os',disk_path or (self.directory/'workspace.qcow2' if installed else self.disk),'qcow2',False)]
        if self.manifest['cloud_init']:
            disks.append(('seed',self.directory/'seed.iso','raw',True))
        for name, path, fmt, readonly in disks:
            block = {'driver': fmt, 'node-name': name, 'read-only': readonly, 'discard': 'unmap',
                     'file': {'driver': 'file', 'filename': str(path)}}
            boot=(',bootindex='+('2' if settings['boot_iso'] and not installed else '1')) if name=='os' and self.manifest.get('media')=='iso' else ''
            if name=='os' and self.manifest.get('guest_protocol')=='luma-unified-v1':boot=',bootindex=1'
            argv += ['-blockdev', json.dumps(block), '-device', f'virtio-blk-pci,drive={name}'+boot]
        if self.manifest.get('media') == 'iso' and not installed:
            block={'driver':'raw','node-name':'installer','read-only':True,'file':{'driver':'file','filename':str(self.base)}}
            argv += ['-blockdev',json.dumps(block),'-device','virtio-scsi-pci,id=scsi','-device','scsi-cd,drive=installer,bootindex='+('1' if settings['boot_iso'] else '2')]
        bios = self.firmware(firmware)
        if bios and (self.manifest.get('media') == 'iso' or self.manifest.get('guest_protocol') == 'luma-unified-v1' or
                     (arch == 'x86_64' and platform.system() == 'Darwin')):
            code=Path(bios).with_name('QEMU_EFI-pflash.raw') if arch=='aarch64' else Path(bios)
            template=code.with_name('vars-template-pflash.raw' if arch=='aarch64' else 'OVMF_VARS.fd')
            variables=self.directory/'firmware-vars.raw'
            if not code.is_file() or not template.is_file():
                raise TesterError('This installer needs matching UEFI code and variable templates.')
            self.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
            if not variables.exists():
                with template.open('rb') as source, variables.open('xb') as target:
                    variables.chmod(0o600);shutil.copyfileobj(source,target)
            for name,path,readonly in [('fwcode',code,True),('fwvars',variables,False)]:
                argv += ['-blockdev',json.dumps({'driver':'raw','node-name':name,'read-only':readonly,'file':{'driver':'file','filename':str(path)}})]
            argv[argv.index('-machine')+1]+=',pflash0=fwcode,pflash1=fwvars'
        elif bios:
            argv += ['-bios', bios]
        if not headless:
            argv += vm.devices(settings)
        return argv

    def probe_graphics(self, command):
        """Require QEMU to finish EGL initialization before selecting VirGL."""
        display = command[command.index('-display') + 1]
        if not display.startswith('egl-headless'):
            render = self.render_device()
            display = 'egl-headless,rendernode=' + render
        probe = [command[0], '-machine', command[command.index('-machine') + 1],
                 '-cpu', 'max', '-accel', 'tcg', '-m', '256', '-nodefaults', '-vga', 'none',
                 '-device', 'virtio-gpu-gl-pci', '-display', display, '-S', '-qmp', 'stdio']
        requests = ''.join(json.dumps({'execute': action}) + '\n' for action in
                           ('qmp_capabilities', 'query-status', 'quit'))
        result = subprocess.run(probe, input=requests, text=True, capture_output=True, timeout=10)
        if result.returncode or '"status"' not in result.stdout:
            detail = result.stderr.strip()[-1500:] or f'QEMU exited {result.returncode} without confirming graphics readiness.'
            raise TesterError('GPU initialization failed: ' + detail)

    def start(self, **options):
        if platform.system() not in ('Linux', 'Darwin', 'Windows'):
            raise TesterError('This launcher supports Linux, macOS and Windows hosts.')
        with self.lock() as lease:
            self.experience=options.get('experience') or self.manifest['target']
            (self.directory/'setup-cancel').unlink(missing_ok=True)
            self._prepare(print)
            from . import provision, vm
            provision.check_cancel(self)
            if provision.approved(self) and vm.load(self.root)['boot_iso']:
                provision.prepare(self, lease, options)
            command = self.command(**options)
            self.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
            (self.directory/'experience.json').write_text(json.dumps({'experience':options.get('experience') or self.manifest['target'],'arch':self.manifest['arch']})+'\n')
            graphics = {'mode': 'software', 'reason': 'GPU unavailable or software mode selected'}
            if any(arg.startswith('virtio-gpu-gl-pci,') for arg in command):
                try:
                    self.probe_graphics(command)
                    graphics = {'mode': 'virgl', 'render_node': self.render_device(options.get('render_node'))}
                except (TesterError, subprocess.TimeoutExpired) as exc:
                    from . import vm
                    if (options.get('graphics') or vm.load(self.root)['graphics']) == 'virgl':
                        raise TesterError(str(exc)) from exc
                    graphics['reason'] = str(exc)
                    command = self.command(**dict(options, graphics='software'))
                    print('Using software graphics: ' + str(exc), flush=True)
            (self.directory / 'graphics.json').write_text(json.dumps(graphics, indent=2) + '\n')
            print('Graphics: ' + graphics['mode'], flush=True)
            # The exclusive lock proves any old endpoint belongs to a stopped session.
            self.qmp_path.unlink(missing_ok=True)
            self.display_path.unlink(missing_ok=True)
            (self.directory / 'connection.json').write_text(json.dumps({'port': options.get('port', 2228)}))
            (self.directory / 'command.json').write_text(json.dumps(command, indent=2))
            print('Starting ' + self.manifest['build'] + '; acceleration: ' + command[command.index('-accel') + 1], flush=True)
            # Preserve the lease in QEMU if the supervisor dies unexpectedly.
            child = subprocess.Popen(command, **({} if host.windows() else dict(pass_fds=(lease.fileno(),))))
            if host.windows() and options.get('embedded'):
                try:
                    for _ in range(100):
                        if child.poll() is not None:raise TesterError('QEMU stopped before opening its controls.')
                        try:
                            self.qmp('change-vnc-password', {'password':self.windows_display['password']})
                            break
                        except OSError:time.sleep(.1)
                    else:raise TesterError('QEMU did not open its controls.')
                    self.display_path.write_text(json.dumps(self.windows_display))
                    self.qmp('cont')
                except BaseException:
                    child.terminate();child.wait();raise
            previous = {}
            def forward(signum, _frame):
                if child.poll() is None:
                    child.send_signal(signum)
            try:
                for signum in (signal.SIGTERM, signal.SIGINT):
                    previous[signum] = signal.signal(signum, forward)
                return child.wait()
            finally:
                for signum, handler in previous.items():
                    signal.signal(signum, handler)
                if child.poll() is None:
                    child.terminate()
                    child.wait()
                self.qmp_path.unlink(missing_ok=True)
                self.display_path.unlink(missing_ok=True)

    def qmp(self, execute, arguments=None, timeout=5):
        with host.local_control_socket(self.qmp_path, timeout) as sock:
            with sock.makefile('rwb', buffering=0) as stream:
                return self.qmp_stream(stream, execute, arguments)

    @staticmethod
    def qmp_stream(stream, execute, arguments=None):
        def receive():
            while True:
                line = stream.readline(1024 * 1024)
                if not line:
                    raise TesterError('QEMU closed the control connection.')
                result = json.loads(line)
                if 'error' in result:
                    raise TesterError(str(result['error']))
                if 'return' in result:
                    return result['return']
        json.loads(stream.readline())  # greeting
        stream.write(b'{"execute":"qmp_capabilities"}\n')
        receive()
        stream.write(json.dumps({'execute': execute, 'arguments': arguments or {}}).encode() + b'\n')
        return receive()

    def touch(self, phase, x, y, width, height):
        """Deliver a real evdev touch contact through QEMU's multitouch device."""
        mobile=self.manifest['target']=='mobile' or (self.manifest.get('guest_protocol')=='luma-unified-v1' and self.experience=='mobile')
        if not mobile or phase not in ('begin', 'update', 'end', 'cancel'):
            raise TesterError('Touch input requires a Mobile guest and a valid contact phase.')
        if not 1 < width <= 4096 or not 1 < height <= 4096:
            raise TesterError('Invalid touch viewport dimensions.')
        px = int(max(0, min(width - 1, x)) * 32767 / (width - 1))
        py = int(max(0, min(height - 1, y)) * 32767 / (height - 1))
        releasing = phase in ('end', 'cancel')
        def contact(kind, axis='x', value=0):
            return {'type': 'mtt', 'data': {'type': kind, 'slot': 0,
                    'tracking-id': -1 if releasing else 1, 'axis': axis, 'value': value}}
        events = []
        if phase == 'begin':
            events.append({'type': 'btn', 'data': {'button': 'touch', 'down': True}})
        events.append(contact(phase))
        if not releasing:
            events.extend((contact('data', 'x', px), contact('data', 'y', py)))
        else:
            events.append({'type': 'btn', 'data': {'button': 'touch', 'down': False}})
        self.qmp('input-send-event', {'events': events})

    def display_available(self):
        return self.display_path.is_file() if host.windows() else self.display_path.is_socket()

    def status(self):
        try:
            return self.qmp('query-status')['status']
        except (OSError, TesterError):
            return 'stopped'

    def home(self):
        """Invoke the pinned phone renderer's native Home action."""
        if self.manifest['target'] != 'mobile':
            raise TesterError('Home navigation requires a Mobile guest.')
        if not self.manifest.get('capabilities', {}).get('home', True):
            raise TesterError('Use the phone gesture bar to return Home in this image.')
        run(self.ssh(['gdbus', 'call', '--session', '--dest', 'org.project_luma.Tester',
            '--object-path', '/org/project_luma/Tester', '--method',
            'org.project_luma.Tester1.ShowHome']), stdout=subprocess.DEVNULL, timeout=15)

    def shutdown(self):
        from . import provision
        if not self.manifest['cloud_init'] and not provision.ready(self):
            if self.status() in ('paused','prelaunch'):self.qmp('cont')
            self.qmp('system_powerdown')
            return
        # Luma Mobile owns the virtual power button as display sleep/wake.
        # An ACPI button press is therefore not a reliable shutdown request.
        if self.status() in ('paused', 'prelaunch'):
            self.qmp('cont')
        result = subprocess.run(self.ssh(['sudo', 'systemctl', '--no-block', 'poweroff']),
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True, timeout=15)
        if result.returncode == 255:
            # SSH closes early in a normal shutdown; desktop services and disk
            # flushes can take considerably longer than the SSH connection.
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                if self.status()=='stopped':return
                time.sleep(.25)
        if result.returncode != 0:
            raise TesterError('Could not request guest shutdown: ' + result.stderr.strip()[-1024:])

    def screenshot(self, destination):
        destination = Path(destination).resolve()
        if destination.exists():
            raise TesterError('Screenshot destination already exists.')
        try:
            self.qmp('screendump', {'filename': str(destination)})
        except TesterError as exc:
            if 'no surface' not in str(exc) or not self.display_available():
                raise
            # GVnc owns a GLib coroutine context. A capture requested from a
            # worker must never enter the embedded viewer's main context.
            env = dict(os.environ)
            env['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get('PYTHONPATH', '')
            run([sys.executable, '-m', 'luma_tester.rfb', self.display_path, destination],
                env=env, timeout=25)
        return destination

    def ssh(self, command):
        if self.status() == 'stopped':
            raise TesterError('Start the guest before connecting.')
        if self.key.exists():self.key.chmod(0o600)
        port = json.loads((self.directory / 'connection.json').read_text())['port']
        argv = ['ssh', '-F', os.devnull, '-i', str(self.key), '-p', str(port), '-o', 'BatchMode=yes',
                '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=5',
                '-o', 'StrictHostKeyChecking=accept-new', '-o',
                'UserKnownHostsFile="' + str(self.directory / 'known_hosts').replace(
                    '%', '%%').replace('\\', '\\\\').replace('"', '\\"') + '"',
                self.manifest['user'] + '@127.0.0.1']
        # OpenSSH joins arguments into a shell command: quote every guest argument.
        remote = shlex.join(command) if command else ''
        if host.windows() and command:
            # Win32 OpenSSH can stall on nested shell quotes as well as literal
            # newlines. A numeric JSON payload keeps the transport command to
            # one simple quoted argument and preserves the original shell argv.
            payload = list(json.dumps(['sh', 'sh', '-c', remote]).encode())
            script = 'import json,os;os.execlp(*json.loads(bytes(' + repr(payload) + ')))'
            remote = 'python3 -c ' + shlex.quote(script)
        return argv + ([remote] if command else [])

    def sync(self, source):
        source = Path(source).resolve()
        if not source.is_dir() or source == Path('/') or source == Path.home():
            raise TesterError('Choose an application source directory, not your home or root.')
        ssh = self.ssh([])
        destination = ssh[-1] + ':Projects/app/'
        run(self.ssh(['mkdir', '-p', 'Projects/app']))
        run(['rsync', '-rlptz', '--safe-links', '--exclude=.git', '--exclude=.env',
             '--exclude=.env.*', '--exclude=.venv', '--exclude=__pycache__',
             '-e', shlex.join(ssh[:-1]), '--', str(source) + '/', destination])

    def wait(self, timeout=180):
        deadline = time.monotonic() + timeout
        last_error = 'guest is still booting'
        while time.monotonic() < deadline:
            try:
                result = subprocess.run(self.ssh(['true']), stdout=subprocess.DEVNULL,
                                        stderr=subprocess.PIPE, text=True, timeout=15)
                if result.returncode == 0:
                    return
                last_error = result.stderr.strip()[-1024:] or 'SSH exited ' + str(result.returncode)
            except (TesterError, subprocess.TimeoutExpired) as exc:
                last_error = str(exc)
            time.sleep(2)
        raise TesterError('Guest SSH was not ready; inspect console.log. Last result: ' + last_error)

    def wait_graphical(self, timeout=180):
        """Wait for the real graphical session, not merely an open SSH port."""
        self.wait(timeout)
        if self.manifest.get('guest_protocol') == 'luma-unified-v1':
            from . import guest_preview
            guest_preview.configure(self)
        deadline = time.monotonic() + timeout
        from . import provision
        if provision.ready(self) or self.manifest.get('guest_protocol') == 'luma-unified-v1':
            check = """import os, subprocess
uid=str(os.getuid())
display=subprocess.check_output(['loginctl','show-user',uid,'-p','Display','--value'],text=True).strip()
assert display, 'Graphical login is not ready'
properties=dict(line.split('=',1) for line in subprocess.check_output(
    ['loginctl','show-session',display,'-p','Type','-p','Class','-p','Active'],text=True).splitlines())
assert properties['Type'] in ('wayland','x11') and properties['Class']=='user' and properties['Active']=='yes', properties
assert subprocess.run(['pgrep','-u',uid,'-x','gnome-shell'],stdout=subprocess.DEVNULL).returncode==0
"""
            while time.monotonic()<deadline:
                try:
                    if subprocess.run(self.ssh(['python3','-c',check]),stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL,timeout=15).returncode==0:
                        return
                except subprocess.TimeoutExpired:pass
                time.sleep(2)
            raise TesterError('The Luma desktop did not finish signing in. Inspect console.log.')
        check = """import json, os
from pathlib import Path
runtime = Path(os.environ['XDG_RUNTIME_DIR'])
ready = json.loads((runtime / 'luma-tester/ready.json').read_text())
assert ready['boot_id'] == Path('/proc/sys/kernel/random/boot_id').read_text().strip()
assert ready['target'] == Path('/etc/luma-tester-target').read_text().strip()
assert ready['width'] > 0 and ready['height'] > 0
if ready['target'] == 'mobile':
    assert ready['height'] > ready['width']
    import subprocess
    assert ready.get('renderer') == 'luma-phosh'
    assert Path('/run/luma-display/home-ready').is_file()
    assert subprocess.check_output(['pgrep', '-u', str(os.getuid()), '-f', '^/opt/luma/phosh/libexec/phosh( |$)'], text=True).strip()
"""
        while time.monotonic() < deadline:
            try:
                if subprocess.run(self.ssh(['python3', '-c', check]), stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL,timeout=15).returncode == 0:
                    return
            except subprocess.TimeoutExpired:pass
            time.sleep(1)
        raise TesterError('Luma graphical session was not ready; inspect the guest greetd journal.')

    def dev(self, source, command, once=False):
        """Sync/restart a user app; graphical environment comes from guest systemd."""
        if not command:
            raise TesterError('Provide the guest application command after --.')
        source = Path(source).resolve()
        if not source.is_dir() or source in (Path('/'), Path.home()):
            raise TesterError('Choose an application source directory.')
        self.wait_graphical()
        previous = None
        while True:
            snapshot = []
            for parent, dirs, files in os.walk(source, followlinks=False):
                dirs[:] = [d for d in dirs if d not in ('.git', '.venv', '__pycache__') and not d.startswith('.env')]
                for name in files:
                    path = Path(parent) / name
                    if name == '.env' or name.startswith('.env.'):
                        continue
                    try:
                        stat = path.lstat()
                        snapshot.append((str(path.relative_to(source)), stat.st_mtime_ns, stat.st_size))
                    except FileNotFoundError:
                        continue
            snapshot.sort()
            if snapshot != previous:
                self.sync(source)
                subprocess.call(self.ssh(['systemctl', '--user', 'stop', 'luma-tester-app.service']))
                launch = ('exec systemd-run --user --collect --unit=luma-tester-app '
                          '--working-directory="$HOME/Projects/app" -- "$@"')
                run(self.ssh(['sh', '-c', launch, 'luma-tester', *command]))
                print('App restarted from Projects/app. Guest journal: journalctl --user -u luma-tester-app', flush=True)
                previous = snapshot
            if once:
                return
            time.sleep(1)
