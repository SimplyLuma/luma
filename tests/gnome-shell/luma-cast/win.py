# SPDX-License-Identifier: GPL-2.0-or-later
import sys
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk, GLib
app_id, name = sys.argv[1], sys.argv[2]
palette = {"Slides": ("#e8eef7", "#2f5fa7", "Q3 Planning Review"), "Mail": ("#f6f1ea", "#b0563d", "Inbox"),
           "Notes": ("#f7f3de", "#8a7422", "Meeting notes"), "Browser": ("#eef4f0", "#2f7a57", "projectluma.org")}
bg, accent, heading = palette.get(name, ("#eeeeee", "#444444", name))
def activate(app):
    css = Gtk.CssProvider()
    css.load_from_string(f"window {{ background: {bg}; }} .band {{ background: {accent}; color: white; font-size: 30px; font-weight: 800; padding: 22px; }} .body {{ color: #333; font-size: 15px; }}")
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, 800)
    win = Gtk.ApplicationWindow(application=app, title=f"{name} — {heading}")
    win.set_default_size(1100, 720)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    band = Gtk.Label(label=heading, xalign=0); band.add_css_class("band"); box.append(band)
    body = Gtk.Label(label="\n".join(f"•  Item {i+1}: numbers, owners and next steps" for i in range(18)), xalign=0, yalign=0)
    body.add_css_class("body"); body.set_margin_start(24); body.set_margin_top(18); box.append(body)
    win.set_child(box); win.present()
app = Gtk.Application(application_id=app_id)
app.connect("activate", activate)
app.run([])
