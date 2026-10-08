#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Actual GTK/GDK allocation through X11 WM state, independent of compositor.

Xvfb has no WM. This fixture returns only standard EWMH state properties a
WM would supply; GTK private state and native allocation use the real library.
This proves allocation, not compositor geometry or visual qualification.
"""
import ctypes
import json
import time



def settle():
    end = time.monotonic() + .3
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


lib = ctypes.CDLL('libX11.so.6')
lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
lib.XOpenDisplay.restype = ctypes.c_void_p
lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
lib.XInternAtom.restype = ctypes.c_ulong
lib.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
    ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
lib.XFlush.argtypes = [ctypes.c_void_p]
lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
lib.XDefaultRootWindow.restype = ctypes.c_ulong
lib.XCreateSimpleWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
    ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
    ctypes.c_ulong, ctypes.c_ulong]
lib.XCreateSimpleWindow.restype = ctypes.c_ulong
lib.XSetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                  ctypes.c_ulong, ctypes.c_ulong]
display = lib.XOpenDisplay(None)
assert display, 'X11 display required'
# Advertise a compositor before GTK creates its display, so native CSD uses
# transparent shadow buffers instead of the non-composited solid border.
owner = lib.XCreateSimpleWindow(display, lib.XDefaultRootWindow(display),
                                0, 0, 1, 1, 0, 0, 0)
lib.XSetSelectionOwner(display,
    lib.XInternAtom(display, b'_NET_WM_CM_S0', 0), owner, 0)
wm_check = (ctypes.c_ulong * 1)(owner)
check_atom = lib.XInternAtom(display, b'_NET_SUPPORTING_WM_CHECK', 0)
window_atom = lib.XInternAtom(display, b'WINDOW', 0)
for target in (owner, lib.XDefaultRootWindow(display)):
    lib.XChangeProperty(display, target, check_atom, window_atom, 32, 0,
                        ctypes.cast(wm_check, ctypes.c_void_p), 1)
supported = (ctypes.c_ulong * 1)(
    lib.XInternAtom(display, b'_GTK_FRAME_EXTENTS', 0))
lib.XChangeProperty(display, lib.XDefaultRootWindow(display),
    lib.XInternAtom(display, b'_NET_SUPPORTED', 0),
    lib.XInternAtom(display, b'ATOM', 0), 32, 0,
    ctypes.cast(supported, ctypes.c_void_p), 1)
lib.XSync(display, 0)
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GdkX11', '4.0')
from gi.repository import Gdk, GdkX11, GLib, Gtk
Gtk.init()
assert Gdk.Display.get_default().is_composited(), 'composited display required'
window = Gtk.Window(title='Native maximized shadow allocation')
window.add_css_class('luma-no-native-surfaces')
window.set_titlebar(Gtk.HeaderBar())
window.set_child(Gtk.Label(label='Native maximized shadow allocation'))
window.set_default_size(360, 240)
css = Gtk.CssProvider()
css.load_from_string('''
window.csd { margin: 0; border-radius: 20px;
  box-shadow: 0 8px 24px rgba(0,0,0,.6); }
window.csd.maximized { border-radius: 20px;
  box-shadow: 0 8px 24px rgba(0,0,0,.6); }
window.csd.test-no-shadow { box-shadow: none; }
''')
Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                         Gtk.STYLE_PROVIDER_PRIORITY_USER)
window.present()
settle()
assert isinstance(window.get_surface(), GdkX11.X11Surface)
xid = window.get_surface().get_xid()


def state(names):
    atoms = (ctypes.c_ulong * len(names))(*[
        lib.XInternAtom(display, name.encode(), 0) for name in names])
    lib.XChangeProperty(display, xid,
        lib.XInternAtom(display, b'_NET_WM_STATE', 0),
        lib.XInternAtom(display, b'ATOM', 0), 32, 0,
        ctypes.cast(atoms, ctypes.c_void_p), len(names))
    lib.XFlush(display)
    settle()


observed = {}
observed['floating'] = tuple(window.get_surface_transform())
assert all(value >= 6 for value in observed['floating']), observed
state(['_NET_WM_STATE_MAXIMIZED_HORZ', '_NET_WM_STATE_MAXIMIZED_VERT'])
assert window.is_maximized(), 'actual GTK maximized state required'
observed['maximized'] = tuple(window.get_surface_transform())
assert observed['maximized'] == observed['floating'], (
    'Maximized styled shadow lost its native buffer extents', observed)
window.add_css_class('test-no-shadow')
settle()
observed['maximized_none'] = tuple(window.get_surface_transform())
assert observed['maximized_none'] == (0.0, 0.0), observed
window.remove_css_class('test-no-shadow')
settle()
state(['_NET_WM_STATE_FULLSCREEN'])
assert window.is_fullscreen(), 'actual GTK fullscreen state required'
observed['fullscreen'] = tuple(window.get_surface_transform())
assert observed['fullscreen'] == (0.0, 0.0), observed
state([])
assert not window.is_fullscreen() and not window.is_maximized()
observed['restored'] = tuple(window.get_surface_transform())
assert observed['restored'] == observed['floating'], observed
window.add_css_class('test-no-shadow')
settle()
observed['floating_resize_handles'] = tuple(window.get_surface_transform())
assert all(value >= 6 for value in observed['floating_resize_handles']), observed
window.destroy()
lib.XCloseDisplay(display)
print(json.dumps({'status': 'PASS', 'observed_native_shadow_origins': observed},
                 sort_keys=True))
