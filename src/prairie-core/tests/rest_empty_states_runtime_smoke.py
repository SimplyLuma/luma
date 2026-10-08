#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise real empty stores and retry dispatch without touching user data."""
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk
from luma_appkit import EmptyState, install_appkit
from prairie_apps import clock, weather, camera

def descendants(w):
    yield w
    child=w.get_first_child()
    while child:
        yield from descendants(child); child=child.get_next_sibling()

def states(w):
    return [x for x in descendants(w) if isinstance(x,EmptyState)]

with tempfile.TemporaryDirectory() as root:
    def isolate(run):
        # Each pass gets its own stores. The weather section deliberately ends
        # with an undone removal, so the place it restored is still on disk;
        # sharing one directory across the passes meant the second one opened a
        # Weather that was not empty and failed an assertion about empty states.
        for key,sub in [('XDG_DATA_HOME','data'),('XDG_STATE_HOME','state'),('XDG_CONFIG_HOME','config')]:
            os.environ[key]=str(Path(root)/run/sub)
    isolate('shared')
    app=Adw.Application(application_id='org.projectluma.EmptyStatesTest',flags=Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    install_appkit()
    compact=EmptyState('Nothing selected','Choose something to see its details.','image-x-generic-symbolic',compact=True)
    assert compact.disc.get_size_request()==(48,48)
    assert compact.icon.get_pixel_size()==24
    for scheme in [Adw.ColorScheme.FORCE_LIGHT,Adw.ColorScheme.FORCE_DARK]:
        isolate(f'scheme-{int(scheme)}')
        Adw.StyleManager.get_default().set_color_scheme(scheme)
        os.environ['TZ'] = 'Europe/Paris'
        w=clock.ClockWindow(app)
        # A fresh store now seeds the actual setup zone. An empty world list
        # follows the person's explicit removal and must stay empty on reopen.
        seeded = w.store.world_clocks()
        assert len(seeded) == 1 and seeded[0].zone == 'Europe/Paris'
        w.store.remove_world_clock(seeded[0].uid)
        w._load_cities()
        w.close()
        w=clock.ClockWindow(app)
        assert not w._city_views and w.add_card.get_parent() is not None
        assert w.alarm_eyebrow.get_text() == 'No alarms on'
        assert not w.store.world_clocks() and not w.store.alarms()
        record=w.store.add_world_clock('UTC','UTC');w._load_cities()
        assert len(w._city_views)==1 and w._city_views[0][0]=='UTC'
        assert w.add_card.get_parent() is not None
        w.store.remove_world_clock(record.uid);w._load_cities()
        assert not w._city_views and w.add_card.get_parent() is not None
        w.close()
        w=weather.WeatherWindow(app)
        assert w.detail_stack.get_visible_child_name()=='empty'
        assert w.empty.heading.get_text()=='No cities'
        assert w.empty.description.get_text()=='Add a city to see its forecast.'
        # The empty state's button opens the same card the sidebar's + opens,
        # anchored to itself.
        with patch.object(w,'_open_add_card') as add:
            w.empty.primary_button.emit('clicked');add.assert_called_once()
        assert not w.store.list()
        # A failed refresh is not an error state: the app keeps showing what it
        # has and never puts a dialog in the way. Removal is the one
        # destructive act and Undo, not a confirmation, is what covers it.
        place=weather.new_place(weather.Place(uid='',name='Test place',latitude=1.0,longitude=2.0))
        # Match Weather's public add path: the initial loader owns this lock too.
        with w.store_lock:
            record=w.store.add(place)
        w._rebuild_rows_keeping_selection()
        w._fetch_done(record,None)
        assert w.detail_stack.get_visible_child_name()=='forecast'
        assert not w.get_visible_dialog()
        w._remove(record)
        assert not w.store.list() and w.detail_stack.get_visible_child_name()=='empty'
        w.undo_removal()
        assert [p.name for p in w.store.list()]==['Test place']
        w.close()
        with patch.object(camera,'list_camera_devices',return_value=[]) as discovery:
            w=camera.CameraWindow(app)
            def empty_camera():
                deadline=time.monotonic()+5
                context=GLib.MainContext.default()
                while time.monotonic()<deadline:
                    while context.pending():
                        context.iteration(False)
                    found=states(w)
                    if found and found[0].heading.get_text()=='No camera found':
                        return found[0]
                    time.sleep(.005)
                raise AssertionError('Camera discovery did not show No camera found')
            empty_camera().primary_button.emit('clicked')
            assert not w._devices, 'Unavailable Camera must not expose a device'
            empty_camera()
            assert not w._devices, 'Unavailable Camera retry must stay empty'
            assert discovery.call_count==2, 'Camera retry did not rediscover devices'
            w.close()
    print('PASS: compact dimensions; Clock real empty/populated/empty; Weather empty/undo; Camera retry; light/dark')
