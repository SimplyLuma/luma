"""Mapped fixed pane actions and compact lead/value alignment on real GTK."""
import time
import subprocess
import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
gi.require_version('GdkX11', '4.0')
from gi.repository import Adw, GdkX11, Gio, GLib, Gtk
from luma_appkit import AppWindow, CommandRegistry, DetailsFacts, DetailsPane, Island, TextButton, install_appkit, install_lumaui


def settle():
    end = time.monotonic() + .35
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


app = Adw.Application(application_id='org.projectluma.DetailsFooterTest', flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
install_appkit(); install_lumaui()
for width in (360, 500, 1024):
    window = AppWindow(application=app, app_id=app.get_application_id(), title='Pane actions', icon_name='org.projectluma.Tide',
                       commands=CommandRegistry(()), default_width=width, default_height=520,
                       minimum_width=360, minimum_height=420)
    row = Gtk.Box(spacing=8)
    row.append(Island())
    pane = DetailsPane('Details')
    row.append(pane); window.set_body(row)
    for n in range(30):
        pane.add(Gtk.Label(label=f'Long scroll content {n}'))
    clicked = []
    button = TextButton('Add item', on_click=lambda: clicked.append(True))
    pane.set_footer(button)
    window.present(); settle()
    window.set_size_request(width, 520)
    subprocess.run(['xdotool', 'windowsize', '--sync', str(GdkX11.X11Surface.get_xid(window.get_surface())),
                    str(width + 10), '530'], check=True, timeout=5)
    settle(); assert window.get_width() == width
    pane.open(subject='native footer test'); settle()
    assert button.get_mapped()
    ok, bounds = button.compute_bounds(window)
    assert ok and bounds.get_y() >= 0 and bounds.get_y() + bounds.get_height() <= window.get_height()
    adjustment = pane.scroller.get_vadjustment()
    adjustment.set_value(adjustment.get_upper()); settle()
    ok, moved = button.compute_bounds(window)
    assert ok and abs(moved.get_y() - bounds.get_y()) < 1
    if width <= 500:
        for height in (874, 420, 520):
            window.set_size_request(width, height)
            subprocess.run(['xdotool', 'windowsize', '--sync', str(GdkX11.X11Surface.get_xid(window.get_surface())),
                            str(width + 10), str(height + 10)], check=True, timeout=5)
            settle()
            ok, resized = button.compute_bounds(window)
            assert ok and resized.get_y() >= 0 and resized.get_y() + resized.get_height() <= window.get_height()
    button.emit('clicked'); assert clicked == [True]
    try:
        pane.set_footer(pane.title_label)
    except ValueError:
        pass
    else:
        raise AssertionError('must reject an already parented footer')
    pane.clear(); settle()
    assert not pane.footer.get_visible() and button.get_parent() is None
    pane.close(); settle(); window.destroy()

# Lead and value form one end-aligned group: a colour dot has no expanding
# space and the short label has no reserved eight-character blank area.
for value in ('Personal', 'Work', 'A long calendar title that wraps by word'):
    lead = Gtk.Box(width_request=8, height_request=8)
    facts = DetailsFacts([('Calendar', value, lead)])
    window = Gtk.ApplicationWindow(application=app, default_width=288, default_height=120)
    window.add_css_class('luma-app-window'); window.set_child(facts); window.present(); settle()
    line = lead.get_parent(); shown = lead.get_next_sibling()
    ok, dot_bounds = lead.compute_bounds(line)
    ok_label, text_bounds = shown.compute_bounds(line)
    assert ok and ok_label
    assert 4 <= text_bounds.get_x() - dot_bounds.get_x() - dot_bounds.get_width() <= 8
    assert not lead.get_hexpand() and shown.get_width_chars() == 1
    window.destroy()
print('PASS fixed native footer, scroll independence, attached-parent rejection, clear and lead/value alignment')
