#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

"""Content-neutral typing surface for a temporary native keyboard lab."""

import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402


class KeyboardLab(Gtk.Window):
    def __init__(self, engine):
        super().__init__(title=f"{engine.title()} keyboard lab")
        self.set_default_size(720, 1040)
        self.set_border_width(24)
        self.connect("destroy", Gtk.main_quit)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.add(box)

        heading = Gtk.Label()
        heading.set_markup(f"<span size='xx-large' weight='bold'>{engine.title()} keyboard lab</span>")
        heading.set_xalign(0)
        box.pack_start(heading, False, False, 0)

        compositor = ("physical compositor" if engine in ("wkeys", "lomiri")
                      else "native nested compositor")
        explainer = Gtk.Label(
            label=f"This is the real keyboard in its {compositor}. "
                  "Nothing typed here is saved or logged. Close the lab to return to Luma.")
        explainer.set_line_wrap(True)
        explainer.set_xalign(0)
        box.pack_start(explainer, False, False, 0)

        fields = (
            ("General typing", Gtk.InputPurpose.FREE_FORM),
            ("Email", Gtk.InputPurpose.EMAIL),
            ("Web address", Gtk.InputPurpose.URL),
            ("Phone number", Gtk.InputPurpose.PHONE),
            ("Numbers", Gtk.InputPurpose.NUMBER),
        )
        first = None
        for label, purpose in fields:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            title = Gtk.Label(label=label)
            title.set_xalign(0)
            entry = Gtk.Entry()
            entry.set_input_purpose(purpose)
            entry.set_placeholder_text(f"Tap to test {label.lower()}")
            row.pack_start(title, False, False, 0)
            row.pack_start(entry, False, False, 0)
            box.pack_start(row, False, False, 0)
            if first is None:
                first = entry

        close = Gtk.Button(label="Close keyboard lab")
        close.connect("clicked", lambda _button: self.destroy())
        box.pack_end(close, False, False, 0)

        self.show_all()
        if first is not None:
            first.grab_focus()


if __name__ == "__main__":
    selected = sys.argv[1] if len(sys.argv) == 2 else "keyboard"
    KeyboardLab(selected)
    Gtk.main()
