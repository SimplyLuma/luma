# SPDX-License-Identifier: Apache-2.0
"""Mapped installed Ari navigation, with no daemon or model connection.

Run under a private D-Bus session and Xvfb. The old Ari13 must fail the
standalone-control assertion; source-shape assertions are insufficient here.
"""
import ctypes
import os
from pathlib import Path
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ari_ui.fixture import FixtureSource
from ari_ui.lumaui_app import AriApplication, AriWindow
from luma_appkit import icons


def settle(predicate=lambda: True):
    deadline = time.monotonic() + 4
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            # Let the allocation, animations, and frame finish before inspecting paint.
            end = time.monotonic() + .15
            while time.monotonic() < end:
                while context.pending():
                    context.iteration(False)
                time.sleep(.005)
            if predicate():
                return
        time.sleep(.005)
    raise AssertionError("Ari did not reach the requested native state")


def f9():
    x11 = ctypes.CDLL("libX11.so.6")
    xtst = ctypes.CDLL("libXtst.so.6")
    x11.XOpenDisplay.restype = ctypes.c_void_p
    display = x11.XOpenDisplay(None)
    assert display, "F9 gate requires an actual X11 display"
    x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XKeysymToKeycode.restype = ctypes.c_uint
    xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
    x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    code = x11.XKeysymToKeycode(display, 0xffc6)
    assert code, "F9 is absent from the native keymap"
    assert xtst.XTestFakeKeyEvent(display, code, True, 0)
    assert xtst.XTestFakeKeyEvent(display, code, False, 0)
    x11.XSync(display, False)
    x11.XCloseDisplay(display)


def capture(name):
    destination = os.environ.get("LUMA_ARI_CAPTURE_DIR")
    if destination:
        from PIL import ImageGrab
        Path(destination).mkdir(parents=True, exist_ok=True)
        ImageGrab.grab().save(Path(destination) / (name + ".png"))


def check_welcome_text(window):
    # Real computed GTK colors: Ari15 inherited white through the button box.
    # A source/CSS-string test cannot establish readable rendered text.
    expected = {"Put my tax documents in one folder", "Quiet until 6, except Priya",
                "What model should I use for code?", "Explain local models in two sentences"}
    found = {}
    def walk(widget):
        if isinstance(widget, Gtk.Label) and widget.has_css_class("ari-suggestion-copy"):
            found[widget.get_text()] = widget
        child = widget.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()
    walk(window)
    assert set(found) == expected, set(found)
    def luminance(color):
        def linear(value):
            return value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
        return sum(weight * linear(value) for weight, value in
                   zip((.2126, .7152, .0722), (color.red, color.green, color.blue)))
    for words, label in found.items():
        assert label.get_mapped() and label.get_width() > 0 and label.get_height() > 0, words
        ok, background = label.get_style_context().lookup_color("luma_content")
        assert ok, "Shared content color missing"
        color = label.get_color()
        dark, light = sorted((luminance(color), luminance(background)))
        assert color.alpha >= .99 and (light + .05) / (dark + .05) >= 4.5, (words, color.to_string(), background.to_string())
    print("Ari mapped welcome: all four texts allocated with readable computed contrast PASS", flush=True)


def main():
    fixture = Path(__file__).resolve().parents[1] / "fixtures/ari-v70.json"
    # Navigation does not need receipt portraits, which are capture-only data
    # outside the installed Python module's directory.
    os.environ["LUMA_ARI_STATE"] = "new"
    app = AriApplication(FixtureSource.read(fixture))
    assert app.register(None)
    window = AriWindow(app, app.source)
    window.set_default_size(1024, 740)
    window.present()
    try:
        settle(lambda: window.get_mapped())
        # Fail before menu lookup on old13, so the negative control diagnoses
        # the real visible widget rather than an absent new command.
        assert not window.toggle.get_visible(), "Ari still displays a standalone sidebar control"
        command = window.commands.get("ari.sidebar")
        assert command.icon == "panel-left" and command.shortcut == ("F9",)
        theme = Gtk.IconTheme.get_for_display(window.get_display())
        commands = [c for g in window.commands.visible_groups(menu=True) for c in g.commands]
        assert len(commands) >= 4
        for item in commands:
            assert item.icon and theme.has_icon(icons.icon_name(item.icon)), item.id
        menu = window.identity.get_popover()
        for width in (360, 500, 1024, 1440):
            window.set_default_size(width, 740)
            settle(lambda: window.get_surface().get_width() == width)
            assert not window.toggle.get_visible() and not window.toggle.get_mapped()
            check_welcome_text(window)
            if width < 560:
                settle(lambda: window.title_island.get_mapped())
                assert not window.title_island.grown
                window.title_island.lead_button.emit("clicked")
                settle(lambda: window.title_island.grown and window.sidebar.get_mapped())
                assert window.foot.entry.get_mapped()
                capture(f"ari-phone-sidebar-{width}")
                window.title_island.lead_button.emit("clicked")
                settle(lambda: not window.title_island.grown)
            else:
                settle(lambda: window.sidebar.get_mapped())
                assert window.sidebar.get_width() > 150
            window.identity.popup()
            settle(lambda: menu.get_mapped())
            # The shared menu ignores the pointer release that opened it.
            opened = time.monotonic()
            settle(lambda: time.monotonic() - opened >= .35)
            assert menu.activate_action("menu.ari-sidebar", None)
            settle(lambda: window.title_island.grown if width < 560 else not window.toggle.shown)
            window.commands.invoke("ari.sidebar")
            settle(lambda: not window.title_island.grown if width < 560 else window.toggle.shown)
            window.entry.focus()
            settle()
            f9()
            settle(lambda: window.title_island.grown if width < 560 else not window.toggle.shown)
            f9()
            settle(lambda: not window.title_island.grown if width < 560 else window.toggle.shown)
            capture(f"ari-navigation-{width}")
            print(f"Ari {width}px: hidden control, mapped navigation, menu icons/action, native F9 PASS", flush=True)
    finally:
        window.close()
        settle()
    print("ARI RESPONSIVE NAVIGATION PASS (4 widths, installed GTK)", flush=True)


if __name__ == "__main__":
    main()
