# SPDX-License-Identifier: Apache-2.0
"""Identity menus reach their final anchor before the first mapped frame."""
import time
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from luma_appkit import AppWindow, Command, CommandGroup, CommandRegistry


def settle(seconds=.3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.002)


app = Adw.Application(application_id="org.projectluma.IdentityPositionTest",
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
commands = CommandRegistry((CommandGroup(None, (
    Command("new", "New item", lambda: None, icon="file-plus"),
    Command("search", "Search this application's content", lambda: None, icon="search"),
)),))
for variant in ("app", "compact"):
    window = AppWindow(application=app, app_id=app.get_application_id(),
                       title="Menu position", icon_name="application-x-executable",
                       commands=commands, identity_menu_variant=variant,
                       default_width=900, default_height=600,
                       minimum_width=360, minimum_height=294)
    window.present()
    settle()
    identity = window.identity
    menu = identity.get_popover()
    mapped = []
    frames = []

    def first_map(popover):
        present, rect = popover.get_pointing_to()
        assert present, "Identity popup mapped before its final anchor was specified"
        mapped.append((rect.x, rect.y, rect.width, rect.height))

    def frame(popover, clock):
        surface = popover.get_surface()
        if isinstance(surface, Gdk.Popup) and surface.get_mapped():
            frames.append((surface.get_position_x(), surface.get_position_y()))
        return GLib.SOURCE_CONTINUE

    menu.connect("map", first_map)
    tick = menu.add_tick_callback(frame)
    for width in (900, 500, 900):
        window.set_default_size(width, 600)
        settle()
        for _ in range(2):
            mapped.clear()
            frames.clear()
            identity.popup()  # The same GtkMenuButton path used by pointer and keyboard.
            settle(.35)
            assert len(mapped) == 1, mapped
            present, rect = menu.get_pointing_to()
            assert present and mapped[0] == (rect.x, rect.y, rect.width, rect.height), (
                "Identity popup anchor changed after map", mapped, rect)
            assert len(frames) >= 2, (variant, width, frames)
            assert len(set(frames)) == 1, ("Visible popup shifted", variant, width, frames)
            identity.popdown()
            settle(.15)
    menu.remove_tick_callback(tick)
    window.destroy()
    settle(.1)
print("Identity menu first-map anchor and frame positions PASS (app/compact, repeated opens/resizes)")
