# SPDX-License-Identifier: Apache-2.0
"""Native callback regression with real disposable catalog/media and GTK."""
import os
from pathlib import Path
import sqlite3
from contextlib import closing
import time
from unittest.mock import patch
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Adw, GdkPixbuf, GLib, Gtk
from prairie_apps import camera
from prairie_apps.camera_backend import CameraDevice
from prairie_apps.camera_state import SessionShot

runtime = Path(os.environ['XDG_RUNTIME_DIR'])
assert str(runtime).startswith('/tmp/lumaui-camera-test.')
for kind in ('DATA', 'CONFIG', 'STATE', 'CACHE'):
    assert Path(os.environ[f'XDG_{kind}_HOME']).is_relative_to(runtime)
assert os.environ['GSETTINGS_BACKEND'] == 'memory'
assert os.environ['GDK_BACKEND'] == 'x11'
assert os.environ['DBUS_SESSION_BUS_ADDRESS'].split(',')[0] == f'unix:path={runtime}/bus'
assert not os.environ.get('LUMA_CAMERA_FIXTURE')
os.environ['XDG_PICTURES_DIR'] = str(runtime / 'Pictures')
root = Path(os.environ['XDG_PICTURES_DIR'])
root.mkdir()
older, selected = root / 'older.jpg', root / 'capture.jpg'
pixels = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 32, 24)
pixels.fill(0x4488ccff)
pixels.savev(str(older), 'jpeg', [], [])
payload = older.read_bytes()
selected.write_bytes(payload)
os.utime(older, (2000000000, 2000000000))
library = camera.PhotoLibrary()
source = library.ensure_default_source()
library.scan_source(source.id)
assert library.reconcile_exact_copies() == 1
record = library.assets()[0]
assert len(record.copies) == 2 and record.path == older
library.set_favorite(record.id, True)
library.set_metadata(record.id, caption='retain native caption')
Gtk.init()
Adw.init()
app = camera.CameraApplication()
app.set_application_id('org.projectluma.Camera.CopyReviewCheck')
assert app.register(None) and not app.get_is_remote()
device = CameraDevice('synthetic', 'Test', 'external', desktop_uvc=True)
context = GLib.MainContext.default()
def settle(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(.01)
    assert predicate(), 'native window did not settle'

# Only discovery/device acquisition is denied; callbacks, GTK and catalog are real.
with patch.object(camera, 'list_camera_devices', return_value=(device,)), patch.object(camera.CameraWindow, '_start_camera'):
    window = camera.CameraWindow(app)
    try:
        window.present()
        settle(lambda: hasattr(window, '_surface') and window.get_mapped())
        indexed = window._index_capture(selected)
        assert indexed and indexed.id == record.id, 'selected nonpreferred copy was not indexed'
        window._last_capture_path = selected
        window._last_capture_record = indexed
        window.state.roll[:] = [SessionShot(selected, 32, 24, record=indexed), SessionShot(older, 32, 24, record=indexed)]
        window._refresh_session_tray()
        settle(lambda: window._last_shot_picture.get_paintable() is not None)
        window._delete_review_response(None, 'cancel', selected)
        assert selected.read_bytes() == payload and older.read_bytes() == payload
        window._delete_review_response(None, 'trash', selected)
        assert not selected.exists() and older.read_bytes() == payload
        retained = library.asset(record.id)
        assert retained.favorite and retained.caption == 'retain native caption'
        assert [copy.path for copy in retained.copies] == [older]
        deleted = library.assets(collection='deleted')[0].copies[0]
        assert deleted.original_path == selected and deleted.path.read_bytes() == payload
        assert [shot.path for shot in window.state.roll] == [older]
        assert window._last_capture_path == older and window._last_capture_record.id == record.id
        assert window._last_shot_button.get_sensitive(), 'remaining session was disabled'
        settle(lambda: window._last_shot_picture.get_paintable() is not None)
        assert window._last_shot_picture.get_paintable(), 'remaining thumbnail was cleared'
        backups = list((Path(os.environ['XDG_STATE_HOME']) / 'prairie-core/camera/backups').glob('*.sqlite3'))
        assert len(backups) == 1
        with closing(sqlite3.connect(backups[0])) as saved:
            assert saved.execute('SELECT COUNT(*) FROM copies WHERE trashed=0').fetchone() == (2,)
        window._last_capture_path = older
        window._last_capture_record = retained
        window._delete_review_response(None, 'trash', older)
        assert not older.exists() and not window.state.roll
        assert not window._last_shot_button.get_sensitive()
        assert window._last_shot_picture.get_paintable() is None
        assert window._surface.session_placeholder.get_visible()
    finally:
        window.close()
        window._library_executor.shutdown(wait=True)
    assert window._closed and app.props.active_window is None
print('PASS: native selected-copy index/trash/cancel, retained fields/media, prior backup, remaining/empty session and closed window')
