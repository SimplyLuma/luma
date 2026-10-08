#!/usr/bin/env python3
"""A scripted stand-in phone that exercises a running Luma Connect trial over the real LAN.

Pairs with the trial daemon started by companion_trial.py on a Luma desktop, then drives
every desktop owner that does not need a physical phone: status, notifications, media
controls, Do Not Disturb, links, files (chunked and stream), desktop-to-phone requests,
the PipeWire camera from a recorded stream, and the screen window. Synthetic data only.

    PYTHONPATH=src/luma-continuity python3 lan_phone_trial.py --desktop nick@192.168.11.83 \
        --phone-address 192.168.10.146 --dump camera-vp9.lmd --screen-dump screen-vp9.lmd
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import secrets
import shlex
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time

from luma_continuity import transport
from luma_continuity.bootstrap import create_identity
from luma_continuity.companion import SCHEMA, PAIRING_ALPN, CompanionListener, Registry, call
from luma_continuity.policy import Journal

BUS = 'org.projectluma.ConnectTrial1'
PATH = '/org/projectluma/Connect'


class Desktop:
    def __init__(self, target):
        self.target = target

    def run(self, command, check=True, timeout=120):
        result = subprocess.run(['ssh', '-o', 'BatchMode=yes', self.target, command], capture_output=True, text=True, timeout=timeout)
        if check and result.returncode != 0:
            raise RuntimeError(f'{command}: {result.stderr.strip()}')
        return result.stdout.strip()

    def call(self, method, signature='', *args, timeout=120):
        quoted = ' '.join(shlex.quote(str(arg)) for arg in args)
        return self.run(f'busctl --user --timeout={timeout} call {BUS} {PATH} {BUS} {method} {signature} {quoted}', timeout=timeout + 10)

    @staticmethod
    def string(reply):
        # busctl prints: s "<escaped text>"; the text is itself JSON for most methods.
        if not reply.startswith('s '): return reply
        text = json.loads(reply[2:])
        try: return json.loads(text)
        except ValueError: return text


def step(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}{"  " + str(detail)[:300] if detail else ""}', flush=True)
    return ok


def read_dump(path):
    with open(path, 'rb') as source:
        size, = struct.unpack('>I', source.read(4))
        header = json.loads(source.read(size))
        packets = []
        while True:
            head = source.read(13)
            if len(head) < 13: break
            kind, pts, length = struct.unpack('>BqI', head)
            packets.append((kind, pts, source.read(length)))
    return header, packets


def send_media(phone_dir, desktop_pin, host, port, session, dump, seconds=12):
    header, packets = read_dump(dump)
    header = dict(header, session=session)
    tls = transport.context(phone_dir / 'device.pem', phone_dir / 'device.key', phone_dir / 'peers' / (desktop_pin + '.pem'), server=False)
    tls.set_alpn_protocols(['luma-media/1'])
    with socket.create_connection((host, port), timeout=10) as raw, tls.wrap_socket(raw) as stream:
        if hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest() != desktop_pin: raise PermissionError('wrong desktop')
        transport.send(stream, header)
        deadline, offset, loop = time.monotonic() + seconds, 0, 0
        frame_us = 1_000_000 // max(1, header['fps'])
        while time.monotonic() < deadline:
            for kind, pts, data in packets:
                if kind == 1 and loop: continue
                stream.sendall(struct.pack('>BqI', kind, offset, len(data)) + data)
                if kind != 1:
                    offset += frame_us
                    time.sleep(frame_us / 1_000_000)
                if time.monotonic() >= deadline: break
            loop += 1
        stream.sendall(struct.pack('>BqI', 0x7f, 0, 0))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--desktop', required=True)
    parser.add_argument('--phone-address', required=True)
    parser.add_argument('--dump', required=True)
    parser.add_argument('--screen-dump', required=True)
    options = parser.parse_args()
    desktop = Desktop(options.desktop)
    results = []
    phone_to_desktop = ['device.status', 'notifications.mirror', 'media.mirror', 'files.write', 'links.open', 'dnd.set']
    desktop_to_phone = ['device.ring', 'files.write', 'notifications.act', 'camera.stream', 'screen.view', 'dnd.set']
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        phone = root / 'phone'
        create_identity(phone, days=398)
        events = queue.Queue()
        camera_requests = queue.Queue()

        def adapters(peer):
            def record(name):
                def handler(capability, payload):
                    events.put((name, payload))
                    if capability in ('camera.stream', 'screen.view'):
                        camera_requests.put((capability, payload))
                        return {'error': 'needs-user'}
                    if capability == 'files.write' and 'stream' not in payload:
                        return {'received': payload['offset'] + len(base64.b64decode(payload['data'])), **({'name': payload['name']} if payload['final'] else {})}
                    if capability == 'dnd.set':
                        return {'on': payload['on']}
                    return {'accepted': True}
                return handler
            return {capability: record(capability) for capability in desktop_to_phone}

        listener = CompanionListener(phone, addresses=[options.phone_address], adapter_factory=adapters, port=0)
        phone_port = listener.start()
        try:
            uri = desktop.string(desktop.call('StartCompanionPairing', 'asas', len(phone_to_desktop), *phone_to_desktop,
                                              len(desktop_to_phone), *desktop_to_phone))
            fields = dict(part.split('=', 1) for part in uri.split('?', 1)[1].split('&'))
            host, port, pin, token = fields['host'], int(fields['port']), fields['pin'], fields['token']
            results.append(step('desktop shows a pairing invitation (StartCompanionPairing)', uri.startswith('luma-connect://pair?v=1&host='), uri.split('&token=')[0]))

            pem = (phone / 'device.pem').read_text()
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); tls.check_hostname = False; tls.verify_mode = ssl.CERT_NONE
            tls.minimum_version = ssl.TLSVersion.TLSv1_3; tls.set_alpn_protocols([PAIRING_ALPN])
            with socket.create_connection((host, port), timeout=10) as raw, tls.wrap_socket(raw) as stream:
                if hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest() != pin: raise PermissionError('pin mismatch')
                desktop_pem = ssl.DER_cert_to_PEM_cert(stream.getpeercert(binary_form=True))
                transport.send(stream, {'schema': SCHEMA, 'kind': 'request', 'token': token, 'certificate': pem,
                                        'pin': transport.fingerprint(pem), 'name': 'LAN test phone', 'model': 'Scripted phone',
                                        'platform': 'android', 'listen_port': phone_port,
                                        'phone_to_desktop': phone_to_desktop + ['device.rotate'],
                                        'desktop_to_phone': desktop_to_phone + ['device.rotate']})
                response = transport.receive(stream)
            results.append(step('pairing over the real network', response.get('kind') == 'accepted', f"sas {response.get('sas')}"))
            epoch = response['epoch']
            (phone / 'peers').mkdir(mode=0o700, exist_ok=True)
            (phone / 'peers' / (pin + '.pem')).write_text(desktop_pem)
            journal = Journal(phone / 'continuity.db')
            journal.approve(pin, epoch, set(response['desktop_to_phone']), outgoing_grants=set(response['phone_to_desktop']))
            registry = Registry(phone)
            opened = registry._open()
            try:
                registry.record(opened, pin, name='Trial desktop', model='Luma', platform='android', host=host, port=response['listen_port'], now=time.time())
                opened.db.commit()
            finally:
                opened.close(); journal.close()
            desktop.call('CancelCompanionPairing')
            fingerprint = transport.fingerprint(pem)

            def phone_call(capability, payload):
                return call(phone, pin, capability, payload)

            receipt = phone_call('device.status', {'battery': 77, 'charging': False, 'network': 'wifi', 'listen_port': phone_port})
            time.sleep(1.5)
            devices = desktop.string(desktop.call('CompanionDevices'))
            device = next((d for d in devices if d['fingerprint'] == fingerprint), {})
            results.append(step('battery shows in the Phones data (device.status)', (device.get('status') or {}).get('battery') == 77, device.get('status')))

            key = secrets.token_hex(16)
            receipt = phone_call('notifications.mirror', {'op': 'post', 'key': key, 'app': 'Messages', 'package': 'com.example.messages',
                                                          'title': 'Luma Connect trial', 'text': 'This notification came from the test phone.',
                                                          'when': int(time.time() * 1000), 'silent': True, 'conversation': None,
                                                          'actions': [{'id': secrets.token_hex(16), 'label': 'Mark as read', 'reply': False}]})
            time.sleep(1)
            shown = desktop.run("gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell --method org.freedesktop.DBus.Peer.Ping >/dev/null && echo shell", check=False)
            results.append(step('notification delivered to the desktop notification owner (notifications.mirror)', receipt['result'] == {'accepted': True}, receipt))
            phone_call('notifications.mirror', {'op': 'remove', 'key': key})

            receipt = phone_call('media.mirror', {'state': 'playing', 'title': 'Trial track', 'artist': 'Luma', 'album': 'Connect',
                                                  'app': 'Music', 'duration_ms': 180000, 'position_ms': 1000, 'actions': ['play', 'pause', 'next']})
            time.sleep(1.5)
            players = desktop.run("busctl --user list | grep -o 'org.mpris.MediaPlayer2.LumaConnect[^ ]*' | head -1", check=False)
            title = desktop.run(f"busctl --user get-property {players} /org/mpris/MediaPlayer2 org.mpris.MediaPlayer2.Player Metadata 2>/dev/null | tr ' ' '\\n' | grep -A2 xesam:title | tail -1", check=False) if players else ''
            results.append(step('phone media appears as an MPRIS player (media.mirror)', bool(players) and 'Trial' in title, f'{players} {title}'))
            phone_call('media.mirror', {'state': 'none', 'title': '', 'artist': '', 'album': '', 'app': '', 'duration_ms': None, 'position_ms': None, 'actions': []})

            banners = lambda: desktop.run('gsettings get org.gnome.desktop.notifications show-banners')
            before = banners()
            phone_call('dnd.set', {'on': True}); time.sleep(.5); on = banners()
            phone_call('dnd.set', {'on': False}); time.sleep(.5); off = banners()
            phone_call('dnd.set', {'on': before == 'false'}); time.sleep(.5); restored = banners()
            results.append(step('Do Not Disturb follows the phone both ways, then is restored (dnd.set)',
                                on == 'false' and off == 'true' and restored == before,
                                f'show-banners before={before} on={on} off={off} restored={restored}'))

            receipt = phone_call('links.open', {'url': 'https://example.org/luma-connect-trial', 'title': 'Trial link'})
            results.append(step('link offered as a desktop notification (links.open)', receipt['result'] == {'accepted': True}, receipt))

            data = secrets.token_bytes(1_500_000)
            source = root / 'trial-upload.bin'
            source.write_bytes(data)
            from luma_continuity import companion_streams
            try:
                companion_streams.send_file_stream(phone, pin, str(source), transfer=secrets.token_hex(16), call=lambda d, f, c, p, **k: call(d, f, c, p))
                mode = 'stream'
            except companion_streams.StreamUnsupported:
                mode = 'unsupported'
            # user-dirs.dirs says "$HOME/Downloads", and the trial's HOME is private, so files land in the trial's Downloads.
            received = desktop.run("sha256sum ~/luma-connect-companion-trial/home/Downloads/trial-upload*.bin 2>/dev/null", check=False)
            results.append(step(f'phone file reaches the desktop Downloads ({mode})', hashlib.sha256(data).hexdigest() in received, received))

            receipt = desktop.string(desktop.call('CompanionInvoke', 'sss', fingerprint, 'device.ring', json.dumps({'ring': True})))
            got = events.get(timeout=10)
            results.append(step('desktop rings the phone through D-Bus (CompanionInvoke)', got[0] == 'device.ring' and receipt['state'] == 'complete', receipt))

            state = desktop.call('CompanionStartCamera', 's', fingerprint)
            capability, payload = camera_requests.get(timeout=20)
            results.append(step('desktop asks the phone for its camera with decodable codecs', capability == 'camera.stream' and 'vp9' in payload['codecs'], {k: payload[k] for k in ('codecs', 'width', 'height')}))
            sender = threading.Thread(target=send_media, args=(phone, pin, host, payload['port'], payload['session'], options.dump, 14))
            sender.start()
            time.sleep(4)
            node = desktop.run("pw-dump 2>/dev/null | python3 -c \"import json,sys; print(next((str(o['id'])+' '+o['info']['props'].get('node.description','') for o in json.load(sys.stdin) if o.get('type','').endswith('Node') and o.get('info',{}).get('props',{}).get('node.name','').startswith('luma-connect-camera-')), ''))\"", check=False)
            node_id = node.split(' ')[0] if node else ''
            pulled = desktop.run(f"timeout 10 gst-launch-1.0 pipewiresrc target-object={node_id} num-buffers=20 ! videoconvert ! fakesink 2>&1 | tail -4; echo exit=$?", check=False) if node_id else ''
            sender.join()
            results.append(step('the phone camera is a PipeWire video source another app can read', bool(node_id) and 'Got EOS' in pulled, f'node={node} pull={pulled[-240:]}'))

            state = desktop.call('CompanionShowScreen', 's', fingerprint)
            capability, payload = camera_requests.get(timeout=20)
            sender = threading.Thread(target=send_media, args=(phone, pin, host, payload['port'], payload['session'], options.screen_dump, 6))
            sender.start(); time.sleep(3)
            sessions = desktop.string(desktop.call('GetState'))
            media = sessions.get('companion_media') or []
            sender.join()
            results.append(step('phone screen opens a viewer on the desktop (screen.view)', any(m['kind'] == 'screen' and m['state'] == 'streaming' for m in media), media))

            desktop.run('rm -f "$(xdg-user-dir DOWNLOAD)"/trial-upload*.bin ~/luma-connect-companion-trial/home/Downloads/trial-upload*.bin', check=False)
            desktop.call('RemoveCompanion', 's', fingerprint)
            time.sleep(1.5)
            try:
                phone_call('device.status', {'battery': 1})
                refused = False
            except Exception:
                refused = True
            results.append(step('removing the phone on the desktop refuses it', refused))
        finally:
            listener.stop()
            desktop.run('busctl --user call org.projectluma.ConnectTrial1 /org/projectluma/Connect org.projectluma.ConnectTrial1 CancelCompanionPairing', check=False)
    print(f'{sum(results)}/{len(results)} checks passed')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
