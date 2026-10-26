# SPDX-License-Identifier: Apache-2.0
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--application-id", default="org.project_luma.PresentationFixture")
parser.add_argument("--title", default="Presentation document fixture")
parser.add_argument("--no-decorations", action="store_true")
args = parser.parse_args()
app = Gtk.Application(application_id=args.application_id)
def activate(app):
    window = Gtk.ApplicationWindow(application=app, title=args.title)
    window.set_default_size(640, 480)
    if args.no_decorations: window.set_decorated(False)
    window.set_child(Gtk.Entry(text="Unsaved document survives presentation changes"))
    window.present()
app.connect("activate", activate)
app.run(None)
