#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run with the portable `python` inside an X11 or Wayland session.

Exercises GTK's default renderer and an OpenGL context without forcing Cairo,
then rejects graphics implementations loaded from outside the portable bundle.
DEV_ARTIFACT_DIR receives the machine-readable report. Xvfb proves library
isolation and software GL; a physical GPU/session is needed for hardware QA.
"""
import ctypes
import json
import os
from pathlib import Path
import sys
import time

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, GLib, Gtk

base = Path(sys.executable).resolve().parent
allow_cairo = '--allow-cairo' in sys.argv
out = Path(os.environ['DEV_ARTIFACT_DIR'])
out.mkdir(parents=True, exist_ok=True)
window = Gtk.Window(title='Try Luma graphics qualification', default_width=960, default_height=600)
picture = Gtk.Picture(hexpand=True, vexpand=True)
window.set_child(picture)
frames = 0
started = time.monotonic()
failures = []
loop = GLib.MainLoop()


def tick(_widget, _clock):
    global frames
    pixels = GLib.Bytes.new(bytes([frames % 255, 120, 210, 255]) * (960 * 600))
    picture.set_paintable(Gdk.MemoryTexture.new(960, 600, Gdk.MemoryFormat.R8G8B8A8, pixels, 960 * 4))
    frames += 1
    return True


def finish():
    result = dict(passed=False, renderer=window.get_renderer().__class__.__name__,
                  frames=frames, seconds=time.monotonic()-started)
    try:
        try:
            context = window.get_surface().create_gl_context()
            context.realize()
            context.make_current()
            egl = ctypes.CDLL('libEGL.so.1')
            egl.eglGetProcAddress.argtypes = [ctypes.c_char_p]
            egl.eglGetProcAddress.restype = ctypes.c_void_p
            get_string = ctypes.CFUNCTYPE(ctypes.c_char_p, ctypes.c_uint)(egl.eglGetProcAddress(b'glGetString'))
            result['gl_vendor'] = get_string(0x1f00).decode()
            result['gl_renderer'] = get_string(0x1f01).decode()
            context.clear_current()
        except Exception as exc:
            result['gl_error'] = str(exc)
            if not allow_cairo:
                raise
        names = ('libEGL', 'libGL', 'libGLES', 'libgbm', 'libgallium', '_dri.so', 'libvulkan', 'libLLVM')
        mappings = sorted(set(line.split()[-1] for line in Path('/proc/self/maps').read_text().splitlines()
                              if any(name in line for name in names)))
        result['graphics_mappings'] = mappings
        result['host_graphics_loaded'] = [p for p in mappings if not Path(p).resolve().is_relative_to(base)]
        assert not result['host_graphics_loaded'], result['host_graphics_loaded']
        assert frames >= 30, 'Default GTK rendering did not advance normally.'
        assert allow_cairo or 'GL' in result['renderer'] or 'Vulkan' in result['renderer'], result['renderer']
        result['passed'] = True
    except BaseException as exc:
        result['error'] = str(exc)
        failures.append(exc)
    (out/'GRAPHICS-QUALIFICATION.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result), flush=True)
    window.destroy()
    loop.quit()
    return False


picture.add_tick_callback(tick)
window.present()
GLib.timeout_add_seconds(5, finish)
loop.run()
if failures:
    raise SystemExit('Portable graphics qualification failed: '+str(failures[0]))
