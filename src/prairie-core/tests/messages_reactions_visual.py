#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Crops of message reactions, for review: one, two and several, sent and received.

Invented data only. Renders the real Messages window headless and crops each
reacted message together with the message before it, so a reviewer sees where
the reactions sit, what they overlap and what they come close to:

    LUMA_VISUAL_MODE=dark xvfb-run -a -s "-screen 0 1200x1700x24" \\
        messages_reactions_visual.py OUTPUT_DIR [--label before|after]

The window is rendered straight to a texture, so every crop is exactly the
widgets' own bounds, and a JSON sidecar records the pill and bubble geometry.
The same script runs against an older tree (PYTHONPATH and the style paths
pointed at it) to make the "before" crops.

The pill is an overlay child pulled 18px above its overlay by a negative
margin, so it draws only because nothing between it and the conversation
clips. Setting overflow hidden on the bubble row, the Clamp or the overlay
would cut it off without any error; these crops are where that would show.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Gio, GLib, Graphene, Gtk  # noqa: E402

MODE = os.environ.get("LUMA_VISUAL_MODE", "light")
NOW = int(time.time())

# (direction, body, reactions as (person, emoji), crop name)
CONVERSATION = (
    ("incoming", "Are we still on for Saturday?", (), None),
    ("incoming", "Becky says the north gate needs a new hinge too.", (("p1", "👍"),), "incoming-one"),
    ("outgoing", "On it, I'll grab one at the hardware store on the way.", (("p1", "❤️"),), "outgoing-one"),
    ("incoming", "Perfect. Bring the good coffee too", (("p1", "😂"), ("p2", "👍")), "incoming-two"),
    ("outgoing", "Obviously", (("p1", "😂"), ("p2", "😂")), "outgoing-two"),
    ("incoming", "Weather looks clear all weekend, finally. We could do the fence and the gate in one go.",
     (("p1", "👍"), ("p2", "👍"), ("p3", "😮"), ("p4", "❤️"), ("p5", "😂")), "incoming-several"),
    ("outgoing", "ok", (("p1", "👍"), ("p2", "❤️"), ("p3", "😂"), ("p4", "😮")), "outgoing-several"),
    ("incoming", "See you at 9", (), None),
)


def settle(seconds=0.3):
    context = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def descendants(widget):
    stack = [widget]
    while stack:
        item = stack.pop()
        yield item
        child = item.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()


def bounds(widget, target):
    found, rect = widget.compute_bounds(target)
    return (rect.get_x(), rect.get_y(), rect.get_width(), rect.get_height()) if found else None


def main() -> int:
    output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
    label = sys.argv[sys.argv.index("--label") + 1] if "--label" in sys.argv else "after"
    root = Path(tempfile.mkdtemp(prefix="messages-reactions-"))
    os.environ.update(XDG_DATA_HOME=str(root / "data"), XDG_STATE_HOME=str(root / "state"),
                      XDG_CONFIG_HOME=str(root / "config"), PRAIRIE_EDS_MODE="disabled", LUMA_MESSAGES_AGENT="0")
    gi.require_version("LumaAppearance", "1")
    from gi.repository import LumaAppearance
    patch.object(LumaAppearance.SurfacePolicy, "get_has_selection", lambda self: True).start()
    patch.object(LumaAppearance.SurfacePolicy, "get_effective", lambda self: MODE).start()
    from prairie_apps import messages
    from prairie_apps.messages_accounts import Account, AccountStore
    from prairie_apps.messages_backend import MessagingCapability

    store = AccountStore(root / "google" / "messages.sqlite3")
    store.remember_conversation({"id": "c-1", "kind": "direct",
                                 "participants": [{"id": "p1", "name": "Becky Hale", "phone": "+15125550120"}]})
    store.set_display_name("c-1", "Becky Hale")
    names = {}
    for index, (direction, body, reactions, name) in enumerate(CONVERSATION):
        record = store.add("c-1", body, direction=direction, state="read" if direction == "incoming" else "sent",
                           timestamp=NOW - (len(CONVERSATION) - index) * 60, transport_id=f"gmessages:r{index}")
        if reactions:
            store.replace_reactions(record.uid, [{"sender": who, "emoji": emoji} for who, emoji in reactions])
        if name:
            names[record.uid] = name
    store.mark_read("c-1")

    class Provider:
        label = "Google Messages"
        remote = False
        status = {"state": "ready"}
        store_path = root / "google"

        def start(self, _callback):
            pass

        def stop(self):
            pass

    def services(self, _application):
        account = Account("fixture", "gmessages", name="", handle="", pending=False)
        return [messages.MessageService("account:fixture", "Google Messages", store, None, None, Provider(),
                                        MessagingCapability(True, ""), account=account)]

    app = messages.MessagesApplication()
    app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    messages.install_messages_theme()
    report = {"mode": MODE, "label": label, "crops": {}}
    with patch.object(messages.MessagesWindow, "_discover_services", services), \
         patch.object(messages.MessagesWindow, "_start_external_context_load", return_value=False), \
         patch.object(messages.MessagesWindow, "_restore_last_conversation", return_value=False), \
         patch.object(messages.MessagesWindow, "_watch_wake", lambda self: None):
        window = messages.MessagesWindow(app)
        window.set_default_size(1100, 1600)
        window.present()
        settle(0.6)
        service = window.services[0]
        record = next(item for item in store.threads() if item.address == "c-1")
        window._open_thread(record, reveal=True, service=service)
        settle(0.8)

        width, height = window.get_width(), window.get_height()
        paintable = Gtk.WidgetPaintable(widget=window)
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, width, height)
        node = snapshot.to_node()
        texture = window.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, width, height))
        whole = output / f"reactions-{label}-{MODE}.png"
        texture.save_to_png(str(whole))

        column = bounds(window.message_box, window)
        order = window._block_order
        for uid, name in names.items():
            block = window._blocks[uid]
            everything = [w for top in block.widgets for w in descendants(top)]
            row = bounds(block.anchor, window)
            pill_widget = next((w for w in everything if w.has_css_class("messages-reactions")), None)
            bubble_widget = next((w for w in everything if w.has_css_class("messages-bubble")), None)
            pill = bounds(pill_widget, window) if pill_widget else None
            bubble = bounds(bubble_widget, window) if bubble_widget else None
            index = order.index(uid)
            above = bounds(window._blocks[order[index - 1]].widgets[-1], window) if index else None
            top = min(v for v in (row[1], pill[1] if pill else row[1], above[1] if above else row[1]))
            bottom = max(row[1] + row[3], (pill[1] + pill[3]) if pill else 0) + 12
            box = (int(column[0]), int(max(0, top - 10)), int(column[2]), int(min(height, bottom) - max(0, top - 10)))
            crop = output / f"reactions-{label}-{MODE}-{name}.png"
            texture_crop(texture, box, crop)
            report["crops"][name] = dict(file=crop.name, crop=box, pill=pill, bubble=bubble, row=row, above=above)
        window.close()
        settle(0.3)
    (output / f"reactions-{label}-{MODE}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def texture_crop(texture, box, path: Path) -> None:
    """Cut a rectangle out of a rendered texture and save it."""
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    full = path.with_suffix(".full.png")
    texture.save_to_png(str(full))
    pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(full))
    x, y, w, h = box
    w, h = min(w, pixbuf.get_width() - x), min(h, pixbuf.get_height() - y)
    pixbuf.new_subpixbuf(x, y, w, h).savev(str(path), "png", [], [])
    full.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
