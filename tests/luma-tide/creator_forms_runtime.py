"""Native short-window pane actions, adaptive playback and real file delivery."""
import json
import os
from pathlib import Path
import sys
import time
from dataclasses import replace
import tempfile
from unittest.mock import patch
import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk
from luma_appkit import install_appkit, install_lumaui, add_style_sheet
from luma_tide.fixture import FixtureLibrary
from luma_tide.ui import TideWindow
from lumaui_runtime import click, key, named, walk, window_id, xdo


def settle():
    end = time.monotonic() + .4
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


def visible(widget, window):
    assert widget is not None and widget.get_mapped()
    ok, bounds = widget.compute_bounds(window)
    assert ok and bounds.get_x() >= 0 and bounds.get_y() >= 0
    assert bounds.get_x() + bounds.get_width() <= window.get_width() + 1, bounds
    assert bounds.get_y() + bounds.get_height() <= window.get_height() + 1, bounds
    return bounds


def capture(window, name):
    folder = os.environ.get('TIDE_ARTIFACTS')
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(window).snapshot(snap, window.get_width(), window.get_height())
        node = snap.to_node(); assert node is not None
        window.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, window.get_width(), window.get_height())).save_to_png(str(Path(folder) / (name + '.png')))


root = Path(__file__).resolve().parents[1]
app = Adw.Application(application_id='org.projectluma.TideCreatorForms', flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
install_appkit(); install_lumaui(); add_style_sheet(str(root / 'data/tide.css'))
failures = []
original = sys.excepthook
sys.excepthook = lambda kind, value, trace: (failures.append(str(value)), original(kind, value, trace))
quit_calls = []
action = Gio.SimpleAction.new('quit', None)
action.connect('activate', lambda *_: quit_calls.append(True))
app.add_action(action)
results = []
for dark in (False, True):
    Gio.Settings.new('org.project_luma.shell-state').set_string('surface-treatment', 'dark' if dark else 'light')
    settle()
    assert Adw.StyleManager.get_default().get_dark() is dark
    for width in (360, 500, 1024, 1440):
        window = TideWindow(app, FixtureLibrary(root / 'fixtures/tide-v70.json'))
        window.set_default_size(width, 520); window.present(); settle()
        window.set_size_request(width, 520)
        xdo('windowsize', '--sync', window_id(window), str(width + 10), '530'); settle()
        source = window.library.sources[0]
        window.library = replace(window.library, sources=tuple(replace(source, id=f'long-source-{n}') for n in range(24)))
        window.open_sources(); settle()
        add = named(window, 'td-source-add')
        visible(add, window)  # Old inline action lies beyond the short viewport.
        adjustment = window.pane.scroller.get_vadjustment()
        assert adjustment.get_upper() > adjustment.get_page_size()
        before = visible(add, window)
        adjustment.set_value(adjustment.get_upper()); settle()
        after = visible(add, window)
        assert abs(before.get_y() - after.get_y()) <= 1
        assert window.root.get_spacing() == (0 if window.pane.is_drawer else 8)
        capture(window, f'sources-{width}-{int(dark)}')
        add.emit('clicked'); settle()
        assert window.connect_button.get_mapped()
        visible(window.connect_button, window)
        for field in (window.server_address, window.server_user, window.server_password):
            field.entry.grab_focus(); settle()
            visible(field, window)
        capture(window, f'source-form-{width}-{int(dark)}')
        window.pane.close(); settle()
        if width >= 1024:
            click(window, named(window, 'td-search-open')); settle()
            assert window.search.get_mapped()
            assert not window.search_item.entry.get_state_flags() & Gtk.StateFlags.FOCUS_VISIBLE
            capture(window, f'search-pointer-{width}-{int(dark)}')
            key(window, window.search_item.entry, 'Tab'); settle()
            key(window, None, 'shift+Tab'); settle()
            assert window.search.get_mapped()
            assert window.search_item.entry.get_state_flags() & Gtk.StateFlags.FOCUS_VISIBLE
            capture(window, f'search-keyboard-{width}-{int(dark)}')
            window.close_search(); settle()
        window.open_now_playing(); settle()
        assert not window.corner.get_mapped(), 'Now Playing owns its navigation header'
        visible(named(window, 'td-now-close'), window)
        if width > 720:
            visible(named(window, 'td-now-next'), window)
            visible(named(window, 'td-now-about'), window)
        capture(window, f'now-playing-{width}-{int(dark)}')
        window.close_now_playing(); settle()
        # Actual application action dispatch, rather than close-request/hide.
        command = next(command for group in window.commands.visible_groups(menu=True)
                       for command in group.commands if command.id == 'tide.quit')
        command.execute()
        assert len(quit_calls) == len(results) + 1
        results.append({'width': width, 'dark': dark, 'footer': 'fixed', 'now-header': 'separate', 'quit': 'app-action'})
        window.close_resources(); window.destroy(); settle()
assert not failures, failures
print(json.dumps({'result': 'pass', 'cases': results}), flush=True)
