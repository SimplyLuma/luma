# SPDX-License-Identifier: Apache-2.0
"""A decorated GTK window whose text field sits at the bottom, under where an
on-screen keyboard opens. Every change to the text is written to OUTPUT so a
native check can compare what was typed with what the application received."""
import sys

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

output = sys.argv[1]
app = Gtk.Application(application_id="org.example.TypingFixture")


def activate(application):
    window = Gtk.ApplicationWindow(application=application, title="Typing fixture")
    window.set_default_size(360, 560)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.append(Gtk.Label(label="Typing fixture", vexpand=True))
    entry = Gtk.Entry(input_hints=Gtk.InputHints.WORD_COMPLETION)

    def changed(widget):
        with open(output, "w", encoding="utf-8") as stream:
            stream.write(widget.get_text())
    entry.connect("changed", changed)
    box.append(entry)
    window.set_child(box)
    window.present()
    entry.grab_focus()


app.connect("activate", activate)
app.run(None)
