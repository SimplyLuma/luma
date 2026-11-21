#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""One ordinary application window for the picker to preview.

  window.py APP_ID TITLE [--live]

--live repaints every frame, a moving bar and a frame counter, standing in for
a playing video: two screenshots of its preview a moment apart must differ,
which is what shows the preview is live and not a snapshot.
"""

import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

app_id, title = sys.argv[1], sys.argv[2]
live = "--live" in sys.argv[3:]


def draw(_area, cr, width, height, state):
    state["frame"] += 1
    t = state["frame"]
    cr.set_source_rgb(0.08, 0.10, 0.14)
    cr.paint()
    x = (t * 9) % max(1, width)
    cr.set_source_rgb(0.95, 0.55, 0.20)
    cr.rectangle(x, 0, width / 5, height)
    cr.fill()
    cr.set_source_rgb(1, 1, 1)
    cr.select_font_face("Sans")
    cr.set_font_size(height / 6)
    cr.move_to(width * 0.06, height * 0.9)
    cr.show_text(f"{t:06d}")


def activate(app):
    win = Gtk.ApplicationWindow(application=app, title=title)
    win.set_default_size(900, 600)
    if live:
        area = Gtk.DrawingArea()
        state = {"frame": 0}
        area.set_draw_func(draw, state)
        area.add_tick_callback(lambda a, _clock: (a.queue_draw(), True)[1])
        win.set_child(area)
    else:
        label = Gtk.Label(label=title)
        label.add_css_class("title-1")
        win.set_child(label)
    win.present()


application = Gtk.Application(application_id=app_id)
application.connect("activate", activate)
application.run([])
