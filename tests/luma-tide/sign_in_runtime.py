#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Real startup/import, failed/successful reauthentication and offline retry.

v70 puts sign-in in Sources details/Edit, replacing the legacy banner and
ServerDialog. Test Connection is retained as the read-only remote API; no
extra form button is invented. Stores, credentials, audio and home paths are
isolated; the package runs this on Xvfb and a private bus.
"""
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

temporary = tempfile.TemporaryDirectory(prefix='tide-sign-in-')
root = Path(temporary.name)
home = root / 'home'
for name, relative in (('XDG_DATA_HOME', '.local/share'), ('XDG_CACHE_HOME', '.cache'),
                       ('XDG_STATE_HOME', '.local/state'), ('XDG_CONFIG_HOME', '.config')):
    os.environ[name] = str(home / relative)
os.environ['GSETTINGS_BACKEND'] = 'memory'

import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1'); gi.require_version('Graphene', '1.0')
from gi.repository import GLib, Graphene, Gtk
import luma_tide.application as application
from luma_tide.identity import PREVIEW_APP_ID
from luma_tide.credentials import MemoryCredentialStore, reference_for
from luma_tide.model import SourceState
from lumaui_runtime import count, named, settle, walk
from subsonic_fixture import FakeSubsonic
from test_playback import FakeEngine
from test_sign_in_recovery import REMOTE_ID, PreviewFixture

failures = []
def record_exception(kind, value, trace):
    failures.append(f'{kind.__name__}: {value}')
    sys.__excepthook__(kind, value, trace)
sys.excepthook = record_exception
class Errors(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR:
            failures.append(record.getMessage())
logging.basicConfig(level=logging.INFO, format='%(message)s')
logging.getLogger('tide').addHandler(Errors())

fake = None
fake_closed = False
if os.environ.get('TIDE_SERVER_URL'):
    address = os.environ['TIDE_SERVER_URL'].rstrip('/')
    user, password = os.environ['TIDE_SERVER_USER'], os.environ['TIDE_SERVER_PASSWORD']
else:
    fake = FakeSubsonic(username='listener', password='correct horse', songs=40)
    address, user, password = fake.address, fake.username, fake.password
preview = PreviewFixture(root, server_address=address, account=user)
keyring = MemoryCredentialStore()
application.SecretServiceStore = lambda: keyring
application.GStreamerEngine = FakeEngine
artifacts = Path(os.environ['TIDE_ARTIFACTS']) if os.environ.get('TIDE_ARTIFACTS') else None
if artifacts:
    artifacts.mkdir(parents=True, exist_ok=True)


def screenshot(window, name):
    if artifacts is None:
        return
    until = time.monotonic() + .6
    while time.monotonic() < until:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.01)
    width, height = window.get_width(), window.get_height()
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(window).snapshot(snapshot, width, height)
    node = snapshot.to_node()
    assert node is not None
    texture = window.get_native().get_renderer().render_texture(node, Graphene.Rect().init(0, 0, width, height))
    texture.save_to_png(str(artifacts / (name + '.png')))


def error_visible(window):
    return any(isinstance(widget, Gtk.Label) and widget.get_mapped()
               and 'server did not accept' in widget.get_text() for widget in walk(window))


app = application.TideApplication(application_id=PREVIEW_APP_ID)
try:
    assert app.register(None)
    app.activate()
    window = app.window
    window.set_default_size(1280, 800)
    def state():
        return app.store.source(REMOTE_ID).state
    settle(lambda: window.get_width() > 0 and state() is SourceState.AUTH_REQUIRED,
           timeout=30, what='startup')
    source = app.store.source(REMOTE_ID)
    assert (source.account, source.auth_ref) == (user, reference_for(REMOTE_ID))
    assert len(app.store.tracks(source_id=REMOTE_ID)) == 3
    assert app.store.preview_import_done() and app.store.playlist_tracks('pl')[0].id == 'track-2'
    window.open_sources()
    settle(lambda: any(item.id == REMOTE_ID for item in window.library.sources))
    assert window.library.source(REMOTE_ID).subtitle == 'Signed out'
    assert window.view == 'albums'
    named(window, 'td-source-' + REMOTE_ID).emit('clicked')
    sign = named(window, 'td-source-sign-out')
    assert sign is not None
    assert any(isinstance(item, Gtk.Label) and item.get_text() == 'Sign in' for item in walk(sign))
    screenshot(window, '1-signed-out-source')
    sign.emit('clicked')
    assert window.editing and window.source_id == REMOTE_ID and window.pane.shown
    assert window.server_address.text == address and window.server_user.text == user
    assert window.server_password.text == ''
    screenshot(window, '2-sign-in-form')

    window.server_password.text = 'not the password'
    window.connect_button.emit('clicked')
    settle(lambda: window.connect_button.get_sensitive() and error_visible(window), timeout=30,
           what='wrong password')
    assert window.editing and window.source_id == REMOTE_ID and state() is SourceState.AUTH_REQUIRED
    assert keyring.secrets == {}
    screenshot(window, '3-wrong-password')

    # The retained read-only connection test cannot save a credential/source.
    before = app.store.sources()
    future = app.remote.test_server(address, user, password, allow_plaintext=address.startswith('http://'))
    settle(future.done, timeout=30, what='read-only connection test')
    future.result()
    assert keyring.secrets == {} and app.store.sources() == before
    window.server_password.text = password
    window.connect_button.emit('clicked')
    settle(lambda: not window.editing and window.source_id is None, timeout=30, what='form closing')
    settle(lambda: state() is SourceState.ONLINE and REMOTE_ID not in app._syncing, timeout=120, what='sync')
    synced = app.store.tracks(source_id=REMOTE_ID)
    assert synced and (fake is None or len([track for track in synced if track.title.startswith('Song')]) >= len(fake.songs) - 3)
    assert keyring.lookup(reference_for(REMOTE_ID)) == password
    settle(lambda: window.library.source(REMOTE_ID).state == 'ok' and count(window) >= len(synced),
           timeout=30, what='live library')
    assert app.window is window, 'signing in restarted the window'
    window.show_view('songs')
    assert window._rows
    screenshot(window, '4-signed-in-synced')
    app.scan_source(app.store.source(REMOTE_ID))
    settle(lambda: REMOTE_ID not in app._syncing and state() is SourceState.ONLINE,
           timeout=60, what='online refresh')

    keyring.clear(reference_for(REMOTE_ID)); app.remote.mark_offline(REMOTE_ID)
    app.scan_source(app.store.source(REMOTE_ID))
    settle(lambda: state() is SourceState.AUTH_REQUIRED and REMOTE_ID not in app._syncing,
           timeout=30, what='lost password')
    window.open_sources(); window.refresh_library()
    settle(lambda: window.library.source(REMOTE_ID).subtitle == 'Signed out')
    named(window, 'td-source-' + REMOTE_ID).emit('clicked')
    named(window, 'td-source-sign-out').emit('clicked')
    assert window.editing and window.server_address.text == address
    window.server_password.text = password; window.connect_button.emit('clicked')
    settle(lambda: state() is SourceState.ONLINE and REMOTE_ID not in app._syncing,
           timeout=60, what='signing in again')
    assert keyring.lookup(reference_for(REMOTE_ID)) == password and app.window is window

    if fake is not None:
        fake.close(); fake_closed = True
        app.remote.mark_offline(REMOTE_ID); app.scan_source(app.store.source(REMOTE_ID))
        settle(lambda: state() is SourceState.OFFLINE and REMOTE_ID not in app._syncing,
               timeout=60, what='out-of-reach server')
        window.open_sources(); window.refresh_library()
        settle(lambda: window.library.source(REMOTE_ID).subtitle.startswith('Out of reach'))
        assert len(app.store.tracks(source_id=REMOTE_ID)) == len(synced), 'offline lost cached songs'
        named(window, 'td-source-' + REMOTE_ID).emit('clicked')
        sync = next(widget for widget in walk(window.pane) if isinstance(widget, Gtk.Button)
                    and any(isinstance(item, Gtk.Label) and item.get_text() == 'Sync now' for item in walk(widget)))
        sync.emit('clicked')
        settle(lambda: REMOTE_ID not in app._syncing and state() is SourceState.OFFLINE,
               timeout=60, what='retrying offline server')
        assert len(app.store.tracks(source_id=REMOTE_ID)) == len(synced)
        screenshot(window, '5-out-of-reach-retry')
    unexpected = [message for message in failures if 'Scanning' not in message]
    assert not unexpected, unexpected
    print('PASS: actual startup/import, Sources sign-in, wrong-password/no-write, read-only connection test, sync without restart, lost-password recovery and offline retry')
finally:
    if app.window:
        app.window.close_resources()
        app.window.destroy()
    app.do_shutdown()
    app.quit()
    if fake is not None and not fake_closed:
        fake.close()
    temporary.cleanup()
