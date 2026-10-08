#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
# Stand-in app for window memory tests: app.py APP_ID NAME COLOR [--dialog] [--splash] [--windows N] [--size WxH] [--clicks] [--adw] [--slow-relayout MS]
# --adw: a libadwaita window with a header bar, like the Luma apps.
# --slow-relayout MS: every layout at a size other than the window's first takes
#   MS longer, like a heavy app relayouting after a resize.
# --clicks: the window is one button; each click appends a line to $XDG_RUNTIME_DIR/clicks-APP_ID.
import sys
import time
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk, GLib

app_id, name, color = sys.argv[1:4]
flags = sys.argv[4:]
windows = int(flags[flags.index("--windows") + 1]) if "--windows" in flags else 1

css = Gtk.CssProvider()
css.load_from_string(f"""
window.standin {{ background: {color}; }}
label.name {{ color: white; font-size: 44px; font-weight: 800; }}
window.dialog {{ background: #fafafa; }}
label.dialog {{ color: #222; font-size: 20px; }}
""")


def slow_relayout(base, ms):
    class Slow(base):
        first = None

        def do_size_allocate(self, width, height, baseline):
            if self.first is None:
                self.first = (width, height)
            elif (width, height) != self.first:
                time.sleep(ms / 1000)
            base.do_size_allocate(self, width, height, baseline)
    return Slow


def activate(app):
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, 800)
    if "--splash" in flags:
        splash = Gtk.Window(application=app, title=f"{name} splash", resizable=False, decorated=False)
        splash.add_css_class("standin")
        splash.set_default_size(360, 200)
        splash.set_child(Gtk.Label(label=f"{name}…", css_classes=["name"]))
        splash.present()
    main = None
    for i in range(windows):
        if "--adw" in flags:
            gi.require_version("Adw", "1")
            from gi.repository import Adw
            Adw.init()
            cls = Adw.ApplicationWindow
        else:
            cls = Gtk.ApplicationWindow
        if "--slow-relayout" in flags:
            cls = slow_relayout(cls, int(flags[flags.index("--slow-relayout") + 1]))
        win = cls(application=app, title=name if i == 0 else f"{name} {i + 1}")
        win.add_css_class("standin")
        size = flags[flags.index("--size") + 1].split("x") if "--size" in flags else (800, 600)
        win.set_default_size(int(size[0]), int(size[1]))
        label = Gtk.Label(label=name if i == 0 else f"{name} {i + 1}", css_classes=["name"])
        if "--clicks" in flags and i == 0:
            button = Gtk.Button(child=label, hexpand=True, vexpand=True)
            log = f"{GLib.get_user_runtime_dir()}/clicks-{app_id}"
            def clicked(_button, path=log):
                with open(path, "a") as f:
                    f.write("click\n")
            button.connect("clicked", clicked)
            content = button
        else:
            content = label
        if "--adw" in flags:
            from gi.repository import Adw
            view = Adw.ToolbarView()
            view.add_top_bar(Adw.HeaderBar())
            view.set_content(content)
            win.set_content(view)
        else:
            win.set_child(content)
        win.present()
        main = main or win
    if "--dialog" in flags:
        def show_dialog():
            dialog = Gtk.Window(application=app, title=f"{name} dialog", transient_for=main, modal=True)
            dialog.add_css_class("dialog")
            dialog.set_default_size(420, 220)
            dialog.set_child(Gtk.Label(label=f"{name} dialog", css_classes=["dialog"]))
            dialog.present()
            return False
        GLib.timeout_add(700, show_dialog)


app = Gtk.Application(application_id=app_id)
app.connect("activate", activate)
app.run([])
