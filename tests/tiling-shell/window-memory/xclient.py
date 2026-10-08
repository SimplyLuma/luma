#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
# X11 stand-in for apps that choose their own window position, like Electron
# restoring saved bounds: xclient.py CLASS X Y W H [--move]
# Without --move the position and size are requested in WM_NORMAL_HINTS
# (USPosition/USSize) before the window is mapped; with --move the window is
# mapped first and then moved there with a ConfigureRequest. With --wiggle the
# window keeps resizing itself for a second after it is mapped, as Electron
# does while it lays out, and never waits for the window manager.
import sys
import time
from Xlib import X, Xutil, display

cls, x, y, w, h = sys.argv[1], *map(int, sys.argv[2:6])
move = "--move" in sys.argv
wiggle = "--wiggle" in sys.argv
d = display.Display()
screen = d.screen()
win = screen.root.create_window(x, y, w, h, 0, screen.root_depth, X.InputOutput, X.CopyFromParent,
                                background_pixel=0xd97757, event_mask=X.StructureNotifyMask)
win.set_wm_name(cls)
win.set_wm_class(cls.lower(), cls)
hints = {"flags": Xutil.USPosition | Xutil.USSize | Xutil.PPosition | Xutil.PSize, "x": x, "y": y, "width": w, "height": h}
if move:
    hints = {"flags": Xutil.PSize, "width": 640, "height": 400}
    win.configure(width=640, height=400)
win.set_wm_normal_hints(**hints)
win.map()
d.flush()
if move:
    time.sleep(0.6)
    win.configure(x=x, y=y, width=w, height=h)
    d.flush()
if wiggle:
    for i in range(10):
        time.sleep(0.1)
        win.configure(width=w - 40 + (i % 2) * 40, height=h - 30 + (i % 2) * 30)
        d.flush()
while True:
    while d.pending_events():
        d.next_event()
    time.sleep(0.05)
