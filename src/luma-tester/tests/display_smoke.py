# SPDX-License-Identifier: Apache-2.0
"""Check GTK4/GVnc against a real private QEMU display; no guest OS required."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk
from luma_tester.core import Session, digest
from luma_tester.display import GuestDisplay


def main():
    with tempfile.TemporaryDirectory(prefix='luma-tester-display-') as temporary:
        root = Path(temporary)
        output = Path(os.environ.get('DEV_ARTIFACT_DIR', temporary))
        source = root / 'fixture.qcow2'
        subprocess.run(['qemu-img', 'create', '-q', '-f', 'qcow2', str(source), '32M'], check=True)
        manifest = root / 'fixture.json'
        manifest.write_text(json.dumps({'schema': 1, 'id': 'display-test', 'target': 'mobile',
            'arch': 'x86_64', 'boot': 'bios', 'cloud_init': True, 'user': 'luma', 'build': 'NO OS display fixture',
            'image': {'url': source.as_uri(), 'sha256': digest(source), 'bytes': source.stat().st_size}}))
        session = Session(manifest, root / 'state')
        with socket.socket() as port_socket:
            port_socket.bind(('127.0.0.1', 0))
            port = port_socket.getsockname()[1]
        log = open(output / 'display-qemu.log', 'w')
        child = subprocess.Popen([sys.executable, '-m', 'luma_tester', '--state', str(session.root),
            'start', str(manifest), '--embedded', '--software', '--graphics', 'software', '--memory', '1024', '--cpus', '1',
            '--port', str(port)], stdout=log, stderr=log)
        errors = []
        viewer = None
        phase = 0
        last_frame = 0
        deadline = time.monotonic() + 20
        loop = GLib.MainLoop()
        window = Gtk.Window(title='Luma display integration', default_width=500, default_height=700)

        def inspect():
            nonlocal viewer, phase, last_frame
            try:
                if time.monotonic() > deadline:
                    raise AssertionError('Live display did not produce a frame within 20 seconds.')
                if child.poll() is not None:
                    raise AssertionError('QEMU stopped unexpectedly: ' + (output / 'display-qemu.log').read_text())
                if phase == 0 and session.display_path.is_socket():
                    viewer = GuestDisplay(session.display_path)
                    viewer.connect('display-error', lambda _v, message: errors.append(RuntimeError(message)))
                    window.set_child(viewer)
                    # The real launcher opens VNC behind its loading page.
                    # Frames must flow before the viewer is ever mapped.
                    assert viewer.open()
                    phase = 1
                elif phase == 1 and viewer.frames >= 1:
                    assert not viewer.get_mapped()
                    window.present()
                    session.qmp('stop')
                    last_frame = viewer.frames
                    viewer.connection.framebuffer_update_request(False, 0, 0,
                        viewer.framebuffer.width, viewer.framebuffer.height)
                    phase = 2
                elif phase == 2 and viewer.frames > last_frame:
                    assert viewer.ready
                    texture = viewer.get_paintable()
                    assert texture.get_width() == viewer.framebuffer.width
                    assert texture.get_height() == viewer.framebuffer.height
                    assert texture.save_to_png(str(output / 'live-frame.png'))
                    session.screenshot(output / 'qmp-frame.ppm')
                    from luma_tester.rfb import capture
                    capture(session.display_path, output / 'rfb-frame.ppm')
                    subprocess.run(['compare', '-metric', 'AE', str(output / 'rfb-frame.ppm'),
                                    str(output / 'qmp-frame.ppm'), 'null:'], check=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    # Equal pixels verify channel order, stride and ownership against
                    # the emulator's own screenshot, beyond a mere successful handshake.
                    subprocess.run(['compare', '-metric', 'AE', str(output / 'live-frame.png'),
                                    str(output / 'qmp-frame.ppm'), 'null:'], check=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    width, height = viewer.get_width(), viewer.get_height()
                    assert viewer.coordinates(width / 2, height / 2) is not None
                    assert viewer.coordinates(-10, -10) is None
                    viewer.grab_focus()
                    viewer.key_pressed(None, 0x61, 0, 0)
                    assert 0x61 in viewer.pressed_keys
                    viewer.key_released(None, 0x61, 0, 0)
                    assert not viewer.pressed_keys
                    viewer.close()
                    session.qmp('quit')
                    child.wait(timeout=10)
                    assert child.returncode == 0
                    loop.quit()
                    return False
                if errors:
                    raise errors[0]
            except BaseException as exc:
                errors.append(exc)
                loop.quit()
                return False
            return True

        GLib.timeout_add(100, inspect)
        try:
            loop.run()
            if errors:
                raise errors[0]
            assert phase == 2
            print('Private Unix VNC connection, real GTK4 frames, exact QMP pixel parity, coordinate mapping and input dispatch passed.')
        finally:
            if viewer:
                viewer.close()
            window.destroy()
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
            log.close()


if __name__ == '__main__':
    main()
