"""Exercise Valet's visible removal controls against a harmless fake job."""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

import gi
gi.require_version('Adw', '1')
gi.require_version('GLib', '2.0')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, GLib, Gtk

from luma_installer.installer import InstallerWindow
from luma_installer.progress import Cancelled


def settle(check, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        if check():
            return
        time.sleep(0.01)
    raise AssertionError('Valet control did not respond')


app = Adw.Application(application_id='org.projectluma.ValetClickTest')
app.register(None)


def fake_remove(_record, _keep):
    from luma_installer.progress import current
    transaction = current.get()
    assert transaction.cancelled.wait(3)
    raise Cancelled('Stopped')


with patch('luma_installer.installer.iter_records', return_value=[]), \
     patch.object(InstallerWindow, '_start_inspection'), \
     patch('luma_installer.installer.workflow.remove', side_effect=fake_remove):
    window = InstallerWindow(app, remove_id='org.example.Sample.desktop')
    window.report = SimpleNamespace(title='Sample', kind='flatpak', requires_restart=False)
    window.record = {'application_id': 'sample', 'format': 'flatpak',
                     'flatpak_id': 'org.example.Sample', 'removable': True}
    window.inspecting = False
    window.eligible = True
    window.card.set_phase('ready', 'Uninstall', removing=True)
    window.card.primary.set_sensitive(True)
    window.present()
    settle(lambda: window.get_width() > 0)
    assert window.get_width() >= 720, window.get_width()
    assert window.get_height() < window.get_width(), (window.get_width(), window.get_height())
    ok, bounds = window.card.primary.compute_bounds(window)
    assert ok
    target = window.pick(bounds.get_center().x, bounds.get_center().y, Gtk.PickFlags.DEFAULT)
    assert target is window.card.primary or target.is_ancestor(window.card.primary), target
    ok, bounds = window.card.secondary.compute_bounds(window)
    assert ok
    target = window.pick(bounds.get_center().x, bounds.get_center().y, Gtk.PickFlags.DEFAULT)
    assert target is window.card.secondary or target.is_ancestor(window.card.secondary), target
    window.card.primary.emit('clicked')
    settle(lambda: window.busy and window.transaction is not None)
    window.card.secondary.emit('clicked')
    settle(lambda: window.transaction.cancelled.is_set())
    window.close()

print('PASS: wide removal window, Uninstall starts, Cancel responds')
