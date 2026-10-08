"""Private-X11 Calculator layout check: run with GTK/AppKit on the host or toolbox."""

from __future__ import annotations

import os
from decimal import Decimal

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

import luma_calc.app as calc_app


calc_app.APP_ID = "org.projectluma.Calculator.LayoutCheck"
application = calc_app.CalculatorApplication()
application.register(None)
window = calc_app.CalculatorWindow(application)
width = int(os.environ["CALC_CHECK_WIDTH"])
expression = os.environ["CALC_CHECK_EXPRESSION"]
scientific = os.environ.get("CALC_CHECK_SCIENTIFIC") == "1"
if scientific:
    window._set_scientific(True)
window.set_default_size(width, 640)
window.display._on_reuse = None
if os.environ.get("CALC_CHECK_HISTORY"):
    window.display.set_tape([("1368+2420", Decimal("3788")),
                             ("1234+134", Decimal("1368"))])
else:
    window.display.set_tape([("132×5", Decimal("660"))])
window.display.set_expression(expression)
window.present()
loop = GLib.MainLoop()
failures = []


def inspect() -> bool:
    display = window.display
    view = display.tape_view
    assert width - 20 <= window.get_width() <= width, (window.get_width(), width)
    line = display.tape_lines.get_first_child()
    result_stop = None
    while line is not None:
        calculation = line.get_first_child()
        result = calculation.get_next_sibling()
        line_end = line.get_allocation().x + line.get_width()
        result_end = result.get_allocation().x + result.get_width()
        assert result_end <= line.get_width(), (result_end, line.get_width())
        assert line_end <= display.tape_lines.get_width(), (line_end, display.tape_lines.get_width())
        result_stop = (result_end, line.get_width())
        line = line.get_next_sibling()
    assert display.tape_lines.get_width() <= view.get_width()
    shown = "".join(label.get_text() for label in display.expression_label._labels)
    assert "".join(shown.split()) == "".join(expression.split()), (shown, expression)
    if " + " in expression:
        assert [label.get_text() for label in display.expression_label._labels] == [
            "660123 ", "+", " 242424553"
        ]
    fit = display.expression_label
    assert 24 <= fit._size <= 72
    adjustment = fit.get_hadjustment()
    if adjustment.get_upper() > adjustment.get_page_size() + 1:
        assert fit._size == 24
        assert abs(adjustment.get_value() - (adjustment.get_upper() - adjustment.get_page_size())) <= 1
    else:
        assert sum(label.get_width() for label in fit._labels) <= fit.get_width(), fit.get_width()
    zero = window._keys["0"]
    # v71 .cazero: the desktop double-width zero starts 28 px inside the key.
    assert zero.get_child().get_xalign() == 0
    assert zero.get_child().get_margin_start() + zero.get_style_context().get_padding().left == 28
    padding = zero.get_style_context().get_padding()
    assert padding.left == padding.right, (padding.left, padding.right)
    keys = tuple(window._keys.values())
    assert keys and all(key.has_css_class("calc-key") for key in keys)
    visible_keys = [key for key in keys if key.get_mapped()]
    assert len(visible_keys) == (44 if scientific else 19), len(visible_keys)
    assert all(key.get_height() >= 48 for key in visible_keys), [key.get_height() for key in visible_keys]
    assert bool(window.keypad.scientific_bank.get_parent()) == scientific
    if scientific:
        bank_scroll = window._keypad_container.get_hadjustment()
        if width <= 390:
            assert bank_scroll.get_upper() > bank_scroll.get_page_size()
        else:
            assert bank_scroll.get_upper() <= bank_scroll.get_page_size() + 1
    if image_path := os.environ.get("CALC_CHECK_SCREENSHOT"):
        paintable = Gtk.WidgetPaintable.new(window)
        snapshot = Gtk.Snapshot.new()
        paintable.snapshot(snapshot, window.get_width(), window.get_height())
        node = snapshot.to_node()
        window.get_renderer().render_texture(node, None).save_to_png(image_path)
    print(f"PASS request={width} window={window.get_width()} scientific={scientific} expression_px={fit._size} "
          f"tape_result_right={result_stop[0]}/{result_stop[1]} "
          f"scroll={adjustment.get_value()}/{adjustment.get_upper()}", flush=True)
    loop.quit()
    return GLib.SOURCE_REMOVE


def safely_inspect() -> bool:
    try:
        return inspect()
    except Exception as exc:
        failures.append(exc)
        print(f"FAIL width={width}: {exc!r}", flush=True)
        loop.quit()
        return GLib.SOURCE_REMOVE


GLib.timeout_add(500, safely_inspect)
loop.run()
window.close()
if failures:
    raise failures[0]
