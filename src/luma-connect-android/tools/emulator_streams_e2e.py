#!/usr/bin/env python3
"""End-to-end check of `luma-input/1` and `luma-files/1` between the Android app and the desktop modules.

Same setup as `emulator_e2e.py`: a synthetic desktop identity on the host's loopback,
which the emulator reaches as 10.0.2.2, and `adb forward` for host-to-phone
connections (the phone's one-shot file listener port is forwarded when an offer
returns it). Checks:

- desktop to phone file stream, interrupted and resumed with the same transfer
- phone to desktop file stream through the share sheet
- the trackpad screen opens a `luma-input/1` stream and its events reach the injector

    PYTHONPATH=src/luma-continuity python3 src/luma-connect-android/tools/emulator_streams_e2e.py \\
        --adb ~/Library/Android/sdk/platform-tools/adb \\
        --apk src/luma-connect-android/app/build/outputs/apk/play/debug/app-play-debug.apk

It clears the app's data. Never run it against a real identity directory or a personal phone.
"""
import argparse
import hashlib
import os
from pathlib import Path
import queue
import re
import secrets
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ElementTree

os.environ['LUMA_CONNECT_TEST_LOOPBACK'] = '1'
from luma_continuity.bootstrap import create_identity  # noqa: E402
from luma_continuity.companion import CompanionListener, DesktopAdapters, PairingSession, Registry, call  # noqa: E402
from luma_continuity.companion_desktop import Cancelled  # noqa: E402
from luma_continuity.companion_streams import FileStreams, InputStreams, send_file_stream, stream_adapters  # noqa: E402

PACKAGE = 'org.projectluma.connect'
PHONE_PORT = 47811
LAST_SCREEN = []


class Adb:
    def __init__(self, binary):
        self.binary = binary

    def __call__(self, *args, check=True, timeout=60):
        result = subprocess.run([self.binary, *args], capture_output=True, text=True, timeout=timeout)
        if check and result.returncode != 0:
            raise RuntimeError(f'adb {" ".join(args)} failed: {result.stderr.strip()}')
        return result.stdout

    def shell(self, *args, **options):
        return self('shell', *args, **options)

    def bytes(self, *args):
        return subprocess.run([self.binary, 'exec-out', *args], capture_output=True, timeout=120).stdout


def step(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}{"  " + detail if detail else ""}', flush=True)
    return ok


def wait_for(predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.1)
    return predicate()


def bounds(adb, predicate):
    """Center of the first UI node matching `predicate(node)` in a uiautomator dump."""
    adb.shell('uiautomator', 'dump', '/sdcard/luma-ui.xml', check=False)
    tree = ElementTree.fromstring(adb.bytes('cat', '/sdcard/luma-ui.xml').decode('utf-8', 'replace') or '<x/>')
    LAST_SCREEN[:] = [node.get('text') or node.get('content-desc') for node in tree.iter('node')
                      if node.get('text') or node.get('content-desc')]
    for node in tree.iter('node'):
        if predicate(node):
            left, top, right, bottom = map(int, re.findall(r'\d+', node.get('bounds', '')))
            return (left + right) // 2, (top + bottom) // 2, right - left, bottom - top
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--adb', required=True)
    parser.add_argument('--apk', required=True)
    options = parser.parse_args()
    adb = Adb(os.path.expanduser(options.adb))
    results, forwards = [], {PHONE_PORT}

    adb('wait-for-device')
    adb('install', '-r', '-g', options.apk, timeout=180)
    adb.shell('pm', 'clear', PACKAGE)
    adb.shell('pm', 'grant', PACKAGE, 'android.permission.POST_NOTIFICATIONS', check=False)
    adb('forward', f'tcp:{PHONE_PORT}', f'tcp:{PHONE_PORT}')

    events = queue.Queue()
    sessions = []
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        directory = root / 'identity'
        create_identity(directory)

        def inject(peer, batch):
            events.put(('input', batch))
            return True
        adapters = DesktopAdapters(directory, downloads=root / 'Downloads', input=inject,
                                   notify=lambda kind, peer, value: events.put((kind, value)))
        record = lambda session: sessions.append((session.kind, session.state, getattr(session, 'frames', None)))
        inputs = InputStreams(adapters, addresses=['127.0.0.1'], on_change=record)
        files = FileStreams(adapters, addresses=['127.0.0.1'], on_change=record)
        listener = CompanionListener(directory, addresses=['127.0.0.1'], port=0,
                                     adapter_factory=stream_adapters(adapters, inputs=inputs, files=files),
                                     after=adapters.after_request)
        port = listener.start()
        paired = queue.Queue()
        session = PairingSession(directory, name='Emulator Desk', addresses=['127.0.0.1'], listen_port=port,
                                 phone_to_desktop=['device.status', 'files.write', 'input.control'],
                                 desktop_to_phone=['files.write'], on_paired=paired.put)
        uri = session.start().replace('host=127.0.0.1', 'host=10.0.2.2')
        stored = None
        try:
            adb.shell('am', 'start', '-W', '-a', 'android.intent.action.VIEW', '-d', f"'{uri}'", PACKAGE)
            try:
                result = paired.get(timeout=40)
            except queue.Empty:
                result = None
            results.append(step('pairing', result is not None))
            if result is None:
                return 1
            fingerprint = result['fingerprint']
            # The link service reports status once its listener is up.
            reachable = wait_for(lambda: (Registry(directory).active().get(fingerprint) or {}).get('status') is not None, 30)
            results.append(step('phone link service is up (device.status)', reachable))
            time.sleep(1)

            # Desktop to phone: interrupted, then resumed with the same transfer.
            def forwarding_call(directory_, fingerprint_, capability, payload):
                receipt = call(directory_, fingerprint_, capability, payload)
                stream_port = (receipt.get('result') or {}).get('port')
                if isinstance(stream_port, int):
                    adb('forward', f'tcp:{stream_port}', f'tcp:{stream_port}')
                    forwards.add(stream_port)
                return receipt
            data = secrets.token_bytes(8 * 1024 * 1024)
            source = root / 'luma-stream-e2e.bin'
            source.write_bytes(data)
            transfer = secrets.token_hex(16)
            seen = [0]
            interrupted = False
            try:
                send_file_stream(directory, fingerprint, str(source), lambda sent, size: seen.__setitem__(0, sent),
                                 lambda: seen[0] >= 3 * 1024 * 1024, transfer=transfer, call=forwarding_call, attempts=1)
            except Cancelled:
                interrupted = True
            time.sleep(1)
            started = time.monotonic()
            sent = send_file_stream(directory, fingerprint, str(source), transfer=transfer, call=forwarding_call, attempts=1)
            elapsed = time.monotonic() - started
            stored = sent['name']
            on_phone = adb.bytes('cat', f'/sdcard/Download/{stored}')
            remaining = len(data) - sent['offset']
            results.append(step('desktop to phone file stream resumes after interruption (luma-files/1)',
                                interrupted and sent['offset'] > 0 and hashlib.sha256(on_phone).digest() == hashlib.sha256(data).digest(),
                                f'resumed at {sent["offset"]}, {remaining} bytes in {elapsed:.2f} s through adb forward'))

            # Phone to desktop: share the file the phone just stored (the app owns that MediaStore row).
            rows = adb.shell('content', 'query', '--uri', 'content://media/external/downloads', '--projection', '_id',
                             '--where', f"\"_display_name='{stored}'\"", check=False)
            row = re.search(r'_id=(\d+)', rows)
            shared = None
            if row:
                started = time.monotonic()
                adb.shell('am', 'start', '-a', 'android.intent.action.SEND', '-t', 'application/octet-stream', '--eu',
                          'android.intent.extra.STREAM', f'content://media/external/downloads/{row.group(1)}',
                          '-n', f'{PACKAGE}/.share.ShareActivity')
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline and shared is None:
                    try:
                        kind, value = events.get(timeout=1)
                    except queue.Empty:
                        continue
                    if kind == 'file':
                        shared = value
                elapsed = time.monotonic() - started
            desktop_copy = root / 'Downloads' / (shared or {}).get('name', '-')
            # The desktop notifies as it commits, just before the session records its final state.
            via_stream = wait_for(lambda: any(kind == 'files' and state == 'closed' for kind, state, _ in sessions), 10)
            results.append(step('phone shares a file to the desktop on a stream (luma-files/1)',
                                shared is not None and via_stream and desktop_copy.exists()
                                and hashlib.sha256(desktop_copy.read_bytes()).digest() == hashlib.sha256(data).digest(),
                                f'{elapsed:.2f} s including hashing on the phone, sessions={[x for x in sessions if x[0] == "files"]}, name={(shared or {}).get("name")}' if shared else f'row={bool(row)}'))

            # Trackpad: the screen opens a stream on first use.
            adb.shell('am', 'start', '-W', '-n', f'{PACKAGE}/.ui.MainActivity')
            time.sleep(2)
            confirm = bounds(adb, lambda node: node.get('text') == 'The codes match')
            if confirm:
                adb.shell('input', 'tap', str(confirm[0]), str(confirm[1]))
                time.sleep(1.5)
            button = bounds(adb, lambda node: node.get('text') == 'Trackpad' and node.get('clickable') == 'true') or \
                bounds(adb, lambda node: node.get('text') == 'Trackpad')
            pad = None
            if button:
                adb.shell('input', 'tap', str(button[0]), str(button[1]))
                time.sleep(1.5)
                pad = bounds(adb, lambda node: (node.get('content-desc') or '').startswith('Trackpad.'))
            moved = []
            if pad:
                x, y, width, height = pad
                for _ in range(3):
                    adb.shell('input', 'swipe', str(x - width // 4), str(y), str(x + width // 4), str(y + height // 8), '400')
                    time.sleep(.5)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    try:
                        kind, value = events.get(timeout=1)
                    except queue.Empty:
                        if moved and any(kind == 'input' and state == 'streaming' for kind, state, _ in sessions):
                            break
                        continue
                    if kind == 'input':
                        moved.extend(value)
            streamed = any(kind == 'input' and state == 'streaming' for kind, state, _ in sessions)
            results.append(step('trackpad events reach the desktop on a stream (luma-input/1)',
                                streamed and any(event['type'] == 'move' for event in moved),
                                f'button={bool(button)} pad={bool(pad)} events={len(moved)} sessions={[s for s in sessions if s[0] == "input"]}'
                                + ('' if pad else f' screen={LAST_SCREEN[:12]}')))
        finally:
            adb.shell('am', 'force-stop', PACKAGE, check=False)
            inputs.close_all()
            files.close_all()
            listener.stop()
            session.cancel()
            if stored:
                adb.shell('rm', '-f', f"'/sdcard/Download/{stored}'", check=False)
            adb.shell('rm', '-f', '/sdcard/luma-ui.xml', check=False)
            for forwarded in forwards:
                adb('forward', '--remove', f'tcp:{forwarded}', check=False)
    passed = sum(1 for ok in results if ok)
    print(f'{passed}/{len(results)} checks passed')
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())
