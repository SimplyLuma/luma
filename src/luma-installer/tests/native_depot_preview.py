#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Actual GTK page geometry and progress under a private fixture/session."""
import os
from pathlib import Path
import tempfile
import time
from dataclasses import replace
from unittest.mock import patch

work = tempfile.TemporaryDirectory(prefix='depot-native-')
for name in ('HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME'):
    directory = Path(work.name) / name
    directory.mkdir()
    os.environ[name] = str(directory)
os.environ['LUMA_DEPOT_FIXTURE'] = str(Path(__file__).parent / 'fixtures/depot-v70.json')
os.environ['GSETTINGS_BACKEND'] = 'memory'
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk
from luma_depot.window import DepotApplication, DepotWindow, Job
from luma_depot.providers import App, InstalledApp, Progress
from luma_appkit import Toast


def pump(seconds=.3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.002)


def descendants(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from descendants(child)
        child = child.get_next_sibling()


def capture(widget, name):
    if not os.environ.get('LUMA_DEPOT_CAPTURE_DIR'):
        return
    snapshot = Gtk.Snapshot.new()
    Gtk.WidgetPaintable.new(widget).snapshot(snapshot, widget.get_width(), widget.get_height())
    node = snapshot.to_node()
    assert node is not None
    output = Path(os.environ['LUMA_DEPOT_CAPTURE_DIR'])
    output.mkdir(parents=True, exist_ok=True)
    widget.get_native().get_renderer().render_texture(node, None).save_to_png(str(output / name))


app = DepotApplication()
app.set_application_id('org.projectluma.Depot.NativePreviewTest')
app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
window = DepotWindow(app)
window.present()
pump(.8)
assert window.catalogue and not window.loading
assert not window.sidebar_toggle.get_visible(), 'standalone sidebar control visible'
# A real private desktop entry supplies the installed owner's current icon.
# Deliberately conflicting legacy catalogue artwork must not replace it.
from luma_depot.lumaui_views import _icon
from luma_depot.v70_data import Listing
from luma_appkit import TableHeader
applications = Path(os.environ['XDG_DATA_HOME']) / 'applications'
applications.mkdir(exist_ok=True)
identity = 'org.projectluma.DepotIdentityTest'
(applications / (identity + '.desktop')).write_text(
    '[Desktop Entry]\nType=Application\nName=Identity test\nExec=true\n'
    'Icon=folder\n')
legacy = Path(work.name) / 'legacy-icons'
legacy.mkdir()
(legacy / 'luma-v3-browser.svg').write_text(
    '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" '
    'viewBox="0 0 32 32"><rect width="32" height="32" fill="red"/></svg>')
theme = Gtk.IconTheme.get_for_display(window.get_display())
Gio.Settings.new('org.gnome.desktop.interface').set_string('icon-theme', 'Adwaita')
pump(.2)
assert theme.has_icon('folder'), 'real installed Adwaita folder icon missing'
with patch.dict(os.environ, {'LUMA_DEPOT_ICON_ROOT': str(legacy)}):
    owner_icon = _icon(window, Listing(identity, 'Identity test', '', 'work', icon='browser'), 28)
assert owner_icon.paintable is not None
owner_path = owner_icon.paintable.get_file().get_path()
assert '/Adwaita/' in owner_path and not owner_path.startswith(str(legacy)), owner_path
print('PASS actual installed desktop icon wins over conflicting legacy catalogue artwork', owner_path)
curated = 'catalog:identity-test'
provider_app = App(curated, 'Identity test', '', icon_name='. GThemedIcon folder folder-symbolic')
with patch.dict(window.installed, {curated: InstalledApp(curated, '1', 1000, app=provider_app)}), \
     patch.dict(os.environ, {'LUMA_DEPOT_ICON_ROOT': str(legacy)}):
    curated_icon = _icon(window, Listing(curated, 'Identity test', '', 'work', icon='browser'), 28)
assert curated_icon.paintable is not None
curated_path = curated_icon.paintable.get_file().get_path()
assert '/Adwaita/' in curated_path and not curated_path.startswith(str(legacy)), curated_path
print('PASS curated catalogue identity keeps its installed provider-owned GIcon', curated_path)
# Enough actual provider records to prove vertical overflow, without user files.
window.installed = {a.app_id: InstalledApp(a.app_id, '1', 1000, app=a)
                    for a in window.catalogue.apps}
window._installed_ready = True
for width in (1040, 800, 500, 1440):
    window.set_default_size(width, 700)
    window.go('mine', remember=False)
    pump(.4)
    adjustment = window.content_scroll.get_vadjustment()
    assert adjustment.get_upper() > adjustment.get_page_size() + 100, (
        width, adjustment.get_upper(), adjustment.get_page_size())
    adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
    pump()
    assert adjustment.get_value() > 100, (width, adjustment.get_value())
    rows = [w for w in descendants(window.content) if w.has_css_class('depot-table-row')]
    assert len(rows) == len(window.installed)
    assert rows[-1].get_height() >= 40
    header = next(w for w in descendants(window.content) if isinstance(w, TableHeader))
    if header.cells[1].get_visible():
        for row in rows:
            cells = list(row)
            for left, right in zip(cells, cells[1:]):
                a = left.compute_bounds(row)[1]
                b = right.compute_bounds(row)[1]
                assert b.get_x() - (a.get_x() + a.get_width()) >= 6, (
                    width, 'installed cells crowd', a.get_x(), a.get_width(), b.get_x())
        for left, right in zip(header.cells, header.cells[1:]):
            a = left.compute_bounds(header)[1]
            b = right.compute_bounds(header)[1]
            assert b.get_x() - (a.get_x() + a.get_width()) >= 6, (
                width, 'header cells crowd', a.get_x(), a.get_width(), b.get_x())
        print('PASS actual installed table cells and headings have shared gutters', width)
    print('PASS installed rows scroll', width, len(rows), round(adjustment.get_value()))
    adjustment.set_value(0)
    pump(.1)
    capture(window, f'your-apps-{width}.png')
window.set_default_size(1040, 700)
window.go('app', 'tide', remember=False)
window.jobs['tide'] = Job('tide', 'install', Gio.Cancellable())
window.render()
pump(.6)
adjustment = window.content_scroll.get_vadjustment()
assert adjustment.get_upper() > adjustment.get_page_size() + 100
adjustment.set_value(min(200, adjustment.get_upper() - adjustment.get_page_size()))
pump()
position = adjustment.get_value()
first = window.content.get_first_child()
serial = window._render_serial
for percentage in range(1, 100):
    window._progress(Progress('tide', percentage / 100, percentage * 1000, 100000))
    pump(.005)
    assert window.content.get_first_child() is first, 'progress replaced page'
    assert window._render_serial == serial, 'progress rebuilt page'
    assert abs(adjustment.get_value() - position) < 1, 'progress moved scroll'
assert any(isinstance(w, Gtk.Label) and '99%' in w.get_label() for w in descendants(window.content))
print('PASS 99 progress updates retain actual widgets, scroll, and visible percent')
# A structural refresh must also retain scroll after GTK allocates new content.
window.render()
pump(.4)
assert abs(adjustment.get_value() - position) < 1, ('refresh scroll', position, adjustment.get_value())
print('PASS refresh restores scroll after allocation')
window.jobs.clear()
window.installed = {key: replace(record, update_version='') for key, record in window.installed.items()}
window.os_state = None
window.fixture_system_phase = 'done'
window.go('updates', remember=False)
pump()
assert window.nav_rows['updates'].badge.count == 0
print('PASS zero real app/system offers produces no update badge')
# Genuine shared policy transitions, including medium-width editorial content.
# The package fixture compiles the exact production shell schema privately.
appearance = Gio.Settings.new('org.project_luma.shell-state')
style = Adw.StyleManager.get_default()
for dark in (False, True):
    appearance.set_string('surface-treatment', 'dark' if dark else 'light')
    pump(.4)
    assert style.get_dark() is dark, ('appearance did not change', dark)
    window.set_default_size(800, 700)
    window.go('home', remember=False)
    pump(.5)
    feature = next(w for w in descendants(window.content) if w.has_css_class('depot-feature'))
    assert feature.get_height() < 380, ('medium feature height', feature.get_height())
    key = next(w for w in descendants(feature) if isinstance(w, Gtk.Button) and w.has_css_class('key'))
    color = key.label_widget.get_style_context().get_color()
    assert color.red < .3 if dark else color.red > .7, ('feature key ink', dark, color.to_string())
    window.go('category:create', remember=False)
    pump(.4)
    for heading in (w for w in descendants(window.content) if w.has_css_class('depot-section-heading')):
        minimum, natural, *_ = heading.measure(Gtk.Orientation.VERTICAL, heading.get_width())
        assert heading.get_height() >= minimum, ('clipped category heading', heading.get_height(), minimum)
    buttons = [w for w in descendants(window.content) if isinstance(w, Gtk.Button) and w.has_css_class('depot-get')]
    assert buttons and all(w.has_css_class('lumaui-text-button') and
                           (w.has_css_class('raised') or w.has_css_class('key')) for w in buttons)
print('PASS actual Light/Dark medium feature, category heading and shared buttons')

# Exercise the non-fixture production dispatch without opening an external app.
window.fixture = ''
app_record = window.catalogue.apps[0]
launches = []
painted_frames = []
clock = window.get_frame_clock()
handler = clock.connect('after-paint', lambda c: painted_frames.append(c.get_frame_counter()))
def launched(*args):
    toast = next((t for t in Toast._current.values() if t.kind == 'opening'), None)
    assert toast is not None and toast.get_mapped()
    assert toast.get_width() > 0 and toast.get_height() > 0 and toast.has_css_class('shown')
    assert painted_frames, 'launch ran before a completed notification frame'
    launches.append(args)
errors = []
with patch.object(window.installer, 'launch', side_effect=launched), \
     patch.object(window, '_report', side_effect=lambda *args: errors.append(args)):
    window._open_installed(app_record)
    window._open_installed(app_record)
    assert not launches, 'launcher ran synchronously before feedback'
    pump(.5)
    assert len(launches) == 1 and not errors, (launches, errors)
    window._open_installed(app_record)
    window.set_visible(False)
    pump()
    assert len(launches) == 1, 'unmapped window dispatched pending launch'
    window.present()
    pump()
clock.disconnect(handler)
with patch.object(window.installer, 'launch', side_effect=RuntimeError('fixture launch failed')), \
     patch.object(window, '_report', side_effect=lambda *args: errors.append(args)):
    window._open_installed(app_record)
    pump(.5)
    assert len(errors) == 1 and errors[0][0] == 'Could not open the application'
print('PASS completed Opening frame before dispatch, coalescing, close cancellation and launch errors')

# This is the production renderer, with owned model records and real widgets.
# A labelled Studio fixture must never be mistaken for reachable update actions.
from types import SimpleNamespace
from luma_installer import depot_app_history, depot_counting, depot_system_update
from luma_depot.native import NativeInstallation
window.preview = False
window.jobs.clear()
window.installer = NativeInstallation()
window.settings = depot_counting.Settings(app_updates=True)
window.system = SimpleNamespace(
    state=depot_system_update.from_values({
        'State': 'idle', 'Channel': 'beta', 'BootedVersion': '1.0.0-beta.test',
        'Managed': True, 'AutomaticDownload': True, 'RollbackAvailable': True,
        'RepositoryUrl': 'https://dl.simplyluma.com/os/repo',
        'SignatureVerified': True, 'AvailableChannels': ['stable', 'beta', 'nightly'],
    }, 'dbus'), busy='', busy_channel='', error='')
window.firmware = None
previous = depot_app_history.Entry(app_record.app_id, app_record.name, '1', '2',
    int(time.time()), from_commit='a' * 64, to_commit='b' * 64)
depot_app_history.record(previous)
window.installed = {app_record.app_id: InstalledApp(app_record.app_id, '2', 1000,
    app=app_record, managed=True, commit='b' * 64)}
window._forget_history_cache()
window.go('updates', remember=False)
pump(.4)
history_buttons = [w for w in descendants(window.content)
                   if isinstance(w, Gtk.Button) and w.get_label() == 'Go Back…']
assert len(history_buttons) == 1, 'production update history has no genuine Go Back action'
with patch.object(window, '_confirm_revert') as confirm:
    history_buttons[0].emit('clicked')
    assert confirm.call_args.args == (previous,), 'rollback did not bind the retained exact update'
channel_rows = [w for w in descendants(window.content) if isinstance(w, Adw.ActionRow)
                and w.get_title() in ('Beta', 'Nightly')]
assert {w.get_title() for w in channel_rows} == {'Beta', 'Nightly'}, 'production channels unreachable'
assert all(w.get_activatable_widget() is not None and w.get_activatable_widget().get_sensitive()
           for w in channel_rows), 'production channel choices are inert'
switches = {w.get_title(): w for w in descendants(window.content) if isinstance(w, Adw.SwitchRow)}
assert 'Update apps automatically' in switches, 'production app preference unreachable'
assert 'Download updates automatically' in switches, 'production system preference unreachable'
with patch.object(window, '_save_settings') as saved:
    switches['Update apps automatically'].set_active(False)
    assert saved.call_count == 1 and not window.settings.app_updates, 'app switch did not save real settings'
print('PASS production GTK history rollback callback, Beta/Nightly choices and app/system update switches')
window.close()
pump()

# The shared rounded Card, not its square inner Overlay, clips Valet artwork.
from luma_installer.installer import InstallerWindow
with patch('luma_installer.installer.iter_records', return_value=[]), \
     patch.object(InstallerWindow, '_start_inspection'):
    valet = InstallerWindow(app)
    valet.card.set_lit_name('Firefox')
    valet.present()
    pump(.5)
    card = valet.card.main_card
    assert card.get_overflow() == Gtk.Overflow.HIDDEN, 'Valet card leaves opaque artwork unclipped'
    snapshot = Gtk.Snapshot.new()
    Gtk.WidgetPaintable.new(card).snapshot(snapshot, card.get_width(), card.get_height())
    node = snapshot.to_node()
    assert node is not None
    scene = Path(work.name) / 'valet-card.node'
    node.write_to_file(str(scene))
    assert 'rounded-clip' in scene.read_text(), 'Valet card has no actual rounded snapshot clipping'
    print('PASS Valet native shared Card clips artwork at its rounded boundary')
    capture(card, 'valet-card.png')
    valet.close()
    pump()
