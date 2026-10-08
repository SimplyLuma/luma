"""Private Wayland check of v71 phone key geometry and live calculator actions."""
import os
os.environ['LUMA_FORM_FACTOR'] = 'phone'
import time
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
from luma_calc.app import CalculatorApplication, CalculatorWindow


def settle():
    end = time.monotonic() + .6
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.01)


app = CalculatorApplication(); app.register(None)
window = CalculatorWindow(app)
window.present()
try:
    for width in (360, 402, 500):
        window.set_default_size(width, 1000); settle()
        bank = window.keypad.basic_bank
        expected = round((bank.get_width() - 36) / 4 * .9 * 5 + 48)
        assert abs(bank.get_height() - expected) <= 1, (width, bank.get_height(), expected)
        visible = [key for key in window._keys.values() if key.get_mapped()]
        assert len(visible) == 20
        heights = [key.get_allocated_height() for key in visible]
        assert max(heights) - min(heights) <= 1, heights
        assert min(heights) >= 48
        for key in visible:
            ok, bounds = key.compute_bounds(bank)
            assert ok and bounds.get_x() >= 0 and bounds.get_y() >= 0
            assert bounds.get_x() + bounds.get_width() <= bank.get_width() + 1
            assert bounds.get_y() + bounds.get_height() <= bank.get_height() + 1
        print('PASS basic keypad', width, bank.get_height(), flush=True)
    window._keys['mode'].emit('clicked'); settle()
    frame = window._mode_panel
    assert frame.is_open and frame.get_allocated_width() == 240
    assert not frame.handle.scrim.has_css_class('lumaui-scrim')
    frame.close(); settle()
    assert window._mode_panel is None
    assert not window._keys['mode'].has_css_class('on')
    for key in ('2', '+', '3', '='):
        window._keys[key].emit('clicked')
    assert window.calculator.display == '5', window.calculator.display
    window._set_scientific(True); settle()
    assert window.keypad.scientific_bank.get_mapped()
    assert len([key for key in window._keys.values() if key.get_mapped()]) == 45
    for width in (360, 402, 500):
        window.set_default_size(width, 1000); settle()
        assert window.get_width() == width, (width, window.get_width())
        assert window.keypad.scientific_bank.get_width() == width - 32, (width, window.keypad.scientific_bank.get_width())
    window._set_scientific(False); settle()
    assert not window.keypad.scientific_bank.get_mapped()
    print('PASS arithmetic and scientific transitions', flush=True)
finally:
    window.close(); settle()
