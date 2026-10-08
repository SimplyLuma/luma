#!/usr/bin/env python3
"""Text messages through the `full` build on an emulator: messages.read and messages.send.

The emulator console delivers a synthetic SMS (`adb emu sms send`); the desktop side
reads the thread and sends a reply through the phone. Synthetic numbers only.

    PYTHONPATH=src/luma-continuity python3 src/luma-connect-android/tools/emulator_sms_e2e.py \
        --adb ~/Library/Android/sdk/platform-tools/adb \
        --apk src/luma-connect-android/app/build/outputs/apk/full/debug/app-full-debug.apk
"""
import argparse
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
from luma_continuity.companion import CompanionListener, DesktopAdapters, PairingSession, call  # noqa: E402

PACKAGE = 'org.projectluma.connect'
NUMBER = '5550100'


def adb(binary, *args, check=True, timeout=60):
    result = subprocess.run([binary, *args], capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError(f'adb {" ".join(args)}: {result.stderr.strip()}')
    return result.stdout


def step(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}{"  " + detail if detail else ""}', flush=True)
    return ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--adb', required=True)
    parser.add_argument('--apk', required=True)
    options = parser.parse_args()
    binary = os.path.expanduser(options.adb)
    adb(binary, 'install', '-r', '-g', options.apk, timeout=180)
    adb(binary, 'shell', 'pm', 'clear', PACKAGE)
    for permission in ('POST_NOTIFICATIONS', 'READ_SMS', 'SEND_SMS', 'READ_CONTACTS'):
        adb(binary, 'shell', 'pm', 'grant', PACKAGE, f'android.permission.{permission}', check=False)
    adb(binary, 'forward', 'tcp:47811', 'tcp:47811')
    results = []
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp) / 'identity'
        create_identity(directory)
        adapters = DesktopAdapters(directory, downloads=Path(temp) / 'Downloads')
        listener = CompanionListener(directory, addresses=['127.0.0.1'], adapter_factory=adapters.for_peer, port=0,
                                     after=adapters.after_request)
        port = listener.start()
        paired = queue.Queue()
        session = PairingSession(directory, name='Messages Desk', addresses=['127.0.0.1'], listen_port=port,
                                 phone_to_desktop=['device.status'], desktop_to_phone=['messages.read', 'messages.send'],
                                 on_paired=paired.put)
        uri = session.start().replace('host=127.0.0.1', 'host=10.0.2.2')
        try:
            adb(binary, 'shell', 'am', 'start', '-W', '-a', 'android.intent.action.VIEW', '-d', f"'{uri}'", PACKAGE)
            result = paired.get(timeout=40)
            fingerprint = result['fingerprint']
            results.append(step('phone offers messages when the full build has SMS access',
                                sorted(result['outgoing']) == ['device.rotate', 'messages.read', 'messages.send'], str(result['outgoing'])))
            time.sleep(4)
            body = f'Running late {secrets.token_hex(3)}'
            adb(binary, 'emu', 'sms', 'send', NUMBER, body)
            threads = []
            for _ in range(20):
                receipt = call(directory, fingerprint, 'messages.read', {'query': '', 'limit': 20})
                threads = (receipt.get('result') or {}).get('threads') or []
                if any(body == thread['preview'] for thread in threads): break
                time.sleep(1)
            thread = next((t for t in threads if t['preview'] == body), None)
            results.append(step('incoming SMS appears as a thread (messages.read)', thread is not None, str(thread)))
            if thread:
                receipt = call(directory, fingerprint, 'messages.read', {'address': thread['address'], 'limit': 10})
                messages = (receipt.get('result') or {}).get('messages') or []
                incoming = next((m for m in messages if m['body'] == body), None)
                results.append(step('thread messages use the NativeMessages record shape',
                                    incoming is not None and incoming['direction'] == 'incoming' and incoming['state'] in {'received', 'read'}
                                    and incoming['uid'].startswith('android-sms:') and incoming['attachments'] == [], str(incoming)))
                operation = secrets.token_hex(16)
                reply = f'On my way {secrets.token_hex(3)}'
                receipt = call(directory, fingerprint, 'messages.send', {'address': thread['address'], 'body': reply})
                sent = (receipt.get('result') or {})
                results.append(step('reply is sent through the phone (messages.send)', sent.get('state') == 'queued'
                                    and str(sent.get('uid', '')).startswith('android-send:'), str(receipt)))
                reconciled = None
                for _ in range(20):
                    messages = (call(directory, fingerprint, 'messages.read', {'address': thread['address'], 'limit': 10}).get('result') or {}).get('messages') or []
                    reconciled = next((m for m in messages if m['body'] == reply), None)
                    if reconciled and reconciled['uid'] == sent.get('uid'): break
                    time.sleep(1)
                results.append(step('the sent message carries the receipt uid, so the desktop reconciles it',
                                     reconciled is not None and reconciled['uid'] == sent.get('uid') and reconciled['direction'] == 'outgoing',
                                     str(reconciled)))
                del operation
        finally:
            listener.stop()
            session.cancel()
            adb(binary, 'forward', '--remove', 'tcp:47811', check=False)
    print(f'{sum(results)}/{len(results)} checks passed')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
