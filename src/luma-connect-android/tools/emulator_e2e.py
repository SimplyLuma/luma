#!/usr/bin/env python3
"""End-to-end check of the Android app against the desktop companion modules.

Runs on a development host with an Android emulator attached through adb.
Everything is synthetic: a temporary desktop identity, loopback listeners and
the emulator's own notifications. The emulator reaches the host's loopback as
10.0.2.2; the host reaches the phone's listener through `adb forward`.

    PYTHONPATH=src/luma-continuity python3 src/luma-connect-android/tools/emulator_e2e.py \
        --adb ~/Library/Android/sdk/platform-tools/adb \
        --apk src/luma-connect-android/app/build/outputs/apk/play/debug/app-play-debug.apk

Never run it against a real identity directory or a personal phone.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import tempfile
import time

os.environ['LUMA_CONNECT_TEST_LOOPBACK'] = '1'
from luma_continuity.bootstrap import create_identity  # noqa: E402
from luma_continuity.companion import CompanionListener, DesktopAdapters, PairingSession, Registry, call  # noqa: E402

PACKAGE = 'org.projectluma.connect'
PHONE_PORT = 47811


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


def step(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}{"  " + detail if detail else ""}', flush=True)
    return ok


def wait(events, kind, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            event = events.get(timeout=max(.1, deadline - time.monotonic()))
        except queue.Empty:
            break
        if event[0] == kind:
            return event
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--adb', required=True)
    parser.add_argument('--apk', required=True)
    parser.add_argument('--screenshots', default=None)
    options = parser.parse_args()
    adb = Adb(os.path.expanduser(options.adb))
    results = []

    adb('wait-for-device')
    adb('install', '-r', '-g', options.apk, timeout=180)
    adb.shell('pm', 'clear', PACKAGE)
    adb.shell('pm', 'grant', PACKAGE, 'android.permission.POST_NOTIFICATIONS', check=False)
    adb.shell('cmd', 'notification', 'allow_listener', f'{PACKAGE}/{PACKAGE}.service.NotificationMirrorService')
    adb('forward', f'tcp:{PHONE_PORT}', f'tcp:{PHONE_PORT}')

    events = queue.Queue()
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        directory = root / 'identity'
        create_identity(directory)
        adapters = DesktopAdapters(
            directory, downloads=root / 'Downloads',
            notify=lambda kind, peer, value: events.put((kind, value)),
            clipboard_set=lambda text, sensitive: events.put(('clipboard', {'text': text, 'sensitive': sensitive})),
            clipboard_get=lambda: 'from the desktop',
            media=lambda peer, value: events.put(('media', value)),
            changed=lambda kind, peer: events.put(('changed', kind)))
        listener = CompanionListener(directory, addresses=['127.0.0.1'], adapter_factory=adapters.for_peer, port=0,
                                     after=adapters.after_request)
        port = listener.start()
        paired = queue.Queue()
        session = PairingSession(
            directory, name='Emulator Desk', addresses=['127.0.0.1'], listen_port=port,
            phone_to_desktop=['device.status', 'notifications.mirror', 'media.mirror', 'clipboard.write',
                              'clipboard.read', 'files.write', 'links.open', 'input.control'],
            desktop_to_phone=['device.ring', 'clipboard.write', 'links.open', 'files.write', 'notifications.act',
                              'media.control', 'camera.stream', 'screen.view'],
            on_paired=paired.put)
        uri = session.start().replace('host=127.0.0.1', 'host=10.0.2.2')
        try:
            adb.shell('am', 'start', '-W', '-a', 'android.intent.action.VIEW', '-d', f"'{uri}'", PACKAGE)
            try:
                result = paired.get(timeout=40)
            except queue.Empty:
                result = None
            results.append(step('pairing over pinned TLS with Keystore identity', result is not None,
                                result and f'SAS {result["sas"]}, phone pin {result["fingerprint"][:12]}…'))
            if result is None:
                return 1
            fingerprint = result['fingerprint']
            time.sleep(3)
            if options.screenshots:
                Path(options.screenshots).mkdir(parents=True, exist_ok=True)
                with open(Path(options.screenshots) / 'confirm.png', 'wb') as out:
                    out.write(subprocess.run([adb.binary, 'exec-out', 'screencap', '-p'], capture_output=True).stdout)

            # The phone's link service reports status when it starts.
            status = wait(events, 'changed', timeout=30)
            device = Registry(directory).active().get(fingerprint, {})
            results.append(step('phone reports battery and route (device.status)', status is not None and device.get('status') is not None,
                                json.dumps(device.get('status'))))

            # Phone notification mirrored to the desktop.
            adb.shell('cmd', 'notification', 'post', '-S', 'bigtext', '-t', "'Sam'", 'luma-e2e', "'Running late, start without me'")
            mirrored = None
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                event = wait(events, 'notification', timeout=max(1, deadline - time.monotonic()))
                if event and event[1].get('op') == 'post' and 'Running late' in event[1].get('text', ''):
                    mirrored = event[1]
                    break
            results.append(step('notification mirrored to desktop (notifications.mirror)', mirrored is not None,
                                mirrored and f'app={mirrored["app"]!r} title={mirrored["title"]!r}'))

            if mirrored:
                receipt = call(directory, fingerprint, 'notifications.act', {'key': mirrored['key'], 'action': 'dismiss'})
                removed = wait(events, 'notification', timeout=15)
                results.append(step('desktop dismisses phone notification (notifications.act)',
                                    receipt['result'] == {'state': 'complete'} and removed is not None and removed[1].get('op') == 'remove',
                                    json.dumps(receipt)))

            # Desktop to phone effects.
            receipt = call(directory, fingerprint, 'clipboard.write', {'text': 'copied on the desktop', 'sensitive': False})
            results.append(step('desktop writes phone clipboard (clipboard.write)', receipt['result'] == {'accepted': True}, json.dumps(receipt)))

            receipt = call(directory, fingerprint, 'device.ring', {'ring': True})
            ringing = 'luma' in adb.shell('dumpsys', 'notification', '--noredact').lower() and receipt['result'] == {'ringing': True}
            call(directory, fingerprint, 'device.ring', {'ring': False})
            results.append(step('desktop rings phone (device.ring)', ringing, json.dumps(receipt)))

            receipt = call(directory, fingerprint, 'links.open', {'url': 'https://projectluma.org/', 'title': 'Luma'})
            results.append(step('desktop sends link as notification (links.open)', receipt['result'] == {'accepted': True}, json.dumps(receipt)))

            data = secrets.token_bytes(600_000)
            transfer = secrets.token_hex(16)
            first = call(directory, fingerprint, 'files.write', {'transfer': transfer, 'name': 'luma-e2e.bin', 'size': len(data), 'offset': 0,
                                                                  'data': base64.b64encode(data[:500_000]).decode(), 'final': False, 'sha256': None})
            last = call(directory, fingerprint, 'files.write', {'transfer': transfer, 'name': 'luma-e2e.bin', 'size': len(data), 'offset': 500_000,
                                                                 'data': base64.b64encode(data[500_000:]).decode(), 'final': True,
                                                                 'sha256': hashlib.sha256(data).hexdigest()})
            stored = last['result'].get('name', '')
            on_phone = subprocess.run([adb.binary, 'exec-out', 'cat', f'/sdcard/Download/{stored}'], capture_output=True).stdout
            adb.shell('rm', '-f', f"'/sdcard/Download/{stored}'", check=False)
            results.append(step('desktop sends file into phone Downloads (files.write)',
                                stored.startswith('luma-e2e') and hashlib.sha256(on_phone).hexdigest() == hashlib.sha256(data).hexdigest(),
                                f'first={first["result"]} phone bytes={len(on_phone)}'))

            receipt = call(directory, fingerprint, 'camera.stream', {'session': secrets.token_hex(16), 'port': 40000, 'facing': 'back',
                                                                      'width': 1280, 'height': 720, 'fps': 30, 'bitrate': 4_000_000, 'audio': False})
            results.append(step('camera request waits for a tap on the phone (camera.stream)', receipt['result'] == {'error': 'needs-user'}, json.dumps(receipt)))

            # Phone to desktop through the share sheet.
            adb.shell('am', 'start', '-a', 'android.intent.action.SEND', '-t', 'text/plain', '--es', 'android.intent.extra.TEXT',
                      "'shared from the phone'", '-n', f'{PACKAGE}/.share.ShareActivity')
            clip = wait(events, 'clipboard', timeout=20)
            results.append(step('phone shares text to desktop clipboard (clipboard.write)', clip is not None and clip[1]['text'] == 'shared from the phone',
                                clip and json.dumps(clip[1])))

            adb.shell('am', 'start', '-a', 'android.intent.action.SEND', '-t', 'text/plain', '--es', 'android.intent.extra.TEXT',
                      "'https://example.org/article'", '-n', f'{PACKAGE}/.share.ShareActivity')
            link = wait(events, 'link', timeout=20)
            results.append(step('phone shares link to desktop (links.open)', link is not None and link[1]['url'] == 'https://example.org/article'))

            # Revocation on the desktop closes the door on the next connection.
            Registry(directory).revoke(fingerprint)
            adb.shell('am', 'start', '-a', 'android.intent.action.SEND', '-t', 'text/plain', '--es', 'android.intent.extra.TEXT',
                      "'after revoke'", '-n', f'{PACKAGE}/.share.ShareActivity')
            leaked = wait(events, 'clipboard', timeout=8)
            results.append(step('revoked phone is refused', leaked is None))
            if options.screenshots:
                with open(Path(options.screenshots) / 'home.png', 'wb') as out:
                    adb.shell('am', 'start', '-n', f'{PACKAGE}/.ui.MainActivity')
                    time.sleep(2)
                    out.write(subprocess.run([adb.binary, 'exec-out', 'screencap', '-p'], capture_output=True).stdout)
        finally:
            listener.stop()
            session.cancel()
            adb('forward', '--remove', f'tcp:{PHONE_PORT}', check=False)
    passed = sum(1 for ok in results if ok)
    print(f'{passed}/{len(results)} checks passed')
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())
