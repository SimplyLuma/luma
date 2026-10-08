#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Real GTK state transitions in private stores; never touches a modem/book.

Run under an isolated desktop session/Xvfb with the packaged source on
PYTHONPATH. Fixture peers/cards are test data, never production seeds.
"""
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
from types import SimpleNamespace

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from luma_appkit import EmptyState, ListEmptyState
from prairie_apps import contacts, messages, notes
from prairie_apps.eds_backend import ContactRecord
from prairie_apps.messages_backend import ThreadRecord


class _EmptySource:
    """Contacts' source for these checks: whatever `load` returns, in memory."""

    lists: dict = {}
    rehearsal = False

    def __init__(self, load=lambda: ()):
        from prairie_apps.contacts_data import SharingStore
        from prairie_apps.eds_backend import LOCAL_ADDRESS_BOOK
        self._load, self.book, self.sharing = load, LOCAL_ADDRESS_BOOK, SharingStore(None)

    def load(self):
        return list(self._load()), self.book

    def can(self, action):
        return False

    def together(self, record):
        return ()

    def me(self):
        return None

    def light(self, record):
        return record.photo, (0.5, 0.5)


def settle(predicate=lambda: True, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("GTK state did not settle")


def descendants(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from descendants(child)
        child = child.get_next_sibling()


def main():
    with tempfile.TemporaryDirectory() as directory:
        os.environ["XDG_DATA_HOME"] = directory + "/data"
        os.environ["XDG_STATE_HOME"] = directory + "/state"
        os.environ["XDG_CONFIG_HOME"] = directory + "/config"
        os.environ["PRAIRIE_EDS_MODE"] = "disabled"
        # Force each requested appearance independently of the host desktop preference.
        patch("luma_appkit.widgets._prefers_dark", lambda: Adw.StyleManager.get_default().get_dark()).start()
        app = notes.NotesApplication()
        app.set_flags(Gio.ApplicationFlags.NON_UNIQUE | Gio.ApplicationFlags.HANDLES_OPEN)
        assert app.register(None)
        contacts._install_contacts_style()
        messages.install_messages_theme()
        icon_path = Path(__file__).resolve().parents[2] / "luma-platform/appkit/icons"
        if icon_path.is_dir():
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(icon_path))
        style = Adw.StyleManager.get_default()
        actions = []
        shared = EmptyState("Empty", "A long wrapped explanation", "luma-empty-notes-symbolic",
                            primary=("Create", lambda: actions.append("create")),
                            secondary=("Import", lambda: actions.append("import")))
        shared.primary_button.emit("clicked")
        shared.secondary_button.emit("clicked")
        assert actions == ["create", "import"]
        assert shared.icon.get_pixel_size() == 38
        assert shared.disc.get_size_request() == (84, 84)
        assert not shared.primary_button.get_child().get_use_markup()
        # Long translation, RTL, and reduced motion remain
        # the same keyboard-operable component at handheld width.
        probe = Gtk.Window(default_width=360, default_height=600)
        long_state = EmptyState("A long translated empty-state heading " * 2,
            "A long translated description " * 8, "luma-empty-notes-symbolic",
            primary=("Create a new translated document", lambda: None),
            secondary=("Import another translated document", lambda: None))
        long_state.set_direction(Gtk.TextDirection.RTL)
        probe.set_child(long_state)
        probe.present()
        Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
        settle(lambda: long_state.get_width() > 0)
        assert probe.get_width() == 360, probe.get_width()
        for button in (long_state.primary_button, long_state.secondary_button):
            ok, bounds = button.compute_bounds(probe)
            assert ok and bounds.get_x() >= 0 and bounds.get_x() + bounds.get_width() <= 360
            assert button.get_accessible_role() == Gtk.AccessibleRole.BUTTON
            assert button.grab_focus()
        probe.close()
        with patch.object(messages.MessagesWindow, "_start_external_context_load", return_value=False), \
             patch.object(messages.MessagesWindow, "_restore_last_conversation", return_value=False), \
             patch.object(contacts, "source_from_environment", lambda: _EmptySource()):
            for dark in (False, True):
                style.set_color_scheme(Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
                for width in (360, 500, 1024, 1440):
                    for cls in (notes.NotesWindow, contacts.ContactsWindow, messages.MessagesWindow):
                        window = cls(app)
                        window.set_default_size(width, 650)
                        window.present()
                        if cls is contacts.ContactsWindow:
                            window._reload(wait=True)
                        # All three panes use the same EmptyState, including in
                        # compact navigation when explicitly showing content.
                        if cls is not contacts.ContactsWindow:
                            window.split.set_show_content(True)
                        # Compare the whole allocation, including window CSS padding.
                        try:
                            settle(lambda: window.get_allocated_width() == width)
                        except AssertionError:
                            raise AssertionError((cls.__name__, width, window.get_width(), window.get_default_size()))
                        if cls is contacts.ContactsWindow:
                            window.list_first.show_detail()
                        empty = window.fresh_state if cls is contacts.ContactsWindow else window.empty_state
                        settle(lambda: empty.disc.get_width() > 0)
                        assert empty.disc.get_width() == 84, (cls.__name__, width, empty.disc.get_width())
                        assert empty.disc.get_height() == 84
                        assert empty.get_width() <= width
                        assert empty.primary_button.get_sensitive()
                        window.close()
                        settle()
            # Library transitions: no implicit page; menu and pane share create.
            window = notes.NotesWindow(app)
            window.present()
            settle()
            assert window.current is None and not window.store.list_notes()
            assert window.empty_state.get_visible()
            assert not window.canvas.get_visible() and not window.format_toolbar.get_visible()
            assert any(isinstance(w, ListEmptyState) and w.get_text() == "No notes yet" for w in descendants(window.sidebar_list))
            window.empty_state.primary_button.emit("clicked")
            settle()
            assert window.current is not None and window.canvas.get_visible()
            assert window.title_entry.has_focus() or window.title_entry.is_focus() or window.title_entry.get_focus_child() is not None
            uid = window.current.id
            window.delete_current()
            assert not window.store.list_notes() and window.empty_state.get_visible()
            window._undo_delete(uid)
            assert window.current.id == uid and not window.empty_state.get_visible()
            folder = window.store.create_folder(name="Empty folder")
            window._reload_sidebar()
            hint = next(w for w in descendants(window.sidebar_list) if w.has_css_class("notes-folder-empty"))
            hint.emit("activate-link", "notes.new")
            assert window.current.folder_id == folder.id
            assert len(window.store.list_notes(folder_id=folder.id)) == 1
            window.close()
            # Contacts search leaves the current card intact and empty actions
            # invoke the same real editor/import commands as application menu.
            book = [ContactRecord("first", "Test Person", "+12025550100")]
            def load(search=""):
                return tuple(book if not search or search.casefold() in book[0].name.casefold() else ())
            with patch.object(contacts, "source_from_environment", lambda: _EmptySource(load)):
                window = contacts.ContactsWindow(app)
                window._reload(wait=True)
                window._show_empty()
                assert window.card_stack.get_visible_child_name() == "empty"
                window._show_contact(book[0])
                window.search_text = "zzz"
                window._reload(wait=True)
                assert window.selected.uid == "first"
                assert "No contacts match “zzz”" in window.list_empty.get_text()
                book.clear()
                window.search_text = ""
                window._reload(wait=True)
                assert window.card_stack.get_visible_child_name() == "fresh"
                assert not window.all_records
                book.append(ContactRecord("imported", "Imported Person"))
                window._import_finished(("imported",), "")
                settle(lambda: window.selected is not None and window.selected.uid == "imported")
                window.close()
            window = messages.MessagesWindow(app)
            window.present()
            settle()
            assert window.empty_state.heading.get_text() == "No messages yet"
            assert not window.composer_region.get_visible()
            assert all(w.get_opacity() == 0 for w in [window.thread_identity_cluster, window.call_button])
            peer = "+12025550100"
            row = window._recipient_row(peer, "Test Person", peer)
            window._recipient_activated(window.recipient_list, row)
            settle()
            assert window.current_address == peer
            assert window.thread_subtitle.get_text() == "New conversation"
            assert window.composer_placeholder.get_text() == "Say hello"
            assert window.composer_region.get_visible()
            assert not window.store.threads() and not window.store.thread(peer)
            assert window.thread_list.get_row_at_index(0).record.preview == "Say hello"
            assert window.thread_list.get_row_at_index(0).is_selected()
            assert any(w.has_css_class("messages-intro") for w in descendants(window.message_box))
            window._show_new_message()
            assert not window.store.threads() and not window.current_address
            window._recipient_activated(window.recipient_list, row)
            # The actual send handler only reports the injected transport result.
            window.capability = SimpleNamespace(available=True, reason="")
            def unavailable(*_args):
                raise RuntimeError("Test transport unavailable")
            window.transport = SimpleNamespace(send=unavailable)
            window._set_composer_text("First message")
            window._send_message()
            settle(lambda: window.store.thread(peer) and window.store.thread(peer)[0].state == "failed")
            assert window.store.thread(peer)[0].state == "failed"
            assert window.thread_subtitle.get_text() != "New conversation"
            assert not any(w.has_css_class("messages-intro") for w in descendants(window.message_box))
            window.store.delete_thread(peer)
            window._show_nothing_selected()
            # First native message replaces the intro; no fake presence.
            record = window.store.add(peer, "Native test message", direction="incoming")
            window._recipient_activated(window.recipient_list, row)
            assert window.current_address == peer and len(window.store.thread(peer)) == 1
            assert window.thread_subtitle.get_text() != "Active now"
            assert not any(w.has_css_class("messages-intro") for w in descendants(window.message_box))
            window.search.set_text("zzz")
            window._reload_threads()
            assert window.list_empty.heading.get_text() == "Nothing matches “zzz”"
            assert window.current_address == peer
            window._show_nothing_selected()
            assert window.empty_state.heading.get_text() == "Your messages"
            window._delete_thread(ThreadRecord(peer, "Test Person", "", 0, 0), window.service)
            assert window.empty_state.heading.get_text() == "No messages yet"
            window.close()
            settle()
    print("PASS: shared empty-state actions/84px geometry in light/dark at 360/500/1024/1440; Notes create/delete/restore/folder; Contacts selection/search/import completion; Messages recipient/draft/intro/history/search/deselection")


if __name__ == "__main__":
    main()
