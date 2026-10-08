# SPDX-License-Identifier: Apache-2.0
"""Read actual GTK pressed state, including a control for the former enum GValue."""
import ctypes
import ctypes.util
import os
import resource
import subprocess
import sys

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
from luma_appkit.content_controls import IconOnlyButton

Gtk.init()
if sys.argv[1:] == ['--old-enum-control']:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    widget = Gtk.Button()
    widget.update_state([Gtk.AccessibleState.PRESSED], [Gtk.AccessibleTristate.TRUE])
    raise AssertionError('the old enum GValue unexpectedly succeeded')

# The GTK test API is variadic and has no GI wrapper. Declare its actual C
# argument types for the one PRESSED tristate value, avoiding vararg inference.
gtk = ctypes.CDLL(ctypes.util.find_library('gtk-4'))
gtk.gtk_test_accessible_check_state.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gtk.gtk_test_accessible_check_state.restype = ctypes.c_void_p
glib = ctypes.CDLL(ctypes.util.find_library('glib-2.0'))
glib.g_free.argtypes = [ctypes.c_void_p]
capsule_pointer = ctypes.pythonapi.PyCapsule_GetPointer
capsule_pointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
capsule_pointer.restype = ctypes.c_void_p

def mismatch(widget, expected):
    pointer = capsule_pointer(widget.__gpointer__, None)
    result = gtk.gtk_test_accessible_check_state(pointer, int(Gtk.AccessibleState.PRESSED), int(expected))
    if result:
        try:
            return ctypes.string_at(result).decode('utf-8')
        finally:
            glib.g_free(result)
    return None

button = IconOnlyButton('heart', 'Favorite', active=False)
for active in (False, True, False, True, False):
    button.set_active(active)
    expected = Gtk.AccessibleTristate.TRUE if active else Gtk.AccessibleTristate.FALSE
    opposite = Gtk.AccessibleTristate.FALSE if active else Gtk.AccessibleTristate.TRUE
    assert mismatch(button, expected) is None, (active, mismatch(button, expected))
    assert mismatch(button, opposite) is not None, 'GTK state observer did not detect a wrong value'
    assert button.has_css_class('on') == active

control = subprocess.run([sys.executable, __file__, '--old-enum-control'],
                         env={**os.environ, 'G_DEBUG': 'fatal-criticals'},
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
assert control.returncode != 0, 'former enum conversion must fail the native control'
assert b'g_value_get_int' in control.stderr, control.stderr.decode(errors='replace')
print('IconOnlyButton actual GTK pressed true/false and former enum GValue RED control PASS')
