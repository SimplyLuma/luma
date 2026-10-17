# SPDX-License-Identifier: Apache-2.0
"""Linux runtime. No GTK dependency; the CLI and GUI share this implementation."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import signal
import shutil
import socket
import subprocess
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
    m = json.loads(Path(path).read_text())
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
    if m.get('cloud_init') is not True:
        raise TesterError('This version requires a cloud-init enabled developer image.')
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', m.get('user', '')):
        raise TesterError('Invalid guest user.')
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
    return m


def data_root(state=None):
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
        self.root = data_root(state)
        self.directory = self.root / 'sessions' / self.manifest['id']
        # UNIX socket paths have a small platform limit. Keep them outside the data path.
        runtime = Path(os.environ.get('XDG_RUNTIME_DIR', f'/tmp/luma-tester-{os.getuid()}'))
        runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        if runtime.stat().st_uid != os.getuid() or runtime.stat().st_mode & 0o077:
            raise TesterError('Runtime directory must be private and owned by this user.')
        token = hashlib.sha256(str(self.directory).encode()).hexdigest()[:16]
        self.runtime = runtime / ('luma-test-' + token)
        self.runtime.mkdir(mode=0o700, exist_ok=True)
        if self.runtime.stat().st_uid != os.getuid() or self.runtime.stat().st_mode & 0o077:
            raise TesterError('Session runtime directory must be private and owned by this user.')
        self.qmp_path = self.runtime / 'qmp'
        self.display_path = self.runtime / 'display'
        self.base = self.root / 'images' / (self.manifest['image']['sha256'] + '.qcow2')
        self.disk = self.directory / 'disk.qcow2'
        self.key = self.directory / 'developer-key'

    @contextlib.contextmanager
    def lock(self):
        with open(self.runtime / 'lock', 'a') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise TesterError('This session is already running or being prepared.') from exc
            yield stream

    def tools(self):
        required = ['qemu-img', 'qemu-system-' + self.manifest['arch'], 'ssh-keygen', 'ssh', 'xorriso']
        return {x: shutil.which(x) for x in required}

    def prepare(self, report=print):
        with self.lock():
            self._prepare(report)

    def _prepare(self, report):
        missing = [k for k, v in self.tools().items() if not v]
        if missing:
            raise TesterError('Install required tools: ' + ', '.join(missing))
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.base.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        saved = self.directory / 'manifest.json'
        if saved.exists() and json.loads(saved.read_text()) != self.manifest:
            raise TesterError('This id already belongs to another image. Use a new manifest id.')
        # Pin identity before any disk/key mutation, including interrupted first preparation.
        saved.write_text(json.dumps(self.manifest, indent=2) + '\n')
        image = self.manifest['image']
        if not self.base.exists():
            if shutil.disk_usage(self.root).free < image['bytes'] + 2 * 1024**3:
                raise TesterError('Insufficient space for image plus 2 GiB working reserve.')
            report('Fetching and verifying guest image…')
            # Unique temporary paths allow two different sessions to share this cache safely.
            with tempfile.NamedTemporaryFile(dir=self.base.parent, delete=False) as output:
                partial = Path(output.name)
                try:
                    with build_opener(HTTPSRedirects).open(image['url'], timeout=30) as source:
                        if urlparse(image['url']).scheme == 'https' and urlparse(source.url).scheme != 'https':
                            raise TesterError('Refusing a download redirected away from HTTPS.')
                        count = 0
                        while chunk := source.read(1024 * 1024):
                            count += len(chunk)
                            if count > image['bytes']:
                                raise TesterError('Download exceeds declared image size.')
                            output.write(chunk)
                    output.flush()
                    if count != image['bytes'] or digest(partial) != image['sha256']:
                        raise TesterError('Guest image size or SHA-256 mismatch.')
                    info = json.loads(subprocess.check_output(['qemu-img', 'info', '--output=json', str(partial)]))
                    details = info.get('format-specific', {}).get('data', {})
                    if (info['format'] != 'qcow2' or info.get('backing-filename') or
                            info.get('data-file') or details.get('data-file') or
                            info.get('encrypted') or details.get('encrypt')):
                        raise TesterError('Image must be a standalone, unencrypted qcow2 disk.')
                    if info.get('virtual-size', 0) > 256 * 1024**3:
                        raise TesterError('Guest virtual disk exceeds 256 GiB.')
                    partial.chmod(0o444)
                    partial.replace(self.base)
                finally:
                    partial.unlink(missing_ok=True)
        # Verify cached inputs too. Do not run a different image under a trusted build label.
        if self.base.stat().st_size != image['bytes'] or digest(self.base) != image['sha256']:
            raise TesterError('Cached image changed; remove only that cache file and prepare again.')
        if not self.disk.exists():
            run(['qemu-img', 'create', '-q', '-f', 'qcow2', '-F', 'qcow2', '-b', self.base, self.disk])
        if not self.key.exists():
            run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', self.key])
        if not (self.directory / 'seed.iso').exists():
            user = self.manifest['user']
            config = {'users': [{'name': user, 'groups': ['wheel'], 'shell': '/bin/bash',
                                 'lock_passwd': True, 'sudo': ['ALL=(ALL) NOPASSWD:ALL'],
                                 'ssh_authorized_keys': [Path(str(self.key) + '.pub').read_text().strip()]}],
                      'ssh_pwauth': False, 'disable_root': True,
                      'write_files': [{'path': '/etc/luma-tester-target', 'permissions': '0644',
                                       'content': self.manifest['target'] + '\n'}]}
            # cloud-init accepts JSON as a YAML subset.
            (self.directory / 'user-data').write_text('#cloud-config\n' + json.dumps(config))
            (self.directory / 'meta-data').write_text(json.dumps({'instance-id': self.manifest['id']}))
            run(['xorriso', '-as', 'mkisofs', '-quiet', '-o', self.directory / 'seed.iso.new',
                 '-V', 'cidata', '-J', '-r', self.directory / 'user-data', self.directory / 'meta-data'])
            (self.directory / 'seed.iso.new').replace(self.directory / 'seed.iso')
        report('Guest image and private developer session are ready.')

    def firmware(self, override=None):
        if self.manifest['boot'] == 'bios':
            return None
        choices = ([override] if override else []) + (
            ['/usr/share/edk2/aarch64/QEMU_EFI.fd', '/usr/share/AAVMF/AAVMF_CODE.fd']
            if self.manifest['arch'] == 'aarch64' else
            ['/usr/share/edk2/ovmf/OVMF_CODE.fd', '/usr/share/OVMF/OVMF_CODE.fd'])
        for candidate in choices:
            if Path(candidate).is_file():
                return str(Path(candidate).resolve())
        raise TesterError('UEFI firmware missing; install edk2 or supply --firmware PATH.')

    def command(self, port=2228, memory=4096, cpus=4, headless=False, firmware=None, software=False, embedded=False):
        if not 1024 <= memory <= 16384 or not 1 <= cpus <= 16 or not 1024 <= port <= 65535:
            raise TesterError('Use memory 1024–16384 MiB, CPUs 1–16, SSH port 1024–65535.')
        arch = self.manifest['arch']
        native = platform.machine() in ({'aarch64', 'arm64'} if arch == 'aarch64' else {'x86_64', 'AMD64'})
        kvm = native and not software and os.access('/dev/kvm', os.R_OK | os.W_OK)
        width, height = (432, 960) if self.manifest['target'] == 'mobile' else (1440, 900)
        argv = ['qemu-system-' + arch, '-name', 'Luma ' + self.manifest['target'].title(),
                '-machine', 'virt,gic-version=3' if arch == 'aarch64' else 'q35',
                '-accel', 'kvm' if kvm else 'tcg,thread=multi', '-cpu', 'host' if kvm else 'max',
                '-m', str(memory), '-smp', str(cpus), '-nic',
                f'user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:{port}-:22',
                '-vga', 'none', '-device', f'virtio-gpu-pci,edid=on,xres={width},yres={height}',
                '-device', 'virtio-keyboard-pci', '-device', 'virtio-tablet-pci',
                '-device', 'virtio-rng-pci', '-qmp', f'unix:{self.qmp_path},server=on,wait=off',
                '-monitor', 'none', '-serial', 'file:' + str(self.directory / 'console.log'),
                '-display', 'none' if headless or embedded else 'gtk,zoom-to-fit=on']
        if embedded:
            argv += ['-vnc', 'unix:' + str(self.display_path)]
        for name, path, fmt, readonly in [('os', self.disk, 'qcow2', False),
                                           ('seed', self.directory / 'seed.iso', 'raw', True)]:
            block = {'driver': fmt, 'node-name': name, 'read-only': readonly, 'discard': 'unmap',
                     'file': {'driver': 'file', 'filename': str(path)}}
            argv += ['-blockdev', json.dumps(block), '-device', f'virtio-blk-pci,drive={name}']
        bios = self.firmware(firmware)
        if bios:
            argv += ['-bios', bios]
        return argv

    def start(self, **options):
        if platform.system() != 'Linux':
            raise TesterError('This first launcher supports Linux hosts. macOS and Windows are planned.')
        with self.lock() as lease:
            self._prepare(print)
            command = self.command(**options)
            # The exclusive lock proves any old endpoint belongs to a stopped session.
            self.qmp_path.unlink(missing_ok=True)
            self.display_path.unlink(missing_ok=True)
            (self.directory / 'connection.json').write_text(json.dumps({'port': options.get('port', 2228)}))
            (self.directory / 'command.json').write_text(json.dumps(command, indent=2))
            print('Starting ' + self.manifest['build'] + '; acceleration: ' + command[command.index('-accel') + 1], flush=True)
            # Preserve the lease in QEMU if the supervisor dies unexpectedly.
            child = subprocess.Popen(command, pass_fds=(lease.fileno(),))
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

    def qmp(self, execute, arguments=None):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(str(self.qmp_path))
            with sock.makefile('rwb', buffering=0) as stream:
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

    def status(self):
        try:
            return self.qmp('query-status')['status']
        except (OSError, TesterError):
            return 'stopped'

    def shutdown(self):
        # Luma Mobile owns the virtual power button as display sleep/wake.
        # An ACPI button press is therefore not a reliable shutdown request.
        if self.status() in ('paused', 'prelaunch'):
            self.qmp('cont')
        result = subprocess.run(self.ssh(['sudo', 'systemctl', '--no-block', 'poweroff']),
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True, timeout=15)
        if result.returncode != 0:
            raise TesterError('Could not request guest shutdown: ' + result.stderr.strip()[-1024:])

    def screenshot(self, destination):
        destination = Path(destination).resolve()
        if destination.exists():
            raise TesterError('Screenshot destination already exists.')
        self.qmp('screendump', {'filename': str(destination)})
        return destination

    def ssh(self, command):
        if self.status() == 'stopped':
            raise TesterError('Start the guest before connecting.')
        port = json.loads((self.directory / 'connection.json').read_text())['port']
        argv = ['ssh', '-F', '/dev/null', '-i', str(self.key), '-p', str(port), '-o', 'BatchMode=yes',
                '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=5',
                '-o', 'StrictHostKeyChecking=accept-new', '-o',
                'UserKnownHostsFile=' + str(self.directory / 'known_hosts'),
                self.manifest['user'] + '@127.0.0.1']
        # OpenSSH joins arguments into a shell command: quote every guest argument.
        return argv + ([shlex.join(command)] if command else [])

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
                                        stderr=subprocess.PIPE, text=True)
                if result.returncode == 0:
                    return
                last_error = result.stderr.strip()[-1024:] or 'SSH exited ' + str(result.returncode)
            except TesterError as exc:
                last_error = str(exc)
            time.sleep(2)
        raise TesterError('Guest SSH was not ready; inspect console.log. Last result: ' + last_error)

    def wait_graphical(self, timeout=180):
        """Wait for the real graphical session, not merely an open SSH port."""
        self.wait(timeout)
        deadline = time.monotonic() + timeout
        check = """import json, os
from pathlib import Path
runtime = Path(os.environ['XDG_RUNTIME_DIR'])
ready = json.loads((runtime / 'luma-tester/ready.json').read_text())
assert ready['boot_id'] == Path('/proc/sys/kernel/random/boot_id').read_text().strip()
assert ready['target'] == Path('/etc/luma-tester-target').read_text().strip()
assert ready['width'] > 0 and ready['height'] > 0
if ready['target'] == 'mobile':
    assert ready['height'] > ready['width']
"""
        while time.monotonic() < deadline:
            if subprocess.call(self.ssh(['python3', '-c', check]), stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL) == 0:
                return
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
                run(self.ssh(['systemd-run', '--user', '--collect', '--unit=luma-tester-app',
                              '--property=WorkingDirectory=%h/Projects/app', '--', *command]))
                print('App restarted from Projects/app. Guest journal: journalctl --user -u luma-tester-app', flush=True)
                previous = snapshot
            if once:
                return
            time.sleep(1)
