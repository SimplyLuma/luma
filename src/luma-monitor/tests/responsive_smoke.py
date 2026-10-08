# SPDX-License-Identifier: Apache-2.0
"""Actual GTK allocations and semantic keyboard controls, without a live session."""
import os
import tempfile
os.environ['XDG_CONFIG_HOME'] = tempfile.mkdtemp(prefix='monitor-layout-')
from luma_monitor.machine_application import MonitorApplication, Gtk, Gdk, GLib, Adw

app = MonitorApplication()
failures = []
cases = [(width, advanced, direction) for width in (360, 500, 1024, 1400)
         for advanced in (False, True) for direction in (Gtk.TextDirection.LTR, Gtk.TextDirection.RTL)]
completed = []

def finish():
    app.quit()
    return GLib.SOURCE_REMOVE

def verify(width, advanced, direction):
    try:
        window = app.get_active_window()
        assert 0 < window.get_width() <= width, (width, window.get_width())
        assert window.search.get_width() > 0 and window.search.get_height() >= 28
        assert window.table_stack.get_width() <= window.get_width()
        assert window.toolbar.get_width() <= window.get_width()
        assert window.figure_row.get_width() <= window.get_width()
        assert window.buttons['filesystems'].get_visible() == advanced
        assert window.advanced_button.get_visible()
        assert window.advanced_button.get_active() == advanced
        assert window.advanced_button.get_width() > 0
        assert window.search.get_accessible_role() != Gtk.AccessibleRole.GENERIC
        assert window.table.get_accessible_role() != Gtk.AccessibleRole.GENERIC
        assert window._key_pressed(None, Gdk.KEY_f, 0, Gdk.ModifierType.CONTROL_MASK)
        assert window.get_focus() is not None
        window.search.set_text('layout-test-no-match')
        window.refresh_rows()
        assert window.table_stack.get_visible_child_name() == 'empty'
        assert window._key_pressed(None, Gdk.KEY_Escape, 0, Gdk.ModifierType(0))
        assert window.search.get_text() == ''
        completed.append((width, advanced, direction.value_nick))
    except Exception as error:
        failures.append(repr(error))
        return finish()
    return next_case()

def next_case():
    if not cases:
        return finish()
    width, advanced, direction = cases.pop(0)
    window = app.get_active_window()
    Gtk.Widget.set_default_direction(direction)
    Adw.StyleManager.get_default().set_color_scheme(
        Adw.ColorScheme.FORCE_DARK if advanced else Adw.ColorScheme.FORCE_LIGHT)
    if window.advanced != advanced:
        window.toggle_advanced()
    window.set_default_size(width, 760)
    window._adapt()
    GLib.timeout_add(250, lambda: verify(width, advanced, direction))
    return GLib.SOURCE_REMOVE

GLib.timeout_add(1500, next_case)
app.run([])
assert not failures, failures
assert len(completed) == 16, completed
print('PASS responsive GTK: 360/500/1024/1400, normal/Advanced, LTR/RTL, light/dark, semantic roles and search keyboard handling')
