#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""A conversation keeps its reading position, and its pictures show whole, at their shape, bounded in memory.

Nick's reports (2026-09-16): the thread snapped to the bottom by itself, and
every picture was a blocky kilobyte image that never became the photo. This
drives the real window with invented data only: generated JPEG (rotated by
EXIF), PNG, GIF and an undecodable HEIC with its thumbnail, a network account
whose picture arrives as a preview first and in full later, and a long
conversation that redraws for status changes, receipts and new messages.
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


def descendants(widget):
    stack = [widget]
    while stack:
        item = stack.pop()
        yield item
        child = item.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()


def image(path: Path, width: int, height: int, kind: str, **options) -> Path:
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    pixbuf.fill(0x4878b8ff)
    pixbuf.savev(str(path), kind, list(options), list(options.values()))
    return path


def gif(path: Path) -> Path:
    """A real 1x1 GIF (GdkPixbuf reads GIF but cannot write it)."""
    path.write_bytes(bytes.fromhex("47494638396101000100800000ffffff00000021f90401000000002c00000000010001000002024401003b"))
    return path


def with_exif_orientation(path: Path, orientation: int) -> Path:
    """Insert a minimal EXIF APP1 segment carrying an orientation into a JPEG."""
    data = path.read_bytes()
    tiff = b"II*\x00\x08\x00\x00\x00" + b"\x01\x00" + b"\x12\x01\x03\x00\x01\x00\x00\x00" + \
        orientation.to_bytes(2, "little") + b"\x00\x00" + b"\x00\x00\x00\x00"
    segment = b"Exif\x00\x00" + tiff
    app1 = b"\xff\xe1" + (len(segment) + 2).to_bytes(2, "big") + segment
    path.write_bytes(data[:2] + app1 + data[2:])
    return path


class Provider:
    """Stands in for a network account: its media states and nothing else."""
    remote = False
    status = {"state": "ready"}
    label = "Google Messages"

    def __init__(self, store):
        self.store = store
        self.retried = []

    def media_parts(self, uid):
        return self.store.media_parts(uid)

    def sender(self, _uid):
        return ""

    def retry_media(self, uid, part):
        self.retried.append((uid, part))

    def start(self, _callback):
        pass

    def close(self):
        pass


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="messages-thread-"))
    os.environ.update(XDG_DATA_HOME=str(root / "data"), XDG_STATE_HOME=str(root / "state"), LUMA_MESSAGES_AGENT="0",
                      PRAIRIE_EDS_MODE="disabled")
    from prairie_apps import messages, messages_photos as photos
    from prairie_apps.messages_accounts import Account, AccountStore
    from prairie_apps.messages_backend import MessageStore, MessagingCapability

    # Pure shape rules first.
    assert photos.display_size(4000, 3000) == (260, 195), photos.display_size(4000, 3000)
    assert photos.display_size(1080, 2400) == (144, 320), photos.display_size(1080, 2400)
    assert photos.display_size(8000, 1000) == (260, 96), "a panorama keeps a readable height"
    assert photos.display_size(40, 30) == (96, 96), "a tiny picture is not a speck"
    rotated = with_exif_orientation(image(root / "rotated.jpg", 1200, 800, "jpeg", quality="90"), 6)
    assert photos.jpeg_orientation(rotated) == 6
    assert photos.photo_dimensions(rotated, "image/jpeg") == (800, 1200), "EXIF rotation decides the shape"
    cache = photos.TextureCache(limit=3 * 100 * 100 * 4)
    for index in range(5):
        cache.put(("k", index), photos.texture(photos.decode(image(root / f"c{index}.png", 100, 100, "png"), 100, 100)))
    assert len(cache) == 3 and cache.size <= cache.limit, "the texture cache is bounded by bytes"

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
    for index in range(80):
        google.add(conversation, f"Message {index} " + "words " * (index % 7), direction="incoming" if index % 3 else "outgoing",
                   state="read" if index % 3 else "sent", timestamp=now - 5000 + index * 30, transport_id=f"gmessages:t{index}")

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
        adjustment = window.message_scroll.get_vadjustment()
        end = lambda: adjustment.get_upper() - adjustment.get_page_size()
        window._open_thread(record, reveal=True, service=service)
        wait(lambda: adjustment.get_upper() > adjustment.get_page_size() * 2 and abs(adjustment.get_value() - end()) < 2,
             "opening a conversation shows its newest message")

        # Reading older messages: redraws must not move the reader.
        adjustment.set_value(400)
        pump(0.1)
        bubbles_before = [w for w in descendants(window.message_box) if w.has_css_class("messages-bubble")]
        for _ in range(5):
            window._render_messages()  # a reconnect or account status change: nothing changed
            pump(0.05)
        bubbles_after = [w for w in descendants(window.message_box) if w.has_css_class("messages-bubble")]
        if bubbles_before[0] is not bubbles_after[0]:
            failures.append("an unchanged conversation was rebuilt on redraw")
        google.set_receipt(google.thread(conversation)[-3].uid, "read")  # a receipt arrives
        window._render_messages(); pump(0.3)
        if abs(adjustment.get_value() - 400) > 1:
            failures.append(f"a receipt moved the reader from 400 to {adjustment.get_value()}")
        google.add(conversation, "A new message while reading older ones", direction="incoming", state="received",
                   timestamp=now + 10, transport_id="gmessages:new1")
        window._received_changed(); pump(0.3)
        if abs(adjustment.get_value() - 400) > 1:
            failures.append(f"a new message moved a reader who was not at the end: {adjustment.get_value()}")

        # At the end, a new message is followed; sending always goes to the end.
        adjustment.set_value(end()); pump(0.1)
        google.add(conversation, "Another one", direction="incoming", state="received", timestamp=now + 20,
                   transport_id="gmessages:new2")
        window._received_changed()
        wait(lambda: abs(adjustment.get_value() - end()) < 2, "a reader at the end follows new messages")
        adjustment.set_value(100); pump(0.1)
        google.add(conversation, "Sent", direction="outgoing", state="sent", timestamp=now + 30)
        window._render_messages(to_end=True)
        wait(lambda: abs(adjustment.get_value() - end()) < 2, "sending shows the sent message")

        # Pictures: a JPEG rotated by EXIF, a PNG, a GIF, and a HEIC this system can't decode with its thumbnail.
        pictures = {
            "rotated.jpg": ("image/jpeg", rotated, (213, 320)),
            "wide.png": ("image/png", image(root / "wide.png", 900, 600, "png"), (260, 173)),
            "dot.gif": ("image/gif", gif(root / "dot.gif"), (96, 96)),
        }
        uids = {}
        for index, (name, (mime, path, _shape)) in enumerate(pictures.items()):
            uid = google.add_media_message(conversation, "", direction="incoming", state="read", timestamp=now + 40 + index,
                                           transport_id=f"gmessages:p{index}")
            google.set_media(uid, "0", state="downloading", mime=mime, name=name, size=path.stat().st_size)
            uids[name] = uid
        # A picture first known only by its kilobyte preview: shown dimmed at its shape, with a spinner.
        preview = image(root / "preview.jpg", 64, 48, "jpeg", quality="40")
        google.set_media_preview(uids["rotated.jpg"], "0", preview)
        window._received_changed(); pump(0.3)
        waiting = [w for w in descendants(window.message_box) if w.has_css_class("messages-photo") and w.has_css_class("waiting")]
        if len(waiting) != 1:
            failures.append(f"the preview is shown as a picture still on its way: {len(waiting)}")
        captions = [w.get_text() for w in descendants(window.message_box) if isinstance(w, Gtk.Label)]
        if not any("Downloading photo" in text and ("KB" in text or "bytes" in text) for text in captions):
            failures.append(f"a downloading picture says so with its real size: {captions[-6:]}")
        # The full files arrive.
        for name, (mime, path, _shape) in pictures.items():
            google.complete_media(uids[name], "0", path, name=name, mime=mime)
        window._received_changed(); pump(0.2)
        shown = {}
        for widget in descendants(window.message_box):
            if isinstance(widget, Gtk.Picture) and not widget.get_ancestor(Gtk.Overlay).has_css_class("waiting"):
                shown[tuple(widget.get_size_request())] = widget
        for name, (_mime, _path, shape) in pictures.items():
            if shape not in shown:
                failures.append(f"{name} is not shown at its shape {shape}: {sorted(shown)}")
        tooltips = [w.get_tooltip_text() or "" for w in descendants(window.message_box) if w.has_css_class("messages-photo-open")]
        if not any("rotated.jpg · " in text and "KB" in text for text in tooltips):
            failures.append(f"a photo names its real size: {tooltips}")
        adjustment.set_value(end()); pump(0.1)
        wait(lambda: all(widget.get_paintable() is not None for widget in shown.values()), "pictures in view decode")
        texture = next(iter(shown.values())).get_paintable()
        if texture.get_width() > 260 * 2 * max(1, window.get_scale_factor()):
            failures.append(f"a picture decoded larger than shown: {texture.get_width()}")
        # Far out of view, a picture gives its pixels back; back in view, it gets them again.
        adjustment.set_value(0); pump(0.4)
        if any(widget.get_paintable() is not None for widget in shown.values()):
            failures.append("pictures far out of view still hold their pixels")
        adjustment.set_value(end()); pump(0.1)
        wait(lambda: all(widget.get_paintable() is not None for widget in shown.values()), "pictures back in view")
        if window.picture_cache.size > window.picture_cache.limit:
            failures.append("the picture cache exceeded its bound")

        # Clicking a picture opens the lightbox inside this window, never another application.
        launched = []
        from gi.repository import Gdk
        from luma_appkit import Lightbox
        with patch.object(Gio.AppInfo, "launch_default_for_uri", lambda *a: launched.append(a)), \
             patch.object(Gio.AppInfo, "get_default_for_type", lambda *a: launched.append(a)):
            photo_buttons = [w for w in descendants(window.message_box) if w.has_css_class("messages-photo-open")
                             and w.get_sensitive() and "rotated.jpg" in (w.get_tooltip_text() or "")]
            photo_buttons[0].grab_focus()
            photo_buttons[0].emit("clicked")
            pump(0.2)
            lightbox = Lightbox.for_window(window)
            if not lightbox.is_open or launched or len(Gtk.Window.list_toplevels()) != 1:
                failures.append(f"a picture did not open in the window's lightbox: open={lightbox.is_open} launched={launched}")
            else:
                shown = [item.name for item in lightbox.items]
                if shown[:3] != ["rotated.jpg", "wide.png", "dot.gif"] or lightbox.items[lightbox.index].name != "rotated.jpg":
                    failures.append(f"the lightbox does not step through the conversation's pictures in order: {shown}")
                if lightbox.items[0].title != "Alex Rivera" or "Today" not in lightbox.items[0].subtitle:
                    failures.append(f"sender and time: {lightbox.items[0].title!r} {lightbox.items[0].subtitle!r}")
                wait(lambda: lightbox.current_paintable() is not None, "the lightbox shows the picture")
                if lightbox.natural_size() != (800, 1200):
                    failures.append(f"EXIF orientation in the lightbox: {lightbox.natural_size()}")
                keys = next(c for c in lightbox.observe_controllers() if isinstance(c, Gtk.EventControllerKey))
                keys.emit("key-pressed", Gdk.KEY_Right, 0, 0)
                if lightbox.index != 1:
                    failures.append("Right does not step to the next picture")
                lightbox.set_zoom(3.0)
                if lightbox.zoom != 3.0:
                    failures.append("zoom")
                keys.emit("key-pressed", Gdk.KEY_Escape, 0, 0)
                keys.emit("key-pressed", Gdk.KEY_Escape, 0, 0)
                wait(lambda: not lightbox.get_visible(), "the lightbox closes")
                if not photo_buttons[0].has_focus():
                    failures.append("closing the lightbox does not return focus to the picture")
                if lightbox.decoded_indices():
                    failures.append("the lightbox kept pixels after closing")
                if launched:
                    failures.append(f"an external application was launched: {launched}")

        # A HEIC this system can't decode says so and shows its thumbnail instead.
        heic_uid = google.add_media_message(conversation, "", direction="incoming", state="read", timestamp=now + 60,
                                            transport_id="gmessages:heic")
        heic = root / "IMG_0001.heic"
        heic.write_bytes(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64)
        google.complete_media(heic_uid, "0", heic, name="IMG_0001.heic", mime="image/heic")
        google._connection.execute("UPDATE account_media SET preview_key=NULL WHERE message_uid=?", (heic_uid,))
        google.set_media_preview(heic_uid, "0", image(root / "heic-thumb.jpg", 320, 240, "jpeg", quality="80"))
        window._received_changed(); pump(0.3)
        texts = [w.get_text() for w in descendants(window.message_box) if isinstance(w, Gtk.Label)]
        if not any("can't show HEIC photos" in text for text in texts):
            failures.append(f"an undecodable HEIC does not say so: {photos.photo_dimensions(google.attachment_path(google.message(heic_uid).attachments[0]), 'image/heic')} {[t for t in texts if 'photo' in t.lower() or 'HEIC' in t]} {[w.get_tooltip_text() for w in descendants(window.message_box) if w.has_css_class('messages-photo-open')]}")

        # A failed picture offers Try Again, which asks the account.
        failed_uid = google.add_media_message(conversation, "", direction="incoming", state="read", timestamp=now + 70,
                                              transport_id="gmessages:failed")
        google.set_media(failed_uid, "0", state="failed", mime="image/jpeg", name="IMG_2.jpg", size=2_400_000, error="expired")
        window._received_changed(); pump(0.3)
        again = [w for w in descendants(window.message_box) if isinstance(w, Gtk.Button) and w.get_label() == "Try Again"]
        if not again:
            failures.append("a failed picture has no Try Again")
        else:
            again[0].emit("clicked")
            if provider.retried != [(failed_uid, "0")]:
                failures.append(f"Try Again did not ask the account: {provider.retried}")
        window.close()
        pump(0.2)
    if failures:
        print("FAIL: " + "; ".join(failures), file=sys.stderr)
        return 1
    print("ok: the thread keeps its reading position and shows pictures whole, at their shape, bounded in memory")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
