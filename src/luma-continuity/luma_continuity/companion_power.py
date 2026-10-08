"""Luma Connect Power Mode: Android wireless debugging and scrcpy (ADR-021, contract section 6).

Power Mode is started by the desktop owner, never by the phone. The desktop shows a
wireless-debugging QR code, the phone's Settings camera handler starts a pairing
server advertised under the requested mDNS instance name, and the desktop runs the
system `adb` and `scrcpy` binaries with explicit argument lists. Nothing is vendored
or downloaded. Every method returns a `Result` with a short error code; raw
subprocess output never reaches users.

Sources (fetched 2026-09-13):

- QR format and mDNS service names: AOSP adb "Architecture of ADB Wifi",
  https://android.googlesource.com/platform/packages/modules/adb/+/HEAD/docs/dev/adb_wifi.md
  (`WIFI:T:ADB;S:<instance>;P:<password>;;`, instance `studio-<RANDOM-10>`,
  `_adb-tls-pairing._tcp`, `_adb-tls-connect._tcp`).
- AirSync (MPL-2.0, studied only, no code used) uses `adb-wireless-NNNNNN` with an
  8-digit password and browses `_adb-tls-pairing._tcp.` for that instance:
  https://github.com/sameerasw/airsync-mac/blob/b3b4b5503cd748973a9d21ae66dd5b1d304805d8/airsync-mac/Core/Util/CLI/ADBPairingManager.swift
- `adb mdns services` prints "List of discovered mdns services" then
  `"%s\t%s\t%s:%u\n"` (instance, service type, IPv4, port); the Bonjour backend
  reports types with a trailing dot. `adb pair`/`connect`/`disconnect` print the
  server's reply and exit 0 whenever the server answered:
  https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/client/commandline.cpp
  https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/client/transport_mdns.cpp
  https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/client/adb_wifi.cpp
  https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/adb.cpp
- scrcpy flags (master 19c1261d, release v4.1): doc/virtual-display.md,
  doc/control.md, doc/audio.md, doc/video.md, doc/keyboard.md, doc/mouse.md,
  doc/window.md, doc/device.md, app/scrcpy.1 at https://github.com/Genymobile/scrcpy.
  `--new-display` and `--start-app` arrived in v3.0; `--flex-display` in v4.0.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import string
import subprocess
import threading
import time

from .policy import DIGEST

PAIRING_SERVICE = '_adb-tls-pairing._tcp'
CONNECT_SERVICE = '_adb-tls-connect._tcp'
LEGACY_SERVICE = '_adb._tcp'
SERVICES = frozenset({PAIRING_SERVICE, CONNECT_SERVICE, LEGACY_SERVICE})
LINKS_FILE = 'power-mode.json'
MAX_LINKS_FILE = 64 * 1024

PAIR_TIMEOUT = 30
CONNECT_TIMEOUT = 20
QUERY_TIMEOUT = 10
VERSION_TIMEOUT = 5

_INSTANCE = re.compile(r'[^\x00-\x1f\x7f]{1,255}\Z')
_MDNS_LINE = re.compile(r'(?P<name>\S.*?)\s+(?P<service>_adb(?:-tls-(?:pairing|connect))?\._tcp)\.?\s+'
                        r'(?P<host>[0-9.]+):(?P<port>[0-9]{1,5})\s*\Z')
_PASSWORD = re.compile(r'[A-Za-z0-9]{6,64}\Z')
_SERIAL = re.compile(r'[A-Za-z0-9\[][A-Za-z0-9._:\[\]-]{0,254}\Z')
_PACKAGE = re.compile(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+\Z')
_VERSION = re.compile(r'scrcpy v?([0-9]+)\.([0-9]+)(?:\.([0-9]+))?')
_TITLE_UNSAFE = re.compile(r'[\x00-\x1f\x7f]')


@dataclass(frozen=True)
class Result:
    ok: bool
    code: str
    data: dict = field(default_factory=dict)


def _ok(code='ok', **data):
    return Result(True, code, data)


def _error(code, **data):
    return Result(False, code, data)


# -- QR invitation ------------------------------------------------------------

def wifi_pairing_qr():
    """Return `(qr_text, service_name, password)` for Settings > Wireless debugging > Pair with QR code.

    Matches Android Studio: instance `studio-` plus ten random characters. Only
    [a-z0-9] and [A-Za-z0-9] are used so no WIFI-QR escaping (`\\ ; , : "`) is needed.
    The 12-character password carries about 71 bits from `secrets`.
    """
    service = 'studio-' + ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(10))
    password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
    return f'WIFI:T:ADB;S:{service};P:{password};;', service, password


# -- adb output parsing --------------------------------------------------------

@dataclass(frozen=True)
class MdnsService:
    name: str
    service: str
    host: str
    port: int


def parse_mdns_services(text):
    """Parse `adb mdns services` output. Malformed, IPv6-less or unknown lines are skipped."""
    services = []
    if not isinstance(text, str):
        return services
    for line in text.splitlines():
        match = _MDNS_LINE.fullmatch(line.strip())
        if not match or not _INSTANCE.fullmatch(match['name']):
            continue
        try:
            host = ipaddress.IPv4Address(match['host'])
        except ValueError:
            continue
        port = int(match['port'])
        if not 1 <= port <= 65535 or host.is_unspecified or host.is_multicast:
            continue
        services.append(MdnsService(match['name'], match['service'], str(host), port))
    return services


def find_pairing_service(services, service_name):
    """The pairing server the phone started for our QR code, if it has been discovered."""
    return next((item for item in services if item.service == PAIRING_SERVICE and item.name == service_name), None)


def find_connect_service(services, host):
    """The TLS connect service published by the phone at `host` (after pairing)."""
    return next((item for item in services if item.service == CONNECT_SERVICE and item.host == host), None)


def _host(host):
    value = ipaddress.ip_address(host)
    if value.is_unspecified or value.is_multicast:
        raise ValueError('unicast address required')
    return str(value)


def _endpoint(host, port):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('invalid port')
    host = _host(host)
    return f'[{host}]:{port}' if ':' in host else f'{host}:{port}'


def valid_serial(serial):
    return isinstance(serial, str) and bool(_SERIAL.fullmatch(serial))


# -- subprocess boundary -------------------------------------------------------

def run_process(args, *, input=None, timeout, env=None):
    """Default runner: no shell, captured text output, bounded time."""
    return subprocess.run(args, input=input, stdin=None if input is not None else subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=timeout, env=env, check=False)


def _invoke(runner, args, *, input=None, timeout, env=None):
    """Run and classify. Returns (Result-or-None, output) where a Result means failure."""
    try:
        completed = runner(args, input=input, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return _error('timeout'), ''
    except FileNotFoundError:
        return _error('binary-missing'), ''
    except (OSError, subprocess.SubprocessError, ValueError):
        return _error('launch-failed'), ''
    output = ((completed.stdout or '') + '\n' + (completed.stderr or '')).strip()
    return None, output


class Adb:
    """Narrow wrapper over the system `adb` for Power Mode.

    The ADB key stays in adb's default location (~/.android); it is shared with
    every other adb use on this account and is never deleted here.
    """

    def __init__(self, path=None, *, runner=run_process, which=shutil.which):
        self.path = path if path is not None else which('adb')
        self.runner = runner

    @property
    def available(self):
        return bool(self.path)

    def _run(self, arguments, *, timeout, input=None):
        if not self.path:
            return _error('adb-missing'), ''
        failure, output = _invoke(self.runner, [self.path, *arguments], input=input, timeout=timeout)
        if failure and failure.code == 'binary-missing':
            return _error('adb-missing'), ''
        return failure, output

    def discover(self):
        """Resolved adb mDNS services: `data['services']` is a list of `MdnsService`."""
        failure, output = self._run(['mdns', 'services'], timeout=QUERY_TIMEOUT)
        if failure:
            return failure
        if 'List of discovered mdns services' not in output:
            return _error('mdns-unavailable')
        return _ok(services=parse_mdns_services(output))

    def pair(self, host, port, password):
        """`adb pair HOST:PORT`, with the password on stdin so it never appears in argv."""
        try:
            endpoint = _endpoint(host, port)
        except ValueError:
            return _error('invalid-argument')
        if not isinstance(password, str) or not _PASSWORD.fullmatch(password):
            return _error('invalid-argument')
        failure, output = self._run(['pair', endpoint], input=password + '\n', timeout=PAIR_TIMEOUT)
        if failure:
            return failure
        match = re.search(r'Successfully paired to \S+ \[guid=([^\]\s]{1,128})\]', output)
        if match:
            return _ok('paired', guid=match.group(1))
        if 'Wrong password' in output:
            return _error('wrong-password')
        if 'Failed to parse address' in output or 'Invalid port' in output:
            return _error('invalid-argument')
        return _error('pair-failed')

    def connect(self, host, port):
        """`adb connect HOST:PORT`. `data['serial']` is the adb serial to use afterwards."""
        try:
            endpoint = _endpoint(host, port)
        except ValueError:
            return _error('invalid-argument')
        failure, output = self._run(['connect', endpoint], timeout=CONNECT_TIMEOUT)
        if failure:
            return failure
        for line in output.splitlines():
            match = re.fullmatch(r'(already )?connected to (\S+)', line.strip())
            if match and valid_serial(match.group(2)):
                return _ok('already-connected' if match.group(1) else 'connected', serial=match.group(2))
        return _error('connect-failed')

    def state(self, serial):
        """`adb -s SERIAL get-state`: ok with `data['state']` (for example `device`)."""
        if not valid_serial(serial):
            return _error('invalid-argument')
        failure, output = self._run(['-s', serial, 'get-state'], timeout=QUERY_TIMEOUT)
        if failure:
            return failure
        last = output.splitlines()[-1].strip() if output else ''
        if last in ('device', 'offline', 'unauthorized', 'bootloader', 'recovery', 'sideload'):
            return _ok(state=last)
        return _error('no-such-device')

    def forget(self, serial):
        """`adb disconnect SERIAL`. Idempotent: an unknown serial is reported as not connected."""
        if not valid_serial(serial):
            return _error('invalid-argument')
        failure, output = self._run(['disconnect', serial], timeout=QUERY_TIMEOUT)
        if failure:
            return failure
        if re.search(r'^disconnected ', output, re.MULTILINE):
            return _ok('disconnected')
        if 'no such device' in output:
            return _ok('not-connected')
        return _error('disconnect-failed')


# -- companion link -----------------------------------------------------------

class PowerLinks:
    """Maps companion fingerprints to Power Mode ADB serials in a private JSON file.

    Unpairing a companion calls `forget_companion`, which removes the link and
    disconnects ADB. The file lives beside the identity (`power-mode.json`, 0600).
    """

    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / LINKS_FILE
        self._lock = threading.Lock()

    def _check_directory(self):
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError('private identity directory required')

    def _load(self):
        self._check_directory()
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return {}
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
                or info.st_size > MAX_LINKS_FILE):
            raise PermissionError('private Power Mode link file required')
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            value = json.load(stream)
        if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('links'), dict):
            raise ValueError('invalid Power Mode link file')
        links = {}
        for peer, entry in value['links'].items():
            if (not DIGEST.fullmatch(peer) or not isinstance(entry, dict) or set(entry) != {'serial', 'linked_at'}
                    or not valid_serial(entry['serial']) or type(entry['linked_at']) is not int):
                raise ValueError('invalid Power Mode link file')
            links[peer] = entry
        return links

    def _save(self, links):
        data = json.dumps({'version': 1, 'links': links}, sort_keys=True, separators=(',', ':')).encode()
        if len(data) > MAX_LINKS_FILE:
            raise ValueError('too many Power Mode links')
        temporary = self.directory / ('.power-mode-' + secrets.token_hex(16))
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            temporary.unlink(missing_ok=True)

    def links(self):
        with self._lock:
            return {peer: entry['serial'] for peer, entry in self._load().items()}

    def serial_for(self, peer):
        return self.links().get(peer)

    def link(self, peer, serial, *, now=None):
        if not isinstance(peer, str) or not DIGEST.fullmatch(peer) or not valid_serial(serial):
            raise ValueError('invalid Power Mode link')
        with self._lock:
            links = self._load()
            links[peer] = {'serial': serial, 'linked_at': int(time.time() if now is None else now)}
            self._save(links)

    def unlink(self, peer):
        """Remove and return the linked serial, or None."""
        with self._lock:
            links = self._load()
            entry = links.pop(peer, None)
            if entry is not None:
                self._save(links)
        return entry['serial'] if entry else None


def forget_companion(directory, peer, *, adb=None):
    """Called when a companion is unpaired: drop its link and disconnect its ADB serial."""
    serial = PowerLinks(directory).unlink(peer)
    if serial is None:
        return _ok('not-linked')
    adb = adb or Adb()
    result = adb.forget(serial)
    return Result(result.ok, result.code, {**result.data, 'serial': serial})


# -- scrcpy -------------------------------------------------------------------

def _title(value):
    value = _TITLE_UNSAFE.sub('', value if isinstance(value, str) else '').strip()[:128]
    return value or 'Phone'


class ScrcpyLauncher:
    """Builds argument lists for the system `scrcpy` and starts it without a shell."""

    CODECS = ('h264', 'h265')
    INPUT_MODES = ('uhid', 'sdk', 'disabled')
    AUDIO_MODES = (True, False, 'dup')

    def __init__(self, path=None, *, adb_path=None, runner=run_process, popen=subprocess.Popen, which=shutil.which):
        self.path = path if path is not None else which('scrcpy')
        self.adb_path = adb_path
        self.runner, self.popen = runner, popen

    def version(self):
        """`scrcpy --version`: `data['version']` is `(major, minor, patch)`."""
        if not self.path:
            return _error('scrcpy-missing')
        failure, output = _invoke(self.runner, [self.path, '--version'], timeout=VERSION_TIMEOUT)
        if failure:
            return _error('scrcpy-missing') if failure.code == 'binary-missing' else failure
        match = _VERSION.search(output)
        if not match:
            return _error('unknown-version')
        return _ok(version=(int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)))

    @staticmethod
    def _common(serial, *, codec, bit_rate, max_size, audio, title):
        if not valid_serial(serial):
            raise ValueError('invalid serial')
        if codec not in ScrcpyLauncher.CODECS:
            raise ValueError('invalid codec')
        if audio not in ScrcpyLauncher.AUDIO_MODES or type(audio) not in (bool, str):
            raise ValueError('invalid audio mode')
        args = [f'--serial={serial}', f'--video-codec={codec}']
        if max_size is not None:
            if type(max_size) is not int or not 64 <= max_size <= 8192:
                raise ValueError('invalid max size')
            args.append(f'--max-size={max_size}')
        if bit_rate is not None:
            if type(bit_rate) is not int or not 100_000 <= bit_rate <= 200_000_000:
                raise ValueError('invalid bit rate')
            args.append(f'--video-bit-rate={bit_rate}')
        if audio is False:
            args.append('--no-audio')
        elif audio == 'dup':
            # Keep sound on the phone too; Android 13+, apps may opt out (doc/audio.md).
            args += ['--audio-source=playback', '--audio-dup']
        args.append(f'--window-title={_title(title)}')
        return args

    def mirror_args(self, serial, *, title, codec='h264', max_size=None, bit_rate=None, audio=True,
                    keyboard='uhid', mouse='uhid'):
        """Full-screen mirror. UHID mouse captures the pointer; LAlt/LSuper release it (scrcpy.1)."""
        if keyboard not in self.INPUT_MODES or mouse not in self.INPUT_MODES:
            raise ValueError('invalid input mode')
        return [self.path or 'scrcpy',
                *self._common(serial, codec=codec, bit_rate=bit_rate, max_size=max_size, audio=audio, title=title),
                f'--keyboard={keyboard}', f'--mouse={mouse}']

    def app_args(self, serial, package, *, title, width=1080, height=1920, dpi=420, codec='h264', bit_rate=None,
                 audio=False, decorations=False, flex=False):
        """One Android app in its own virtual display window (scrcpy >= 3.0; `flex` needs 4.0)."""
        if not isinstance(package, str) or not _PACKAGE.fullmatch(package) or len(package) > 255:
            raise ValueError('invalid package')
        for value, low, high in ((width, 64, 8192), (height, 64, 8192), (dpi, 72, 1000)):
            if type(value) is not int or not low <= value <= high:
                raise ValueError('invalid display geometry')
        args = [self.path or 'scrcpy',
                *self._common(serial, codec=codec, bit_rate=bit_rate, max_size=None, audio=audio, title=title),
                f'--new-display={width}x{height}/{dpi}', f'--start-app={package}']
        if not decorations:
            args.append('--no-vd-system-decorations')
        if flex:
            args.append('--flex-display')
        return args

    def _environment(self):
        environment = dict(os.environ)
        if self.adb_path:
            environment['ADB'] = self.adb_path  # scrcpy.1 ENVIRONMENT: "ADB  Path to adb."
        return environment

    def launch(self, args):
        """Start scrcpy detached from our stdio. `data['process']` is the Popen handle."""
        if not self.path:
            return _error('scrcpy-missing')
        if not isinstance(args, list) or not args or args[0] != self.path or any(not isinstance(item, str) for item in args):
            return _error('invalid-argument')
        try:
            process = self.popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 env=self._environment(), start_new_session=True)
        except FileNotFoundError:
            return _error('scrcpy-missing')
        except (OSError, ValueError, subprocess.SubprocessError):
            return _error('launch-failed')
        return _ok('launched', process=process)

    def mirror(self, serial, **options):
        try:
            args = self.mirror_args(serial, **options)
        except ValueError:
            return _error('invalid-argument')
        return self.launch(args)

    def app_window(self, serial, package, **options):
        version = self.version()
        if not version.ok:
            return version
        required = (4, 0, 0) if options.get('flex') else (3, 0, 0)
        if version.data['version'] < required:
            return _error('unsupported-version', version=version.data['version'])
        try:
            args = self.app_args(serial, package, **options)
        except ValueError:
            return _error('invalid-argument')
        return self.launch(args)
