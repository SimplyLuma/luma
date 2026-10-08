# SPDX-License-Identifier: GPL-3.0-only
"""Exercise native capture teardown on an isolated builder Wayland display.

Use Weston on a private Xvfb display (with a seat), not the user's compositor.
This drives the actual engine and DMA-BUF leases without a GTK import consumer.
It is a GPU lifetime regression, not AppKit visual/performance acceptance.
"""
import argparse
import array
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from engine_pipe import EnginePipe

DOCUMENT = b'''<body><canvas id="c" width="900" height="700"></canvas><script>
let g=c.getContext("webgl2");function f(t){if(g){
g.clearColor(Math.sin(t/1000),.3,.5,1);g.clear(g.COLOR_BUFFER_BIT);}
requestAnimationFrame(f)}f(0)</script>'''


class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(DOCUMENT)

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--work-root', type=Path, required=True)
    parser.add_argument('--steps', type=int, default=80)
    parser.add_argument('--private-mutter', action='store_true',
                        help='Use EnginePipe’s existing private Mutter display')
    args = parser.parse_args()
    if (not args.private_mutter and not os.environ.get('WAYLAND_DISPLAY')) or not 1 <= args.steps <= 500:
        parser.error('Provide an isolated WAYLAND_DISPLAY and 1–500 steps')
    args.work_root.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='capture-', dir=args.work_root.resolve()))
    stop = threading.Event()
    counts = {'frames': 0}
    server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    socket_path = str(root / 'frames')
    server.bind(socket_path)
    server.listen(8)
    server.settimeout(.5)

    def receive(peer):
        with peer:
            peer.settimeout(.5)
            while not stop.is_set():
                try:
                    data, ancillary, flags, _ = peer.recvmsg(65536, socket.CMSG_SPACE(16))
                except socket.timeout:
                    continue
                except OSError:
                    return
                for level, kind, payload in ancillary:
                    if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                        descriptors = array.array('i')
                        descriptors.frombytes(payload[:len(payload) // descriptors.itemsize * descriptors.itemsize])
                        for fd in descriptors:
                            os.close(fd)
                if not data or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
                    return
                frame = json.loads(data)
                if 'id' in frame and 'planes' in frame:
                    counts['frames'] += 1
                    # Keep a real capture lease briefly in flight across navigation.
                    time.sleep(.025)
                    try:
                        peer.send(struct.pack('!I', frame['id']))
                    except OSError:
                        return

    def accept():
        while not stop.is_set():
            try:
                peer, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=receive, args=(peer,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    http = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    # Reuse EnginePipe's authenticated parent and CDP descriptors. The temporary
    # exec-only wrapper selects the supplied private Wayland display rather than
    # the helper's default headless mode. It is never used by the personal host.
    wrapper = root / 'engine-wrapper'
    extra = ['--viola-native-frame-probe', '--viola-native-frame-socket=' + socket_path,
             '--disable-features=Vulkan', '--use-angle=gl', '--enable-logging=stderr']
    wrapper.write_text('#!/usr/bin/python3\nimport os,sys\n'
                       f'engine={str(args.engine.resolve())!r}\n'
                       'args=[a for a in sys.argv[1:] if not a.startswith("--headless")]\n'
                       f'os.execv(engine,[engine,*args,*{extra!r}])\n')
    wrapper.chmod(0o700)
    engine = None
    report = {'steps_requested': args.steps, 'steps_completed': 0, 'pass': False}
    try:
        if args.private_mutter:
            engine = EnginePipe(args.engine.resolve(), root / 'profile', lambda event: None,
                                native_frame_probe=True, frame_socket=root / 'frames')
        else:
            engine = EnginePipe(wrapper, root / 'profile', lambda event: None)
        report['version'] = engine.call('Browser.getVersion')['product']
        gpu = engine.call('SystemInfo.getInfo')['gpu']['auxAttributes']
        report['renderer'] = gpu.get('glRenderer')
        target = engine.call('Target.createTarget', {'url': 'about:blank'})['targetId']
        session = engine.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
        for step in range(args.steps):
            host = 'localhost' if step % 2 else '127.0.0.1'
            engine.call('Page.navigate', {'url': f'http://{host}:{http.server_port}/?n={step}'}, session=session)
            engine.call('Emulation.setDeviceMetricsOverride', {
                'width': 900 + step % 3 * 40, 'height': 650 + step % 2 * 30,
                'deviceScaleFactor': 1, 'mobile': False}, session=session)
            time.sleep(.35)
            report['steps_completed'] += 1
        report['webgl_healthy'] = engine.call('Runtime.evaluate', {
            'expression': 'typeof g !== "undefined" && !!g && !g.isContextLost()',
            'returnByValue': True}, session=session).get('result', {}).get('value') is True
        report['gpu_crashes'] = engine.call('SystemInfo.getInfo')['gpu']['auxAttributes'].get('processCrashCount')
    except Exception as error:
        report['error'] = repr(error)
    finally:
        if engine:
            engine.close()
            report['exit_code'] = engine.process.returncode
        stop.set()
        server.close()
        http.shutdown()
        http.server_close()
    report.update(counts)
    report['pass'] = (report['steps_completed'] == args.steps and counts['frames'] >= args.steps
                      and report.get('webgl_healthy') is True and report.get('gpu_crashes') == 0 and report.get('exit_code') == 0
                      and 'error' not in report)
    (root / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)
    print('REPORT', root, flush=True)
    return 0 if report['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
