#!/usr/bin/env python3
"""End-to-end camera and screen streaming check on an Android emulator.

Pairs the app with a temporary desktop identity, asks for a camera session and a
screen session, taps the phone's own "Tap to start" notification and Android's
screen-capture consent through UI Automator, and records what the desktop media
receiver gets. Synthetic state only; never use a personal phone or identity.

    PYTHONPATH=src/luma-continuity python3 src/luma-connect-android/tools/emulator_media_e2e.py \
        --adb ~/Library/Android/sdk/platform-tools/adb \
        --apk src/luma-connect-android/app/build/outputs/apk/play/debug/app-play-debug.apk
"""
import argparse
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time

os.environ['LUMA_CONNECT_TEST_LOOPBACK'] = '1'
from luma_continuity.bootstrap import create_identity  # noqa: E402
from luma_continuity.companion import CompanionListener, DesktopAdapters, PairingSession, call  # noqa: E402
from luma_continuity.companion_media import MediaSessions  # noqa: E402

PACKAGE = 'org.projectluma.connect'
PHONE_PORT = 47811


def adb(binary, *args, check=True, timeout=60):
    result = subprocess.run([binary, *args], capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError(f'adb {" ".join(args)}: {result.stderr.strip()}')
    return result.stdout


def tap_text(binary, pattern, timeout=20):
    """Finds a UI node whose text matches and taps its centre."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        adb(binary, 'shell', 'uiautomator', 'dump', '/sdcard/luma-ui.xml', check=False)
        xml = adb(binary, 'exec-out', 'cat', '/sdcard/luma-ui.xml', check=False)
        for node in re.finditer(r'<node [^>]*>', xml):
            text = re.search(r' text="([^"]*)"', node.group(0))
            bounds = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node.group(0))
            if text and bounds and re.search(pattern, text.group(1), re.I):
                x1, y1, x2, y2 = map(int, bounds.groups())
                adb(binary, 'shell', 'input', 'tap', str((x1 + x2) // 2), str((y1 + y2) // 2))
                return True
        time.sleep(1)
    return False


class RecordingSink:
    DUMP = None

    def __init__(self, session):
        self.session, self.header, self.config, self.frames, self.keys = session, None, 0, 0, 0
        self.done = threading.Event()
        self.packets = []

    def configure(self, header): self.header = header

    def codec_config(self, data):
        self.config += 1
        self.packets.append((1, 0, data))

    def audio_config(self, data): pass
    def audio(self, pts_us, data): pass

    def video(self, pts_us, data, key):
        self.frames += 1
        self.keys += int(key)
        self.packets.append((3 if key else 2, pts_us, data))
        if self.frames >= 60:
            self.done.set()

    def dump(self):
        """Writes the header and packets for an offline decode check (tools/decode_media_dump.py)."""
        if not self.DUMP or not self.header: return
        import json, struct
        Path(self.DUMP).mkdir(parents=True, exist_ok=True)
        name = Path(self.DUMP) / f"{self.header['kind']}-{self.header['codec']}.lmd"
        with open(name, 'wb') as out:
            header = json.dumps(self.header).encode()
            out.write(struct.pack('>I', len(header)) + header)
            for kind, pts, data in self.packets:
                out.write(struct.pack('>BqI', kind, pts, len(data)) + data)

    def close(self): self.done.set()


def step(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}{"  " + detail if detail else ""}', flush=True)
    return ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--adb', required=True)
    parser.add_argument('--apk', required=True)
    parser.add_argument('--codecs', default='av1,vp9,vp8', help='what the desktop says it can decode, most preferred first')
    parser.add_argument('--dump', default=None, help='directory for recorded streams')
    options = parser.parse_args()
    RecordingSink.DUMP = options.dump
    codecs = options.codecs.split(',')
    binary = os.path.expanduser(options.adb)
    adb(binary, 'wait-for-device')
    adb(binary, 'install', '-r', '-g', options.apk, timeout=180)
    adb(binary, 'shell', 'pm', 'clear', PACKAGE)
    adb(binary, 'shell', 'pm', 'grant', PACKAGE, 'android.permission.POST_NOTIFICATIONS', check=False)
    adb(binary, 'shell', 'pm', 'grant', PACKAGE, 'android.permission.CAMERA', check=False)
    adb(binary, 'forward', f'tcp:{PHONE_PORT}', f'tcp:{PHONE_PORT}')
    results = []
    sinks = []
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        directory = root / 'identity'
        create_identity(directory)
        adapters = DesktopAdapters(directory, downloads=root / 'Downloads')
        listener = CompanionListener(directory, addresses=['127.0.0.1'], adapter_factory=adapters.for_peer, port=0,
                                     after=adapters.after_request)
        port = listener.start()
        paired = queue.Queue()
        session = PairingSession(directory, name='Media Desk', addresses=['127.0.0.1'], listen_port=port,
                                 phone_to_desktop=['device.status'], desktop_to_phone=['camera.stream', 'screen.view', 'screen.control'],
                                 on_paired=paired.put)
        uri = session.start().replace('host=127.0.0.1', 'host=10.0.2.2')
        media = MediaSessions(directory, addresses=['127.0.0.1'],
                              sink_factory=lambda media_session: sinks.append(RecordingSink(media_session)) or sinks[-1])
        try:
            adb(binary, 'shell', 'am', 'start', '-W', '-a', 'android.intent.action.VIEW', '-d', f"'{uri}'", PACKAGE)
            result = paired.get(timeout=40)
            fingerprint = result['fingerprint']
            time.sleep(4)
            adb(binary, 'shell', 'input', 'keyevent', 'KEYCODE_HOME')

            for kind, capability, payload, consent in (
                ('camera', 'camera.stream', {'facing': 'back', 'width': 1280, 'height': 720, 'fps': 30, 'bitrate': 3_000_000, 'audio': False}, None),
                ('screen', 'screen.view', {'max_size': 1280, 'fps': 30, 'bitrate': 4_000_000}, r'^(Start now|Start|Share screen|Next)$'),
            ):
                sinks.clear()
                # A stock Luma desktop cannot decode H.264, so the phone must pick one of these.
                session_id, media_port = media.open_session(fingerprint, kind, kind == 'screen', codecs)
                receipt = call(directory, fingerprint, capability, {'session': session_id, 'port': media_port, 'codecs': codecs, **payload})
                asked = receipt['result'] == {'error': 'needs-user'}
                adb(binary, 'shell', 'cmd', 'statusbar', 'expand-notifications', check=False)
                tapped = tap_text(binary, r'wants to (use this phone as a camera|show this phone)')
                if consent:
                    # The app requests the entire display, so Android shows a single confirmation.
                    tap_text(binary, consent, timeout=15)
                deadline = time.monotonic() + 30
                while not sinks and time.monotonic() < deadline:
                    time.sleep(.2)
                sink = sinks[0] if sinks else None
                if sink:
                    sink.done.wait(30)
                    sink.dump()
                if kind == 'screen' and sink and sink.frames:
                    sent = media.get(session_id).send_control({'type': 'key', 'key': 'home'})
                    time.sleep(1)
                else:
                    sent = None
                results.append(step(
                    f'{kind} stream reaches the desktop receiver',
                    asked and tapped and sink is not None and sink.header is not None and (sink.config >= 1 or sink.header['codec'] in ('vp8', 'vp9')) and sink.frames >= 20 and sink.keys >= 1 and sink.header['codec'] in codecs,
                    f'receipt={receipt["result"]} tapped={tapped} header={sink and sink.header} config={sink and sink.config} '
                    f'frames={sink and sink.frames} keys={sink and sink.keys} control_sent={sent}'))
                media.close(session_id)
                adb(binary, 'shell', 'am', 'broadcast', '-a', f'{PACKAGE}.STOP_CAMERA', '-n', f'{PACKAGE}/.service.ActionReceiver', check=False)
                adb(binary, 'shell', 'am', 'broadcast', '-a', f'{PACKAGE}.STOP_SCREEN', '-n', f'{PACKAGE}/.service.ActionReceiver', check=False)
                time.sleep(2)
        finally:
            media.close_all()
            listener.stop()
            session.cancel()
            adb(binary, 'forward', '--remove', f'tcp:{PHONE_PORT}', check=False)
    print(f'{sum(results)}/{len(results)} checks passed')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
