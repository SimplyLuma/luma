# SPDX-License-Identifier: Apache-2.0
"""Fixed, signed Connect UI access to its host-owned cloud sync engine."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import selectors
import subprocess
import threading
import time

from .connect_cloud_contract import command_plan

APP = 'org.projectluma.Connect'
METHOD = 'ConnectCloudRequest'
LIMIT = 262144


def require_connect_if_sandbox(connection, sender, method):
    """Existing native session callers remain native; sandboxes need signed UI."""
    from gi.repository import GLib, Gio
    def credential(method):
        return connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', method, GLib.Variant('(s)', (sender,)),
            GLib.VariantType.new('(u)'), Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    uid, pid = credential('GetConnectionUnixUser'), credential('GetConnectionUnixProcessID')
    if uid != os.getuid() or pid <= 0:
        raise PermissionError('Connect is owned by this user session.')
    from luma_installer.app_data_broker import _start, authenticate
    before = _start(pid)
    try:
        with Path(f'/proc/{pid}/root/.flatpak-info').open() as stream:
            stream.read(1)
    except FileNotFoundError:
        if _start(pid) != before: raise PermissionError('Connect caller changed.')
        return
    app = authenticate(connection, sender)
    phone_calls = {'GetCallState','ExchangeCall','StartCallAudio','StopCallAudio','SetCallAudioMuted'}
    if app != APP and not (app == 'org.projectluma.Phone' and method in phone_calls):
        raise PermissionError('Only the installed Connect interface may change this account.')


def execute(arguments, timeout, input_text, cancelled):
    """Bound native output/time without spawning a thread per incoming request."""
    process = subprocess.Popen(['/usr/bin/luma-connect-sync', *arguments],
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    selector = selectors.DefaultSelector()
    output = {'stdout': bytearray(), 'stderr': bytearray()}
    try:
        if input_text is not None:
            process.stdin.write(input_text.encode()); process.stdin.close()
        for label in output:
            pipe = getattr(process, label); os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, label)
        deadline = time.monotonic() + timeout
        while selector.get_map():
            if cancelled() or time.monotonic() >= deadline:
                raise TimeoutError('Luma Connect took too long to answer.')
            for key, _mask in selector.select(0.1):
                block = os.read(key.fd, 65536)
                if not block: selector.unregister(key.fileobj); continue
                output[key.data].extend(block)
                if sum(map(len, output.values())) > LIMIT:
                    raise ValueError('Luma Connect response exceeded its size bound.')
        code = process.wait(timeout=max(0.1, deadline-time.monotonic()))
        return code, *(bytes(output[key]).decode('utf-8', errors='replace') for key in ('stdout', 'stderr'))
    finally:
        selector.close()
        if process.poll() is None:
            import signal
            os.killpg(process.pid, signal.SIGKILL); process.wait()
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None: pipe.close()


class ConnectUIBroker:
    def __init__(self, *, authenticate=None, runner=execute):
        if authenticate is None:
            from luma_installer.app_data_broker import authenticate
        self.authenticate = authenticate; self.runner = runner
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='connect-ui')
        self.slots = threading.BoundedSemaphore(2); self.serial = threading.Lock()
        self.closed = False

    def dispatch(self, connection, sender, values, invocation, GLib):
        try:
            if self.closed or self.authenticate(connection, sender) != APP: raise PermissionError()
            if len(values) != 1 or not isinstance(values[0], str) or len(values[0].encode()) > 8192: raise ValueError()
            plan = command_plan(json.loads(values[0]))
            if not self.slots.acquire(blocking=False): raise RuntimeError()
        except Exception:
            invocation.return_dbus_error('org.projectluma.Connect1.UI.Refused', 'Connect access is unavailable.'); return
        def run():
            try:
                with self.serial:
                    if self.closed or self.authenticate(connection, sender) != APP: raise PermissionError()
                    code, out, err = self.runner(*plan, lambda: self.closed)
                    # A status/profile response carries only the existing public
                    # CLI report, never device.json/enrollment bearer material.
                    problem = None if code == 0 else ('This computer needs to be connected to Luma Connect again.'
                        if code == 3 else (err or out).strip().splitlines()[-1:][0] if (err or out).strip()
                        else 'Luma Connect could not be reached. Try again.')
                    response = json.dumps({'code': code, 'problem': problem, 'output': out}, allow_nan=False)
                    if len(response.encode()) > LIMIT*2: raise ValueError()
                GLib.idle_add(lambda: (invocation.return_value(GLib.Variant('(s)', (response,))), False)[1])
            except Exception:
                GLib.idle_add(lambda: (invocation.return_dbus_error('org.projectluma.Connect1.UI.Unavailable',
                    'Luma Connect information is unavailable.'), False)[1])
            finally: self.slots.release()
        try: self.pool.submit(run)
        except Exception:
            self.slots.release(); invocation.return_dbus_error('org.projectluma.Connect1.UI.Unavailable', 'Connect is closing.')

    def close(self):
        self.closed = True; self.pool.shutdown(wait=True, cancel_futures=True)
