#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""A window for the window-screencast test.

  window.py adw|gtk4|gtk3 TITLE

adw and gtk4 are Wayland windows with client-side decorations and shadows.
gtk3 runs under Xwayland (GDK_BACKEND=x11): with its header bar it is a
client-decorated X11 window with _GTK_FRAME_EXTENTS shadows; with GTK_CSD=0
it has no header bar and Mutter draws its frame.
"""
import os
import sys

toolkit, title = sys.argv[1:3]

if toolkit == "gtk3":
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    window = Gtk.Window(title=title)
    if os.environ.get("GTK_CSD") != "0":
        window.set_titlebar(Gtk.HeaderBar(show_close_button=True, title=title))
    window.set_default_size(520, 360)
    window.add(Gtk.Label(label=title))
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    Gtk.main()
else:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, Gtk
    if toolkit == "adw":
        gi.require_version("Adw", "1")
        from gi.repository import Adw

    def activate(app):
        if toolkit == "adw":
            window = Adw.ApplicationWindow(application=app, title=title)
            view = Adw.ToolbarView()
            view.add_top_bar(Adw.HeaderBar())
            view.set_content(Gtk.Label(label=title))
            window.set_content(view)
        else:
            window = Gtk.ApplicationWindow(application=app, title=title)
            window.set_titlebar(Gtk.HeaderBar())
            window.set_child(Gtk.Label(label=title))
        window.set_default_size(520, 360)
        window.present()

    app = (Adw.Application if toolkit == "adw" else Gtk.Application)(
        application_id=f"org.projectluma.WindowScreencast.{toolkit}",
        flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.connect("activate", activate)
    app.run([])
