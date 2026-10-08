# SPDX-License-Identifier: Apache-2.0
"""The page room: the toolbar, the page, the contents panel and the player.

Everything outside the page is GTK and follows the window. The page itself is
WebKit (data/reader), because an EPUB is HTML and CSS columns are how a page
of it is laid out; this module is the page's only correspondent.
"""
from __future__ import annotations

import hashlib
import json
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango, WebKit

from luma_appkit import ActionCenter, BarAction, BarChip, SubjectAction, Card, Island, LayerHost, MenuSection, ModeSwitch, ProgressLine, SEPARATOR, SelectionBubble
from luma_appkit.action_bubble import FloatingMenu
from luma_appkit.action_center import make_control
from luma_appkit import lumaui_tokens as tokens

from . import sentences
from .covers import AvailabilityGlyph  # noqa: F401  (kept for the D-Ink sweep of this room)
from .epub import Epub, EpubError
from .fixture import FixtureLibrary
from .library_view import _icon, _label
from .narrator import SLEEP_CHOICES, Narrator, NarratorState
from .position import Locator, Stats
from .scheme import LeafScheme
from .speech import SPEEDS, Speaker
from .store import COLOURS, Book, Library

DEFAULT_PREFS = {"theme": "auto", "face": "literata", "size": 19, "spacing": "normal", "margins": "normal",
                 "pages": "two", "justify": True}
COLOUR_NAMES = {"y": "Yellow", "g": "Green", "b": "Blue", "r": "Rose"}
NARROW = 760


def device_id() -> str:
    try:
        machine = Path("/etc/machine-id").read_text().strip()
    except OSError:
        machine = GLib.get_host_name()
    return hashlib.sha256(("leaf:" + machine).encode()).hexdigest()[:16]


def _js(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _segment(options, active, on_change, *, label="Mode", soft=False, fill=False) -> ModeSwitch:
    # All text-only segmented controls use the kit's shared selection part.
    return ModeSwitch(options, current=active, on_change=on_change, label=label, labels_only=True, fill=fill)


class ReaderView(Gtk.Overlay):
    __gsignals__ = {"library": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, library: Library, locator: Locator, speaker: Speaker) -> None:
        super().__init__()
        self.library = library
        self.locator = locator
        self.book: Book | None = None
        self.stats: Stats | None = None
        self.sections: list[dict] = []
        self.ready = False
        self._pending: str | None = None
        self.position: dict = {}
        self.minutes_left = 0
        self._phone = False
        self.selection: dict | None = None
        self._open_generation = 0
        self.selected_highlight = None
        self.prefs = {**DEFAULT_PREFS, **library.pref("page", {})}
        self.device = device_id()
        self.add_css_class("leaf-reader")

        self.row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        self.set_child(self.row)
        self.contents = self._build_contents()
        self.contents.set_visible(False)
        self.add_overlay(self.contents)
        self.read = Island()
        self.read.set_hexpand(True)
        self.read.add_css_class("leaf-read")
        self.read.set_name("lf-island")
        self.set_name("lf-reader")
        self.row.append(self.read)

        self._build_toolbar()
        self.web = self._build_web()
        page_overlay = Gtk.Overlay(vexpand=True)
        page_overlay.set_child(self.web)
        page_overlay.add_overlay(self._build_elsewhere())
        self.read.append(page_overlay)
        self.narrator = Narrator(speaker, self, library, locator)
        self.player = self._build_player()
        self.player.set_visible(False)
        self.action_center = ActionCenter()
        self.action_center.set_name("lf-action-center")
        self._refresh_actions()
        self.narrator.subscribe(self._narrator_changed)
        library.subscribe(self._library_changed)
        self._narrow = None
        self.add_tick_callback(self._track_width)
        style = Adw.StyleManager.get_default()
        style.connect("notify::dark", lambda *_: self._send_colours())
        style.connect("notify::high-contrast", lambda *_: self._send_colours())

    def do_map(self) -> None:
        Gtk.Overlay.do_map(self)
        if self.action_center.host is None:
            self.action_center.attach(self.read)

    def _refresh_actions(self) -> None:
        # The bar is attached to the window's overlay after the first map.
        # Reader updates may continue while the library is the visible room.
        if not getattr(self.get_root(), "reading", False):
            self.action_center.hide_bar()
            return
        contents_open=self.contents_button.get_active()
        chapter = self.chapter_label.get_label() or "Contents"
        if isinstance(self.library, FixtureLibrary) and self.book and self.book.id == "totc":
            chapter = "II · The Mail"
        if self.narrator.state.active if hasattr(self, "narrator") else False:
            state = self.narrator.state
            self.action_center.show_bar([
                BarAction("list", f"II · The Mail" if isinstance(self.library, FixtureLibrary) else chapter,
                          tooltip="Contents", on_activate=self.toggle_contents),
                SEPARATOR,
                BarAction("rotate-ccw", tooltip="Back a sentence", on_activate=self.narrator.previous_sentence),
                BarAction("pause" if state.playing else "play", tooltip="Pause" if state.playing else "Play",
                          on_activate=self.narrator.toggle),
                BarAction("rotate-cw", tooltip="Forward a sentence", on_activate=self.narrator.next_sentence),
                SEPARATOR,
                BarAction("", f"{state.speed:g}×", tooltip="Speed", on_activate=self.narrator.cycle_speed),
                BarAction("", "Stop", tooltip="Stop listening", on_activate=self.narrator.stop),
            ])
        else:
            self.chapter_subject=SubjectAction(chapter,f"{self.minutes_left} min left",on_activate=self.toggle_contents)
            self.action_center.show_bar([
                BarAction("chevron-left", tooltip="Library", on_activate=lambda: self.emit("library")),
                self.chapter_subject,
                BarAction("bookmark", tooltip="Bookmark", on_activate=self.toggle_bookmark,
                          active=self.bookmark_button.get_active()),
                BarAction("type", tooltip="Type and page", on_activate=self.show_type_panel),
                BarAction("headphones", "Listen", on_activate=self.toggle_listen),
            ],fill=self._phone)

        if self._phone and contents_open and self.action_center.grown != "contents":
            self.contents_button.set_active(True)
            if self.action_center.grown != "contents":self._show_contents(True)

    # ── toolbar ──────────────────────────────────────────────────────────
    def _build_toolbar(self) -> None:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.add_css_class("luma-toolbar")
        bar.add_css_class("leaf-rbar")
        back = Gtk.Button(valign=Gtk.Align.CENTER)
        back.add_css_class("leaf-back")
        content = Gtk.Box(spacing=3)
        content.append(_icon("chevron-left"))
        content.append(Gtk.Label(label="Library"))
        back.set_child(content)
        back.set_tooltip_text("Show library")
        back.connect("clicked", lambda *_: self.emit("library"))
        bar.append(back)
        self.contents_button = Gtk.ToggleButton(valign=Gtk.Align.CENTER)
        self.contents_button.set_child(_icon("list"))
        self.contents_button.add_css_class("luma-icon-button")
        self.contents_button.set_tooltip_text("Contents")
        self.contents_button.update_property([Gtk.AccessibleProperty.LABEL], ["Contents"])
        self.contents_button.connect("toggled", lambda b: self._show_contents(b.get_active()))
        bar.append(self.contents_button)
        titles = Gtk.Box(spacing=8, hexpand=True, valign=Gtk.Align.CENTER)
        titles.add_css_class("leaf-rtitle")
        self.title_label = _label("", "leaf-rtitle-book", ellipsize=Pango.EllipsizeMode.END)
        self.chapter_label = _label("", "leaf-rtitle-chapter", ellipsize=Pango.EllipsizeMode.END)
        self.title_label.set_width_chars(4)
        self.chapter_label.set_width_chars(4)
        titles.append(self.title_label)
        titles.append(self.chapter_label)
        bar.append(titles)
        self.type_button = Gtk.Button(valign=Gtk.Align.CENTER)
        self.type_button.set_child(_icon("a-large-small"))
        self.type_button.add_css_class("luma-icon-button")
        self.type_button.set_tooltip_text("Text and page")
        self.type_button.update_property([Gtk.AccessibleProperty.LABEL], ["Text and page"])
        self.type_button.connect("clicked", lambda *_: self.show_type_panel())
        bar.append(self.type_button)
        self.bookmark_button = Gtk.ToggleButton(valign=Gtk.Align.CENTER)
        self.bookmark_button.set_child(_icon("bookmark"))
        self.bookmark_button.add_css_class("luma-icon-button")
        self.bookmark_button.set_tooltip_text("Bookmark this page")
        self._bookmark_handler = self.bookmark_button.connect("toggled", self._bookmark_toggled)
        bar.append(self.bookmark_button)
        self.listen_button = Gtk.ToggleButton(valign=Gtk.Align.CENTER)
        self.listen_button.add_css_class("leaf-listen")
        listen = Gtk.Box(spacing=6)
        listen.append(_icon("headphones"))
        listen.append(Gtk.Label(label="Listen"))
        self.listen_button.set_child(listen)
        self.listen_button.set_tooltip_text("Read aloud")
        self._listen_handler = self.listen_button.connect("toggled", self._listen_toggled)
        bar.append(self.listen_button)
        # The old toolbar's controls remain as signal endpoints while the
        # visible controls are LumaUI ActionCenter items at the page foot.
        self._control_endpoints = bar

    # ── the page ─────────────────────────────────────────────────────────
    def _build_web(self) -> WebKit.WebView:
        # TODO(kit-request leaf-09-webkit-reader-inventory): conform must read this document's visible text.
        context = WebKit.WebContext.new()
        self.scheme = LeafScheme(self._resolve_book)
        self.scheme.register(context)
        manager = WebKit.UserContentManager()
        manager.register_script_message_handler("leaf", None)
        manager.connect("script-message-received::leaf", self._message)
        settings = WebKit.Settings(enable_javascript=True, enable_developer_extras=bool(GLib.getenv("LEAF_INSPECTOR")),
                                   allow_file_access_from_file_urls=False, allow_universal_access_from_file_urls=False,
                                   enable_back_forward_navigation_gestures=False, enable_media=False,
                                   javascript_can_access_clipboard=False, enable_webgl=False)
        settings.set_enable_write_console_messages_to_stdout(bool(GLib.getenv("LEAF_DEBUG")))
        web = WebKit.WebView(web_context=context, user_content_manager=manager, settings=settings, vexpand=True, hexpand=True)
        web.add_css_class("leaf-web")
        web.set_background_color(Gdk.RGBA(0, 0, 0, 0))
        web.connect("decide-policy", self._policy)
        web.connect("context-menu", lambda *_: True)
        web.connect("web-process-terminated", lambda *_: GLib.timeout_add_seconds(1, self._reload))
        web.load_uri("leaf://reader/app/reader.html")
        return web

    def _reload(self) -> bool:
        self.ready = False
        self.web.load_uri("leaf://reader/app/reader.html")
        if self.book is not None:
            self._pending = self._open_script(self.book)
        return GLib.SOURCE_REMOVE

    def _resolve_book(self, book_id: str) -> str | None:
        book = self.library.book(book_id)
        return book.path if book and book.on_device else None

    def _policy(self, _web, decision, kind) -> bool:
        if kind == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if not uri.startswith("leaf://reader/"):
                decision.ignore()
                return True
        elif kind == WebKit.PolicyDecisionType.NEW_WINDOW_ACTION:
            decision.ignore()
            return True
        return False

    def run(self, script: str) -> bool:
        if self.ready:
            self.web.evaluate_javascript(script, -1, None, None, None, None, None)
        return True

    def call(self, expression: str, callback) -> None:
        """Run an expression that may return a promise and hand its JSON value back."""
        def done(web, result):
            try:
                value = web.call_async_javascript_function_finish(result)
                text = value.to_json(0) if value is not None and not value.is_undefined() else "null"
                callback(json.loads(text) if text else None)
            except (GLib.Error, ValueError):
                callback(None)
        self.web.call_async_javascript_function(f"return await ({expression});", -1, None, None, None, None, done)

    # ── opening a book ───────────────────────────────────────────────────
    def open(self, book: Book, *, listen: bool = False) -> None:
        self._open_generation += 1
        generation = self._open_generation
        if self.narrator.state.active and self.narrator.state.book_id != book.id:
            self.narrator.stop()
        self.book = book
        self.stats = Stats.from_json(book.stats)
        self.library.open_book(book.id)
        self.title_label.set_label(book.short_title)
        self.chapter_label.set_label("")
        self._refresh_actions()
        if isinstance(self.library, FixtureLibrary):
            try:
                self._finish_open(book, Epub(book.path), listen, generation)
            except (EpubError, TypeError):
                self.run(f"leaf.status({_js('This book can’t be opened.')})")
            return
        self.run(f"leaf.status({_js('Opening the book…')})")

        def load() -> None:
            try:
                epub = Epub(book.path)
            except (EpubError, TypeError, OSError):
                epub = None
            GLib.idle_add(self._finish_open, book, epub, listen, generation)

        threading.Thread(target=load, daemon=True, name="leaf-open-book").start()

    def _finish_open(self, book: Book, epub: Epub | None, listen: bool, generation: int) -> bool:
        if generation != self._open_generation or self.book is None or self.book.id != book.id:
            return GLib.SOURCE_REMOVE
        if epub is None:
            self.run(f"leaf.status({_js('This book can’t be opened.')})")
            return GLib.SOURCE_REMOVE
        self.sections = [{"index": s.index, "href": s.href, "linear": s.linear} for s in epub.sections]
        self._toc = epub.toc()
        self._minutes = None
        self.scheme.forget(book.id)
        self._listen_on_open = listen
        script = self._open_script(book, epub)
        if self.ready:
            self.run(script)
        else:
            self._pending = script
        self._fill_contents()
        GLib.idle_add(lambda: self.web.grab_focus() and False)
        return GLib.SOURCE_REMOVE

    def _open_script(self, book: Book, epub: Epub | None = None) -> str:
        epub = epub or Epub(book.path)
        payload = {
            "book": {"id": book.id, "language": book.language, "sections": self.sections,
                     "sectionWords": list(self.stats.section_words),
                     "toc": [{"spine": e.spine, "fragment": e.fragment} for e in self._toc]},
            "position": self._opening_position(book),
            "highlights": [self._highlight_json(h) for h in self.library.highlights(book.id)],
            "bookmarks": [{"id": b.id, "position": b.position} for b in self.library.bookmarks(book.id)],
            "prefs": self.prefs,
            "colours": self._colours(),
            "debug": bool(GLib.getenv("LEAF_DEBUG")),
            "fixture": isinstance(self.library, FixtureLibrary),
            "fixtureDark": Adw.StyleManager.get_default().get_dark(),
        }
        return f"leaf.open({_js(payload)})"

    def _opening_position(self, book: Book) -> str | None:
        """Open where this device left off; if another device is further on, offer it."""
        own = self.library.pref(f"here.{book.id}", None)
        if book.position and book.position_device and book.position_device != self.device and own and own != book.position:
            GLib.idle_add(lambda: self._offer_elsewhere(book.position) and False)
            return own
        return book.position

    @staticmethod
    def _highlight_json(h) -> dict:
        return {"id": h.id, "range": h.range, "colour": h.colour, "note": h.note}

    def _colours(self) -> dict:
        def lookup(*names):
            context = self.get_style_context()
            for name in names:
                found, colour = context.lookup_color(name)
                if found:
                    return f"rgba({round(colour.red * 255)},{round(colour.green * 255)},{round(colour.blue * 255)},{colour.alpha:.3f})"
            return None
        dink = bool(GLib.getenv("LUMA_DINK")) or Path("/run/luma/dink").exists()
        return {"paper": lookup("luma_content", "view_bg_color", "window_bg_color"),
                "text": lookup("luma_ink", "view_fg_color", "window_fg_color"),
                "quiet": lookup("luma_muted", "luma_muted_ink", "dim_label_color"), "dink": dink}

    def _send_colours(self) -> None:
        self.run(f"leaf.colours({_js(self._colours())})")

    # ── messages from the page ───────────────────────────────────────────
    def _message(self, _manager, value) -> None:
        try:
            message = json.loads(value.to_string())
        except (ValueError, AttributeError):
            return
        kind = message.get("type")
        if GLib.getenv("LEAF_DEBUG") and kind != "relocate":
            print("leaf: message", kind, {k: v for k, v in message.items() if k in ("rect", "id", "sentence")}, flush=True)
        if kind == "ready":
            self.ready = True
            self._send_colours()
            if self._pending:
                script, self._pending = self._pending, None
                self.run(script)
        elif kind == "relocate":
            self._relocated(message)
        elif kind == "blank":
            # Always to the journal: the page found nothing drawn where the
            # reader is, and this is everything needed to say why.
            detail = {k: v for k, v in message.items() if k != "type"}
            print(f"leaf: blank page in {self.book.id if self.book else '?'}: {json.dumps(detail, ensure_ascii=False)}",
                  file=sys.stderr, flush=True)
        elif kind == "debug":
            if GLib.getenv("LEAF_DEBUG"):
                print("leaf: page", json.dumps({k: v for k, v in message.items() if k != "type"}), flush=True)
        elif kind == "select":
            self._show_selection(message, None)
        elif kind == "highlight":
            highlight = next((h for h in self.library.highlights(self.book.id) if h.id == message.get("id")), None) if self.book else None
            if highlight is not None:
                self._show_selection(message, highlight)
        elif kind == "dismiss":
            if hasattr(self, "selection_bubble"):
                self.selection_bubble.hide()
            if hasattr(self, "selection_popover"):
                self.selection_popover.popdown()
        elif kind == "key" and message.get("key") == "l":
            self.toggle_listen()
        elif kind == "open-uri":
            uri = message.get("uri", "")
            if uri.startswith(("http://", "https://", "mailto:")):
                Gtk.UriLauncher.new(uri).launch(self.get_root(), None, None, None)

    def _relocated(self, message: dict) -> None:
        if self.book is None:
            return
        self.position = message
        cfi = message.get("cfi")
        if cfi and "!" in cfi:
            self.library.set_position(self.book.id, cfi, device=self.device)
            self.library.set_pref(f"here.{self.book.id}", cfi)
            self.book = self.library.book(self.book.id) or self.book
        place = self.locator.place(self.book.path, self.stats, cfi) if cfi else None
        if place is not None:
            self.minutes_left=place.minutes_left_in_chapter()
            left = f"{place.chapter_label} · {place.minutes_left_in_chapter()} min left in chapter"
            self.chapter_label.set_label(place.chapter_label)
            if isinstance(self.library, FixtureLibrary):
                sample = self.library.sample(self.book.id)
                if sample and sample.get("p") is not None:
                    place_percent = round(sample["p"] * 100)
                    self.run(f"leaf.place({_js({'left': left, 'percent': place_percent, 'fraction': sample['p']})})")
                else:
                    self.run(f"leaf.place({_js({'left': left, 'percent': place.percent, 'fraction': place.fraction})})")
            else:
                self.run(f"leaf.place({_js({'left': left, 'percent': place.percent, 'fraction': place.fraction})})")
            self._current_chapter = place.chapter
            self._select_current_chapter()
            self._refresh_actions()
        if (isinstance(self.library, FixtureLibrary) and GLib.getenv("LUMA_LEAF_STATE") == "selection"
                and not getattr(self, "_fixture_selection_done", False)):
            self._fixture_selection_done = True
            GLib.timeout_add(250, lambda: (self.run("leaf.fixtureSelect()"), False)[1])
        self.bookmark_button.handler_block(self._bookmark_handler)
        self.bookmark_button.set_active(bool(message.get("bookmark")))
        self.bookmark_button.set_tooltip_text("Remove this bookmark" if message.get("bookmark") else "Bookmark this page")
        self.bookmark_button.update_property([Gtk.AccessibleProperty.LABEL],
                                             ["Remove this bookmark" if message.get("bookmark") else "Bookmark this page"])
        self.bookmark_button.handler_unblock(self._bookmark_handler)
        if getattr(self, "_listen_on_open", False):
            self._listen_on_open = False
            self.start_listening()

    # ── another device is ahead ──────────────────────────────────────────
    def _build_elsewhere(self) -> Gtk.Widget:
        self.elsewhere = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.NONE, halign=Gtk.Align.CENTER,
                                      valign=Gtk.Align.START, margin_top=8)
        bar = Gtk.Box(spacing=8)
        bar.add_css_class("leaf-elsewhere")
        self.elsewhere_label = _label("", "leaf-elsewhere-text")
        bar.append(self.elsewhere_label)
        go = Gtk.Button(label="Continue")
        go.add_css_class("luma-button")
        go.add_css_class("small")
        go.add_css_class("primary")
        go.connect("clicked", lambda *_: self._continue_elsewhere())
        stay = Gtk.Button()
        stay.set_child(_icon("x"))
        stay.add_css_class("luma-icon-button")
        stay.add_css_class("quiet")
        stay.update_property([Gtk.AccessibleProperty.LABEL], ["Stay here"])
        stay.connect("clicked", lambda *_: self.elsewhere.set_reveal_child(False))
        bar.append(go)
        bar.append(stay)
        self.elsewhere.set_child(bar)
        self._elsewhere_cfi = None
        return self.elsewhere

    def _offer_elsewhere(self, cfi: str) -> None:
        place = self.locator.place(self.book.path, self.stats, cfi)
        where = f"{place.chapter_label} · {place.percent}%" if place else "a later page"
        self._elsewhere_cfi = cfi
        self.elsewhere_label.set_label(f"Continue from {where} on your other device?")
        self.elsewhere.set_reveal_child(True)

    def _continue_elsewhere(self) -> None:
        self.elsewhere.set_reveal_child(False)
        if self._elsewhere_cfi:
            self.run(f"leaf.goTo({_js(self._elsewhere_cfi)})")

    def _library_changed(self, what: str, _book_id) -> None:
        if what != "external" or self.book is None:
            return
        fresh = self.library.book(self.book.id)
        if fresh is None:
            return
        # Marks from another device appear on the page as they arrive.
        self.run(f"leaf.highlights({_js([self._highlight_json(h) for h in self.library.highlights(self.book.id)])})")
        self.run(f"leaf.bookmarks({_js([{'id': b.id, 'position': b.position} for b in self.library.bookmarks(self.book.id)])})")
        ahead = (fresh.position and fresh.position_device and fresh.position_device != self.device
                 and fresh.position != self.position.get("cfi") and (fresh.position_at or 0) > (self.book.position_at or 0))
        self.book = fresh
        if ahead:
            self._offer_elsewhere(fresh.position)
        self._fill_marks()

    # ── bookmarks ────────────────────────────────────────────────────────
    def _bookmark_toggled(self, button) -> None:
        if self.book is None:
            return
        existing = self.position.get("bookmark")
        if existing:
            self.library.remove_bookmark(existing)
        elif self.position.get("cfi"):
            self.library.add_bookmark(self.book.id, self.position["cfi"], self.position.get("text", ""),
                                      int(self.position.get("spine", 0)))
        self.run(f"leaf.bookmarks({_js([{'id': b.id, 'position': b.position} for b in self.library.bookmarks(self.book.id)])})")
        self._fill_marks()

    def toggle_bookmark(self) -> None:
        self.bookmark_button.set_active(not self.bookmark_button.get_active())
        self._refresh_actions()

    # ── contents panel ───────────────────────────────────────────────────
    def _build_contents(self) -> Gtk.Widget:
        drawer = Card(padded=False)
        drawer.add_css_class("leaf-toc")
        drawer.set_name("lf-drawer")
        drawer.set_size_request(340, -1)
        drawer.set_hexpand(False)
        drawer.set_halign(Gtk.Align.START)
        drawer.set_valign(Gtk.Align.FILL)
        for margin in (drawer.set_margin_top, drawer.set_margin_bottom, drawer.set_margin_start):
            margin(14)
        inside = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inside.set_margin_top(10)
        inside.set_margin_bottom(10)
        inside.set_margin_start(10)
        inside.set_margin_end(10)
        drawer.append(inside)
        self.contents_body=inside
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.add_css_class("leaf-toc-header")
        header.set_size_request(-1, 59)
        self.drawer_cover = Gtk.Box(halign=Gtk.Align.START, valign=Gtk.Align.START)
        self.drawer_cover.set_size_request(34, 51)
        header.append(self.drawer_cover)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        self.drawer_title = _label("", "leaf-drawer-title", ellipsize=Pango.EllipsizeMode.END)
        self.drawer_author = _label("", "leaf-drawer-author", ellipsize=Pango.EllipsizeMode.END)
        names.append(self.drawer_title)
        names.append(self.drawer_author)
        header.append(names)
        close = Gtk.Button(child=_icon("x", 18), valign=Gtk.Align.START)
        close.add_css_class("leaf-drawer-close")
        close.set_size_request(36, 36)
        close.set_margin_top(7)
        close.update_property([Gtk.AccessibleProperty.LABEL], ["Close"])
        close.connect("clicked", lambda *_: self.contents_button.set_active(False))
        header.append(close)
        inside.append(header)
        divider = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        divider.add_css_class("leaf-toc-divider")
        inside.append(divider)
        self.contents_tab = "contents"
        self.contents_segment = _segment((("contents", "Contents"), ("highlights", "Highlights")),
                                         "contents", self._contents_tab, label="Show", fill=True)

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_overlay_scrolling(False)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(360)
        self.contents_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.contents_list.add_css_class("leaf-toc-list")
        self.contents_list.connect("row-activated", self._contents_activated)
        scroller.set_child(self.contents_list)
        inside.append(scroller)
        inside.append(self.contents_segment)
        return drawer

    def _show_contents(self, shown: bool) -> None:
        if self._phone:
            if shown:
                if self.contents_body.get_parent() is self.contents:self.contents.remove(self.contents_body)
                self.contents.set_visible(False)
                self.action_center.grow('contents',self.contents_body,on_fold=lambda:self.contents_button.set_active(False))
            else:self.action_center.fold_panel()
        else:
            if self.contents_body.get_parent() is not self.contents:
                parent=self.contents_body.get_parent()
                if parent:parent.remove(self.contents_body)
                self.contents.append(self.contents_body)
            self.contents.set_visible(shown)
        if shown:
            if self.book is not None:
                self.drawer_title.set_label(self.book.short_title)
                self.drawer_author.set_label(self.book.author)
                while child := self.drawer_cover.get_first_child():
                    self.drawer_cover.remove(child)
                from .covers import book_cover
                mini_cover = book_cover(title=self.book.short_title, author=self.book.author,
                                               image=self.book.cover, width=34,
                                               edition=self.library.sample(self.book.id)
                                               if isinstance(self.library, FixtureLibrary) else None)
                mini_cover.set_halign(Gtk.Align.START)
                mini_cover.set_valign(Gtk.Align.START)
                self.drawer_cover.append(mini_cover)
            self._fill_contents()

    def toggle_contents(self) -> None:
        self.contents_button.set_active(not self.contents_button.get_active())

    def _contents_tab(self, tab: str) -> None:
        self.contents_tab = tab
        self._fill_contents()

    def _fill_contents(self) -> None:
        if self.book is None or not self.contents_button.get_active():
            return
        if self.contents_tab == "contents":
            self._fill_chapters()
        else:
            self._fill_marks()

    def _fill_chapters(self) -> None:
        self.contents_list.remove_all()
        self._chapter_rows = {}
        if self._minutes is None:
            try:
                self._minutes = self.locator.chapter_minutes(self.book.path, self.stats)
            except (EpubError, OSError, KeyError):
                self._minutes = [0] * len(self.stats.chapters)
        groups = (self.library.document.get("contents", {}).get(self.book.id)
                  if isinstance(self.library, FixtureLibrary) else None)
        group_starts = {}
        if groups:
            at = 0
            for part, titles in groups:
                group_starts[at] = part
                at += len(titles)
        roman = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV")
        for index, (label, spine, _offset, depth, title) in enumerate(self.stats.chapters):
            if index in group_starts:
                heading = Gtk.ListBoxRow(selectable=False, activatable=False)
                heading.add_css_class("leaf-toc-heading")
                heading.set_child(_label(group_starts[index], "leaf-toc-group"))
                self.contents_list.append(heading)
            row = Gtk.ListBoxRow()
            row.add_css_class("leaf-toc-row")
            row.set_size_request(-1, 38)
            line = Gtk.Box(spacing=12)
            text = f"{label} · {title}" if title else label
            if groups:
                part_start = max(start for start in group_starts if start <= index)
                number = _label(roman[index - part_start] if index - part_start < len(roman)
                                else str(index - part_start + 1), "leaf-toc-number")
                number.set_size_request(24, -1)
                line.append(number)
            name = _label(text, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
            name.set_margin_start(depth * 12)
            line.append(name)
            if groups:
                if index == 1:
                    line.append(_label("Reading", "leaf-toc-reading"))
                elif index == 0:
                    line.append(_icon("check", 12))
            else:
                minutes = self._minutes[index] if index < len(self._minutes) else 0
                line.append(_label(f"{minutes} min" if minutes else "", "leaf-toc-minutes"))
            row.set_child(line)
            row.leaf_target = ("chapter", index)
            row.update_property([Gtk.AccessibleProperty.LABEL], [text])
            self.contents_list.append(row)
            self._chapter_rows[index] = row
        self._select_current_chapter()

    def _select_current_chapter(self) -> None:
        if not self.contents.get_visible() or self.contents_tab != "contents":
            return
        current = getattr(self, "_current_chapter", -1)
        row = self._chapter_rows.get(current) if current >= 0 else None
        if row is not None:
            self.contents_list.select_row(row)

    def _fill_marks(self) -> None:
        if self.book is None or self.contents_tab == "contents" or not self.contents.get_visible():
            return
        self.contents_list.remove_all()
        if self.contents_tab == "highlights":
            items = sorted(self.library.highlights(self.book.id), key=lambda h: self._order(h.range))
            if not items:
                self.contents_list.set_placeholder(_label("Select text on the page to highlight it", "leaf-toc-empty",
                                                          xalign=0.5, wrap=True))
            for highlight in items:
                row = Gtk.ListBoxRow()
                row.add_css_class("leaf-mark-row")
                grid = Gtk.Grid(column_spacing=10, row_spacing=3)
                dot = Gtk.Box(width_request=8, height_request=8, valign=Gtk.Align.START)
                dot.add_css_class("leaf-mark-dot")
                dot.add_css_class(f"colour-{highlight.colour}")
                grid.attach(dot, 0, 0, 1, 1)
                quote = _label(highlight.text, "leaf-mark-quote", wrap=True, lines=3, ellipsize=Pango.EllipsizeMode.END,
                               hexpand=True)
                quote.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                grid.attach(quote, 1, 0, 1, 1)
                line = 1
                if highlight.note:
                    note = _label(highlight.note, "leaf-mark-note", wrap=True)
                    grid.attach(note, 1, line, 1, 1)
                    line += 1
                grid.attach(_label(self._chapter_of(highlight.range), "leaf-mark-chapter"), 1, line, 1, 1)
                row.set_child(grid)
                row.leaf_target = ("cfi", highlight.range)
                spoken = f"{COLOUR_NAMES[highlight.colour]} highlight: {highlight.text}"
                if highlight.note:
                    spoken += f". Note: {highlight.note}"
                row.update_property([Gtk.AccessibleProperty.LABEL], [spoken])
                self.contents_list.append(row)
        else:
            items = sorted(self.library.bookmarks(self.book.id), key=lambda b: self._order(b.position))
            if not items:
                self.contents_list.set_placeholder(_label("No bookmarks in this book", "leaf-toc-empty", xalign=0.5))
            for mark in items:
                row = Gtk.ListBoxRow()
                row.add_css_class("leaf-mark-row")
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
                quote = _label(mark.text or "Bookmark", "leaf-mark-quote", wrap=True, lines=3,
                               ellipsize=Pango.EllipsizeMode.END)
                box.append(quote)
                box.append(_label(self._chapter_of(mark.position), "leaf-mark-chapter"))
                row.set_child(box)
                row.leaf_target = ("cfi", mark.position)
                self.contents_list.append(row)

    def _order(self, cfi: str):
        place = self.locator.place(self.book.path, self.stats, cfi.split(",")[0] + (")" if "," in cfi else ""))
        return place.words_before if place else 0

    def _chapter_of(self, cfi: str) -> str:
        start = cfi.split(",")[0] + (")" if "," in cfi else "")
        place = self.locator.place(self.book.path, self.stats, start)
        return place.chapter_label if place else ""

    def _contents_activated(self, _list, row) -> None:
        target = getattr(row, "leaf_target", None)
        if target is None:
            return
        kind, value = target
        if kind == "chapter":
            _label_, spine, _offset, _depth, _title = self.stats.chapters[value]
            entry = self._toc_entry_for(value)
            self.run(f"leaf.goToChapter({int(spine)}, {_js(entry.fragment if entry else None)})")
        else:
            self.run(f"leaf.goTo({_js(value)})")
        if self._narrow:
            self.contents_button.set_active(False)
        self.web.grab_focus()

    def _toc_entry_for(self, chapter_index: int):
        chapter = self.stats.chapters[chapter_index]
        return next((e for e in self._toc if e.spine == chapter[1] and self._anchor(e) == chapter[2]), None)

    def _anchor(self, entry) -> int:
        if not entry.fragment:
            return 0
        try:
            return Epub(self.book.path).text(entry.spine).anchors.get(entry.fragment, 0)
        except (EpubError, OSError, KeyError):
            return 0

    # ── text and page ────────────────────────────────────────────────────
    def _build_type_panel(self) -> Gtk.Widget:
        grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        grid.set_size_request(316, -1)
        grid.set_name("lf-type-page")
        grid.add_css_class("leaf-type-panel")
        themes = Gtk.Box(spacing=5, homogeneous=True)
        themes.add_css_class("leaf-themes")
        themes.set_margin_bottom(7)
        self.theme_buttons = {}
        first = None
        for key, name in (("auto", "Auto"), ("paper", "Paper"), ("sepia", "Sepia"),
                          ("stone", "Stone"), ("night", "Night")):
            button = Gtk.ToggleButton(active=self.prefs["theme"] == key)
            button.add_css_class("leaf-theme")
            face = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            swatch = Gtk.Label(label="Aa")
            swatch.add_css_class("leaf-theme-swatch")
            swatch.add_css_class(f"theme-{key}")
            # Keep the coloured preview square while the choice button
            # shares the width of the five-column theme row.
            # TODO(kit-request leaf-14-page-and-app-surface-tokens): use the
            # generated Leaf swatch metric when it lands.
            swatch.set_size_request(44, 44)
            swatch.set_halign(Gtk.Align.CENTER)
            face.append(swatch)
            face.append(Gtk.Label(label=name))
            button.set_child(face)
            button.update_property([Gtk.AccessibleProperty.LABEL], [f"{name} page"])
            if first is None:
                first = button
            else:
                button.set_group(first)
            button.connect("toggled", lambda b, k=key: b.get_active() and self.set_pref("theme", k))
            themes.append(button)
            self.theme_buttons[key] = button
        grid.append(themes)
        grid.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        stepper = Gtk.Box(spacing=8)
        stepper.add_css_class("leaf-type-size")
        smaller = make_control(BarAction("minus", tooltip="Smaller", on_activate=lambda: self.change_size(-1)), size="tool")
        self.size_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 14, 28, 1)
        self.size_scale.set_value(float(self.prefs["size"]))
        self.size_scale.set_draw_value(False)
        self.size_scale.set_hexpand(True)
        self.size_scale.connect("value-changed", lambda scale: self.set_pref("size", int(scale.get_value())))
        larger = make_control(BarAction("plus", tooltip="Larger", on_activate=lambda: self.change_size(1)), size="tool")
        for child in (smaller, self.size_scale, larger):
            stepper.append(child)
        grid.append(stepper)
        grid.append(_segment((("literata", "Literata"), ("news", "Newsreader"), ("legible", "Legible")),
                             self.prefs["face"], lambda k: self.set_pref("face", k), label="Typeface"))
        grid.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        pair = Gtk.Box(spacing=8)
        spacing = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        spacing.append(_label("Line spacing", "leaf-type-label"))
        spacing.append(_segment((("tight", "Snug"), ("normal", "Normal"), ("loose", "Airy")),
                                self.prefs["spacing"], lambda k: self.set_pref("spacing", k), label="Spacing"))
        spacing.set_hexpand(True)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        layout.append(_label("Layout", "leaf-type-label"))
        layout.append(_segment((("two", "Spread"), ("one", "Page")),
                               self.prefs["pages"], lambda k: self.set_pref("pages", k), label="Layout"))
        pair.append(spacing)
        pair.append(layout)
        grid.append(pair)
        grid.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        justify = Gtk.Switch(active=bool(self.prefs["justify"]), valign=Gtk.Align.CENTER)
        justify.connect("notify::active", lambda s, *_: self.set_pref("justify", s.get_active()))
        last = Gtk.Box(spacing=12)
        last.append(_label("Justify and hyphenate", hexpand=True))
        last.append(justify)
        grid.append(last)
        # The menu has its own outer inset. Reuse the card's vertical inset,
        # then give the page controls the narrower v70 horizontal room.
        # TODO(kit-request leaf-14-page-and-app-surface-tokens): use Leaf's
        # generated panel inset after the app token fragment lands.
        grid.set_margin_top(tokens.CARD["padding_top"] + 8)
        grid.set_margin_bottom(tokens.CARD["padding_bottom"] + 1)
        grid.set_margin_start(10)
        grid.set_margin_end(10)
        card = Card(padded=False)
        card.append(grid)
        return card

    def show_type_panel(self) -> None:
        anchor = self.action_center
        child = self.action_center.bar_row.get_first_child()
        while child is not None:
            if child.get_tooltip_text() == "Type and page":
                anchor = child
                break
            child = child.get_next_sibling()
        panel = self._build_type_panel()
        self._type_menu = FloatingMenu([MenuSection(panel)], label="Type and page",
                                       title="Type and page").popup(anchor, align="center")

    def set_pref(self, key: str, value) -> None:
        if self.prefs.get(key) == value:
            return
        self.prefs[key] = value
        self.library.set_pref("page", self.prefs)
        # Every change keeps your place: the page re-paginates and returns to
        # the sentence, not the page number.
        self.run(f"leaf.prefs({_js({key: value})})")

    def change_size(self, step: int) -> None:
        size = max(14, min(28, int(self.prefs["size"]) + step))
        self.size_scale.set_value(size)
        self.set_pref("size", size)

    # ── the selection menu ───────────────────────────────────────────────
    def _ensure_bubble(self) -> SelectionBubble:
        if hasattr(self, "selection_bubble"):
            return self.selection_bubble
        # TODO(kit-request leaf-05-selection-colours): add IM4 BarSwatches
        # between the quote chip and the actions when the shared part lands.
        bubble = SelectionBubble(self.web, [
            BarChip("Selected text", icon="quote"),
            BarAction("highlighter", "Highlight", on_activate=self._open_highlight_picker),
            BarAction("pencil", "Note", on_activate=self._begin_note),
            BarAction("book-open", "Define", on_activate=self._define_selection),
            BarAction("copy", tooltip="Copy", on_activate=self._copy_or_remove),
        ], label="Selected text actions")
        bubble.set_name("lf-bubble")
        self.selection_bubble = bubble
        self._bubble_quote = bubble.get_first_child().get_last_child()
        self._bubble_define = bubble._buttons["book-open"]
        return bubble

    def _open_highlight_picker(self) -> None:
        """Keep the four existing colours usable until IM4 supplies swatches."""
        popover = self._ensure_selection_popover()
        for colour, dot in self.dot_buttons.items():
            dot.set_active(self.selected_highlight is not None and self.selected_highlight.colour == colour)
        self.copy_button.set_label("Remove" if self.selected_highlight is not None else "Copy")
        self.selection_stack.set_visible_child_name("menu")
        rect = (self.selection or {}).get("rect") or {}
        area = Gdk.Rectangle()
        area.x, area.y = int(rect.get("x", 0)), int(rect.get("y", 0))
        area.width, area.height = max(1, int(rect.get("width", 1))), max(1, int(rect.get("height", 1)))
        popover.set_pointing_to(area)
        self.selection_bubble.hide()
        popover.popup()

    def _define_selection(self) -> None:
        if not isinstance(self.library, FixtureLibrary) or not self.selection:
            return
        word = self.selection.get("selected", "").strip(" .,;:!?“”‘’").casefold()
        definition = self.library.document.get("dictionary", {}).get(word)
        if not definition:
            return
        card = Card()
        card.append(_label(word, "leaf-definition-word"))
        card.append(_label(definition[0], "leaf-definition-kind"))
        card.append(_label(definition[1], "leaf-definition-meaning", wrap=True))
        LayerHost.for_widget(self.web).present_modal(card)
        self.selection_bubble.hide()

    def _ensure_selection_popover(self) -> Gtk.Popover:
        if hasattr(self, "selection_popover"):
            return self.selection_popover
        popover = Gtk.Popover(has_arrow=False, autohide=True)
        popover.add_css_class("leaf-selpop")
        popover.set_parent(self.web)
        popover.set_position(Gtk.PositionType.TOP)
        popover.connect("closed", lambda *_: setattr(self, "selected_highlight", None))
        stack = Gtk.Stack(hhomogeneous=False, vhomogeneous=False, interpolate_size=False)
        menu = Gtk.Box(spacing=4)
        menu.add_css_class("leaf-selmenu")
        self.dot_buttons = {}
        for colour in COLOURS:
            dot = Gtk.ToggleButton(valign=Gtk.Align.CENTER)
            dot.add_css_class("leaf-dot")
            dot.add_css_class(f"colour-{colour}")
            dot.set_tooltip_text(COLOUR_NAMES[colour])
            dot.update_property([Gtk.AccessibleProperty.LABEL], [f"Highlight {COLOUR_NAMES[colour]}"])
            dot.connect("clicked", lambda _b, c=colour: self._apply_colour(c))
            menu.append(dot)
            self.dot_buttons[colour] = dot
        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        separator.add_css_class("leaf-sp-sep")
        menu.append(separator)
        note = Gtk.Button(label="Note")
        note.add_css_class("leaf-sp-btn")
        note.connect("clicked", lambda *_: self._begin_note())
        menu.append(note)
        self.copy_button = Gtk.Button(label="Copy")
        self.copy_button.add_css_class("leaf-sp-btn")
        self.copy_button.connect("clicked", lambda *_: self._copy_or_remove())
        menu.append(self.copy_button)
        read_here = Gtk.Button(label="Read from here")
        read_here.add_css_class("leaf-sp-btn")
        read_here.connect("clicked", lambda *_: self._read_from_here())
        menu.append(read_here)
        stack.add_named(menu, "menu")

        editor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        editor.add_css_class("leaf-note")
        self.note_view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False)
        self.note_view.add_css_class("leaf-note-text")
        self.note_view.set_size_request(260, 74)
        self.note_view.update_property([Gtk.AccessibleProperty.LABEL], ["Note"])
        editor.append(self.note_view)
        actions = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        cancel.add_css_class("luma-button")
        cancel.add_css_class("small")
        cancel.connect("clicked", lambda *_: popover.popdown())
        save = Gtk.Button(label="Save")
        save.add_css_class("luma-button")
        save.add_css_class("small")
        save.add_css_class("primary")
        save.connect("clicked", lambda *_: self._save_note())
        actions.append(cancel)
        actions.append(save)
        editor.append(actions)
        stack.add_named(editor, "note")
        self.selection_stack = stack
        popover.set_child(stack)
        self.selection_popover = popover
        return popover

    def _show_selection(self, detail: dict, highlight) -> None:
        bubble = self._ensure_bubble()
        self.selection = detail
        self.selected_highlight = highlight
        quote = (detail.get("selected") or detail.get("text") or (highlight.text if highlight else "")).strip()
        snippet = quote[:22] + ("…" if len(quote) > 22 else "")
        self._bubble_quote.set_label(f"“{snippet}”")
        word = quote.strip(" .,;:!?“”‘’").casefold()
        definitions = self.library.document.get("dictionary", {}) if isinstance(self.library, FixtureLibrary) else {}
        self._bubble_define.set_visible(word in definitions)
        rect = detail.get("rect") or {}
        area = Gdk.Rectangle()
        area.x, area.y = int(rect.get("x", 0)), int(rect.get("y", 0))
        area.width, area.height = max(1, int(rect.get("width", 1))), max(1, int(rect.get("height", 1)))
        bubble.show_for(area)

    def _apply_colour(self, colour: str) -> None:
        if self.book is None:
            return
        if self.selected_highlight is not None:
            self.library.update_highlight(self.selected_highlight.id, colour=colour)
        elif self.selection:
            existing = next((h for h in self.library.highlights(self.book.id) if h.range == self.selection["range"]), None)
            if existing:
                self.library.update_highlight(existing.id, colour=colour)
            else:
                self.library.add_highlight(self.book.id, self.selection["range"], colour, self.selection.get("text", ""),
                                           int(self.selection.get("spine", 0)))
        self._push_highlights()
        self.run("leaf.clearSelection()")
        self.selection_popover.popdown()

    def _push_highlights(self) -> None:
        self.run(f"leaf.highlights({_js([self._highlight_json(h) for h in self.library.highlights(self.book.id)])})")
        self._fill_marks()

    def _begin_note(self) -> None:
        popover = self._ensure_selection_popover()
        if hasattr(self, "selection_bubble"):
            self.selection_bubble.hide()
        rect = (self.selection or {}).get("rect") or {}
        area = Gdk.Rectangle()
        area.x, area.y = int(rect.get("x", 0)), int(rect.get("y", 0))
        area.width, area.height = max(1, int(rect.get("width", 1))), max(1, int(rect.get("height", 1)))
        popover.set_pointing_to(area)
        note = self.selected_highlight.note if self.selected_highlight is not None else ""
        self.note_view.get_buffer().set_text(note)
        self.selection_stack.set_visible_child_name("note")
        popover.popup()
        self.note_view.grab_focus()

    def _save_note(self) -> None:
        buffer = self.note_view.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).strip()
        if self.selected_highlight is not None:
            self.library.update_highlight(self.selected_highlight.id, note=text)
        elif self.selection and self.book is not None:
            # A note belongs to the first sentence of the selection, which is
            # highlighted in yellow if it was not already.
            first = self.selection.get("firstRange") or self.selection["range"]
            covering = next((h for h in self.library.highlights(self.book.id) if h.range == first), None)
            if covering is not None:
                self.library.update_highlight(covering.id, note=text)
            else:
                self.library.add_highlight(self.book.id, first, "y", self.selection.get("firstText", ""),
                                           int(self.selection.get("spine", 0)), note=text)
        self._push_highlights()
        self.run("leaf.clearSelection()")
        self.selection_popover.popdown()

    def _copy_or_remove(self) -> None:
        if self.selected_highlight is not None:
            self.library.remove_highlight(self.selected_highlight.id)
            self._push_highlights()
        elif self.selection:
            # Copy what was selected, not the whole sentences.
            self.get_clipboard().set(self.selection.get("selected", ""))
        self.run("leaf.clearSelection()")
        if hasattr(self, "selection_bubble"):
            self.selection_bubble.hide()
        if hasattr(self, "selection_popover"):
            self.selection_popover.popdown()

    def _read_from_here(self) -> None:
        cfi = self.selected_highlight.range if self.selected_highlight is not None else (self.selection or {}).get("start")
        self.selection_popover.popdown()
        self.run("leaf.clearSelection()")
        if not cfi or self.book is None:
            return

        def located(found):
            if found:
                self.narrator.start(self.book, spine=int(found["spine"]), index=int(found["index"]))

        start = cfi.split(",")[0] + ")" if "," in cfi else cfi
        self.call(f"leaf.sentenceAt({_js(start)})", located)

    # ── read aloud ───────────────────────────────────────────────────────
    def _build_player(self) -> Gtk.Widget:
        bar = Gtk.Box(spacing=14)
        bar.add_css_class("leaf-player")
        bar.set_visible(False)
        transport = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        previous = Gtk.Button(valign=Gtk.Align.CENTER)
        previous.set_child(_icon("skip-back"))
        previous.add_css_class("luma-icon-button")
        previous.add_css_class("quiet")
        previous.update_property([Gtk.AccessibleProperty.LABEL], ["Previous sentence"])
        previous.connect("clicked", lambda *_: self.narrator.previous_sentence())
        self.play_button = Gtk.Button(valign=Gtk.Align.CENTER)
        self.play_button.add_css_class("leaf-pl-play")
        self.play_icon = _icon("pause", 16)
        self.play_button.set_child(self.play_icon)
        self.play_button.connect("clicked", lambda *_: self.narrator.toggle())
        following = Gtk.Button(valign=Gtk.Align.CENTER)
        following.set_child(_icon("skip-forward"))
        following.add_css_class("luma-icon-button")
        following.add_css_class("quiet")
        following.update_property([Gtk.AccessibleProperty.LABEL], ["Next sentence"])
        following.connect("clicked", lambda *_: self.narrator.next_sentence())
        for widget in (previous, self.play_button, following):
            transport.append(widget)
        bar.append(transport)

        now = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True, valign=Gtk.Align.CENTER)
        now.add_css_class("leaf-pl-now")
        self.player_title = _label("", "leaf-pl-title", ellipsize=Pango.EllipsizeMode.END)
        self.player_status = _label("", "leaf-pl-status", ellipsize=Pango.EllipsizeMode.END)
        self.player_progress = ProgressLine(0, size="tile", tone="neutral", label="Chapter progress")
        self.player_progress.set_hexpand(True)
        now.append(self.player_title)
        now.append(self.player_status)
        now.append(self.player_progress)
        now.set_size_request(140, -1)
        bar.append(now)

        self.speed_segment = _segment([(str(s), f"{s:g}×") for s in SPEEDS], "1.0", lambda k: self.narrator.set_speed(float(k)),
                                      soft=False)
        self.speed_segment.add_css_class("leaf-speeds")
        bar.append(self.speed_segment)

        self.voice_box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        self.voice_box.add_css_class("leaf-pl-pick")
        self.voice_box.append(_icon("volume-2"))
        speaker = self.narrator.speaker
        voices = Gtk.StringList.new([voice.label for voice in speaker.voices] or ["No voices installed"])
        self.voice_drop = Gtk.DropDown(model=voices)
        self.voice_drop.update_property([Gtk.AccessibleProperty.LABEL], ["Voice"])
        self._voice_handler = self.voice_drop.connect("notify::selected", self._voice_selected)
        self.voice_box.append(self.voice_drop)
        bar.append(self.voice_box)

        self.sleep_box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        self.sleep_box.add_css_class("leaf-pl-pick")
        self.sleep_box.append(_icon("timer"))
        self.sleep_drop = Gtk.DropDown(model=Gtk.StringList.new([label for _key, label in SLEEP_CHOICES]))
        self.sleep_drop.update_property([Gtk.AccessibleProperty.LABEL], ["Sleep timer"])
        self.sleep_drop.connect("notify::selected", lambda d, *_: self.narrator.set_sleep(SLEEP_CHOICES[d.get_selected()][0]))
        self.sleep_box.append(self.sleep_drop)
        bar.append(self.sleep_box)

        close = Gtk.Button(valign=Gtk.Align.CENTER)
        close.set_child(_icon("x"))
        close.add_css_class("luma-icon-button")
        close.add_css_class("quiet")
        close.update_property([Gtk.AccessibleProperty.LABEL], ["Stop reading aloud"])
        close.connect("clicked", lambda *_: self.narrator.stop())
        bar.append(close)

        # restore the reader's own speed and voice
        saved_voice = self.library.pref("listen.voice", None)
        chosen = next((v for v in speaker.voices if v.id == saved_voice), speaker.voices[0] if speaker.voices else None)
        if chosen is not None:
            self.narrator.set_voice(chosen)
            self.voice_drop.set_selected(speaker.voices.index(chosen))
        self.narrator.set_speed(float(self.library.pref("listen.speed", 1.0)))
        return bar

    def _voice_selected(self, drop, *_args) -> None:
        voices = self.narrator.speaker.voices
        index = drop.get_selected()
        if 0 <= index < len(voices):
            self.narrator.set_voice(voices[index])

    def _refresh_voices(self) -> None:
        speaker = self.narrator.speaker
        voices = speaker.refresh_voices(self.library.pref("listen.voice", None))
        self.voice_drop.handler_block(self._voice_handler)
        try:
            self.voice_drop.set_model(Gtk.StringList.new([voice.label for voice in voices] or ["No voices installed"]))
            self.voice_drop.set_sensitive(bool(voices))
            if speaker.voice is not None:
                self.voice_drop.set_selected(voices.index(speaker.voice))
                self.narrator.set_voice(speaker.voice)
        finally:
            self.voice_drop.handler_unblock(self._voice_handler)

    def _narrator_changed(self, state: NarratorState) -> None:
        self.player.set_visible(state.active)
        self.listen_button.handler_block(self._listen_handler)
        self.listen_button.set_active(state.active and state.playing)
        self.listen_button.handler_unblock(self._listen_handler)
        self.play_icon.set_from_icon_name("leaf-pause-symbolic" if state.playing else "leaf-play-symbolic")
        self.play_button.update_property([Gtk.AccessibleProperty.LABEL], ["Pause" if state.playing else "Resume"])
        chapter = f" · {state.chapter_label}" if state.chapter_label else ""
        self.player_title.set_label(f"{state.title}{chapter}")
        self.player_status.set_label(f"{'Reading aloud' if state.playing else 'Paused'} · {state.minutes_left} min left in chapter")
        self.player_progress.set_fraction(state.chapter_progress)
        self._refresh_actions()
        key = str(min(SPEEDS, key=lambda s: abs(s - state.speed)))
        button = self.speed_segment.buttons.get(key)
        if button is not None and not button.get_active():
            button.set_active(True)
        # Screen readers hear Reading aloud, Paused and Stopped — never each word.
        announcement = "Reading aloud" if state.active and state.playing else ("Paused" if state.active else "Stopped")
        if getattr(self, "_announced", None) != announcement:
            if getattr(self, "_announced", None) is not None:
                self.announce(announcement, Gtk.AccessibleAnnouncementPriority.MEDIUM)
            self._announced = announcement

    def _listen_toggled(self, button) -> None:
        if button.get_active():
            self.start_listening()
        else:
            self.narrator.pause()

    def toggle_listen(self) -> None:
        state = self.narrator.state
        if state.active:
            self.narrator.toggle()
        else:
            self.start_listening()

    def start_listening(self) -> None:
        if self.book is None:
            return
        speaker = self.narrator.speaker
        self._refresh_voices()
        if not speaker.available:
            self._voice_problem(speaker.error or "Speech isn't available on this computer.")
            self.listen_button.handler_block(self._listen_handler)
            self.listen_button.set_active(False)
            self.listen_button.handler_unblock(self._listen_handler)
            return
        if not speaker.has_neural_voice and not self.library.pref("listen.basic-voice-ok", False):
            self._offer_voice()
            return
        if self.narrator.state.active and self.narrator.state.book_id == self.book.id:
            self.narrator.resume()
        else:
            self.narrator.start(self.book)

    def _offer_voice(self) -> None:
        from luma_appkit.application_directory import discover, launch, lookup, sandboxed
        identity = "org.projectluma.NaturalVoices.desktop"
        def show_offer(manager):
            body = ("LJSpeech is the local natural English voice. Open Natural Voices "
                    "to reload it, or use the basic voice now.") if manager else (
                    "A natural voice is not installed on this computer. You can use "
                    "the basic voice now. Installed voices appear here automatically.")
            dialog = Adw.AlertDialog(heading="Choose a reading voice", body=body)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("basic", "Use Basic Voice")
            if manager:
                dialog.add_response("manage", "Open Natural Voices")
                dialog.set_default_response("manage")
            else:
                dialog.set_default_response("basic")
            dialog.set_close_response("cancel")

            def chosen(_dialog, response):
                if response == "basic":
                    self.library.set_pref("listen.basic-voice-ok", True)
                    self.start_listening()
                elif response == "manage" and manager:
                    launch("org.projectluma.NaturalVoices.desktop",
                           callback=lambda opened, error: None if opened else
                           self._voice_problem(f"Natural Voices could not open: {error}"))
                self.listen_button.handler_block(self._listen_handler)
                self.listen_button.set_active(self.narrator.state.playing)
                self.listen_button.handler_unblock(self._listen_handler)

            dialog.connect("response", chosen)
            dialog.present(self.get_root())

        if sandboxed():
            def discovered(apps, error):
                if self.get_root() is not None:
                    show_offer(next((app for app in apps if app.get_id() == identity), None))
            self._voice_discovery = discover('', discovered)
        else:
            show_offer(lookup(identity))

    def _voice_problem(self, message: str) -> None:
        dialog = Adw.AlertDialog(heading="Leaf can’t read aloud", body=message)
        dialog.add_response("ok", "OK")
        dialog.present(self.get_root())

    # ── size ─────────────────────────────────────────────────────────────
    def _track_width(self, _widget, _clock) -> bool:
        root = self.get_root()
        width = root.get_width() if root else self.get_width()
        phone=width<560
        if phone != self._phone:
            was_open=self.contents_button.get_active()
            self.contents_button.set_active(False)
            self._phone=phone
            self._refresh_actions()
            if was_open:self.contents_button.set_active(True)
        narrow = width < NARROW
        if narrow != self._narrow:
            self._narrow = narrow
            # The contents card floats over the page at every width.
            self.contents.set_size_request(min(340, max(280, width - 28)), -1)
            self.voice_box.set_visible(not narrow)
            self.sleep_box.set_visible(not narrow)
            self._refresh_actions()
        return GLib.SOURCE_CONTINUE

    def close_transient(self) -> bool:
        if hasattr(self, "selection_bubble") and self.selection_bubble.shown:
            self.selection_bubble.hide()
            return True
        menu = getattr(self, "_type_menu", None)
        if menu is not None and menu.is_open:
            menu.close()
            return True
        """Esc: close whichever panel or menu is open."""
        if hasattr(self, "selection_popover") and self.selection_popover.get_visible():
            self.selection_popover.popdown()
            return True
        if self.contents.get_visible():
            self.contents_button.set_active(False)
            return True
        return False
