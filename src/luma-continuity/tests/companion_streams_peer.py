"""Scripted desktop peer for the Kotlin stream interoperability test.

Like `companion_peer.py`, but the listener's adapters are wrapped by
`companion_streams.stream_adapters`, input goes to a recording injector, and the
desktop can send files to the phone with `luma-files/1`. JSON lines:

stdout: `uri`, `paired`, `event` (`input`, `file`), `sent`
stdin:  `{"send_stream": {"path", "transfer", "interrupt_after"}}`, `{"quit": true}`

All state is synthetic and lives in a caller-supplied temporary directory.
Never point it at a real identity directory.
"""
import json
import os
from pathlib import Path
import sys
import threading

os.environ['LUMA_CONNECT_TEST_LOOPBACK'] = '1'
from luma_continuity.bootstrap import create_identity  # noqa: E402
from luma_continuity.companion import CompanionListener, DesktopAdapters, PairingSession  # noqa: E402
from luma_continuity.companion_streams import FileStreams, InputStreams, send_file_stream, stream_adapters  # noqa: E402

lock = threading.Lock()


def emit(value):
    with lock:
        sys.stdout.write(json.dumps(value, sort_keys=True) + '\n')
        sys.stdout.flush()


def main():
    root = Path(sys.argv[1])
    directory = root / 'identity'
    create_identity(directory)

    def inject(peer, events):
        emit({'event': 'input', 'value': events})
        return True
    adapters = DesktopAdapters(directory, downloads=root / 'Downloads', input=inject,
                               notify=lambda kind, peer, value: emit({'event': kind, 'value': value}))
    inputs = InputStreams(adapters, addresses=['127.0.0.1'])
    files = FileStreams(adapters, addresses=['127.0.0.1'])
    listener = CompanionListener(directory, addresses=['127.0.0.1'], port=0,
                                 adapter_factory=stream_adapters(adapters, inputs=inputs, files=files))
    port = listener.start()
    session = PairingSession(directory, name='Test Desk', addresses=['127.0.0.1'], listen_port=port,
                             phone_to_desktop=['device.status', 'input.control', 'files.write'],
                             desktop_to_phone=['files.write'], on_paired=lambda result: emit({'paired': result}))
    emit({'uri': session.start()})
    for line in sys.stdin:
        command = json.loads(line)
        if command.get('quit'):
            break
        order = command['send_stream']
        limit = order.get('interrupt_after')
        seen = [0]

        def progress(sent, size):
            seen[0] = sent
        try:
            result = send_file_stream(directory, session.result['fingerprint'], order['path'], progress,
                                      lambda: limit is not None and seen[0] >= limit, transfer=order['transfer'], attempts=1)
            emit({'sent': result})
        except Exception as error:
            emit({'sent': None, 'error': type(error).__name__})
    inputs.close_all()
    files.close_all()
    listener.stop()
    session.cancel()


if __name__ == '__main__':
    main()
