#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise opt-in compact and media controls through real GTK allocation."""

from __future__ import annotations

import os
from pathlib import Path
import time

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk


def settle() -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.005)


base_path = Path(os.environ.get("LUMA_APPKIT_BASE_PATH", "appkit/luma-appkit-base.css"))
style_root = base_path.parent
display = Gdk.Display.get_default()
assert display is not None
app = Adw.Application(
    application_id="org.projectluma.ControlStyleTest",
    flags=Gio.ApplicationFlags.NON_UNIQUE,
)
assert app.register(None)
results: dict[str, dict[str, tuple[int, ...]]] = {}

for dark in (False, True):
    scheme = Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT
    Adw.StyleManager.get_default().set_color_scheme(scheme)
    tokens = Gtk.CssProvider()
    token_name = "luma-appkit-dark-tokens.css" if dark else "luma-appkit-tokens.css"
    tokens.load_from_path(str(style_root / token_name))
    base = Gtk.CssProvider()
    base.load_from_path(str(base_path))
    Gtk.StyleContext.add_provider_for_display(
        display, tokens, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    Gtk.StyleContext.add_provider_for_display(
        display, base, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    ordinary_height: int | None = None
    for capability in ("pointer", "control-touch", "window-touch"):
        window = Adw.ApplicationWindow(application=app)
        window.add_css_class("luma-app-window")
        if capability == "window-touch":
            window.add_css_class("luma-touch")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        window.set_content(box)

        button = Gtk.Button(label="Open")
        button.add_css_class("luma-button")
        button.add_css_class("small")
        dropdown = Gtk.DropDown.new_from_strings(["Editable documents", "All files"])
        dropdown.add_css_class("luma-dropdown")
        dropdown.add_css_class("small")
        ordinary = Gtk.DropDown.new_from_strings(["Unmodified control"])
        media = Gtk.Button(icon_name="media-playback-start-symbolic")
        media.add_css_class("luma-media-play")
        media.update_property([Gtk.AccessibleProperty.LABEL], ["Play"])
        waveform = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.01)
        waveform.set_draw_value(False)
        waveform.add_css_class("luma-audio-waveform-range")
        waveform.set_size_request(240, 1)

        if capability == "control-touch":
            button.add_css_class("touch-targets")
            dropdown.add_css_class("touch-targets")
        for child in (button, dropdown, ordinary, media, waveform):
            box.append(child)
        window.present()
        settle()

        heights = tuple(
            child.get_height() for child in (button, dropdown, ordinary, media)
        )
        touch = capability != "pointer"
        assert heights[0] == (44 if touch else 28), heights
        assert heights[1] == heights[0], heights
        assert heights[3] == 46, heights
        if ordinary_height is None:
            ordinary_height = heights[2]
        assert heights[2] == ordinary_height, heights
        minimum, natural, _minimum_baseline, _natural_baseline = waveform.measure(
            Gtk.Orientation.VERTICAL, -1
        )
        assert minimum >= 0 and natural >= minimum, (minimum, natural)
        results[f"{'dark' if dark else 'light'}-{capability}"] = {
            "heights": heights,
            "waveform_vertical": (minimum, natural),
        }
        window.destroy()
        settle()

    Gtk.StyleContext.remove_provider_for_display(display, base)
    Gtk.StyleContext.remove_provider_for_display(display, tokens)

print(
    "PASS: compact dropdown/button 28px pointer and 44px touch; "
    "ordinary dropdown unchanged; media control 46px; waveform range nonnegative"
)
