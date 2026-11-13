# SPDX-License-Identifier: Apache-2.0
"""A tooltip leaves its target when the target is disposed, and a finger never shows it."""
import gc
import time
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from luma_appkit import install_appkit, install_lumaui
from luma_appkit.content_tooltip import Tooltip, _touch


def settle():
    deadline = time.monotonic() + .3
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


class Device:
    def __init__(self, source):
        self.source = source

    def get_source(self):
        return self.source


class Controller:
    def __init__(self, device):
        self.device = device

    def get_current_event_device(self):
        return self.device


def main():
    app = Adw.Application(application_id="org.projectluma.TooltipLifecycleTest",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    install_appkit(); install_lumaui()
    window = Gtk.ApplicationWindow(application=app, default_width=360, default_height=220)
    box = Gtk.Box()
    window.set_child(box)
    window.present(); settle()

    def fill():
        made = []
        for index in range(8):
            button = Gtk.Button(label=str(index))
            made.append(Tooltip.attach(button, f"Button {index}"))
            box.append(button)
        return made
    tips = fill()
    settle()
    tips[0].later(); settle()  # shown, as a pointer hovering would
    # A page rebuild: the old content goes, as Depot's pages replace theirs.
    while (child := box.get_first_child()) is not None:
        box.remove(child)
    gc.collect(); settle()
    for tip in tips:
        assert tip.get_parent() is None, "a tooltip must not outlive its target as its child"
        assert not tip.get_visible()

    assert _touch(Controller(Device(Gdk.InputSource.TOUCHSCREEN)))
    assert not _touch(Controller(Device(Gdk.InputSource.MOUSE)))
    assert not _touch(Controller(None))
    window.destroy(); settle()
    print("PASS tooltips leave disposed targets; touch crossings do not show them", flush=True)


if __name__ == "__main__":
    main()
