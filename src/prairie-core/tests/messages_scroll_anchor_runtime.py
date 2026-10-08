#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""A conversation never moves while someone reads it, and stays at the newest message when they are there.

Nick's report (2026-09-17): "Messages often jumps me up ... a few messages."
Every redraw rebuilt the whole conversation and put back a pixel offset, so
anything that changed height above the reader (a picture arriving, a failed
download's card, a delivery line, an older message synced in) moved what they
were reading, and GTK's viewport scrolled to any message text that took focus.

This drives the real window headless, with invented data only, and samples the
position of the message at the top of the view on every painted frame (not only
once things settle) through: late-loading pictures, receipts, a delivery line
appearing, reactions (above, below and on the message being read, whose text
must not move), idempotent and changed upserts, a typing-indicator row
appearing and going at the end, new incoming messages, older history arriving,
a forced full redraw, a resize, and a click on a message's text. Allowed drift:
0 px once settled, and at most 1 px in any painted frame. At the end, the newest
message stays flush with the bottom on every frame.
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
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

context = GLib.MainContext.default()
TOLERANCE = 0.5  # sub-pixel rounding only: 0 px of visible drift
# The rows and the scroll position are placed in the same layout, so nothing
# is corrected a frame late: no painted frame may show the message being read,
# or the newest message at the end, more than a pixel from where it was.
REMAINDER = 1


def pump(seconds=0.05):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.003)


def wait(predicate, what, timeout=10):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError("timed out: " + what)
        pump(0.02)


def image(path: Path, width: int, height: int) -> Path:
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    pixbuf.fill(0x4878b8ff)
    pixbuf.savev(str(path), "png", [], [])
    return path


class Provider:
    """Stands in for a network account: its media states and senders, nothing else."""
    remote = False
    status = {"state": "ready"}
    label = "Google Messages"

    def __init__(self, store):
        self.store = store

    def media_parts(self, uid):
        return self.store.media_parts(uid)

    def sender(self, _uid):
        return ""

    def retry_media(self, _uid, _part):
        pass

    def start(self, _callback):
        pass

    def close(self):
        pass


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="messages-anchor-"))
    os.environ.update(XDG_DATA_HOME=str(root / "data"), XDG_STATE_HOME=str(root / "state"), LUMA_MESSAGES_AGENT="0",
                      PRAIRIE_EDS_MODE="disabled")
    from prairie_apps import messages
    from prairie_apps.messages_accounts import Account, AccountStore
    from prairie_apps.messages_backend import MessageStore, MessagingCapability

    native = MessageStore(root / "native" / "messages.db")
    google = AccountStore(root / "google" / "messages.db")
    provider = Provider(google)

    def services(self, _application):
        account = Account("fixture", "gmessages", pending=False)
        return [messages.MessageService(messages.NATIVE_SERVICE, "This device", native, None, None),
                messages.MessageService("account:fixture", "Google Messages", google, None, None, provider,
                                        MessagingCapability(True, ""), account=account)]

    now = int(time.time())
    conversation = "c1"
    google.remember_conversation({"id": conversation, "kind": "direct", "participants": []})
    google.set_display_name(conversation, "Alex Rivera")
    counter = iter(range(10_000))

    def say(body, *, at, incoming=True, state=None):
        return google.add(conversation, body, direction="incoming" if incoming else "outgoing",
                          state=state or ("read" if incoming else "sent"), timestamp=at,
                          transport_id=f"gmessages:m{next(counter)}")

    uids = []
    for index in range(120):
        media_slot = index in {50, 60, 70, 110}
        if media_slot:
            uid = google.add_media_message(conversation, "", direction="incoming", state="read",
                                           timestamp=now - 7200 + index * 40, transport_id=f"gmessages:p{index}")
            google.set_media(uid, "0", state="downloading", mime="image/png", name=f"IMG_{index}.png", size=900_000)
            uids.append(uid)
        else:
            uids.append(say(f"Message {index} " + "words " * (index % 9), at=now - 7200 + index * 40,
                            incoming=bool(index % 3)).uid)

    app = messages.MessagesApplication()
    app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    failures = []
    with patch.object(messages.MessagesWindow, "_discover_services", services), \
         patch.object(messages.MessagesWindow, "_start_external_context_load", return_value=False), \
         patch.object(messages.MessagesWindow, "_restore_last_conversation", return_value=False), \
         patch.object(messages.MessagesWindow, "_watch_wake", lambda self: None):
        window = messages.MessagesWindow(app)
        window.set_default_size(980, 700)
        window.present()
        pump(0.4)
        service = next(item for item in window.services if item.account is not None)
        record = next(item for item in google.threads() if item.address == conversation)
        scroll = window.message_scroll
        adjustment = scroll.get_vadjustment()
        end = lambda: max(0.0, adjustment.get_upper() - adjustment.get_page_size())
        window._open_thread(record, reveal=True, service=service)
        wait(lambda: adjustment.get_upper() > adjustment.get_page_size() * 3 and abs(adjustment.get_value() - end()) < 1,
             "opening a conversation shows its newest message")
        pump(0.3)

        def row_of(uid):
            block = window._blocks.get(uid)
            return block.anchor if block is not None else None

        def y_in_view(uid):
            widget = row_of(uid)
            if widget is None or not widget.get_mapped():
                return None
            found, bounds = widget.compute_bounds(scroll)
            return bounds.get_y() if found else None

        def gap_at_end():
            last = window.message_box.get_last_child()
            found, bounds = last.compute_bounds(scroll)
            return scroll.get_height() - (bounds.get_y() + bounds.get_height()) if found else None

        frames = []
        text_frames = []  # a message's text, per frame, where a scenario asks for it
        watched = {"uid": None, "mode": "anchor", "text": None}

        def sample(_clock):
            if watched["mode"] == "anchor" and watched["uid"]:
                frames.append(y_in_view(watched["uid"]))
                if watched["text"]:
                    text_frames.append(text_top(watched["text"]))
            elif watched["mode"] == "end":
                frames.append((gap_at_end(), adjustment.get_value(), end()))

        clock = window.get_frame_clock()
        clock.connect("after-paint", sample)

        def settle(seconds=0.35):
            # Keep frames coming so every layout the change causes is painted and sampled.
            end_at = time.monotonic() + seconds
            while time.monotonic() < end_at:
                window.message_box.queue_draw()
                pump(0.02)

        def top_uid():
            value = adjustment.get_value()
            for key in window._block_order:
                widget = row_of(key)
                found, bounds = widget.compute_bounds(window.message_box)
                if found and bounds.get_y() >= value + 4:
                    return key
            raise AssertionError("no message in view")

        def reading(label, change, *, anchor_uid=None):
            settle(0.15)
            uid = anchor_uid or top_uid()
            before = y_in_view(uid)
            watched.update(uid=uid, mode="anchor")
            frames.clear()
            change()
            settle()
            watched["uid"] = None
            after = y_in_view(uid)
            if after is None or abs(after - before) > TOLERANCE:
                failures.append(f"reading: {label} left the message being read at {after} instead of {before}")
            moved = [abs(seen - before) for seen in frames
                     if seen is not None and abs(seen - before) > TOLERANCE]
            worst = max(moved) if moved else 0.0
            # At most one frame may differ, and by no more than a pixel of rounding.
            if len(moved) > 1 or worst > REMAINDER:
                failures.append(f"reading: {label} moved the message being read by {worst:.1f}px "
                                f"in {len(moved)} of {len(frames)} frames")
            print(f"  reading  {label:<48} frames={len(frames):>3} carried={len(moved)} worst={worst:.2f}px")

        def at_end(label, change, *, offset=0.0):
            adjustment.set_value(end() - offset)
            settle(0.15)
            watched.update(uid=None, mode="end")
            frames.clear()
            change()
            settle()
            watched["mode"] = None
            if abs(adjustment.get_value() - end()) > TOLERANCE:
                failures.append(f"at the end: {label} left the newest message: "
                                f"{adjustment.get_value():.0f} of {end():.0f}")
            gaps = [gap for gap, _value, _bottom in frames if gap is not None]
            drifted = [abs(gap - gaps[-1]) for gap in gaps if abs(gap - gaps[-1]) > TOLERANCE] if gaps else []
            off_end = [abs(value - bottom) for _gap, value, bottom in frames
                       if abs(value - bottom) > TOLERANCE]
            if len(off_end) > 1 or (off_end and max(off_end) > REMAINDER):
                failures.append(f"at the end: {label} was away from the newest message by "
                                f"{max(off_end):.1f}px in {len(off_end)} of {len(frames)} frames")
            if len(drifted) > 1 or (drifted and max(drifted) > REMAINDER):
                failures.append(f"at the end: {label} moved the newest message's bottom edge by "
                                f"{max(drifted):.1f}px in {len(drifted)} frames")
            print(f"  at end   {label:<48} frames={len(frames):>3} carried={len(off_end)}")

        def redraw():
            window._received_changed()

        # ---- Reading older messages: the message at the top of the view never moves.
        target = row_of(uids[80])
        found, bounds = target.compute_bounds(window.message_box)
        adjustment.set_value(bounds.get_y() - 40)
        settle(0.3)

        bubbles = {key: window._blocks[key].anchor for key in window._block_order}

        def late_picture(uid, size):
            def change():
                google.complete_media(uid, "0", image(root / f"{uid}.png", *size), name=f"{uid}.png", mime="image/png")
                redraw()
            return change

        reading("a tall picture finishing above", late_picture(uids[70], (1080, 2400)))
        unchanged = [key for key in window._block_order if key != uids[70] and key in bubbles]
        rebuilt = [key for key in unchanged if window._blocks[key].anchor is not bubbles[key]]
        if rebuilt:
            failures.append(f"a picture arriving rebuilt {len(rebuilt)} other messages; updates must be in place")
        if window._blocks[uids[70]].anchor is bubbles[uids[70]]:
            failures.append("the message whose picture arrived was not redrawn")

        def picture_fails():
            google.set_media(uids[60], "0", state="failed", mime="image/png", name="IMG_60.png", size=900_000, error="gone")
            redraw()
        reading("a picture download failing above", picture_fails)

        def receipt_above():
            outgoing = [uid for uid in uids[:78] if google.message(uid).direction == "outgoing"]
            google.set_receipt(outgoing[-1], "read")
            redraw()
        reading("a read receipt above", receipt_above)

        def delivery_line_above():
            outgoing = [uid for uid in uids[:78] if google.message(uid).direction == "outgoing"]
            google.update_state(outgoing[-2], "failed")
            redraw()
        reading("a 'Not delivered' line appearing above", delivery_line_above)

        def reaction_above():
            google.replace_reactions(uids[76], [{"sender": "p1", "emoji": "♥"}, {"sender": "p2", "emoji": "😂"}])
            redraw()
        reading("reactions arriving above", reaction_above)

        # ---- Reactions sit on the bubble's top corner (Nick, 2026-09-17). The
        # pill's room is the row's margin, so a reaction on the very message
        # being read must not move its text, and only that message is redrawn.
        def text_top(uid):
            block = window._blocks.get(uid)
            label = next((w for w in _descendants(block.anchor) if w.has_css_class("messages-bubble-text")), None) \
                if block is not None else None
            if label is None or not label.get_mapped():
                return None
            found, bounds = label.compute_bounds(scroll)
            return bounds.get_y() if found else None

        # The message the window itself is holding: the one a person is reading.
        read_uid = window._anchor_uid if window._anchor_uid in window._blocks else top_uid()
        before_text = text_top(read_uid)
        kept = {key: window._blocks[key].anchor for key in window._block_order if key != read_uid}

        def reaction_on_read():
            google.replace_reactions(read_uid, [{"sender": "p1", "emoji": "👍"}])
            redraw()
        text_frames.clear()
        watched["text"] = read_uid
        reading("a reaction on the message being read", reaction_on_read, anchor_uid=read_uid)
        watched["text"] = None
        # The stricter claim, with no carried frame allowed: the reaction's room
        # is absorbed before anything is drawn, so no painted frame at all shows
        # the text of the message being read anywhere but where it was.
        if before_text is not None:
            off = [y for y in text_frames if y is None or abs(y - before_text) > TOLERANCE]
            worst = max((abs(y - before_text) for y in off if y is not None), default=0.0)
            if not text_frames:
                failures.append("a reaction on the message being read: no frame was painted to check")
            elif off:
                failures.append(f"a reaction on the message being read moved its text in {len(off)} of "
                                f"{len(text_frames)} painted frames (worst {worst:.1f}px); none may")
            print(f"  text     {'reaction on the message being read':<48} frames={len(text_frames):>3} "
                  f"moved={len(off)} worst={worst:.2f}px")
        after_text = text_top(read_uid)
        if before_text is not None and (after_text is None or abs(after_text - before_text) > TOLERANCE):
            failures.append(f"a reaction on the message being read moved its text from {before_text} to {after_text}")
        rebuilt = [key for key, widget in kept.items() if key in window._blocks and window._blocks[key].anchor is not widget]
        if rebuilt:
            failures.append(f"a reaction rebuilt {len(rebuilt)} other messages; only the one it is on may be redrawn")

        def more_reactions_on_read():
            google.replace_reactions(read_uid, [{"sender": "p1", "emoji": "👍"}, {"sender": "p2", "emoji": "😂"},
                                                {"sender": "p3", "emoji": "😮"}, {"sender": "p4", "emoji": "👍"}])
            redraw()
        reading("more reactions joining on the message being read", more_reactions_on_read, anchor_uid=read_uid)

        def reaction_below():
            below = window._block_order[window._block_order.index(read_uid) + 3]
            google.replace_reactions(below, [{"sender": "p1", "emoji": "♥"}])
            redraw()
        reading("a reaction arriving below", reaction_below, anchor_uid=read_uid)

        # Where the pill lands, measured rather than eyeballed: over the top
        # edge, inside the bubble's padding, clear of the text and of the
        # message before, on the trailing corner sent and the leading received.
        outgoing_uid = next(uid for uid in uids[77:100] if google.message(uid).direction == "outgoing"
                            and google.message(uid).body)
        google.replace_reactions(outgoing_uid, [{"sender": "p1", "emoji": "😂"}, {"sender": "p2", "emoji": "😂"}])
        redraw(); settle(0.2)
        for uid in (read_uid, outgoing_uid):
            block = window._blocks[uid]
            pill = next((w for w in _descendants(block.anchor) if w.has_css_class("messages-reactions")), None)
            bubble = next((w for w in _descendants(block.anchor) if w.has_css_class("messages-bubble")), None)
            text = next((w for w in _descendants(block.anchor) if w.has_css_class("messages-bubble-text")), None)
            if pill is None or bubble is None:
                failures.append(f"reaction pill: message {uid} has no pill on its bubble")
                continue
            _, p = pill.compute_bounds(window.message_box)
            _, b = bubble.compute_bounds(window.message_box)
            overlap = p.get_y() + p.get_height() - b.get_y()
            if not 0 < overlap <= 6 + TOLERANCE:
                failures.append(f"reaction pill: overlaps the bubble's top edge by {overlap:.1f}px, wanted 1-6px")
            if text is not None:
                _, t = text.compute_bounds(window.message_box)
                if p.get_y() + p.get_height() + 2 > t.get_y() + TOLERANCE:  # its 2px ring included
                    failures.append(f"reaction pill: its ring reaches the text ({p.get_y() + p.get_height() + 2:.1f} "
                                    f"> {t.get_y():.1f})")
            index = window._block_order.index(uid)
            if index:
                _, above = window._blocks[window._block_order[index - 1]].widgets[-1].compute_bounds(window.message_box)
                if p.get_y() - 2 < above.get_y() + above.get_height() - TOLERANCE:
                    failures.append("reaction pill: its ring meets the message before")
            sent = google.message(uid).direction == "outgoing"
            edge = (b.get_x() + b.get_width()) - (p.get_x() + p.get_width()) if sent else p.get_x() - b.get_x()
            if not 0 <= edge <= 16:
                side = "trailing" if sent else "leading"
                failures.append(f"reaction pill: {edge:.1f}px from the bubble's {side} edge, wanted its corner")
            print(f"  pill     {'outgoing' if sent else 'incoming':<8} overlap={overlap:.1f}px corner={edge:.1f}px")

        def idempotent_upsert():
            # The bridge reports messages it already reported: nothing changes, nothing moves.
            before = {key: window._blocks[key].anchor for key in window._block_order}
            window._render_messages()
            redraw()
            if any(window._blocks[key].anchor is not widget for key, widget in before.items()):
                failures.append("an idempotent upsert rebuilt rows")
        reading("an idempotent upsert", idempotent_upsert)

        def changed_upsert():
            google._connection.execute("UPDATE messages SET body=? WHERE uid=?",
                                       ("Edited " + "much longer text " * 12, uids[74]))
            google._connection.commit()
            redraw()
        reading("an upsert that lengthens a message above", changed_upsert)

        def typing_row():
            indicator = Gtk.Label(label="Alex is typing…")
            indicator.set_size_request(-1, 36)
            window.message_box.append(indicator)
            settle(0.1)
            window.message_box.remove(indicator)
        reading("a typing row appearing and going below", typing_row)

        def new_message():
            say("A new message while reading", at=now + 10)
            redraw()
        reading("a new incoming message below", new_message)

        def history():
            for index in range(30):
                say(f"Older history {index} " + "words " * (index % 5), at=now - 90_000 + index * 60, incoming=bool(index % 2))
            redraw()
        reading("older history loading above", history)

        def forced_redraw():
            window._rendered = None
            window._render_messages()
        reading("a full redraw (reconnect)", forced_redraw)

        anchor_before = top_uid()

        def resize():
            window.set_default_size(820, 640)
        reading("the window resizing", resize, anchor_uid=anchor_before)
        window.set_default_size(980, 700)
        settle(0.3)

        def click_text():
            uid = top_uid()
            labels = [widget for widget in _descendants(window._blocks[uid].anchor) if isinstance(widget, Gtk.Label)
                      and widget.get_selectable()]
            # A click puts focus on the text, which GTK's viewport used to scroll to.
            if labels:
                labels[0].grab_focus()
        reading("a click on a message's text", click_text)

        def deleted_anchor():
            uid = top_uid()
            watched["uid"] = None
            google._connection.execute("DELETE FROM messages WHERE uid=?", (uid,))
            google._connection.commit()
            redraw()
        # The message being read is deleted: the next one holds its place, no jump to elsewhere.
        settle(0.1)
        next_uid = window._block_order[window._block_order.index(top_uid()) + 1]
        before_next = y_in_view(next_uid)
        deleted_anchor()
        settle()
        after_next = y_in_view(next_uid)
        if after_next is None or not before_next - 400 < after_next <= before_next:
            failures.append(f"deleting the message being read moved the view elsewhere: {before_next} -> {after_next}")

        # ---- At the end: the newest message stays flush with the bottom through everything.
        at_end("a picture finishing in the newest messages", late_picture(uids[110], (1080, 2400)))

        def receipt_last():
            uid = say("Sent from here", at=now + 20, incoming=False).uid
            redraw()
            settle(0.1)
            google.set_receipt(uid, "delivered")
            redraw()
            settle(0.05)
            google.set_receipt(uid, "read")
            redraw()
        at_end("a sent message's receipts", receipt_last)

        def reaction_last():
            google.replace_reactions(window._block_order[-1], [{"sender": "p1", "emoji": "👍"}])
            redraw()
        at_end("a reaction on the newest message", reaction_last)

        def typing_at_end():
            indicator = Gtk.Label(label="Alex is typing…")
            indicator.set_size_request(-1, 36)
            window.message_box.append(indicator)
            settle(0.15)
            window.message_box.remove(indicator)
        at_end("a typing row appearing and going", typing_at_end)
        at_end("a new incoming message", lambda: (say("Incoming at the end " + "words " * 30, at=now + 30), redraw()))
        at_end("a new message a few pixels from the end", lambda: (say("Another", at=now + 31), redraw()), offset=10)
        at_end("older history loading", lambda: ([say(f"Oldest {i}", at=now - 200_000 + i) for i in range(20)], redraw()))
        at_end("an upsert", lambda: (window._render_messages(), redraw()))
        at_end("a full redraw (reconnect)", lambda: (setattr(window, "_rendered", None), window._render_messages()))
        at_end("the window resizing", lambda: window.set_default_size(860, 600))
        at_end("the window refocusing", lambda: (window.set_focus(None), window.composer.grab_focus()
                                                 if hasattr(window, "composer") else None))

        window.close()
        pump(0.2)
    if failures:
        print("FAIL: " + "; ".join(failures), file=sys.stderr)
        return 1
    print("ok: the message being read holds still (0 px on every frame) and the end stays the end")
    return 0


def _descendants(widget):
    stack = [widget]
    while stack:
        item = stack.pop()
        yield item
        child = item.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()


if __name__ == "__main__":
    raise SystemExit(main())
