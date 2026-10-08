"""Scripted desktop peer for the Kotlin interoperability test.

Runs the real pairing session, listener, journal and adapters on loopback with
synthetic state in a caller-supplied temporary directory. It speaks JSON lines:
stdout reports `uri`, `paired`, `event` and `receipt`; stdin accepts
`{"call": capability, "payload": {...}}` and `{"quit": true}`.
Never point it at a real identity directory.
"""
import json
import os
from pathlib import Path
import sys
import threading

os.environ['LUMA_CONNECT_TEST_LOOPBACK'] = '1'
from luma_continuity.bootstrap import create_identity
from luma_continuity.companion import CompanionListener, DesktopAdapters, PairingSession, Registry, call
from luma_continuity.companion_rotation import RotationAdapters, rotate_identity

lock = threading.Lock()


def emit(value):
    with lock:
        sys.stdout.write(json.dumps(value, sort_keys=True) + '\n')
        sys.stdout.flush()


def main():
    root = Path(sys.argv[1])
    directory = root / 'identity'
    create_identity(directory)
    clipboard = {'text': 'desktop clipboard'}
    adapters = DesktopAdapters(
        directory, downloads=root / 'Downloads',
        notify=lambda kind, peer, value: emit({'event': kind, 'value': value}),
        clipboard_set=lambda text, sensitive: (clipboard.update(text=text), emit({'event': 'clipboard', 'value': {'text': text, 'sensitive': sensitive}})),
        clipboard_get=lambda: clipboard['text'],
        media=lambda peer, value: emit({'event': 'media', 'value': value}),
        changed=lambda kind, peer: emit({'event': 'changed', 'value': kind}))
    rotation = RotationAdapters(directory, changed=lambda kind, pin: emit({'event': 'rotated', 'value': pin}))
    listener = CompanionListener(directory, addresses=['127.0.0.1'],
                                 adapter_factory=lambda peer: {**adapters.for_peer(peer), **rotation.for_peer(peer)}, port=0,
                                 after=lambda peer: (adapters.after_request(peer), rotation.after_request(peer)))
    port = listener.start()
    session = PairingSession(
        directory, name='Test Desk', addresses=['127.0.0.1'], listen_port=port,
        phone_to_desktop=['device.status', 'clipboard.write', 'clipboard.read', 'notifications.mirror',
                          'files.write', 'links.open', 'media.mirror', 'device.rotate'],
        desktop_to_phone=['clipboard.write', 'device.ring', 'notifications.act', 'links.open', 'device.rotate'],
        on_paired=lambda result: emit({'paired': result}))
    emit({'uri': session.start()})
    for line in sys.stdin:
        command = json.loads(line)
        if command.get('quit'):
            break
        if command.get('rotate'):
            devices = Registry(directory).active()
            result = rotate_identity(directory, {pin: row['epoch'] for pin, row in devices.items()},
                                     lambda pin, capability, payload: call(directory, pin, capability, payload))
            emit({'rotated': result})
            continue
        try:
            fingerprint = next(iter(Registry(directory).active()))
            receipt = call(directory, fingerprint, command['call'], command['payload'])
            emit({'receipt': receipt})
        except Exception as error:
            emit({'receipt': None, 'error': type(error).__name__ + ': ' + str(error)})
    listener.stop()
    session.cancel()


if __name__ == '__main__':
    main()
