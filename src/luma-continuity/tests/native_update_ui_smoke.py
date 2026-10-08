"""Private native GTK test: synthetic updater states, no system mutations."""
import json
import os
from pathlib import Path
import threading
import time
import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk
from luma_appkit import AppWindow, CommandRegistry, install_appkit
from luma_continuity.update_view import UpdateStatusPage

output = Path(os.environ['LUMA_UPDATE_UI_TEST_OUTPUT'])
output.mkdir(parents=True, exist_ok=True)
context = GLib.MainContext.default()
def pump(seconds=.1):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        while context.pending(): context.iteration(False)
        time.sleep(.005)
def wait(predicate):
    until = time.monotonic() + 5
    while not predicate():
        if time.monotonic() > until: raise AssertionError('UI callback timed out')
        pump(.02)
def capture(window, name):
    pump(.25)
    paint = Gtk.WidgetPaintable.new(window); snapshot = Gtk.Snapshot()
    paint.snapshot(snapshot, window.get_width(), window.get_height())
    node = snapshot.to_node(); assert node is not None
    texture = window.get_renderer().render_texture(node, None)
    assert texture.save_to_png(str(output / (name + '.png')))

app = Adw.Application(application_id='org.projectluma.Connect.UpdateUITest', flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
install_appkit()
results = []
for width in (880, 360):
    os.environ['LUMA_PRESENTATION_MODE'] = 'fullscreen-mobile' if width == 360 else 'windowed'
    window = AppWindow(application=app, app_id='org.projectluma.Connect.UpdateUITest',
        title='Software updates', icon_name='software-update-available-symbolic',
        commands=CommandRegistry(()), default_width=width, default_height=620,
        minimum_width=width, minimum_height=440)
    release = threading.Event()
    fixture = {'available': True, 'pending_checksum': 'b' * 64, 'observed_at': 1788820000}
    def reader(**_):
        assert release.wait(3)
        return fixture.copy()
    page = UpdateStatusPage(reader=reader)
    window.set_body(page); window.present(); pump(.3)
    page.refresh()
    assert page.busy and not page.refresh_button.get_sensitive()
    page.refresh()  # coalesced; no second concurrent query
    pulse = []
    GLib.idle_add(lambda: pulse.append(True) and False)
    pump(.1); assert pulse  # read did not block GTK
    release.set(); wait(lambda: not page.busy)
    assert page.status_row.get_title() == 'Restart pending'
    capture(window, 'updates-' + str(width))
    fixture.update(stale=True)
    page.refresh(); wait(lambda: not page.busy)
    assert page.status_row.get_title() == 'Status could not be refreshed'
    fixture.clear(); fixture.update(available=False)
    page.refresh(); wait(lambda: not page.busy)
    assert page.status_row.get_title() == 'Update status unavailable'
    capture(window, 'updates-unavailable-' + str(width))
    results.append({'requested_width': width, 'actual_width': window.get_width(), 'states': 'pending/stale/unavailable'})
    release.clear(); page.refresh()
    before_close = (page.status_row.get_title(), page.status_row.get_subtitle())
    page.close(); release.set(); pump(.3)
    assert page.disposed
    assert before_close == (page.status_row.get_title(), page.status_row.get_subtitle())
    assert not page.refresh_button.get_sensitive()
    window.destroy(); pump()
(output / 'result.json').write_text(json.dumps({'passed': True, 'fixtures': 'synthetic', 'cases': results}, indent=2))
