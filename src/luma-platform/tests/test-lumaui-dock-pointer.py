#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Dock seams dragged with real pointer events (XTest on X11) keep their size: through a relayout and
a window resize, for a plain split and for a fitted side. Skipped (77) without X11 and XTest."""
import ctypes, os, sys, time
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Gdk", "4.0")
from gi.repository import GLib, Gtk, Gdk
from luma_appkit import install_appkit, Dock, DockTile
Gtk.init(); install_appkit()
if type(Gdk.Display.get_default()).__name__ != "X11Display" or not os.environ.get("DISPLAY"):
    sys.exit(77)
try:
    x11 = ctypes.CDLL("libX11.so.6"); xtst = ctypes.CDLL("libXtst.so.6")
except OSError:
    sys.exit(77)
x11.XOpenDisplay.restype = ctypes.c_void_p
dpy = x11.XOpenDisplay(os.environ["DISPLAY"].encode())
def flush(): x11.XFlush(ctypes.c_void_p(dpy))
def move(x, y): xtst.XTestFakeMotionEvent(ctypes.c_void_p(dpy), -1, int(x), int(y), 0); flush()
def button(down): xtst.XTestFakeButtonEvent(ctypes.c_void_p(dpy), 1, 1 if down else 0, 0); flush()
def settle(t=0.3):
    end = time.monotonic() + t
    while time.monotonic() < end:
        while GLib.MainContext.default().pending(): GLib.MainContext.default().iteration(False)
        time.sleep(0.005)
strips = Gtk.Box(height_request=150)
tiles = [DockTile("preview", "Preview", "monitor", Gtk.Box(), main=True, bare=True),
         DockTile("audio", "Audio", "volume-2", strips, fit=True),
         DockTile("widgets", "Widgets", "layers", Gtk.Box(), fit=True),
         DockTile("inspector", "Inspector", "sliders", Gtk.Box(width_request=280))]
d = Dock(tiles, layout={"split": "h", "ratio": 0.75, "start": {"split": "v", "ratio": 0.7, "start": "preview",
         "end": {"split": "h", "ratio": 0.5, "start": "widgets", "end": "audio"}}, "end": "inspector"})
win = Gtk.Window(default_width=1200, default_height=760, decorated=False); win.set_child(d); win.present()
settle(1.0)
def drag(paned, dx, dy, steps=12):
    ok, b = paned.compute_bounds(win)
    child = paned.get_first_child()
    while child is not None and child.get_css_name() != "separator": child = child.get_next_sibling()
    ok, sb = child.compute_bounds(win)
    sx, sy = sb.get_x() + sb.get_width() / 2, sb.get_y() + sb.get_height() / 2
    move(sx, sy); settle(0.15); button(True); settle(0.1)
    for i in range(1, steps + 1):
        move(sx + dx * i / steps, sy + dy * i / steps); settle(0.03)
    settle(0.1); button(False); settle(0.6)
outer = next(p for p, n in d._panes if n["split"] == "h" and n["end"] == "inspector")
vsplit = next(p for p, n in d._panes if n["split"] == "v")
insp = tiles[3]; audio = tiles[1]
w0 = insp.get_width(); h0 = audio.get_height()
print("before inspector", w0, "audio", h0, flush=True)
drag(outer, -150, 0)
w1 = insp.get_width()
print("after drag inspector", w1, flush=True)
drag(vsplit, 0, -120)
h1 = audio.get_height()
print("after drag audio", h1, flush=True)
d.queue_resize(); settle(0.5)
print("after relayout", insp.get_width(), audio.get_height(), flush=True)
win.set_default_size(1300, 800); settle(0.8)
print("after resize", insp.get_width(), audio.get_height(), "layout", d.layout(), flush=True)
ok = abs(w1 - (w0 + 150)) <= 6 and h1 >= h0 + 100
assert ok, "the seams went back"
print("ok")
