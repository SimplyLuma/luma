# SPDX-License-Identifier: GPL-3.0-only
"""Disposable native-host architecture probe; never a shipping browser backend.

Reuses the existing Viola Chromium executable over its anonymous DevTools pipe.
No TCP listener, production profile, downloaded engine, or sandbox bypass.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from concurrent.futures import Future

class EnginePipe:
    def __init__(self, executable: Path, profile: Path, on_event, *, native_frame_probe=False, frame_socket=None, native_menu_probe=False, native_media_probe=False, on_closed=None, manual_browsing=False, reuse_profile=False):
        if manual_browsing and not native_frame_probe:
            raise ValueError('Manual browsing requires the native interactive host')
        if profile.exists() and any(profile.iterdir()) and not reuse_profile:
            raise ValueError("The architecture probe requires a new, empty profile")
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.executable, self.profile = executable, profile
        read_in, self.write_in = os.pipe()
        self.read_out, write_out = os.pipe()
        # Map anonymous descriptors in an exec-only child, without running
        # Python preexec_fn after GTK has started worker threads. Only OS-created
        # integer descriptors enter this script; executable/arguments use "$@".
        fd_setup = f'exec 3<&{read_in} 4>&{write_out}; '
        for original in (read_in, write_out):
            if original not in (3, 4):
                fd_setup += f'exec {original}>&-; '
        fd_setup += 'exec "$@"'
        self.native_new_tabs = native_frame_probe
        self.log = (profile.parent / (profile.name + '.log')).open('wb')
        from engine_display import EngineDisplay
        self.display = EngineDisplay(self.log) if native_frame_probe else None
        process_environment = self.display.environment() if self.display else os.environ.copy()
        if os.environ.get('VIOLA_QA_WINDOWS') == '1':
            process_environment['WAYLAND_DEBUG'] = '1'
        # This Chromium Wayland build rejects Vulkan surface creation, and
        # its Vulkan copy-output cleanup crashed on the actual Intel display.
        # Keep GPU/DMA-BUF rendering through ANGLE OpenGL instead.
        probe_flags = ['--viola-native-frame-probe', '--enable-logging=stderr',
                       '--disable-features=Vulkan', '--use-angle=gl',
                       # Chromium 151 names: these are already default-on with
                       # USE_VAAPI. Pin the supported GL path in our launcher;
                       # obsolete VaapiVideoDecoder flags do not select it.
                       '--enable-features=AcceleratedVideoDecoder,AcceleratedVideoDecodeLinuxGL,AcceleratedVideoDecodeLinuxZeroCopyGL',
                       '--force-device-scale-factor=1'] if native_frame_probe else []
        if manual_browsing:
            # This user-operated browser uses the pipe as internal IPC, not a
            # WebDriver session. Keep QA's normal automation marker unchanged.
            probe_flags.append('--disable-blink-features=AutomationControlled')
        if native_media_probe:
            probe_flags.append('--viola-native-media-probe')
        if native_menu_probe:
            probe_flags.append('--viola-native-menu-probe')
        if frame_socket is not None:
            if not native_frame_probe:
                raise ValueError('Frame transport is available only in the explicit probe')
            probe_flags.append('--viola-native-frame-socket=' + str(frame_socket))
        self.process = subprocess.Popen(
            ['/bin/bash', '-c', fd_setup, 'viola-native-probe',
             str(executable)] + ([] if native_frame_probe else ['--headless=new']) + [
             # Chromium's hidden native windows use GTK3; the visible host
             # owns GTK4. The implicit GTK4 backend crashes during startup
             # on Luma's patched toolkit in both released engines.
             '--enable-gpu', '--gtk-version=3',
             '--ozone-platform=wayland', '--remote-debugging-pipe',
             '--no-first-run', '--disable-default-apps',
             '--user-data-dir=' + str(profile), '--no-startup-window'] + probe_flags,
            stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log,
            env=process_environment,
            close_fds=True, pass_fds=(read_in, write_out),
        )
        os.close(read_in); os.close(write_out)
        self.on_event = on_event
        self.on_closed = on_closed
        self.closing = False
        self.pipe_closed = False
        self.pending = {}
        self.next_id = 0
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.transport_metrics = {'write_wait_ms_max': 0, 'write_ms_max': 0,
                                  'event_callback_ms_max': 0, 'responses': 0}
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def open_external(self, uri):
        from urllib.parse import urlsplit
        if urlsplit(uri).scheme not in ('http', 'https', 'file'):
            return False
        if self.process.poll() is not None:
            return False
        # Reuse Chromium's singleton dispatch: it owns Mini routing, profile
        # cookies, redirects and callbacks. This process only forwards the URL.
        try:
            result = subprocess.run([str(self.executable),
            '--user-data-dir=' + str(self.profile), '--no-first-run', '--', uri],
            env=self.display.environment() if self.display else os.environ.copy(),
                stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log, timeout=12)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def request(self, method, params=None, session=None):
        # Serialize complete commands, but never hold the response registry
        # lock across a blocking pipe write. Chromium must be able to finish
        # earlier requests while the outgoing command pipe is backpressured.
        waiting = time.monotonic()
        with self.write_lock:
            writing = time.monotonic()
            metrics = getattr(self, 'transport_metrics', None)
            if metrics is not None:
                metrics['write_wait_ms_max'] = max(metrics['write_wait_ms_max'], (writing - waiting) * 1000)
            with self.lock:
                if self.pipe_closed:
                    raise RuntimeError('Viola engine pipe closed')
                self.next_id += 1
                key = self.next_id
                future = Future()
                future.command_id = key
                self.pending[key] = future
            message = {'id': key, 'method': method, 'params': params or {}}
            if session:
                message['sessionId'] = session
            try:
                data = json.dumps(message, separators=(',', ':')).encode() + b'\0'
                while data:
                    n = os.write(self.write_in, data)
                    data = data[n:]
                if metrics is not None:
                    metrics['write_ms_max'] = max(metrics['write_ms_max'], (time.monotonic() - writing) * 1000)
            except Exception as error:
                with self.lock:
                    abandoned = self.pending.pop(key, None)
                if abandoned and not abandoned.done():
                    abandoned.set_exception(error)
                raise
            return future

    def call(self, method, params=None, session=None, timeout=15):
        future = self.request(method, params, session)
        try:
            return future.result(timeout)
        except TimeoutError as error:
            import traceback
            from command_timeout import EngineCommandTimeout
            with self.lock:
                if self.pending.get(future.command_id) is future:
                    self.pending.pop(future.command_id)
            callers = [{'file': Path(frame.filename).name, 'function': frame.name,
                        'line': frame.lineno}
                       for frame in traceback.extract_stack(limit=8)[:-1]]
            raise EngineCommandTimeout(method, timeout, callers) from error

    def _read(self):
        buffer = b''
        try:
            while data := os.read(self.read_out, 65536):
                buffer += data
                while b'\0' in buffer:
                    record, buffer = buffer.split(b'\0', 1)
                    if not record:
                        continue
                    message = json.loads(record)
                    if 'id' in message:
                        with self.lock:
                            future = self.pending.pop(message['id'], None)
                        if future:
                            metrics = getattr(self, 'transport_metrics', None)
                            if metrics is not None:
                                metrics['responses'] += 1
                            if 'error' in message:
                                future.set_exception(RuntimeError(str(message['error'])))
                            else:
                                future.set_result(message.get('result', {}))
                    else:
                        began = time.monotonic()
                        self.on_event(message)
                        metrics = getattr(self, 'transport_metrics', None)
                        if metrics is not None:
                            metrics['event_callback_ms_max'] = max(metrics['event_callback_ms_max'], (time.monotonic() - began) * 1000)
        finally:
            with self.lock:
                self.pipe_closed = True
                pending, self.pending = self.pending, {}
            for future in pending.values():
                if not future.done():
                    future.set_exception(RuntimeError('Viola engine pipe closed'))
            if self.on_closed and not self.closing:
                self.on_closed()

    def close(self):
        if self.closing:
            return
        self.closing = True
        if self.process.poll() is None:
            try:
                self.call('Browser.close', timeout=2)
            except Exception:
                pass
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
        self.reader.join(timeout=2)
        os.close(self.write_in); os.close(self.read_out)
        if self.display:
            self.display.close()
        self.log.close()
