"""Luma Connect sync, as the Connect app sees it.

The sync engine is `luma-connect-sync` (prairie-core-apps). This module runs it
off the GTK thread and turns its exit codes into things a person can act on:
0 done, 1 try again (network or Luma Connect trouble), 3 connect this device
again. Everything shown about what a switch does comes from the engine's own
status report, so the words on screen are the ones its code guarantees.
"""
import json
import os
import shutil
import re
import shlex
import socket
import subprocess
import threading

from gi.repository import GLib

DEFAULT_HUB = 'https://hub.simplyluma.com'
HUB_CONNECT_PAGE = DEFAULT_HUB + '/connect'
RETRY = 1
RECONNECT = 3


def connect_problem(problem):
    """Keep older native broker replies consistent with the installed UI."""
    return problem.replace('Luma Cloud', 'Luma Connect') if isinstance(problem, str) else problem


def command():
    found = shutil.which('luma-connect-sync')
    if found:
        return found
    local = os.path.expanduser('~/.local/bin/luma-connect-sync')
    return local if os.access(local, os.X_OK) else None


def device_name(*, machine_info='/etc/machine-info', dmi='/sys/devices/virtual/dmi/id', hostname=None):
    """Suggest the computer's chosen name, then a usable firmware model.

    Reading persisted hostnamed state keeps opening a dialog independent of
    a synchronous system-bus round trip. This is only an editable suggestion;
    the text the person submits is never replaced or filtered here.
    """
    def read(path):
        try:
            with open(path, encoding='utf-8', errors='replace') as stream:
                return stream.read(4096).strip()
        except OSError:
            return ''

    def clean(value):
        return ''.join(character for character in value if character.isprintable()).strip()[:60]

    for line in read(machine_info).splitlines():
        if not line.startswith('PRETTY_HOSTNAME='):
            continue
        try:
            values = shlex.split(line.partition('=')[2])
        except ValueError:
            continue
        if len(values) == 1 and clean(values[0]):
            return clean(values[0])

    chosen = clean(socket.gethostname() if hostname is None else hostname)
    if chosen.casefold() not in {'', 'fedora', 'fedora.localdomain', 'localhost',
                                'localhost-live', 'localhost.localdomain', 'luma'}:
        return chosen
    placeholders = {'none', 'unknown', 'invalid', 'notapplicable', 'notspecified',
                    'notdefined', 'defaultstring', 'tobefilledbyoem', 'systemproductname',
                    'systemversion', 'systemfamily', 'type1productconfigid'}
    for field in ('product_version', 'product_name', 'product_family'):
        product = clean(read(os.path.join(dmi, field)))
        key = re.sub(r'[^a-z0-9]', '', product.casefold())
        if key and key not in placeholders:
            return product
    return 'This computer'


class CloudSync:
    """Runs one engine command at a time and reports back on the main loop."""

    def __init__(self):
        self._lock = threading.Lock()

    @property
    def available(self):
        return os.environ.get('FLATPAK_ID') == 'org.projectluma.Connect' or command() is not None

    def _run(self, arguments, timeout, done, input_text=None):
        if os.environ.get('FLATPAK_ID'):
            self._remote(arguments, timeout, done, input_text)
            return
        executable = command()
        if executable is None:
            GLib.idle_add(done, None, 'Luma Connect sync is not installed on this computer.', None)
            return

        def worker():
            with self._lock:
                try:
                    finished = subprocess.run([executable, *arguments], capture_output=True, text=True, timeout=timeout,
                                              input=input_text)
                    code, out, err = finished.returncode, finished.stdout, finished.stderr
                except subprocess.TimeoutExpired:
                    code, out, err = RETRY, '', 'Luma Connect took too long to answer.'
                except OSError as error:
                    code, out, err = RETRY, '', str(error)
            problem = None
            if code == RECONNECT:
                problem = 'This computer needs to be connected to Luma Connect again.'
            elif code != 0:
                last = (err or out).strip().splitlines()[-1:] or ['']
                problem = last[0].removeprefix('luma-connect-sync: ') or 'Luma Connect could not be reached. Try again.'
            GLib.idle_add(done, code, problem, out)

        threading.Thread(target=worker, daemon=True, name='luma-cloud-sync').start()

    def _remote(self, arguments, timeout, done, input_text):
        from gi.repository import Gio
        if os.environ.get('FLATPAK_ID') != 'org.projectluma.Connect':
            GLib.idle_add(done, None, 'Connect access is unavailable.', None)
            return
        try:
            if arguments == ['status', '--json']: request = {'operation':'status','values':{}}
            elif arguments == ['push']: request = {'operation':'sync','values':{}}
            elif arguments == ['invite']: request = {'operation':'invite','values':{}}
            elif len(arguments) == 3 and arguments[0] == 'service':
                request = {'operation':'service','values':{'id':arguments[1],'enabled':arguments[2]=='on'}}
            elif arguments[:1] == ['sign-out']:
                request = {'operation':'sign-out','values':{'device':arguments[2] if len(arguments)==3 else ''}}
            elif arguments == ['profile','set','--stdin','--json']:
                request = {'operation':'profile','values':json.loads(input_text)}
            elif (len(arguments)==7 or len(arguments)==8 and arguments[-1]=='--force') and arguments[:3] == ['enrol','--hub',DEFAULT_HUB] and arguments[3]=='--code' and arguments[5]=='--name':
                request = {'operation':'connect','values':{'code':arguments[4],'name':arguments[6]}}
            else: raise ValueError('Unsupported cloud operation')
            from .connect_cloud_contract import command_plan
            command_plan(request)  # same typed boundary, never a sandbox CLI fallback
        except Exception:
            GLib.idle_add(done, None, 'That Connect operation is unavailable.', None)
            return
        def connected(_source, result):
            try: connection = Gio.bus_get_finish(result)
            except Exception:
                done(None, 'Luma Connect could not be reached. Try again.', None); return
            def finished(connection, result):
                try:
                    response = json.loads(connection.call_finish(result).unpack()[0])
                    if (set(response) != {'code','problem','output'} or type(response['code']) is not int
                            or not isinstance(response['output'],str)
                            or response['problem'] is not None and not isinstance(response['problem'],str)):
                        raise ValueError('Invalid Connect response')
                    done(response['code'], connect_problem(response['problem']), response['output'])
                except Exception: done(None, 'Luma Connect could not be reached. Try again.', None)
            connection.call('org.projectluma.Connect1','/org/projectluma/Connect',
                'org.projectluma.Connect1','ConnectCloudRequest',
                GLib.Variant('(s)',(json.dumps(request),)), GLib.VariantType.new('(s)'),
                Gio.DBusCallFlags.NONE, (timeout+10)*1000, None, finished)
        Gio.bus_get(Gio.BusType.SESSION, None, connected)

    def status(self, callback):
        def done(code, problem, out):
            report = None
            if code == 0:
                try:
                    report = json.loads(out)
                except ValueError:
                    problem = 'Luma Connect sync gave an answer this app does not understand.'
            callback(report, problem)
            return False
        self._run(['status', '--json'], 45, done)

    def set_service(self, service, enabled, callback):
        self._run(['service', service, 'on' if enabled else 'off'], 180,
                  lambda code, problem, _out: (callback(problem), False)[1])

    def sign_out(self, device, callback):
        arguments = ['sign-out'] + (['--device', device] if device else [])
        self._run(arguments, 120, lambda code, problem, out: (callback(problem, out), False)[1])

    def sync_now(self, callback):
        self._run(['push'], 300, lambda code, problem, _out: (callback(problem), False)[1])

    def set_profile(self, changes, callback):
        """Change the account's name, email, phone number or discoverability.

        The changes travel on standard input, so a name, address or number never
        appears in the process list. callback(profile, problem): the profile as
        the hub now keeps it, or the hub's own words for why it refused.
        """
        def done(code, problem, out):
            profile = None
            if code == 0:
                try:
                    profile = json.loads(out)
                except ValueError:
                    problem = 'Luma Connect gave an answer this app does not understand.'
            if code == 0 and not isinstance(profile, dict):
                profile, problem = None, problem or 'Luma Connect gave an answer this app does not understand.'
            callback(profile, problem)
            return False
        self._run(['profile', 'set', '--stdin', '--json'], 60, done, input_text=json.dumps(changes))

    def invite(self, callback):
        """A single-use code, valid for ten minutes, that connects another device to this account."""
        def done(code, problem, out):
            words = (out or '').split()
            callback(words[0] if code == 0 and words else None, problem)
            return False
        self._run(['invite'], 60, done)

    def connect(self, code, name, callback):
        arguments = ['enrol', '--hub', DEFAULT_HUB, '--code', re.sub(r'\s+', '', code).upper(), '--name', name or device_name(), '--force']
        self._run(arguments, 60, lambda code_, problem, _out: (callback(problem), False)[1])
