#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Messages at v71 phone width: list first, the list bar, New message, the title island,
swipe rows, the held message and the grown bar, then back to a desktop (fixture only)."""

from pathlib import Path
import os
import sys
import time

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src/luma-platform/appkit"))
sys.path.insert(0, str(ROOT / "src/prairie-core"))

from luma_appkit import MessageRun, install_appkit, install_lumaui  # noqa: E402
from prairie_apps.messages import install_messages_theme  # noqa: E402
from prairie_apps.messages_port import FixtureMessagesWindow  # noqa: E402


def pump(seconds: float = 0.6) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)


def find(root: Gtk.Widget, name: str) -> Gtk.Widget | None:
    if root.get_name() == name:
        return root
    child = root.get_first_child()
    while child is not None:
        hit = find(child, name)
        if hit is not None:
            return hit
        child = child.get_next_sibling()
    return None


def press(window: Gtk.Widget, name: str) -> None:
    target = find(window, name)
    assert target is not None, f"no widget named {name}"
    target.emit("clicked") if isinstance(target, Gtk.Button) else target.activate()
    pump()


def main() -> int:
    assert os.environ.get("LUMA_MESSAGES_FIXTURE"), "set LUMA_MESSAGES_FIXTURE to the v70 JSON"
    app = Adw.Application(application_id="org.projectluma.Messages.V71Phone", flags=Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    install_appkit()
    install_lumaui()
    install_messages_theme()
    window = FixtureMessagesWindow(app)
    window.set_default_size(360, 820)
    window.present()
    pump(1.5)
    view = window.surface

    # List first, with the list bar and no title island.
    assert view._phone_mode and not view.split.get_show_content()
    assert view.list_center.state == "bar" and not view.foot.get_visible()
    assert not view.title_island.get_visible()
    assert not window.has_css_class("lumaui-bleed"), "list should keep the standard status inset"
    assert view.sidebar.list.get_selected_row() is None, "a phone list shows no selected row"
    assert view.sidebar.phone_title_label.get_visible()
    assert view.sidebar.phone_title_label.get_label() == "Messages"

    # A swipe right pins (and a second one unpins).
    row = find(window, "msg-row-theo")
    swipe = row.get_child()
    swipe.start.activate(swipe)
    pump()
    assert view._entry_by_id["theo"]["pinned"]
    swipe = find(window, "msg-row-theo").get_child()
    swipe.start.activate(swipe)
    pump()
    assert not view._entry_by_id["theo"]["pinned"]

    # New message: suggestions over the To: row; an existing conversation opens directly.
    press(window, "msg-new")
    assert view.list_center.grown == "new" and len(view._new_candidates) == 4
    view._new_field.set_text("theo")
    pump()
    assert [c["name"] for c in view._new_candidates] == ["Theo Marsh"]
    view._pick_recipient(view._new_candidates[0])
    pump()
    assert view.current["id"] == "theo" and view.split.get_show_content()
    assert view.title_island.get_visible() and view.title_island.title == "Theo Marsh"
    assert not view.header.get_visible()
    assert window.has_css_class("lumaui-bleed"), "conversation light should reach the phone status area"

    # The title island grows into the conversation's options; Pin redraws it in place.
    press(window, "msg-info")
    assert view.title_island.grown
    pinned = view.current["pinned"]
    press(window, "msg-island-pin")
    assert view.current["pinned"] != pinned and view.title_island.grown
    view.title_island.fold()
    pump()

    # Attach and Emoji grow the bar.
    press(window, "msg-attach")
    assert view.center.grown == "attach" and find(window, "msg-attach-photo") is not None
    view.center.fold_panel()
    pump()
    press(window, "msg-emoji")
    assert view.center.grown == "emoji"
    find(window, "msg-emoji-grid").get_first_child().emit("clicked")
    pump()
    assert view.bar_entry.text.endswith("😀") and view.center.grown is None

    # Holding a message: reactions over tiles, no composer row; a reaction lets go.
    first = view.current["meta"]["messages"][0]["id"]
    press(window, f"msg-select-{first}")
    assert view.selected_message and find(window, "msg-held-reply") is not None
    assert view.center.bar.has_css_class("panel-only")
    find(window, "msg-held").get_first_child().get_first_child().emit("clicked")
    pump()
    assert view.selected_message is None and not view.center.bar.has_css_class("panel-only")

    # A run of messages is one block.
    runs = []

    def collect(widget: Gtk.Widget) -> None:
        if isinstance(widget, MessageRun):
            runs.append(widget)
        child = widget.get_first_child()
        while child is not None:
            collect(child)
            child = child.get_next_sibling()

    collect(view.history)
    assert runs and all(len(run.corners()) == len(run.shapes) for run in runs)

    # Back to the list, then a desktop: the foot and header return, the island hides.
    press(window, "msg-island-back")
    assert not view.split.get_show_content() and not view.title_island.get_visible()
    window.set_default_size(720, 740)
    pump(1.5)
    assert not view._phone_mode and view.split.get_collapsed()
    view.open("priya")
    pump()
    assert view.back.get_mapped()
    view.details.open()
    pump()
    ok, bounds = view.details.sheet.compute_bounds(window)
    assert ok and bounds.get_x() >= 0 and bounds.get_x() + bounds.get_width() <= window.get_width()
    view.details.close()
    view._show_conversations()
    pump()
    assert not view.split.get_show_content()
    assert view.header.get_visible() and not view.sidebar.phone_title_label.get_visible()
    window.set_default_size(1180, 740)
    pump(1.5)
    assert not view._phone_mode and view.foot.get_visible() and view.header.get_visible()
    assert not view.sidebar.phone_title_label.get_visible()
    assert view.list_center.state == "hidden" and not view.title_island.get_visible()
    window.close()
    print("messages v71 phone runtime: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
