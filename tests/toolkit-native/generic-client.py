#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Actual generic client ancestry, pointer, keyboard and modal regression."""
import ctypes
import sys
import time
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('GdkX11', '4.0')
from gi.repository import Gtk, Gio, GLib, GdkX11
kind = sys.argv[1]
if kind.startswith('adw'):
    gi.require_version('Adw', '1')
    from gi.repository import Adw
    Adw.init()
Gtk.init()


def settle():
    end = time.monotonic() + .4
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


x11 = ctypes.CDLL('libX11.so.6')
xtest = ctypes.CDLL('libXtst.so.6')
x11.XOpenDisplay.restype = ctypes.c_void_p
x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
x11.XDefaultRootWindow.restype = ctypes.c_ulong
x11.XTranslateCoordinates.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong)]
x11.XFlush.argtypes = [ctypes.c_void_p]
x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
x11.XStringToKeysym.restype = ctypes.c_ulong
x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
x11.XKeysymToKeycode.restype = ctypes.c_uint
xtest.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
xtest.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
xtest.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
display = x11.XOpenDisplay(None)
assert display, 'actual X display required'


def click(widget, window):
    valid, bounds = widget.compute_bounds(window)
    assert valid and widget.get_mapped()
    surface = window.get_surface()
    x, y, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
    assert x11.XTranslateCoordinates(display, surface.get_xid(), x11.XDefaultRootWindow(display), 0, 0, ctypes.byref(x), ctypes.byref(y), ctypes.byref(child))
    dx, dy = window.get_surface_transform()
    xtest.XTestFakeMotionEvent(display, -1, int(x.value + dx + bounds.origin.x + bounds.size.width/2), int(y.value + dy + bounds.origin.y + bounds.size.height/2), 0)
    x11.XFlush(display); settle()
    for pressed in (1, 0):
        xtest.XTestFakeButtonEvent(display, 1, pressed, 0)
        x11.XFlush(display); settle()


app = (Adw.Application if kind.startswith('adw') else Gtk.Application)(application_id='org.example.NativeClient', flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
window_type = (Adw.Window if kind == 'adw-plain' else Adw.ApplicationWindow) if kind.startswith('adw') else (Gtk.Window if kind == 'gtk-plain' else Gtk.ApplicationWindow)
window = window_type(application=app, title='Ordinary native client')
window.set_default_size(540, 360)
header = (Adw.HeaderBar if kind.startswith('adw') else Gtk.HeaderBar)()
button = Gtk.Button(label='Ordinary action')
header.pack_start(button)
original_parent = button.get_parent()
body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
entry = Gtk.Entry()
body.append(entry)
if kind.startswith('adw'):
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(header)
    toolbar.set_content(body)
    window.set_content(toolbar)
else:
    window.set_titlebar(header)
    window.set_child(body)
window.present(); settle()
assert button.get_parent() is original_parent, 'generic HeaderBar action was reparented'
assert not header.has_css_class('luma-command-host'), 'generic header became a synthetic command island'
assert not body.has_css_class('luma-native-work-surface'), 'generic content received a global work island'
assert body.get_overflow() == Gtk.Overflow.VISIBLE, 'generic content clipping changed'
activated = []
button.connect('clicked', lambda *_: activated.append(True))
click(button, window)
assert activated == [True], 'actual pointer activation failed'
entry.grab_focus(); settle()
code = x11.XKeysymToKeycode(display, x11.XStringToKeysym(b'x'))
for pressed in (1, 0):
    xtest.XTestFakeKeyEvent(display, code, pressed, 0)
    x11.XFlush(display); settle()
assert entry.get_text() == 'x', 'actual keyboard entry failed'
modal = Gtk.Window(transient_for=window, modal=True, title='Native modal', default_width=220, default_height=160)
close = Gtk.Button(label='Close')
modal.set_child(close)
closed = []
close.connect('clicked', lambda *_: (closed.append(True), modal.close()))
modal.present(); settle(); click(close, modal)
assert closed == [True], 'native modal pointer activation failed'
# Author-owned work styling and clipping survive adoption and replacement.
authored = Gtk.Box()
authored.set_overflow(Gtk.Overflow.HIDDEN)
authored.add_css_class('luma-native-work-surface')
if kind.startswith('adw'):
    window.set_content(authored)
else:
    window.set_child(authored)
settle()
assert authored.get_overflow() == Gtk.Overflow.HIDDEN
assert authored.has_css_class('luma-native-work-surface'), 'author work style stripped on adoption'
replacement = Gtk.Box()
if kind.startswith('adw'):
    window.set_content(replacement)
else:
    window.set_child(replacement)
settle()
assert authored.get_overflow() == Gtk.Overflow.HIDDEN, 'author clipping changed on detach'
assert authored.has_css_class('luma-native-work-surface'), 'author work style stripped on detach'
window.destroy(); settle()
print(f'{kind}: native ancestry, unclipped content, actual pointer, keyboard and modal PASS')
