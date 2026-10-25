# SPDX-License-Identifier: Apache-2.0
"""Real GTK recovery from unavailable account status; synthetic transport only."""
import time
from unittest.mock import PropertyMock, patch
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk
from luma_continuity.application import ConnectApplication, ConnectWindow
from luma_continuity.cloud_sync import CloudSync

context = GLib.MainContext.default()
def pump():
    until = time.monotonic() + .15
    while time.monotonic() < until:
        while context.pending():
            context.iteration(False)
        time.sleep(.005)

def labels(widget):
    if isinstance(widget, Gtk.Label):
        yield widget.get_label()
    child = widget.get_first_child()
    while child:
        yield from labels(child)
        child = child.get_next_sibling()

app = ConnectApplication()
assert app.register(None)
with patch.object(CloudSync, 'available', new_callable=PropertyMock, return_value=True), \
     patch.object(CloudSync, 'status', lambda _self, done: done(None, 'Temporary transport failure')):
    window = ConnectWindow(app)
    window.state = {'account_status': 'signed_out'}
    window._update()
    window.present()
    pump()
    assert window.cloud is None
    assert window.shape == 'unavailable'
    assert 'Sign In…' not in list(labels(window))
    assert 'Try Again' in list(labels(window))
    report = {'signed_in': True, 'reachable': True, 'profile': {'name': 'Fixture'},
              'device': {'id': 'fixture-device', 'name': 'Fixture'}, 'devices': [], 'services': []}
    window.cloud_sync.status = lambda done: done(report, None)
    window._refresh_cloud()
    pump()
    assert window.cloud['signed_in'] and window.shape == 'account'
    assert 'Sign In…' not in list(labels(window))
    window._close()
    window.destroy()
print('PASS unavailable status preserves unknown authentication; retry restores signed-in UI')
