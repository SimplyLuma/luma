# SPDX-License-Identifier: Apache-2.0
"""Real SQLite writer contention, using disposable records and no account service."""
from pathlib import Path
import os
import sqlite3
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "src/prairie-core"), str(ROOT / "src/luma-platform/appkit")]
from luma_appkit import install_appkit, install_lumaui
from prairie_apps.messages import install_messages_theme
from prairie_apps.messages_accounts import AccountStore
from prairie_apps.messages_app_port import LumaUIMessagesWindow
from prairie_apps.messages_preferences import ConversationPreferences


def until(predicate, timeout=5):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)
    assert predicate(), "condition did not complete"


def main():
    assert os.environ.get("LUMA_MESSAGES_FIXTURE")
    app = Adw.Application(application_id="org.projectluma.Messages.LockTest",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    install_appkit(); install_lumaui(); install_messages_theme()
    window = LumaUIMessagesWindow(app); window.present()
    until(lambda: window.get_mapped())
    # Measure opening a thread in an already displayed window, not cold glyph
    # loading/first software-rendered frame initialization.
    settled = time.monotonic() + .8
    until(lambda: time.monotonic() >= settled)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "messages.db"
        store = AccountStore(path)
        first = store.add("one", "First thread", direction="incoming")
        second = store.add("two", "Second thread", direction="incoming")
        store.set_draft("one", "Retain this draft")
        service = window.service
        service.store = store
        service.provider = SimpleNamespace(reaction_emoji=(), remote=False,
                                           status={"state": "ready"}, close=lambda: None)
        service.account = SimpleNamespace(network="test")
        window._fixture_mode = False
        view = window.surface
        view.fixture = None; view.current = None
        view.preferences = ConversationPreferences(Path(folder) / "preferences.json")
        records = {record.address: record for record in store.threads()}
        ready = threading.Event()
        def lock():
            db = sqlite3.connect(path)
            db.execute("BEGIN IMMEDIATE")
            ready.set()
            time.sleep(1.2)
            db.execute("INSERT INTO messages(uid,address,body,timestamp,direction,state) "
                       "VALUES('late','one','Later arrival',1,'incoming','received')")
            db.commit(); db.close()
        locker = threading.Thread(target=lock)
        locker.start(); assert ready.wait(2)
        beats = [time.monotonic()]
        heartbeat = GLib.timeout_add(10, lambda: (beats.append(time.monotonic()), True)[1])
        started = time.monotonic()
        window._open_thread(records["one"], reveal=True, service=service)
        window._open_thread(records["two"], reveal=True, service=service)
        elapsed = time.monotonic() - started
        assert elapsed < .4, f"opening waited for the SQLite writer: {elapsed:.3f}s"
        future = window.content_worker.submit(lambda: None)
        until(future.done)
        until(lambda: store.message(second.uid).state == "read")
        locker.join()
        GLib.source_remove(heartbeat)
        gap = max(b - a for a, b in zip(beats, beats[1:]))
        assert gap < .4, f"GTK heartbeat blocked: {gap:.3f}s"
        assert store.message(first.uid).state == store.message(second.uid).state == "read"
        assert store.message("late").state == "received", "late arrival was marked read"
        assert store.draft("one") == "Retain this draft", "loading rewrote the draft"
        assert view.current["address"] == window.current_address == "two"
        assert not window._loading_draft

        # A later explicit unread action wins over an already queued open/read.
        store.mark_unread("two")
        release = threading.Event()
        window.content_worker.submit(release.wait, 2)
        window._mark_opened_read(service, "two", (second.uid,))
        window._mark_thread_unread(records["two"], service)
        future = window.content_worker.submit(lambda: None)
        release.set(); until(future.done)
        assert store.message(second.uid).state == "received"

        # Reverse order: reopening after a pending Unread wins even while the
        # main connection still reports the old read state.
        store.mark_read("two")
        release = threading.Event()
        window.content_worker.submit(release.wait, 2)
        window._mark_thread_unread(records["two"], service)
        window._open_thread(records["two"], reveal=True, service=service)
        future = window.content_worker.submit(lambda: None)
        release.set(); until(future.done)
        assert store.message(second.uid).state == "read"

        # Failed persistence leaves durable state intact and reports it without
        # selecting the old conversation again. Loading guards reset on errors.
        notices = []
        with patch.object(service, "writer", side_effect=sqlite3.OperationalError("locked")), \
                patch.object(window, "_notice", side_effect=notices.append):
            window._mark_opened_read(service, "one", ("late",))
            until(lambda: bool(notices))
        assert "read status" in notices[0]
        assert store.message("late").state == "received"
        assert view.current["address"] == "two"
        with patch.object(store, "draft", side_effect=sqlite3.OperationalError("read failure")):
            try:
                window._open_thread(records["two"], reveal=True, service=service)
            except sqlite3.OperationalError:
                pass
            else:
                raise AssertionError("expected injected draft failure")
        assert not window._loading_draft and not window._opening_surface_thread
        future = window.content_worker.submit(lambda: None); until(future.done)
        window.quitting = True; window.close()
        print(f"Messages writer-lock PASS: two opens {elapsed:.3f}s; max heartbeat {gap:.3f}s; "
              "snapshot/late arrival/latest selection/draft/unread ordering/failure verified")


if __name__ == "__main__":
    main()
