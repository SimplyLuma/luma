#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""The production NoCities/add/removal contract at desktop and phone widths.

Retains all Weather assertions from rest_empty_states_runtime_smoke so the
Weather portion can complete in both schemes even when another app fails.
The original shared package test remains required and unchanged.
"""
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib
from luma_appkit import install_appkit
from prairie_apps import weather

with tempfile.TemporaryDirectory() as root:
    app = Adw.Application(application_id='org.projectluma.WeatherEmptyTest',
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    install_appkit()
    for scheme in [Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.FORCE_DARK]:
        for width in (390, 1180):
            for key, sub in [('XDG_DATA_HOME', 'data'), ('XDG_STATE_HOME', 'state'),
                             ('XDG_CONFIG_HOME', 'config')]:
                os.environ[key] = str(Path(root) / f'{int(scheme)}-{width}' / sub)
            Adw.StyleManager.get_default().set_color_scheme(scheme)
            w = weather.WeatherWindow(app)
            w.set_default_size(width, 740)
            w.present()
            deadline = time.monotonic() + 5
            while w.get_width() == 0 and time.monotonic() < deadline:
                GLib.MainContext.default().iteration(False)
                time.sleep(0.01)
            # Gtk.Window's allocation excludes its CSD shadow. The capture's
            # requested width is the native surface, including that shadow.
            assert w.get_surface().get_width() == width, (width, w.get_surface().get_width())
            assert w.detail_stack.get_visible_child_name() == 'empty'
            assert w.empty.heading.get_text() == 'No cities'
            assert w.empty.description.get_text() == 'Add a city to see its forecast.'
            with patch.object(w, '_open_add_card') as add:
                w.empty.primary_button.emit('clicked')
                add.assert_called_once()
            with patch.object(w, '_focus_search') as focus:
                w._open_add_card()
                focus.assert_called_once()
            w.empty.primary_button.emit('clicked')
            deadline = time.monotonic() + 2
            while not w.foot.entry.get_mapped() and time.monotonic() < deadline:
                GLib.MainContext.default().iteration(False)
                time.sleep(0.01)
            assert w.foot.entry.get_mapped() and w.get_focus() is not None
            assert not w.store.list()
            place = weather.new_place(weather.Place(uid='', name='Test place',
                                                     latitude=1.0, longitude=2.0))
            record = w.store.add(place)
            w._rebuild_rows_keeping_selection()
            w._fetch_done(record, None)
            assert w.detail_stack.get_visible_child_name() == 'forecast'
            assert not w.get_visible_dialog()
            w._remove(record)
            assert not w.store.list()
            assert w.detail_stack.get_visible_child_name() == 'empty'
            w.undo_removal()
            assert [p.name for p in w.store.list()] == ['Test place']
            assert w.selected == record.uid
            assert w.detail_stack.get_visible_child_name() == 'forecast'
            w.undo_removal()
            assert len(w.store.list()) == 1
            w.close()
            print(f'PASS: Weather NoCities/add/failed-refresh/remove/Undo scheme={int(scheme)} width={width}')
