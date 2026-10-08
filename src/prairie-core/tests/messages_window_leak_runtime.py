#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Messages' window keeps a bounded number of rows and bubbles across reloads.

A network that drops and recovers every few seconds re-renders the conversation
list and the open conversation each time. Every conversation row and message
bubble used to hold a signal closure over itself through its own gesture, a
cycle Python's collector cannot see through GTK, so none was ever freed:
Messages running in the background grew to 8 GB over a night (2026-09-16).

This renders the list and an open conversation with pictures many times, opens
and closes the context menus, and checks that the widgets from earlier renders
were finalized and that resident memory stays flat.
"""
import gc
import os
import sys
import tempfile
import time
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk  # noqa: E402

RELOADS = int(os.environ.get("LEAK_RELOADS", "150"))
WARMUP = 20
CONVERSATIONS = 60
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cfc00000030101009a0c1d2c0000000049454e44ae426082")

context = GLib.MainContext.default()


def pump(seconds=0.05):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.002)


def rss_kb():
    with open("/proc/self/status") as status:
        return next(int(line.split()[1]) for line in status if line.startswith("VmRSS"))


class Census:
    """Counts widgets GTK has finalized, whatever holds their Python wrappers."""

    def __init__(self):
        self.created = 0
        self.finalized = 0

    def track(self, widget):
        self.created += 1
        widget.weak_ref(self._gone)

    def _gone(self, *_args):
        self.finalized += 1

    @property
    def alive(self):
        return self.created - self.finalized


def descendants(widget):
    stack = [widget]
    while stack:
        item = stack.pop()
        yield item
        child = item.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()


def main() -> int:
    temporary = tempfile.TemporaryDirectory(prefix="messages-leak-")
    root = Path(temporary.name)
    os.environ["XDG_DATA_HOME"] = str(root / "data")
    os.environ.setdefault("XDG_STATE_HOME", str(root / "state"))

    from prairie_apps import messages as module

    rows, bubbles = Census(), Census()
    install_row, install_bubble = module.MessagesWindow._install_thread_gesture, module.MessagesWindow._install_message_gesture

    def tracked_row(self, row):
        rows.track(row)
        return install_row(self, row)

    def tracked_bubble(self, bubble, message):
        bubbles.track(bubble)
        return install_bubble(self, bubble, message)

    module.MessagesWindow._install_thread_gesture = tracked_row
    module.MessagesWindow._install_message_gesture = tracked_bubble

    app = module.MessagesApplication()
    assert app.register(None)
    window = module.MessagesWindow(app)
    window.set_default_size(980, 700)
    window.present()
    pump(0.3)

    store = window.service.store
    picture = root / "picture.png"
    picture.write_bytes(PNG)
    now = int(time.time())
    for index in range(CONVERSATIONS):
        address = f"+1202555{index:04d}"
        for count in range(6):
            store.add(address, f"Message {count} from {index}", direction="incoming" if count % 2 else "outgoing",
                      state="received" if count % 2 else "sent", timestamp=now - index * 600 + count)
    busiest = "+12025550000"
    for count in range(4):
        part = store.attach_file(busiest, picture, name=f"picture-{count}.png")
        store.add(busiest, f"Picture {count}", direction="incoming", state="received", timestamp=now + count,
                  attachment_uids=(part.uid,))

    window._reload_threads()
    record = next(item for item in store.threads() if item.address == busiest)
    window._open_thread(record, reveal=True, service=window.service)
    pump(0.3)
    shown_rows = sum(1 for widget in descendants(window.thread_list) if isinstance(widget, module.ConversationRow))
    shown_bubbles = bubbles.alive
    assert shown_rows >= CONVERSATIONS, f"expected the seeded conversations in the list, found {shown_rows}"
    assert shown_bubbles >= 10, f"expected the open conversation's bubbles, found {shown_bubbles}"

    baseline = None
    for iteration in range(RELOADS):
        # What a reconnect did: the status line, the open conversation and the list.
        # Redraws of an unchanged conversation are skipped now, so force a
        # rebuild to keep proving that replaced bubbles are freed.
        window._rendered = None
        window._render_messages()
        window._reload_threads()
        if iteration % 10 == 0:
            # Context menus on a row and a bubble, opened and dismissed.
            row = next(widget for widget in descendants(window.thread_list) if isinstance(widget, module.ConversationRow))
            window._show_thread_actions(row)
            bubble_menu_anchor = next(widget for widget in descendants(window.message_box)
                                      if widget.observe_controllers().get_n_items() >= 3)
            window._show_message_actions(bubble_menu_anchor, store.thread(busiest)[-1])
            pump(0.02)
            for widget in list(descendants(window)):
                if isinstance(widget, Gtk.Popover) and widget.get_visible():
                    widget.popdown()
        pump(0.01)
        if iteration == WARMUP:
            gc.collect()
            pump(0.1)
            baseline = rss_kb()

    gc.collect()
    pump(0.5)
    gc.collect()
    grown_kb = rss_kb() - baseline
    print(f"reloads={RELOADS} rows created={rows.created} alive={rows.alive} (shown {shown_rows}); "
          f"bubbles created={bubbles.created} alive={bubbles.alive} (shown {shown_bubbles}); "
          f"rss growth after warm-up={grown_kb} KiB")
    failures = []
    if rows.alive > 2 * shown_rows:
        failures.append(f"{rows.alive} conversation rows still alive after {RELOADS} reloads; {shown_rows} are shown")
    if bubbles.alive > 2 * shown_bubbles:
        failures.append(f"{bubbles.alive} message bubbles still alive after {RELOADS} renders; {shown_bubbles} are shown")
    limit = int(os.environ.get("LEAK_RSS_LIMIT_KB", "16384"))
    if grown_kb > limit:
        failures.append(f"resident memory grew {grown_kb} KiB after warm-up (limit {limit} KiB)")
    window.close()
    pump(0.2)
    temporary.cleanup()
    if failures:
        print("FAIL: " + "; ".join(failures), file=sys.stderr)
        return 1
    print("ok: Messages window memory stays bounded across reloads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
