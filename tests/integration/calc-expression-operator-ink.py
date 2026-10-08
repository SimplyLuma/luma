#!/usr/bin/env python3
"""Check Calculator's measured expression ink across generated consumers and GTK."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk


ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / "src/luma-platform/appkit/luma_appkit/lumaui_tokens.py"
HEADER = ROOT / "src/luma-platform/ui/luma-ui-tokens-private.h"
SHEETS = {
    "light": ROOT / "src/luma-platform/appkit/luma-appkit-tokens.css",
    "dark": ROOT / "src/luma-platform/appkit/luma-appkit-dark-tokens.css",
}
EXPECTED = {"light": "#2460b7", "dark": "#80b3fd"}

spec = importlib.util.spec_from_file_location("lumaui_tokens", PYTHON)
tokens = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tokens)
header = HEADER.read_text()

Gtk.init()
display = Gdk.Display.get_default()
assert display is not None
window = Gtk.Window()
box = Gtk.Box()
box.add_css_class("calc-display")
label = Gtk.Label(label="+")
label.add_css_class("calc-expression-operator")
box.append(label)
window.set_child(box)
window.present()
while GLib.MainContext.default().pending():
    GLib.MainContext.default().iteration(False)

for family, expected in EXPECTED.items():
    assert tokens.CALC_EXPRESSION_OPERATOR_INK[family] == expected
    assert f'#define LUMA_UI_CALC_EXPRESSION_OPERATOR_INK_{family.upper()} "{expected}"' in header
    sheet = SHEETS[family].read_text()
    match = re.search(r"^@define-color luma_calc_expression_operator_ink ([^;]+);$", sheet, re.M)
    assert match and match.group(1) == expected
    provider = Gtk.CssProvider()
    provider.load_from_data(
        f"@define-color luma_calc_expression_operator_ink {match.group(1)};\n"
        ".calc-display .calc-expression-operator { color: @luma_calc_expression_operator_ink; }"
    )
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    while GLib.MainContext.default().pending():
        GLib.MainContext.default().iteration(False)
    ink = label.get_style_context().get_color()
    actual = "#{:02x}{:02x}{:02x}".format(*(round(v * 255) for v in (ink.red, ink.green, ink.blue)))
    assert actual == expected, (family, actual, expected)
    Gtk.StyleContext.remove_provider_for_display(display, provider)
    print(f"PASS {family} Calculator expression operator GTK ink {actual}")

assert tokens.CALC_EXPRESSION_OPERATOR_INK["high_contrast"] == "@accent_fg_color"
window.close()
