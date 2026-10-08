#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Render Messages' conversation list and a thread from invented fixtures.

No real contacts, accounts, modem or network: every name and number here is
made up (555 numbers, fake short codes). Run under Xvfb with ImageMagick;
GDK_SCALE=2 (and a doubled screen) gives the 2x captures:

    LUMA_VISUAL_MODE=dark xvfb-run -s "-screen 0 980x700x24" messages_list_visual.py OUTPUT_DIR [--menu]

Each capture also writes a JSON sidecar with the row geometry it measured, so
a reviewer can check row heights without trusting the picture.
"""
import json
import os
import sys
import tempfile
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

NOW = int(time.time())
SCALE = int(os.environ.get("GDK_SCALE", "1"))
MODE = os.environ.get("LUMA_VISUAL_MODE", "light")
MINUTE = 60

# (conversation id, name, kind, participant phone, [(offset minutes, direction, body)], unread)
GOOGLE = (
    ("c-101", "Alexandra Montgomery-Richardson", "direct", "+15125550101",
     [(4, "incoming", "Running late, traffic on Lamar is a mess.\nSave me a seat?\nAlso bring the charger please")], 2),
    ("c-102", "Hive Cow Collective", "group", "",
     [(9, "outgoing", "I can bring the trailer Saturday"),
      (7, "incoming", "Becky: fence is fixed!\n\nphotos tomorrow")], 1),
    ("c-103", "+1 (512) 555-0147", "direct", "+15125550147",
     [(38, "incoming", "Hi, this is the front desk confirming your appointment for Thursday at 2:30.")], 0),
    ("c-104", "72975", "direct", "",
     [(62, "incoming", "Your verification code is 482913. Don't share it with anyone.")], 1),
    ("c-105", "Nick McMillan", "direct", "+15125550105",
     [(180, "incoming", "Can you look at the build?"), (175, "outgoing", "Sounds good 👍 on it now")], 0),
    ("c-106", "Weekend Crew", "group", "",
     [(60 * 20, "incoming", "Priya: who's in for tacos after the game? We could also do the new place on 6th")], 0),
    ("c-107", "李小龙", "direct", "+15125550107", [(60 * 26, "incoming", "明天见！")], 0),
    ("c-108", "(Mom) ❤️", "direct", "+15125550108",
     [(60 * 30, "incoming", "Call me when you land. Love you")], 0),
    ("c-109", "Sam", "direct", "+15125550109", [(60 * 50, "outgoing", "Thanks again!")], 0),
    ("c-110", "Émile Zola-Durand", "direct", "+15125550110",
     [(60 * 80, "incoming", "J'accuse… the coffee machine is broken again")], 0),
    ("c-111", "🙂", "direct", "", [(60 * 100, "incoming", "😀")], 0),
    ("c-112", "", "direct", "", [(60 * 130, "incoming", "Unknown sender test")], 0),
)
NATIVE = (
    ("+15125550199", "", [(15, "incoming", "Package delivered to your porch. Reply STOP to opt out")], 1),
    ("262966", "", [(60 * 40, "incoming", "BANK ALERT: a $42.10 purchase was approved.")], 0),
    ("+15125550150", "Dr. Priya Raman", [(60 * 70, "incoming", "Results look great, see you in six months.")], 0),
)
THREAD = (  # minutes before the group's two latest messages
    (50, "incoming", "Are we still on for Saturday?"),
    (48, "outgoing", "Yes! I'll bring the trailer and the fence posts."),
    (41, "incoming", "Perfect. Becky says the north gate needs a new hinge too, can you grab one at the hardware store on the way?"),
    (40, "outgoing", "On it 👍"),
)


class FakeProvider:
    remote = False
    status = {"state": "ready"}

    def __init__(self, label, store_path):
        self.label, self.store_path = label, store_path

    def start(self, _callback):
        pass

    def stop(self):
        pass


def settle(seconds=0.2):
    context = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def descendants(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from descendants(child)
        child = child.get_next_sibling()


def capture(output: Path) -> None:
    """The X server's own pixels: the window and any open menu, composited as shown."""
    settle(0.25)
    subprocess.run(["import", "-window", "root", str(output)], check=True)


def main() -> int:
    output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="messages-visual-"))
    os.environ.update(XDG_DATA_HOME=str(root / "data"), XDG_STATE_HOME=str(root / "state"),
                      XDG_CONFIG_HOME=str(root / "config"), PRAIRIE_EDS_MODE="disabled", LUMA_MESSAGES_AGENT="0")
    # The treatment a person chose in Settings, as the kit reads it.
    gi.require_version("LumaAppearance", "1")
    from gi.repository import LumaAppearance
    patch.object(LumaAppearance.SurfacePolicy, "get_has_selection", lambda self: True).start()
    patch.object(LumaAppearance.SurfacePolicy, "get_effective", lambda self: MODE).start()
    from prairie_apps import messages
    from prairie_apps.messages_accounts import Account, AccountStore
    from prairie_apps.messages_backend import MessageStore, MessagingCapability

    native_store = MessageStore(root / "native" / "messages.sqlite3")
    google_store = AccountStore(root / "google" / "messages.sqlite3")
    for conversation, name, kind, phone, messages_, _unread in GOOGLE:
        participants = [{"id": "p1", "name": name, "phone": phone}] if kind == "direct" else [
            {"id": "p1", "name": "Becky", "phone": "+15125550120"}, {"id": "p2", "name": "Jordan", "phone": "+15125550121"}]
        google_store.remember_conversation({"id": conversation, "kind": kind, "participants": participants})
        if name:
            google_store.set_display_name(conversation, name)
        for offset, direction, body in messages_:
            google_store.add(conversation, body, direction=direction, state="received" if direction == "incoming" else "sent",
                             timestamp=NOW - offset * MINUTE)
    for conversation, *_rest, unread in GOOGLE:
        if not unread:
            google_store.mark_read(conversation)
    for offset, direction, body in THREAD:
        google_store.add("c-102", body, direction=direction, state="received" if direction == "incoming" else "sent",
                         timestamp=NOW - offset * MINUTE)
    # A photo in the open conversation, for the lightbox capture: a generated gradient, no real picture.
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    pixels = bytearray()
    for y in range(900):
        for x in range(1200):
            pixels += bytes((40 + x * 150 // 1200, 90 + y * 110 // 900, 160 - x * 60 // 1200))
    photo = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(pixels)), GdkPixbuf.Colorspace.RGB, False, 8, 1200, 900, 3600)
    photo_path = root / "IMG_6948.jpeg"
    photo.savev(str(photo_path), "jpeg", ["quality"], ["90"])
    photo_uid = google_store.add_media_message("c-102", "", direction="incoming", state="read",
                                               timestamp=NOW - 25 * MINUTE, transport_id="gmessages:photo1")
    google_store.complete_media(photo_uid, "0", photo_path, name="IMG_6948.jpeg", mime="image/jpeg")
    for address, name, messages_, _unread in NATIVE:
        if name:
            native_store.set_display_name(address, name)
        for offset, direction, body in messages_:
            native_store.add(address, body, direction=direction, state="received", timestamp=NOW - offset * MINUTE)
    native_store.mark_read("262966"); native_store.mark_read("+15125550150")

    def services(self, _application):
        account = Account("fixture-google", "gmessages", name="", handle="", pending=False)
        google = messages.MessageService(
            "account:fixture-google", "Google Messages", google_store, None, None,
            FakeProvider("Google Messages", root / "google"), MessagingCapability(True, ""), account=account)
        native = messages.MessageService(messages.NATIVE_SERVICE, "This device", native_store, None, None)
        return [native, google]

    app = messages.MessagesApplication()
    app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    messages.install_messages_theme()
    report = {}
    with patch.object(messages.MessagesWindow, "_discover_services", services), \
         patch.object(messages.MessagesWindow, "_start_external_context_load", return_value=False), \
         patch.object(messages.MessagesWindow, "_restore_last_conversation", return_value=False), \
         patch.object(messages.MessagesWindow, "_watch_wake", lambda self: None):
        if True:
            mode = MODE
            window = messages.MessagesWindow(app)
            window.set_default_size(980, 700)
            window.present()
            settle(0.6)
            google = next(service for service in window.services if service.id.startswith("account:"))
            record = next(item for item in google.store.threads() if item.address == "c-102")
            window._open_thread(record, reveal=True, service=google)
            # The opened conversation is read now; keep one unread row near it.
            google.store.add("c-101", "One more thing", direction="incoming", state="received", timestamp=NOW - 3 * MINUTE)
            window._reload_threads()
            settle(0.4)
            rows = [widget for widget in descendants(window.thread_list) if isinstance(widget, Gtk.ListBoxRow)]
            heights = sorted({row.get_height() for row in rows})
            scroll = window.thread_list.get_ancestor(Gtk.ScrolledWindow)
            bar = scroll.get_vscrollbar() if scroll else None
            list_bounds = window.thread_list.compute_bounds(scroll)[1] if scroll else None
            bar_bounds = bar.compute_bounds(scroll)[1] if bar else None
            report[mode] = dict(
                row_heights=heights, rows=len(rows),
                scroll_width=scroll.get_width() if scroll else 0,
                list_width=list_bounds.get_width() if list_bounds else 0,
                scrollbar_x=bar_bounds.get_x() if bar_bounds else None,
                scrollbar_width=bar_bounds.get_width() if bar_bounds else None,
                subtitle=window.thread_subtitle.get_label(),
            )
            if scroll:
                adjustment = scroll.get_vadjustment()
                adjustment.set_value(1)
                settle(0.15)
            capture(output / f"messages-{mode}-{SCALE}x.png")
            if "--menu" in sys.argv:
                row = next(widget for widget in rows if getattr(widget, "record", None) and widget.record.address == "c-104")
                shown = window._show_thread_actions(row, 70, 28)
                settle(0.3)
                popover = shown if isinstance(shown, Gtk.Popover) else next(
                    (w for w in descendants(row) if isinstance(w, Gtk.Popover) and w.get_visible()), None)
                labels = [w.get_label() for w in descendants(popover) if isinstance(w, Gtk.Label) and w.get_mapped()] if popover else []
                report[mode]["menu"] = labels
                capture(output / f"messages-menu-{mode}-{SCALE}x.png")
                if popover:
                    popover.popdown()
            if "--lightbox" in sys.argv:
                if shown_menu := locals().get("shown"):
                    shown_menu.popdown()
                settle(0.3)
                button = next(w for w in descendants(window.message_box) if w.has_css_class("messages-photo-open"))
                button.emit("clicked")
                settle(1.0)
                capture(output / f"messages-lightbox-{mode}-{SCALE}x.png")
            window.close()
            settle(0.3)
    (output / f"messages-visual-{MODE}-{SCALE}x.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
