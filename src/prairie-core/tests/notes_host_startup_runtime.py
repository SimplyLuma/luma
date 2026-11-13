#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""A sandboxed Notes window is on screen before it asks Connect anything.

Connect is replaced at its D-Bus call (collaboration_transport.call) by one
that answers slowly (COST per call). The window must present without a single
Connect call on its main thread, then list the shared note and the invitation
and make the shared note editable once the account is known, read-only until
then. A Connect state change makes it read-only again until Connect answers.

    COST=0.37 notes_host_startup_runtime.py   # timing on a slow device
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch

COST = float(os.environ.get('COST', '0.05'))
ROOT = Path(tempfile.mkdtemp(prefix='notes-host-startup-'))
os.environ.update(HOME=str(ROOT), XDG_DATA_HOME=str(ROOT / 'data'), XDG_CONFIG_HOME=str(ROOT / 'config'),
                  XDG_CACHE_HOME=str(ROOT / 'cache'), XDG_STATE_HOME=str(ROOT / 'state'),
                  FLATPAK_ID='org.projectluma.Notes')

import gi  # noqa: E402
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from prairie_apps import collaboration_transport as transport  # noqa: E402
from prairie_apps.collaboration import CollaborationCache  # noqa: E402
from prairie_apps.notes_backend import NotesStore  # noqa: E402
from prairie_apps.notes import NotesApplication  # noqa: E402

MAIN = threading.main_thread()
HUB, DEVICE = 'https://hub.invalid', 'notes-startup-device'
calls = []
signed_in = [True]


def connect(method, values=()):
    """Connect's host service, with the real one's cost."""
    start = time.monotonic()
    time.sleep(COST)
    calls.append((method, threading.current_thread() is MAIN, start, time.monotonic()))
    if method == 'GetCollaborationContext' and not signed_in[0]:
        return {'error': {'status': 401, 'detail': 'Signed out', 'code': 'signed_out'}}
    if method == 'GetCollaborationContext':
        return {'device_id': DEVICE, 'hub': HUB, 'name': 'Notes', 'registered_at': '2026-11-13'}
    raise AssertionError(f'Notes made a Connect request while opening: {method}')


def seed():
    store = NotesStore()
    for index in range(12):
        store.create_note(title=f'Page {index}', body=f'Text {index}')
    shared = next(iter(store.list_notes()))
    store.close()
    cache = CollaborationCache()
    with cache.db:
        cache.db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)',
                         ('shared-doc', HUB, DEVICE, 'note', shared.id, 1, 'edit', '{}'))
        cache.db.execute('INSERT INTO snapshots VALUES(?,?)', ('shared-doc', json.dumps({
            'id': 'shared-doc', 'role': 'edit', 'members': [{'account': 'friend', 'display_name': 'Friend'}],
            'comments': [{'account': 'friend', 'body': 'Looks good'}]})))
        cache.db.execute('INSERT INTO invitations VALUES(?,?,?,?,?)', ('invite-doc', HUB, DEVICE, 'note', 'friend'))
    cache.close()
    return shared.id


def labels(widget):
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            yield child.get_label()
        yield from labels(child)
        child = child.get_next_sibling()


def named(widget, name):
    child = widget.get_first_child()
    while child is not None:
        if child.get_name() == name or named(child, name):
            return True
        child = child.get_next_sibling()
    return False


def settle(predicate, label, seconds):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, label
        GLib.MainContext.default().iteration(False)
        time.sleep(0.002)


def main():
    shared_id = seed()
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    # A real owner, so that Connect's StateChanged reaches the window's subscription.
    Gio.bus_own_name_on_connection(bus, transport.BUS, Gio.BusNameOwnerFlags.NONE, None, None)
    with patch.object(transport, 'call', connect):
        app = NotesApplication(); assert app.register(None)
        started = time.monotonic()
        app.activate()  # constructs and presents the window
        presented = time.monotonic() - started
        window = app.get_active_window() or app.get_windows()[0]
        early = [call for call in calls if call[1]]
        assert not early, f'the window asked Connect {len(early)} times on its main thread before it was presented: {early}'
        assert window.current is not None and window.current.id == shared_id, 'the newest page opens, as before'
        assert not window.editor.get_editable(), 'a shared page is read-only until its account is known'
        stall, last = [0.0], [time.monotonic()]

        def tick():
            now = time.monotonic(); stall[0] = max(stall[0], now - last[0]); last[0] = now
            return GLib.SOURCE_CONTINUE
        GLib.timeout_add(5, tick)
        settle(lambda: window.editor.get_editable(), 'the shared page becomes editable once Connect answers',
               max(5.0, 40 * COST))
        filled = time.monotonic() - started
        text = list(labels(window.sidebar_list))
        assert 'Shared with me' in text, f'the shared page is listed as shared: {text}'
        assert named(window.sidebar_list, 'nt-collaboration-invitation'), 'the invitation is listed'
        assert window._shared_comments.get_visible(), 'the shared page shows its comments'
        assert window.title_entry.get_editable() and window.format_toolbar.get_sensitive()

        # Signing out (or Connect restarting) forgets the account at once.
        before = len(calls)
        bus.emit_signal(None, transport.PATH, transport.BUS, 'StateChanged', GLib.Variant('(t)', (1,)))
        settle(lambda: not window.editor.get_editable(), 'a Connect state change makes the shared page read-only',
               5.0)
        settle(lambda: window.editor.get_editable() and len(calls) > before,
               'the account is asked again off the main thread', max(5.0, 40 * COST))
        # Signed out: the shared pages leave the list and stay read-only.
        signed_in[0] = False
        bus.emit_signal(None, transport.PATH, transport.BUS, 'StateChanged', GLib.Variant('(t)', (2,)))
        settle(lambda: 'Shared with me' not in list(labels(window.sidebar_list)),
               'signing out lists the shared page as shared no more', max(5.0, 40 * COST))
        assert not window.editor.get_editable(), 'a shared page stays read-only once signed out'
        on_main = [call for call in calls if call[1]]
        assert not on_main, f'Notes asked Connect on its main thread: {on_main}'
        print(f'cost per call {COST:.2f} s; presented after {presented:.2f} s with 0 Connect calls; '
              f'shared page editable after {filled:.2f} s; {len(calls)} calls, 0 on the main thread; '
              f'longest main-loop stall {stall[0]:.2f} s')
        window.close()
    print('PASS sandboxed Notes presents before any Connect call and fills in shared pages off the main thread')


if __name__ == '__main__':
    main()
    sys.stdout.flush()
    os._exit(0)  # Connect's stand-in threads need no orderly exit
