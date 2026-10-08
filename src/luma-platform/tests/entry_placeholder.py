# SPDX-License-Identifier: Apache-2.0
"""Mapped compose placeholder alignment, text editing and wrapped-height regression."""
import time
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
from luma_appkit import BarEntry, CornerPill, install_appkit, install_lumaui
from luma_appkit.action_center import make_control


def settle():
    deadline = time.monotonic() + .3
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


def main():
    app = Adw.Application(application_id="org.projectluma.EntryAlignmentTest",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    install_appkit(); install_lumaui()
    window = Gtk.ApplicationWindow(application=app, default_width=360, default_height=220)
    window.add_css_class("luma-app-window")
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
    entry = BarEntry("compose", placeholder="Write a message", grows=True)
    widget = make_control(entry)
    content.append(widget)
    person = Gtk.Button(label="Person", has_frame=False)
    pill = CornerPill(people=person)
    content.append(pill)
    window.set_child(content); window.present(); settle()
    ph = entry.widget.ph
    parent = ph.get_parent()
    ok, rect = ph.compute_bounds(parent)
    assert ok and ph.get_mapped() and ph.get_visible()
    center = rect.get_y() + rect.get_height() / 2
    assert abs(center - parent.get_height() / 2) <= 1, (center, parent.get_height())
    assert person.has_css_class("lumaui-corner-people")
    initial = widget.get_height()
    entry.set_text("First line\nSecond line\nThird line"); settle()
    assert not ph.get_visible() and widget.get_height() > initial
    assert entry.text == "First line\nSecond line\nThird line"
    entry.set_text(""); settle()
    assert ph.get_visible() and widget.get_height() == initial
    window.close()
    print("Compose placeholder centered; edit/wrap/reset and corner identity PASS")


if __name__ == "__main__":
    main()
