#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Actual GTK repair windows and the controller's explicit managed Quit.

The absent host is injected at the connection boundary. Signed admission and
real systemd lifetime are separate installed gates, not claimed by this probe.
"""
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch
import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
from gi.repository import Gio, GLib
from luma_appkit import EmptyState
from prairie_apps.messages import MessagesApplication, MessagesWindow, APPLICATION_ID


def settle():
    until = time.monotonic() + .35
    while time.monotonic() < until:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


def main():
    with tempfile.TemporaryDirectory(prefix='messages-host-unavailable-') as directory:
        root = Path(directory)
        with patch.dict(os.environ, {'HOME': directory, 'XDG_DATA_HOME': str(root/'data'),
                                    'XDG_CONFIG_HOME': str(root/'config'), 'XDG_CACHE_HOME': str(root/'cache')}), \
             patch('prairie_apps.messages_app_client.sandboxed', return_value=True), \
             patch.object(MessagesWindow, '_connect_agent', return_value=None):
            app = MessagesApplication(); assert app.register(None)
            for width, height in ((360, 520), (500, 500)):
                app.pending_address = '+12025550123'
                app.activate(); settle()
                window = app._window(); assert window._service_unavailable
                window.set_default_size(width, height); settle()
                state = window.body.get_first_child()
                assert isinstance(state, EmptyState)
                assert window.get_content() is not state, 'Repair must retain the Luma frame'
                assert window.title_bar.get_mapped()
                assert state.primary_button.get_mapped() and state.primary_button.get_sensitive()
                assert app.pending_address == '+12025550123'
                app._show_conversation('+12025550124')
                assert app.pending_address == '+12025550124'
                state.primary_button.emit('clicked'); settle()
                replacement = app._window()
                assert replacement is not window and replacement._service_unavailable
                replacement.close(); settle()
                assert not app.get_windows()
                assert not tuple(root.rglob('*.db')), 'Unavailable host cannot create replacement mailboxes'
                print(f'PASS native Messages repair/retry/close width={width}, intact frame, preserved pending address/data')
            app.quit()

    calls = []; closed = []; notices = []
    class Connection:
        def call_sync(self, bus, path, interface, method, args, *rest):
            calls.append((bus, interface, method, args.unpack()))
    owner = SimpleNamespace(agent=SimpleNamespace(connection=Connection()), close=lambda: closed.append(True),
                            _notice=notices.append)
    MessagesWindow.quit(owner)
    assert calls == [('org.projectluma.Background1', 'org.projectluma.Background1', 'StopNow', (APPLICATION_ID,))]
    assert owner.quitting and closed == [True] and not notices
    class RefusedConnection:
        def call_sync(self, *args): raise GLib.Error('Refused')
    owner = SimpleNamespace(agent=SimpleNamespace(connection=RefusedConnection()), close=lambda: closed.append(False),
                            _notice=notices.append)
    MessagesWindow.quit(owner)
    assert len(closed) == 1 and len(notices) == 1 and not getattr(owner, 'quitting', False)
    print('PASS explicit Messages Quit requests managed StopNow; refusal keeps a visible retry path')


if __name__ == '__main__': main()
