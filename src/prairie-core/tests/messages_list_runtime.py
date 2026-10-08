#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Messages' conversation list: fixed-height rows, clear unread state, real avatars, a menu of real actions.

Nick's launch review (2026-09-16): rows grew tall with multi-line previews,
carried a "Google Messages" callout, were bold whether read or not, showed "("
and "7" as avatars, and opened a misaligned menu with actions that did nothing.
The thread header said "Chat · Google Messages" beside a dead three-dot button.
Invented data only.
"""
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

context = GLib.MainContext.default()


def pump(seconds=0.05):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.003)


def descendants(widget):
    stack = [widget]
    while stack:
        item = stack.pop()
        yield item
        child = item.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()


class Provider:
    remote = False
    status = {"state": "ready"}
    label = "Google Messages"

    def start(self, _callback):
        pass

    def close(self):
        pass


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="messages-list-"))
    os.environ.update(XDG_DATA_HOME=str(root / "data"), XDG_STATE_HOME=str(root / "state"), LUMA_MESSAGES_AGENT="0",
                      PRAIRIE_EDS_MODE="disabled")
    from prairie_apps import messages
    from prairie_apps.messages_accounts import Account, AccountStore
    from prairie_apps.messages_backend import MessageStore, MessagingCapability

    now = int(time.time())
    native = MessageStore(root / "native" / "messages.db")
    google = AccountStore(root / "google" / "messages.db")
    native.add("+15125550199", "Package delivered.\n\nReply STOP to opt out", direction="incoming", state="received", timestamp=now - 60)
    native.add("72975", "Your code is 482913", direction="incoming", state="read", timestamp=now - 120)
    native.add("+15125550150", "Results look great", direction="incoming", state="read", timestamp=now - 180)
    native.set_display_name("+15125550150", "Dr. Priya Raman")
    native.add("+15125550151", "Only I wrote here", direction="outgoing", state="sent", timestamp=now - 190)
    google.remember_conversation({"id": "g1", "kind": "group", "participants": [{"id": "a", "name": "Becky", "phone": "+15125550120"},
                                                                              {"id": "b", "name": "Jordan", "phone": "+15125550121"}]})
    google.set_display_name("g1", "Hive Cow Collective and the Extremely Long Group Name That Never Ends")
    google.add("g1", "Becky: fence is fixed!\nGoogle Messages\nphotos tomorrow\n\nand more", direction="incoming", state="received", timestamp=now - 30)
    google.remember_conversation({"id": "d1", "kind": "direct", "participants": [{"id": "n", "name": "Nick McMillan", "phone": "+15125550105"}]})
    google.set_display_name("d1", "Nick McMillan")
    google.add("d1", "Sounds good", direction="incoming", state="read", timestamp=now - 240)

    def services(self, _application):
        return [messages.MessageService(messages.NATIVE_SERVICE, "This device", native, None, None),
                messages.MessageService("account:fixture", "Google Messages", google, None, None, Provider(),
                                        MessagingCapability(True, ""), account=Account("fixture", "gmessages", pending=False))]

    app = messages.MessagesApplication()
    app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    failures = []
    called = []
    with patch.object(messages.MessagesWindow, "_discover_services", services), \
         patch.object(messages.MessagesWindow, "_start_external_context_load", return_value=False), \
         patch.object(messages.MessagesWindow, "_restore_last_conversation", return_value=False), \
         patch.object(messages.MessagesWindow, "_watch_wake", lambda self: None), \
         patch.object(messages.MessagesWindow, "_call_number", lambda self, number: called.append(number)):
        window = messages.MessagesWindow(app)
        window.set_default_size(980, 700)
        window.present()
        pump(0.6)
        rows = {row.record.address: row for row in descendants(window.thread_list) if isinstance(row, messages.ConversationRow)}
        heights = {address: row.get_height() for address, row in rows.items()}
        if len(set(heights.values())) != 1:
            failures.append(f"rows differ in height: {heights}")
        if not 48 <= next(iter(heights.values())) <= 68:
            failures.append(f"rows are not a readable fixed height: {heights}")
        for address, row in rows.items():
            if "\n" in row.preview.get_label() or "\n" in row.title.get_label():
                failures.append(f"{address}: a label spans lines")
            layout_lines = row.preview.get_layout().get_line_count()
            if layout_lines != 1:
                failures.append(f"{address}: the preview lays out as {layout_lines} lines")
        texts = [w.get_label() for w in descendants(window.thread_list) if isinstance(w, Gtk.Label)]
        if any(text in ("Google Messages", "This device") for text in texts):
            failures.append("a row still carries its service callout")
        # Read and unread.
        for address, unread in (("+15125550199", True), ("g1", True), ("72975", False), ("d1", False)):
            row = rows[address]
            if row.has_css_class("unread") != unread or row.has_css_class("read") == unread or (row.dot.get_opacity() == 1) != unread:
                failures.append(f"{address}: unread state drawn wrong")
        weight = lambda row: row.title.get_pango_context().get_font_description().get_weight()
        if not weight(rows["g1"]) > weight(rows["d1"]):
            failures.append(f"unread is not bolder than read: {weight(rows['g1'])} vs {weight(rows['d1'])}")
        # Avatars.
        kinds = {address: row.get_child().get_first_child().kind for address, row in rows.items()}
        expected = {"+15125550199": "person", "72975": "business", "+15125550150": "initials", "g1": "group", "d1": "initials"}
        for address, kind in expected.items():
            if kinds[address] != kind:
                failures.append(f"{address}: avatar {kinds[address]}, expected {kind}")
        initials = [w.get_label() for w in descendants(rows["+15125550150"]) if w.has_css_class("luma-avatar-initials")]
        if initials != ["PR"]:
            failures.append(f"Dr. Priya Raman's initials: {initials}")

        # Search and New are in the shared sidebar foot; Accounts stays in the app menu.
        if window.search is not window.foot.entry or window.foot.add_button is None:
            failures.append("the sidebar foot lost search or New message")
        if not window.foot.add_button.get_mapped():
            failures.append("New message is not visible")
        accounts = window.commands.get("messages.accounts")
        if accounts.label != "Accounts…" or accounts.shortcut != ("Ctrl", "comma"):
            failures.append(f"app menu Accounts entry: {accounts.label!r} {accounts.shortcut}")
        found, search_bounds = window.search.compute_bounds(window.foot)
        found_button, button_bounds = window.foot.add_button.compute_bounds(window.foot)
        if found and found_button and abs((search_bounds.get_y() + search_bounds.get_height() / 2) -
                                          (button_bounds.get_y() + button_bounds.get_height() / 2)) > 1:
            failures.append("the sidebar foot search and New message are not centred on one line")

        # The menu: only real actions, each doing what it says.
        def visible(address):
            registry = window.thread_commands(rows[address].record, rows[address].service)
            return [c.id.split(".", 1)[1] for group in registry.visible_groups() for c in group.commands]
        menus = {address: visible(address) for address in ("+15125550199", "72975", "g1", "d1", "+15125550151")}
        want = {"+15125550199": ["open", "call", "mark-read", "delete"], "72975": ["open", "mark-unread", "delete"],
                "g1": ["open", "mark-read", "delete"], "d1": ["open", "call", "mark-unread", "delete"],
                "+15125550151": ["open", "call", "delete"]}
        if menus != want:
            failures.append(f"menus {menus}")
        window.thread_commands(rows["d1"].record, rows["d1"].service).invoke("conversation.call")
        if called != ["+15125550105"]:
            failures.append(f"Call on a Google Messages conversation calls its participant: {called}")
        window.thread_commands(rows["72975"].record, rows["72975"].service).invoke("conversation.mark-unread")
        pump(0.1)
        if next(t.unread for t in native.threads() if t.address == "72975") != 1:
            failures.append("Mark as Unread did not")
        rows = {row.record.address: row for row in descendants(window.thread_list) if isinstance(row, messages.ConversationRow)}
        window.thread_commands(rows["72975"].record, rows["72975"].service).invoke("conversation.mark-read")
        pump(0.1)
        if next(t.unread for t in native.threads() if t.address == "72975") != 0:
            failures.append("Mark as Read did not")
        rows = {row.record.address: row for row in descendants(window.thread_list) if isinstance(row, messages.ConversationRow)}
        presented = []
        with patch.object(Adw.AlertDialog, "present", lambda dialog, parent: presented.append(dialog)):
            window.thread_commands(rows["72975"].record, rows["72975"].service).invoke("conversation.delete")
        if not presented or not native.thread("72975"):
            failures.append("Delete did not ask first")
        else:
            presented[0].emit("response", "delete")
            pump(0.1)
            if native.thread("72975"):
                failures.append("confirmed Delete kept the conversation")
        # The kit menu opens anchored at the pointer inside the row, and is released when closed.
        row = next(row for row in descendants(window.thread_list) if isinstance(row, messages.ConversationRow))
        menu = window._show_thread_actions(row, 40, 20)
        pump(0.2)
        ok, rectangle = menu.get_pointing_to()
        if not menu.get_visible() or menu.get_parent() is not row or (rectangle.x, rectangle.y) != (40, 20):
            failures.append(f"the menu is not anchored at the pointer: {menu.get_visible()} {rectangle.x},{rectangle.y}")
        if menu.get_halign() != Gtk.Align.START:
            failures.append("the menu is centred on the pointer instead of opening from it")
        if not menu.has_css_class("luma-menu-popover"):
            failures.append("the row menu is not the kit menu")
        menu.popdown()
        pump(0.3)
        if menu.get_parent() is not None:
            failures.append("a closed menu stays parented to its row")

        # The thread header: the service alone, no three-dot button, Prairie glyphs only.
        window._open_thread(next(t for t in google.threads() if t.address == "g1"), reveal=True, service=window.services[1])
        pump(0.2)
        if window.thread_subtitle.get_label() != "Google Messages":
            failures.append(f"subtitle {window.thread_subtitle.get_label()!r}")
        if window.call_button.get_sensitive():
            failures.append("a group can be called")
        buttons = [w for w in descendants(window.thread_header) if isinstance(w, Gtk.Button)]
        if any("details" in (b.get_tooltip_text() or "").lower() or "more" in (b.get_tooltip_text() or "").lower() for b in buttons):
            failures.append("the three-dot button is still there")
        if any(isinstance(w, Gtk.DrawingArea) for w in descendants(window)):
            failures.append("a hand-drawn glyph remains")
        theme_dirs = [Path(os.environ.get("PRAIRIE_ICON_DIR", "/usr/share/icons/Prairie")), Path("/usr/share/icons/hicolor")]
        # Messages' own content; the title bar is the kit's and the toolkit's (its
        # pan-down chevron is a known Prairie gap owned there, not here).
        names = {w.get_icon_name() for w in descendants(window.split) if isinstance(w, Gtk.Image) and w.get_icon_name()}
        names |= set(messages.ICONS.values())
        names.discard("image-missing")  # the empty image GTK keeps in every tooltip window; never drawn
        missing = sorted(name for name in names
                         if not any(next(directory.rglob(name + ".svg"), None) for directory in theme_dirs if directory.is_dir()))
        if missing:
            failures.append(f"glyphs Prairie does not carry: {missing}")
        window.close()
        pump(0.2)
    if failures:
        print("FAIL: " + "; ".join(failures), file=sys.stderr)
        return 1
    print("ok: fixed-height rows, unread at a glance, real avatars, a menu of real actions, a clean header, Prairie glyphs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
