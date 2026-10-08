# SPDX-License-Identifier: GPL-3.0-only
"""Unpresented DMA-BUF import/lifetime probe, never a browser preview."""
import argparse
import array
import gc
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time
from urllib.parse import quote

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gtk, Gdk, GLib
from engine_pipe import EnginePipe
from gpu_page import GpuPage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--work-root', type=Path, required=True)
    args = parser.parse_args()
    args.work_root.mkdir(parents=True, exist_ok=True)
    Gtk.init()
    display = Gdk.Display.get_default()
    if display is None:
        raise RuntimeError('A real GDK display is required')
    report = {'classification': 'minimized engine / unpresented GTK import probe',
              'imported': 0, 'released': 0, 'errors': [], 'frames': [],
              'presented_to_user': False}
    loop = GLib.MainLoop()
    texture_holder = [None]
    page = GpuPage()
    report['page_widget'] = page.__gtype__.name
    report['page_minimum_width'] = page.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
    report['page_minimum_height'] = page.measure(Gtk.Orientation.VERTICAL, -1)[0]
    pending = set()
    release_lock = threading.Lock()
    stopped = threading.Event()
    peer = [None]

    def release(frame_id, fds):
        with release_lock:
            if frame_id not in pending:
                return
            pending.remove(frame_id)
            for fd in fds:
                os.close(fd)
            try:
                peer[0].sendall(struct.pack('!I', frame_id))
                report['released'] += 1
            except OSError as error:
                report['errors'].append('release: ' + str(error))

    def import_frame(metadata, fds):
        frame_id = metadata['id']
        try:
            if stopped.is_set():
                release(frame_id, fds)
                return GLib.SOURCE_REMOVE
            builder = Gdk.DmabufTextureBuilder.new()
            builder.set_display(display)
            builder.set_width(metadata['width'])
            builder.set_height(metadata['height'])
            builder.set_fourcc(int.from_bytes(metadata['fourcc'].encode('ascii'), 'little'))
            builder.set_modifier(int(metadata['modifier']))
            builder.set_premultiplied(True)
            builder.set_n_planes(len(fds))
            for index, (plane, fd) in enumerate(zip(metadata['planes'], fds)):
                builder.set_fd(index, fd)
                builder.set_stride(index, plane['stride'])
                builder.set_offset(index, int(plane['offset']))
            texture = builder.build(lambda *_: release(frame_id, fds), frame_id)
            if texture is None:
                raise RuntimeError('GDK returned no texture')
            texture_holder[0] = texture
            page.set_frame(texture, metadata)
            report['imported'] += 1
            if len(report['frames']) < 12:
                report['frames'].append(metadata)
            if report['imported'] == 1:
                # One diagnostic readback of an imported texture. This image
                # never feeds a widget and is not the transport mechanism.
                texture.save_to_png(str(args.work_root / 'first-imported-frame.png'))
        except Exception as error:
            report['errors'].append('import: ' + str(error))
            release(frame_id, fds)
            stopped.set()
            loop.quit()
        return GLib.SOURCE_REMOVE

    with tempfile.TemporaryDirectory(prefix='viola-gpu-', dir=os.environ['XDG_RUNTIME_DIR']) as directory:
        endpoint = Path(directory) / 'frames.sock'
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        listener.bind(str(endpoint))
        os.chmod(endpoint, 0o600)
        listener.listen(1)
        listener.settimeout(15)
        profile = Path(tempfile.mkdtemp(prefix='gtk-import-', dir=args.work_root))
        engine = EnginePipe(args.engine, profile, lambda _: None,
                            native_frame_probe=True, frame_socket=endpoint)

        def receive():
            try:
                connection, _ = listener.accept()
                peer[0] = connection
                pid, uid, _ = struct.unpack('3i', connection.getsockopt(
                    socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
                if pid != engine.process.pid or uid != os.geteuid():
                    raise RuntimeError('Frame sender is not the owned Chromium child')
                connection.settimeout(1)
                while not stopped.is_set():
                    try:
                        data, ancillary, flags, _ = connection.recvmsg(
                            4096, socket.CMSG_SPACE(4 * array.array('i').itemsize),
                            socket.MSG_CMSG_CLOEXEC)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    fds = []
                    try:
                        for level, kind, payload in ancillary:
                            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                                values = array.array('i')
                                values.frombytes(payload)
                                fds.extend(values)
                        if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
                            raise ValueError('Truncated frame packet')
                        metadata = json.loads(data)
                        if (metadata['version'] != 1 or not 1 <= len(fds) <= 4
                                or len(fds) != len(metadata['planes'])
                                or metadata['fourcc'] not in ('AR24', 'AB24')
                                or not 0 < metadata['id'] < 2**31
                                or not 0 < metadata['width'] <= 5120
                                or not 0 < metadata['height'] <= 3200
                                or not 0 <= int(metadata['modifier']) < 2**64):
                            raise ValueError('Invalid native frame metadata')
                        for plane in metadata['planes']:
                            if (not 0 < plane['stride'] < 2**32
                                    or not 0 <= int(plane['offset']) < 2**32
                                    or not 0 < int(plane['size']) < 2**63):
                                raise ValueError('Invalid native plane metadata')
                        with release_lock:
                            if metadata['id'] in pending or len(pending) >= 3:
                                raise ValueError('Duplicate or excess frame lease')
                            pending.add(metadata['id'])
                        GLib.idle_add(import_frame, metadata, fds)
                        fds = []
                    finally:
                        for fd in fds:
                            os.close(fd)
            except Exception as error:
                report['errors'].append('receive: ' + str(error))

        def exercise():
            try:
                target = engine.call('Target.createTarget', {
                    'url': 'about:blank', 'newWindow': True,
                    'background': True, 'windowState': 'minimized'})['targetId']
                session = engine.call('Target.attachToTarget', {
                    'targetId': target, 'flatten': True})['sessionId']
                fixture = ('<html><body><h1>Native GPU import</h1><div style="width:70vw;'
                           'height:60vh;background:linear-gradient(blue,cyan);animation:m 1s '
                           'infinite alternate"></div><style>@keyframes m{to{transform:'
                           'translateX(30px);filter:hue-rotate(180deg)}}</style></body></html>')
                engine.call('Page.navigate', {'url': 'data:text/html,' + quote(fixture)}, session)
                report['gpu'] = engine.call('SystemInfo.getInfo')['gpu']['devices']
                time.sleep(8)
                targets = engine.call('Target.getTargets')['targetInfos']
                target_ids = {item['targetId'] for item in targets if item['type'] == 'page'}
                report['frame_target_ids_verified'] = bool(report['frames']) and all(
                    item.get('target_id') in target_ids for item in report['frames'])
            except Exception as error:
                report['errors'].append('engine: ' + str(error))
            finally:
                GLib.idle_add(loop.quit)

        receiver = threading.Thread(target=receive, daemon=True)
        exerciser = threading.Thread(target=exercise, daemon=True)
        receiver.start()
        exerciser.start()
        try:
            loop.run()
        finally:
            stopped.set()
            receiver.join(timeout=2)
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            texture_holder[0] = None
            page.clear()
            gc.collect()
            report['unreleased_before_engine_close'] = len(pending)
            engine.close()
            if peer[0] is not None:
                peer[0].close()
            listener.close()
        lines = (profile.parent / (profile.name + '.log')).read_text(errors='replace').splitlines()
        report['capture_log'] = [line.split('VIOLA_NATIVE_FRAME_PROBE', 1)[1]
                                 for line in lines if 'VIOLA_NATIVE_FRAME_PROBE' in line]
    (args.work_root / 'gtk-import-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
