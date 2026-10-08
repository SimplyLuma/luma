# SPDX-License-Identifier: Apache-2.0
"""Memos: LumaUI composition around the user's real recording library.

The in-memory LUMA_MEMOS_FIXTURE route never inspects hardware or user files.
The ordinary route keeps the established record, rename, trash and export
operations; v70-only writes remain fixture-only pending Nick's approval.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
import math
import os
from pathlib import Path
import time
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from luma_appkit import (  # noqa: E402
    ActionCenter, AppWindow, BarAction, BarChip, BarWidget, Command, CommandGroup, CommandRegistry,
    CornerPill, DestructiveDialog, EmptyState, HeroTitleField, Island, IslandSplitView,
    Menu, NavigationSidebar, Person, ScrollView, SEPARATOR, ShareSheet, ShareSubject,
    SidebarFoot, SidebarSection, Toast, ToastHost,
    add_style_sheet, apply_type, install_appkit, install_lumaui,
)
from luma_appkit import (  # noqa: E402  v71 parts (KIT-STATUS.md)
    BarSearch, PanelChoices, PanelHeading, PanelKey, PanelSwitch, SharePanel, SwipeAction, SwipeRow,
    TitleIsland, panel_list,
)
from luma_appkit.action_center import make_control
from luma_appkit.action_bubble import MenuItem  # noqa: E402
from luma_appkit.background import LiveExtensionBinding
from luma_appkit.structure_adapt import WidthWatch  # noqa: E402
from . import audio_backend as audio  # noqa: E402


# Memos' phone targets for Share (v71 meBarPhone): the audio, the words, the words copied, Nearby.
_SHARE_TARGETS = (("music", "Audio", "audio"), ("file-text", "Transcript", "transcript"),
                  ("copy", "Copy text", "copy-text"), ("radio-tower", "Nearby", "nearby"))


from .memos_data import Memo, Word, filter_memos, format_duration, load_fixture  # noqa: E402
from .memos_widgets import MemoDeck, MemoRow, TimedTranscript  # noqa: E402

APP_ID = "org.projectluma.VoiceMemos"
FIXTURE_ENV = "LUMA_MEMOS_FIXTURE"
_SHARE_PEOPLE = (
    Person("Priya Raman", username="priya", hue=330),
    Person("Nora Feld", username="nora", hue=45),
    Person("Theo Marsh", username="theo", hue=350),
    Person("Sam Kaur", username="samk", hue=200),
    Person("Alex Ruiz", username="alexr", hue=262),
    Person("Ada Chen", username="ada", hue=200),
    Person("Jordan Kim", username="jordan.k", hue=100),
    Person("Zoe Martin", username="zoe", hue=270),
)
_SHARE_FACES = ("PR", "NF", "TH", "SK", "AR")


def _real_memo(record: audio.Recording, peaks: tuple[float, ...], duration: float) -> Memo:
    """Read existing fields only. Missing word timing and favourites stay absent."""
    day = (datetime.now() - record.modified).days
    group = "Today" if day <= 0 else "This week" if day < 7 else "Earlier"
    transcript = tuple(("You", str(cue["text"])) for cue in record.transcript)
    words = []
    for index, cue in enumerate(record.transcript):
        pieces = str(cue["text"]).split()
        start = float(cue["start"])
        span = max(0.0, float(cue.get("end", start)) - start)
        words.extend(Word(piece, "You", index, start + (number + 0.5) / len(pieces) * span)
                     for number, piece in enumerate(pieces))
    return Memo(str(record.path), record.path.stem, audio.format_date(record.modified),
                group, duration or record.duration or 0, record.source,
                False, (), transcript, tuple(words), tuple(peaks), False)


class VoiceMemosWindow(AppWindow):
    def __init__(self, application: Adw.Application) -> None:
        self.fixture_path = os.environ.get(FIXTURE_ENV)
        self.fixture = bool(self.fixture_path)
        self.memos: list[Memo] = []
        self.deleted: list[Memo] = []
        self.current_id: str | None = None
        self.view = "all"
        self.query = ""
        self.paths: dict[str, Path] = {}
        self.playback = None
        self.session = None
        self._recording_activity = None
        self.recording = False
        self._draft = False
        self._continuing: Path | None = None
        self.record_paused = False
        self.record_started = 0.0
        self.record_elapsed = 0.0
        self.record_pause_started = 0.0
        self.live_peaks: list[float] = []
        self.live_words: list[str] = []
        self.play_position = 0.0
        self.playing = False
        self.speed = 1.0
        self._timer = 0
        self._load_generation = 0
        self._closed = False
        self._busy = False
        self._saving_close = False
        self._menu = None
        self._fixture_share_people: tuple[Person, ...] | None = None
        self._suppress_row = False
        self.phone = False            # under 560: list first, one bar per page, the title island
        self.mic = "phone"            # New memo's microphone (fixture: This phone / Studio headphones)
        self.enhance = True           # Reduce background noise (fixture)
        self.skip_silences = False    # Playback silence skipping (fixture)
        self.title_island = None
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="memos-io")
        self.root = None if self.fixture else audio.recordings_directory()
        commands = CommandRegistry((CommandGroup("", (
            Command("memos.record", "New recording", self._new_recording, "plus", shortcut=("Ctrl", "N")),
            Command("memos.find", "Search what was said", self._focus_search, "search", shortcut=("Ctrl", "F")),
            Command("memos.about", "About Memos", self._about, "info"),
            Command("memos.quit", "Quit", self.close, "log-out"),
        )),))
        super().__init__(application=application, app_id=APP_ID, title="Memos",
                         icon_name=APP_ID, commands=commands, default_width=1180,
                         default_height=740, minimum_width=360, minimum_height=420)
        self._build()
        application.connect("shutdown", lambda *_: self._close_recording_activity())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        if self.fixture:
            selected, memos, deleted = load_fixture(self.fixture_path)
            self.memos, self.deleted, self.current_id = list(memos), list(deleted), selected
            state = os.environ.get("LUMA_MEMOS_STATE", "")
            self.view = "fav" if state == "favourites" else "deleted" if state == "deleted-empty" else "all"
            if state == "music":
                self.current_id = "hum"
            elif state == "deleted-empty":
                self.current_id = None
            if query := os.environ.get("LUMA_MEMOS_SEARCH"):
                self.foot.entry.set_text(query)
            self.foot.set_filter(self.view)
            if state == "recording":
                self._start_fixture_recording(static=True)
            # A phone opens on its list; these fixture states (the gate's phone pages) push a memo's page.
            self._fixture_page = state in ("music", "open", "recording")
            self._fixture_panel = os.environ.get("LUMA_MEMOS_PANEL", "")
            self._render()
        else:
            self._loading()
            self._reload_real()

    def _build(self) -> None:
        self.sidebar = NavigationSidebar()
        self.sidebar.set_name("me-sidebar")
        self.sidebar.set_phone_title("Memos")
        self.sidebar.list.connect("row-selected", self._row_selected)
        self.sidebar.list.connect("row-activated", self._row_activated)
        filters = (("all", "All", "layers"), ("fav", "Favorites", "star"),
                   ("deleted", "Recently deleted", "trash-2")) if self.fixture else ()
        self.foot = SidebarFoot(search="Search what was said", on_search=self._search,
                                filters=filters, on_filter=self._filter)
        self.foot.set_name("me-foot")
        if self.foot.filter_button:
            self.foot.filter_button.set_name("me-filter")
        if filters:
            self.sidebar.append_header(self.foot.heading)
        self.sidebar.append_footer(self.foot)

        self.island = Island()
        self.island.set_name("me-island")
        self.page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.page.add_css_class("me-page")
        self.page.set_margin_top(47)
        self.page.set_margin_start(44)
        self.page.set_margin_end(44)
        self.page.set_margin_bottom(120)
        heading = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        heading.add_css_class("me-heading")
        self.heading = heading
        self.recording_dot = Gtk.Box(width_request=9, height_request=9,
                                     valign=Gtk.Align.CENTER, visible=False)
        self.recording_dot.add_css_class("me-recording-dot")
        heading.append(self.recording_dot)
        self.eyebrow = apply_type(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END), "meta")
        heading.append(self.eyebrow)
        self.page.append(heading)
        self.title_field = HeroTitleField("", editable=False, placeholder="Memo title",
                                         role="detail-title", on_commit=self._rename)
        self.title_field.set_name("me-title")
        self.title_field.set_alignment(0)
        self.title_field.set_halign(Gtk.Align.FILL)
        self.title_field.add_css_class("me-title")
        self.title_label = apply_type(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END), "detail-title")
        self.title_label.add_css_class("me-title-label")
        title_click = Gtk.GestureClick()
        title_click.connect("released", lambda *_args: self._focus_title())
        self.title_label.add_controller(title_click)
        self.title_holder = Gtk.Stack()
        self.title_holder.set_vhomogeneous(False)
        self.title_holder.add_named(self.title_label, "title")
        self.title_holder.add_named(self.title_field, "edit")
        self.title_holder.set_visible_child_name("title")
        title_focus = Gtk.EventControllerFocus()
        title_focus.connect("leave", lambda *_args: GLib.idle_add(self._title_focus_left))
        self.title_field.add_controller(title_focus)
        self.page.append(self.title_holder)
        self.subheading = apply_type(Gtk.Label(xalign=0, visible=False), "body")
        self.subheading.add_css_class("me-place")
        self.page.append(self.subheading)
        self.deck = MemoDeck(on_seek=self._seek, on_play=self._play,
                             on_skip=self._skip, on_speed=self._set_speed, speed_extras=self._speed_extras)
        self.page.append(self.deck)
        self.transcript = TimedTranscript(self._seek)
        self.page.append(self.transcript)
        clamp = Adw.Clamp(maximum_size=820, child=self.page)
        self.scroll = ScrollView(clamp)
        self.content_stack = Gtk.Stack(vexpand=True, hexpand=True)
        self.content_stack.add_named(self.scroll, "memo")
        self.empty = EmptyState("No recording selected", "Choose a recording, or create a new one.", "lumaui-mic-symbolic")
        self.content_stack.add_named(self.empty, "empty")
        self.loading = EmptyState("Loading memos…", "", "lumaui-mic-symbolic")
        self.content_stack.add_named(self.loading, "loading")
        overlay = Gtk.Overlay(child=self.content_stack)
        self.corner_slot = Adw.Bin(halign=Gtk.Align.END, valign=Gtk.Align.START)
        self.corner_slot.set_name("me-corner-slot")
        overlay.add_overlay(self.corner_slot)
        overlay.set_measure_overlay(self.corner_slot, False)
        self.island.append(overlay)
        self.host = ToastHost(self.island)
        self.action = ActionCenter().attach(self.host)
        self.action.set_name("me-action")
        # Phone: the list's foot is a bar (Search, Show, New), so the list gets its own action center.
        self.list_host = ToastHost(self.sidebar)
        self.list_action = ActionCenter().attach(self.list_host)
        self.list_action.set_name("me-list-action")
        self.list_action.hide_bar()
        self.list_search = BarSearch("Search what was said", label="Search memos", span="narrow", keep=True,
                                     on_change=self._search)

        self.split = IslandSplitView(collapsed=False, show_content=True)
        self.split.set_sidebar_width_unit(Adw.LengthUnit.PX)
        self.split.set_min_sidebar_width(280)
        self.split.set_max_sidebar_width(280)
        self.split.set_sidebar(Adw.NavigationPage.new(self.list_host, "Memos"))
        self.split.connect("notify::show-content", lambda *_a: self._sync_island())
        self.split.connect("notify::collapsed", lambda *_a: self._list_first())
        self.split.set_content(Adw.NavigationPage.new(self.host, "Memo"))
        self.set_body(self.split)
        # Phone: ‹ | what's open replaces the floating Back and the in-page eyebrow and title.
        self.title_island = TitleIsland("", "", lead="back", lead_label="Back to Memos", on_lead=self._back)
        self.title_island.set_name("me-title-island")
        self.title_island.float_over(self.island)  # the window's layer: top 52, left 12
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 899px"))
        narrow.add_setter(self.split, "min-sidebar-width", 236.0)
        narrow.add_setter(self.split, "max-sidebar-width", 236.0)
        narrow.add_setter(self.page, "width-request", -1)
        self.add_breakpoint(narrow)
        phone = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 760px"))
        phone.add_setter(self.split, "min-sidebar-width", 236.0)
        phone.add_setter(self.split, "max-sidebar-width", 236.0)
        phone.add_setter(self.split, "collapsed", True)
        phone.add_setter(self.page, "width-request", -1)
        for side, value in (("top", 31), ("start", 20), ("end", 20), ("bottom", 110)):
            phone.add_setter(self.page, f"margin-{side}", value)
        self.add_breakpoint(phone)
        # Under 560 the app redraws in its phone shape (v71: "redraw on crossing the phone width").
        self._tier_watch = WidthWatch(self, on_tier=self._tier_changed)

    def _loading(self) -> None:
        self.content_stack.set_visible_child_name("loading")
        self.sidebar.clear()
        self.action.show_bar([BarAction("plus", "New recording", self._new_recording, primary=True)])

    def _submit(self, work: Callable, done: Callable) -> None:
        future = self.executor.submit(work)

        def completed(result) -> None:
            def deliver() -> bool:
                if not self._closed:
                    try:
                        value, error = result.result(), None
                    except Exception as failure:  # a failed I/O job is shown, not lost
                        value, error = None, failure
                    done(value, error)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(deliver)
        future.add_done_callback(completed)

    def _reload_real(self) -> None:
        self._load_generation += 1
        generation = self._load_generation
        root = self.root

        def read():
            records = audio.list_recordings(root)
            result = []
            paths = {}
            for record in records:
                try:
                    peaks, duration = audio.load_peaks(record.path, root)
                except (OSError, RuntimeError, ValueError):
                    peaks, duration = (), record.duration or 0
                memo = _real_memo(record, tuple(peaks), duration)
                result.append(memo)
                paths[memo.id] = record.path
            return result, paths

        def loaded(result, error) -> None:
            if generation != self._load_generation:
                return
            if error:
                Toast.show(self.host, f"Memos could not be read: {error}", kind="error")
                self.memos, self.paths = [], {}
            else:
                self.memos, self.paths = result
            if not self._draft and self.current_id not in {memo.id for memo in self.memos}:
                self.current_id = self.memos[0].id if self.memos else None
            self._render()
        self._submit(read, loaded)

    def _search(self, query: str) -> None:
        self.query = query
        self._render_list()

    def _filter(self, view: str) -> None:
        self.view = view
        shown = self._visible()
        if self.current_id not in {memo.id for memo in shown}:
            self.current_id = shown[0].id if shown else None
        self._render()

    def _visible(self) -> tuple[Memo, ...]:
        items = self.deleted if self.view == "deleted" else self.memos
        return filter_memos(tuple(items), "fav" if self.view == "fav" else "all", self.query)

    def _find(self, identifier: str | None = None) -> Memo | None:
        wanted = self.current_id if identifier is None else identifier
        return next((memo for memo in (*self.memos, *self.deleted) if memo.id == wanted), None)

    def _render(self) -> None:
        self._render_list()
        self._render_detail()
        self._render_bar()
        self._render_list_bar()

    # ── Phone (under 560): list first, one bar per page, the title island ──────────────

    def _tier_changed(self, tier: str) -> None:
        phone = tier == "phone"
        if phone == self.phone:
            return
        self.phone = phone
        if phone and not getattr(self, "_picked", False) and not getattr(self, "_fixture_page", False):
            # List first: a phone opens on the full-screen list until a memo is picked (after the
            # 760 breakpoint collapses the split).
            GLib.idle_add(lambda: self._list_first() and GLib.SOURCE_REMOVE)
        self._fold_panel()
        self.page.add_css_class("phone") if phone else self.page.remove_css_class("phone")
        # The 760 breakpoint already holds 31/20/20/110; the phone gutter is 16, and the page
        # starts 20 under the title island (52 without it, v71 .mepad). On a phone the kit's one
        # safe area gives the scroller its room above the bar, so the page keeps no guessed bottom.
        top = 76 if phone else 31
        side = 16 if phone else 20
        for name, value in (("top", top), ("start", side), ("end", side), ("bottom", 0 if phone else 110)):
            getattr(self.page, f"set_margin_{name}")(value)
        self._render()
        GLib.idle_add(lambda: self._sync_island() or GLib.SOURCE_REMOVE)  # after the island's own tier pass
        panel = getattr(self, "_fixture_panel", "")
        if phone and panel:
            self._fixture_panel = ""
            GLib.timeout_add(300, lambda: self._open_panel(panel) or GLib.SOURCE_REMOVE)

    def _list_first(self) -> None:
        if (self.phone and self.split.get_collapsed() and not getattr(self, "_picked", False)
                and not getattr(self, "_fixture_page", False)):
            self.split.set_show_content(False)

    def _back(self) -> None:
        """The title island's ‹: up one level, to the list."""
        self._fold_panel()
        self.split.set_show_content(False)

    def _sync_island(self) -> None:
        if self.title_island is None:  # still being built
            return
        memo = None if self.recording else self._find()
        shown = self.phone and self.split.get_show_content() and (self.recording or memo is not None)
        self.title_island.set_visible(shown)
        if not shown:
            return
        self.title_island.set_status(("paused" if self.record_paused else "record") if self.recording else None)
        if self.recording:
            self.title_island.set_title("New memo", f"{'Paused' if self.record_paused else 'Recording'} · Studio")
        else:
            self.title_island.set_title(memo.title, f"{memo.when} · {format_duration(memo.duration)}")

    def _open_panel(self, key: str) -> None:
        """Grow a phone bar into `key`'s panel, as tapping its button does (the gate's panel states)."""
        center = self.list_action if key in ("show", "new") else self.action
        center.grow(key, self._share_panel() if key == "share" else self._phone_panel(key))

    def _fold_panel(self) -> None:
        for center in (self.list_action, self.action):
            if center.grown:
                center.fold_panel()

    def _phone_panel(self, key: str) -> Gtk.Widget:
        """What a grown phone bar holds (v71 meListBar / meBarPhone panels). Rows fold the bar after acting."""
        memo = self._find()
        if key == "more":
            return panel_list(self._more_rows(memo), label="More")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        if key == "show":  # v71 .fexs: one row of chips
            box.append(PanelHeading("Show"))
            box.append(PanelChoices((("all", "All", "layers"), ("fav", "Favorites", "star"),
                                     ("deleted", "Recently deleted", "trash-2")),
                                    selected=self.view, on_choose=self._choose_view))
            return box
        # "new": the microphone, Reduce background noise, then a full-width red Start recording.
        box.append(PanelHeading("New memo"))
        if self.fixture:  # the real route records from the one source audio_backend finds
            box.append(PanelChoices((("phone", "This phone", "smartphone"),
                                     ("buds", "Studio headphones", "headphones")),
                                    selected=self.mic, on_choose=self._choose_mic))
            box.append(PanelSwitch("sparkles", "Reduce background noise", active=self.enhance,
                                   on_toggle=self._set_enhance))
        box.append(PanelKey("New recording", on_activate=self._start_from_panel))
        return box

    def _choose_view(self, view: str) -> None:
        self._fold_panel()
        self.foot.set_filter(view)
        self._filter(view)

    def _choose_mic(self, key: str) -> None:
        self.mic = key

    def _set_enhance(self, active: bool) -> None:
        self.enhance = active

    def _start_from_panel(self) -> None:
        self._fold_panel()
        self._new_recording()

    def _new_recording(self) -> None:
        if self.recording or self._busy:
            return
        self._stop_playback()
        self._continuing = None
        self._draft = True
        self.current_id = None
        self._render()
        self.split.set_show_content(True)

    def _continue_recording(self) -> None:
        memo = self._find()
        if memo is None or self.recording or self._busy:
            return
        if self.fixture:
            Toast.show(self.host, "Continuation requires a saved audio file", kind="warning")
            return
        path = self.paths.get(memo.id)
        if path is None:
            return
        self._stop_playback()
        self._continuing = path
        self._draft = True
        self.current_id = None
        self._render()
        self.split.set_show_content(True)

    def _cancel_draft(self) -> None:
        if self._busy:
            return
        self.current_id = str(self._continuing) if self._continuing else None
        self._continuing = None
        self._draft = False
        self._render()

    def _draft_actions(self):
        return [BarAction("mic", "Start recording", self._record, primary=True),
                BarAction("x", "Cancel", self._cancel_draft)]

    def _render_list_bar(self) -> None:
        """The phone list's foot is a bar: Search, Show, New (v71 meListBar); a computer keeps the sidebar foot."""
        self.foot.set_visible(not self.phone)
        if not self.phone:
            self.list_action.hide_bar()
            return
        self.list_search.set_text(self.query)
        items: list[object] = [self.list_search]
        if self.fixture:  # Favorites and Recently deleted are fixture views (real memos have neither yet)
            items.append(BarAction("list-filter", tooltip="Show", active=self.view != "all", key="show",
                                   panel=lambda: self._phone_panel("show")))
        items.append(BarAction("plus", "New recording", self._new_recording, primary=True, keep_label=True, key="new",
                               sensitive=not self.recording and not self._busy,
                               ))
        self.list_action.show_bar(items, fill=True)

    def _render_phone_bar(self, memo: Memo | None) -> None:
        """A memo's page has one bar, the corner folded in (v71 meBarPhone)."""
        if self.recording:
            # The status is an inset well taking the rest of the row (Nick: the left recording side
            # is inset), then Pause at 48 and Done sized to its label.
            self.action.show_bar([
                self._recording_chip(well=True),
                BarAction("mic" if self.record_paused else "pause",
                          tooltip="Resume" if self.record_paused else "Pause",
                          on_activate=self._pause_recording),
                BarAction("", "Done", self._record, primary=True),
            ], fill=True)
        elif self._draft:
            self.action.show_bar(self._draft_actions(), fill=True)
        elif self.view == "deleted" and memo:
            self.action.show_bar([BarAction("rotate-ccw", "Put back", self._restore, primary=True, keep_label=True,
                                            fill=True),
                                  BarAction("", "Delete for good", self._purge, fill=True)], fill=True)
        elif memo:
            # Share grows the bar: people, then Audio, Transcript, Copy text, Nearby.
            items = [BarAction("share-2", tooltip="Share", key="share", panel=self._share_panel)]
            if self.fixture:  # favourites and trim are fixture-only writes
                items += [BarAction("star", tooltip="Remove from Favorites" if memo.favourite else "Favorite",
                                    active=memo.favourite, on_activate=lambda: self._favourite(not memo.favourite)),
                          BarAction("scissors", tooltip="Trim", on_activate=self._trim)]
            if not self.fixture:
                items.append(BarAction("mic", tooltip="Continue recording", on_activate=self._continue_recording))
            items.append(BarAction("plus", tooltip="New recording", on_activate=self._new_recording, primary=True))
            items.append(BarAction("ellipsis", tooltip="More", key="more", panel=lambda: self._phone_panel("more")))
            self.action.show_bar(items, fill=True)
        else:
            self.action.hide_bar()

    def _share_panel(self) -> Gtk.Widget:
        return SharePanel(people=self._panel_people(), targets=_SHARE_TARGETS, on_choice=self._share_choice)

    def _panel_people(self) -> tuple[Person, ...]:
        """The phone panel's five (v71: Priya, Nora, Sam, Theo, Dad); the fixture's only."""
        if not self.fixture:
            return ()
        people = {person.name.split()[0]: person for person in self._share_people()}
        dad = Person("Dad", hue=20)
        try:
            face = Gdk.Texture.new_from_filename(str(Path(self.fixture_path).parent / "memos-v70" / "face-MO.jpg"))
            dad = replace(dad, picture=face)
        except GLib.Error:
            pass
        return (people["Priya"], people["Nora"], people["Sam"], people["Theo"], dad)

    def _swipe(self, memo: Memo) -> Callable[[Gtk.Widget], Gtk.Widget] | None:
        """Right = Favorite (yellow), left = Delete (red): v71 swipe rows (a phone only)."""
        start = (SwipeAction("star", "yellow", lambda _row: self._favourite(not memo.favourite, memo),
                             label="Favorite")
                 if self.fixture and self.view != "deleted" else None)
        end = (SwipeAction("trash-2", "red", lambda _row: self._delete(memo), label="Delete")
               if self.view != "deleted" else None)
        return lambda child: SwipeRow(child, start=start, end=end)

    def _render_list(self) -> None:
        self._suppress_row = True
        self.sidebar.clear()
        if self.recording:
            # TODO(kit-request memos-02-recording-row): use NavigationRow(recording=True).
            row = Gtk.ListBoxRow(selectable=False, activatable=False)
            row.add_css_class("me-recording-row")
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            dot = Gtk.Box(width_request=9, height_request=9, valign=Gtk.Align.CENTER)
            dot.add_css_class("me-recording-dot")
            content.append(dot)
            names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            names.append(apply_type(Gtk.Label(label="Recording…", xalign=0), "body", weight=600))
            elapsed = apply_type(Gtk.Label(label=format_duration(self.record_elapsed), xalign=0), "caption")
            elapsed.add_css_class("me-recording-elapsed")
            names.append(elapsed)
            content.append(names)
            row.set_child(content)
            self.sidebar.list.append(row)
        shown = self._visible()
        group = None
        for memo in shown:
            if self.view != "deleted" and memo.group != group:
                group = memo.group
                self.sidebar.list.append(SidebarSection(group))
            row = MemoRow(memo, query=self.query, swipe=self._swipe(memo))
            self.sidebar.list.append(row)
            if memo.id == self.current_id:
                row.mini.set_on(True)
                if not self.phone:  # a phone's list marks no row (v71 .phstack .merow.on)
                    self.sidebar.list.select_row(row)
        if not shown and not self.recording:
            message = ("Deleted memos stay here for 30 days." if self.view == "deleted" else
                       "No matching recordings." if self.query else "Your recordings will appear here.")
            label = apply_type(Gtk.Label(label=message, xalign=0, wrap=True), "caption")
            label.set_margin_start(12)
            label.set_margin_top(12)
            self.sidebar.list.append(Gtk.ListBoxRow(selectable=False, activatable=False, child=label))
        self._suppress_row = False

    def _row_selected(self, _list, row) -> None:
        # On a phone only a tap (activation) pushes a memo's page; a selection the list makes
        # on its own (focus, first map) must not leave the list.
        if not self._suppress_row and not self.phone and row and hasattr(row, "memo"):
            self._open(row.memo.id)

    def _row_activated(self, _list, row) -> None:
        if hasattr(row, "memo"):
            self._picked = True
            self._open(row.memo.id)

    def _open(self, identifier: str) -> None:
        if self.recording:
            self.current_id = identifier
            self._render_list()
            return
        self._stop_playback()
        self._fold_panel()
        self._draft = False
        self._continuing = None
        self.current_id = identifier
        self.play_position = 0
        self._render_detail()
        self._render_bar()
        self.split.set_show_content(True)

    def _render_detail(self) -> None:
        in_island = self.phone
        self.heading.set_visible(not in_island)
        self.title_holder.set_visible(not in_island)
        (self.page.add_css_class if in_island else self.page.remove_css_class)("me-islanded")
        self._sync_island()
        if self.recording:
            self.content_stack.set_visible_child_name("memo")
            self.eyebrow.set_label("Recording")
            apply_type(self.eyebrow, "meta", weight=650)
            self.recording_dot.set_visible(True)
            self.eyebrow.add_css_class("me-recording-state")
            title = f"Continue {self._continuing.stem}" if self._continuing else "New recording"
            self.title_field.set_text(title)
            self.title_label.set_label(title)
            self.title_field.set_editable_title(False)
            self.title_holder.set_visible_child_name("title")
            self.subheading.set_label("Listening" if not self.record_paused else "Paused")
            self.subheading.set_visible(not in_island)
            self.deck.show_recording(self.record_elapsed, tuple(self.live_peaks))
            self.transcript.set_visible(True)
            self.transcript.show_live(tuple(self.live_words))
            self._set_corner(None)
            return
        memo = self._find()
        if memo is None:
            self.heading.set_visible(False)
            self.title_holder.set_visible(False)
            title = "Continue recording" if self._continuing else "New recording" if self._draft else "No recording selected" if self.memos else "No recordings yet"
            description = (f"Add another part to {self._continuing.stem}. Both original parts are kept." if self._continuing else
                           "Start when you’re ready. Your microphone stays off until then." if self._draft else
                           "Choose a recording, or create a new one." if self.memos else
                           "Create a recording to capture a meeting, thought, or reminder.")
            self.empty.set_text(title, description)
            self.content_stack.set_visible_child_name("empty")
            self._set_corner(None)
            return
        self.content_stack.set_visible_child_name("memo")
        self.eyebrow.remove_css_class("me-recording-state")
        apply_type(self.eyebrow, "meta", weight=400)
        self.recording_dot.set_visible(False)
        self.eyebrow.set_label(f"{memo.when} · {memo.place or 'Unknown place'}")
        self.title_field.set_text(memo.title)
        self.title_label.set_label(memo.title)
        self.title_field.set_editable_title(False)
        self.title_holder.set_visible_child_name("title")
        self.subheading.set_visible(False)
        self.deck.show_audio(memo, self.play_position, playing=self.playing, speed=self.speed)
        self.transcript.set_visible(True)
        self.transcript.show_memo(memo)
        self.transcript.set_position(self.play_position, playing=self.playing)
        self._render_corner(memo)

    def _set_corner(self, pill: CornerPill | None) -> None:
        self.corner_slot.set_child(pill)
        self.corner = pill
        if pill:
            pill.set_name("me-corner")
            for key, name in (("share", "me-share"), ("more", "me-more")):
                if key in pill.controls:
                    pill.controls[key].set_name(name)

    def _render_corner(self, memo: Memo) -> None:
        # Memo actions live in the main action island at every width.
        self._set_corner(None)

    def _more_rows(self, memo: Memo | None):
        if memo is None:
            return []
        rows = [MenuItem("Rename", icon="pencil", on_activate=self._focus_title)]
        if self.fixture:
            rows += [MenuItem("Transcribe again", icon="rotate-cw", on_activate=self._transcribe),
                     MenuItem("Duplicate", icon="copy", on_activate=self._duplicate)]
        rows.append(MenuItem("Delete", icon="trash-2", danger=True, on_activate=lambda: self._delete(memo)))
        return rows

    def _more_commands(self, memo: Memo) -> CommandRegistry:
        commands = [Command("memo.rename", "Rename", self._focus_title, "pencil")]
        if self.fixture:
            commands.extend((Command("memo.transcribe", "Transcribe again", self._transcribe, "rotate-cw"),
                             Command("memo.duplicate", "Duplicate", self._duplicate, "copy")))
        commands.append(Command("memo.delete", "Delete", self._delete, "trash-2", destructive=True))
        return CommandRegistry((CommandGroup("", tuple(commands)),))

    def _recording_chip(self, *, well=False):
        self._record_status = make_control(BarChip(
            "Paused" if self.record_paused else "Recording",
            meta=format_duration(self.record_elapsed), live=True, well=well))
        return BarWidget(self._record_status)

    def _update_recording_time(self):
        status = getattr(self, "_record_status", None)
        if status is None:
            return
        text = format_duration(self.record_elapsed)
        if status.time_label.get_label() != text:
            status.time_label.set_label(text)
            status.update_property([Gtk.AccessibleProperty.LABEL],
                                   [f"{'Paused' if self.record_paused else 'Recording'} {text}"])

    def _render_bar(self) -> None:
        if self.phone:
            self._render_phone_bar(self._find())
            self._name_bar_controls()
            return
        if self.recording:
            self.action.show_bar([
                self._recording_chip(),
                BarAction("pause" if not self.record_paused else "mic", "Pause" if not self.record_paused else "Resume", self._pause_recording),
                BarAction("square", "Done", self._record, primary=True, record=True),
            ])
            return
        memo = self._find()
        if self._draft:
            self.action.show_bar(self._draft_actions())
        elif self.view == "deleted" and memo:
            self.action.show_bar([BarAction("rotate-ccw", "Put back", self._restore, primary=True),
                                  BarAction("trash-2", "Delete for good", self._purge, danger=True)])
        elif memo:
            items = [BarAction("share-2", "Share", key="share", panel=self._share_panel),
                     BarAction("pencil", "Rename", self._focus_title),
                     BarAction("trash-2", "Delete", self._delete, danger=True)]
            if self.fixture:  # real trim would introduce a new write; it awaits approval
                items.extend((BarAction("scissors", "Trim", self._trim), SEPARATOR))
            items.append(BarAction("ellipsis", tooltip="More", key="more", menu=lambda: self._more_rows(self._find())))
            if not self.fixture:
                items.append(BarAction("mic", "Continue recording", self._continue_recording))
            items.append(BarAction("plus", "New recording", self._new_recording, tooltip="New recording (R)", primary=True))
            self.action.show_bar(items)
        else:
            self.action.show_bar([BarAction("plus", "New recording", self._new_recording, tooltip="New recording (R)", primary=True)])

        self._name_bar_controls()

    def _name_bar_controls(self):
        child = self.action.bar_row.get_first_child()
        while child is not None:
            key = getattr(getattr(child, "bar_item", None), "key", None)
            if key in ("share", "more"):
                child.set_name(f"me-{key}")
            child = child.get_next_sibling()

    def _focus_search(self) -> None:
        self.split.set_show_content(False)
        if self.phone:
            self.list_search.focus()
        else:
            self.foot.entry.grab_focus()

    def _focus_title(self) -> None:
        self.title_field.set_editable_title(True)
        self.title_holder.set_visible_child_name("edit")
        self.title_field.grab_focus()
        self.title_field.select_region(0, -1)

    def _title_focus_left(self) -> bool:
        if self.title_holder.get_visible_child_name() == "edit":
            self._rename(self.title_field.get_text())
        return GLib.SOURCE_REMOVE

    def _rename(self, text: str) -> None:
        self.title_field.set_editable_title(False)
        self.title_holder.set_visible_child_name("title")
        memo = self._find()
        if memo is None or self.recording:
            return
        text = text.strip() or memo.title
        if text == memo.title:
            return
        if self.fixture:
            self._replace_memo(replace(memo, title=text))
            self._render()
            return
        path = self.paths.get(memo.id)
        if path is None:
            return
        def done(destination, error):
            if error:
                self.title_field.set_text(memo.title)
                Toast.show(self.host, f"Memo was not renamed: {error}", kind="error")
            else:
                self.current_id = str(destination)
                self._reload_real()
        self._submit(lambda: audio.rename_recording(path, text, self.root), done)

    def _replace_memo(self, changed: Memo) -> None:
        for collection in (self.memos, self.deleted):
            for index, old in enumerate(collection):
                if old.id == changed.id:
                    collection[index] = changed
                    return

    def _favourite(self, active: bool, memo: Memo | None = None) -> None:
        memo = memo or self._find()
        if memo is None or not self.fixture:
            return
        self._replace_memo(replace(memo, favourite=active))
        self._render_list()
        if self.phone:
            self._render_bar()
        Toast.show(self.host, "Added to Favorites" if active else "Removed from Favorites",
                   undo=(lambda: self._favourite(not active, memo)) if not active else None)

    def _open_menu(self, button: Gtk.Widget, registry: CommandRegistry) -> None:
        menu = Menu(registry)
        menu.set_parent(button)
        menu.connect("closed", lambda pop: GLib.idle_add(pop.unparent))
        menu.popup()
        self._menu = menu

    def _share_menu(self, button: Gtk.Widget) -> None:
        memo = self._find()
        if memo is None:
            return
        ShareSheet.present(button, document=ShareSubject(memo.title, "Memos", icon=APP_ID),
                           people=self._share_people(),
                           targets=None if self.fixture else (),
                           choices=("send-copy",), on_choice=self._share_choice)

    def _share_people(self) -> tuple[Person, ...]:
        if not self.fixture:
            return ()
        if self._fixture_share_people is None:
            base = Path(self.fixture_path).parent / "memos-v70"
            people = list(_SHARE_PEOPLE)
            for index, key in enumerate(_SHARE_FACES):
                try:
                    face = Gdk.Texture.new_from_filename(str(base / f"face-{key}.jpg"))
                except GLib.Error:
                    continue
                people[index] = replace(people[index], picture=face)
            self._fixture_share_people = tuple(people)
        return self._fixture_share_people

    def _share_choice(self, choice: str, value: object) -> str | None:
        memo = self._find()
        if memo is None:
            return None
        # The phone's Share panel (v71 meBarPhone): Send to, then Audio, Transcript, Copy text, Nearby.
        if choice == "audio" or choice == "save":
            self._export()
            return None
        if choice == "transcript":
            self._export_transcript()
            return None
        if choice == "copy-text":
            self._copy_transcript()
            return None
        if choice == "nearby":
            return "Looking for devices nearby" if self.fixture else "This recording has no sharing service or link"
        if choice == "send-to" and self.fixture and isinstance(value, Person):
            return f"Sent to {value.name.split()[0]}"
        if choice == "copy":
            if self.fixture:
                self._copy_transcript()
                return None
            source = self.paths.get(memo.id)
            if source is not None:
                file = Gio.File.new_for_path(str(source))
                provider = Gdk.ContentProvider.new_union([
                    Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_list([file])),
                    Gdk.ContentProvider.new_for_bytes("text/uri-list",
                                                      GLib.Bytes.new((file.get_uri() + "\r\n").encode())),
                ])
                self.get_clipboard().set_content(provider)
                return "Audio copied"
        if choice in {"send-to", "target", "print", "copy-link", "link-for"}:
            return "This recording has no sharing service or link"
        return None

    def _speed_extras(self):
        # The fixture models v71's processing options; real playback only offers
        # rates until its audio pipeline implements silence skipping/enhancement.
        if not self.fixture:
            return []
        return [MenuItem("Skip silences" + (" ✓" if self.skip_silences else ""), icon="skip-forward",
                         on_activate=lambda: self._toggle_speed_option("skip_silences")),
                MenuItem("Enhance voice" + (" ✓" if self.enhance else ""), icon="wand-sparkles",
                         on_activate=lambda: self._toggle_speed_option("enhance"))]

    def _toggle_speed_option(self, option):
        if not self.fixture:
            return
        value = not getattr(self, option)
        setattr(self, option, value)
        label = "Skip silences" if option == "skip_silences" else "Enhance voice"
        Toast.show(self.host, f"{label} {'on' if value else 'off'}")

    def _set_speed(self, rate: float) -> None:
        if self.playback:
            try:
                self.playback.set_rate(rate)
            except (OSError, RuntimeError, ValueError) as error:
                Toast.show(self.host, f"Speed did not change: {error}", kind="error")
                self.deck.transport.set_speed(self.speed)
                return
        self.speed = rate
        self.deck.transport.set_speed(rate)

    def _copy_transcript(self) -> None:
        memo = self._find()
        if memo:
            self.get_clipboard().set("\n".join(text for _, text in memo.transcript))
            Toast.show(self.host, "Transcript copied", kind="copied")

    def _export_transcript(self) -> None:
        if self.fixture:
            Toast.show(self.host, "Transcript saved as .txt", kind="done")
        else:
            Toast.show(self.host, "Transcript export is not available for this recording", kind="warning")

    def _export(self) -> None:
        memo = self._find()
        if memo is None:
            return
        if self.fixture:
            Toast.show(self.host, "Audio saved as .m4a", kind="done")
            return
        source = self.paths.get(memo.id)
        if source is None:
            return
        dialog = Gtk.FileDialog(title="Export a copy", initial_name=source.name)
        def chosen(d, result):
            try:
                destination = d.save_finish(result)
                if destination:
                    original = Gio.File.new_for_path(str(source))
                    if original.equal(destination):
                        raise ValueError("Choose a different location for the copy.")
                    self._submit(lambda: original.copy(destination, Gio.FileCopyFlags.NONE, None, None),
                                 lambda _value, error: Toast.show(self.host,
                                     f"Copy could not be exported: {error}" if error else "Copy exported",
                                     kind="error" if error else "done"))
            except (GLib.Error, ValueError) as error:
                Toast.show(self.host, f"Copy could not be exported: {error}", kind="error")
        dialog.save(self, None, chosen)

    def _transcribe(self) -> None:
        if self.fixture:
            Toast.show(self.host, "Transcribing again, on this device")

    def _duplicate(self) -> None:
        memo = self._find()
        if not self.fixture or memo is None:
            return
        copy = replace(memo, id=memo.id + "-copy", title=memo.title + " copy")
        self.memos.insert(0, copy)
        self.current_id = copy.id
        self._render()
        Toast.show(self.host, "Duplicated", undo=lambda: self._remove_fixture(copy.id))

    def _remove_fixture(self, identifier: str) -> None:
        self.memos = [memo for memo in self.memos if memo.id != identifier]
        if self.current_id == identifier:
            self.current_id = self.memos[0].id if self.memos else None
        self._render()

    def _trim(self) -> None:
        if self.fixture:
            Toast.show(self.host, "Drag the ends of the waveform to trim")

    def _delete(self, memo: Memo | None = None) -> None:
        memo = memo or self._find()
        if memo is None:
            return
        if memo.id == self.current_id:
            self._stop_playback()
        if self.fixture:
            self.memos = [m for m in self.memos if m.id != memo.id]
            self.deleted.insert(0, memo)
            if self.current_id == memo.id:
                self.current_id = self.memos[0].id if self.memos else None
            self._render()
            Toast.show(self.host, f"“{memo.title}” moved to Deleted", kind="deleted", undo=self._restore_last)
            return
        path = self.paths.get(memo.id)
        if path is None:
            return
        def done(deleted, error):
            if error:
                Toast.show(self.host, f"Memo was not deleted: {error}", kind="error")
            else:
                if self.current_id == memo.id:
                    self.current_id = None
                self._reload_real()
                Toast.show(self.host, "Recording moved to Trash", kind="deleted",
                           undo=(lambda: self._undo_real(deleted)) if deleted.uri else None)
        self._submit(lambda: audio.delete_recording(path, self.root), done)

    def _undo_real(self, deleted) -> None:
        self._submit(deleted.restore, lambda _result, error: self._reload_real() if not error else
                     Toast.show(self.host, f"Could not restore recording: {error}", kind="error"))

    def _restore_last(self) -> None:
        if self.deleted:
            self._restore(self.deleted[0])

    def _restore(self, memo: Memo | None = None) -> None:
        memo = memo or self._find()
        if not self.fixture or memo is None or memo not in self.deleted:
            return
        self.deleted.remove(memo)
        self.memos.insert(0, memo)
        self.view = "all"
        self.foot.set_filter("all")
        self.current_id = memo.id
        self._render()
        Toast.show(self.host, "Put back")

    def _purge(self) -> None:
        memo = self._find()
        if not self.fixture or memo is None:
            return
        DestructiveDialog.ask(self.host, title=f"Delete “{memo.title}” for good?",
                              body="The recording and its words are removed from this computer. This can’t be undone.",
                              action="Delete", on_confirm=lambda _checked: self._purged(memo))

    def _purged(self, memo: Memo) -> None:
        self.deleted = [m for m in self.deleted if m.id != memo.id]
        self.current_id = self.deleted[0].id if self.deleted else None
        self._render()
        Toast.show(self.host, "Deleted for good", kind="deleted")

    def _close_recording_activity(self) -> None:
        if self._recording_activity is not None:
            self._recording_activity.close()
            self._recording_activity = None

    def _sync_recording_activity(self) -> None:
        # Fixture waveforms do not represent microphone activity. The platform
        # owns lease renewal and the Shell owns all rendering and app activation.
        if self.fixture or not self.recording or self._closed:
            self._close_recording_activity()
            return
        if self._recording_activity is None:
            self._recording_activity = LiveExtensionBinding(
                self.get_application(), extension_id="recording", category="recording",
                title="Voice Memos", subtitle=lambda _: "Recording paused" if self.record_paused else "Recording",
                privacy="private", visible=lambda _: self.recording and not self._closed)
        self._recording_activity.refresh()

    def _record(self) -> None:
        if self._busy:
            return
        if self.recording:
            if self.fixture:
                self._finish_fixture_recording()
            elif self.session:
                self._close_recording_activity()
                self._busy = True
                session = self.session
                continuing = self._continuing
                def save():
                    path = session.stop()
                    return audio.continue_recording(continuing, path, self.root) if continuing else path
                def done(path, error):
                    self._busy = False
                    self.session = None
                    self.recording = False
                    self._draft = False
                    self._continuing = None
                    if error:
                        Toast.show(self.host, f"Recording was not saved: {error}", kind="error")
                    else:
                        self.current_id = str(path)
                        Toast.show(self.host, "Continued recording saved; both parts kept" if continuing else "Recording saved", kind="saved")
                    self._reload_real()
                    if self._saving_close:
                        self.close()
                self._submit(save, done)
            return
        self._stop_playback()
        if self.fixture:
            self._start_fixture_recording()
            return
        self._busy = True
        def work():
            capability = audio.inspect_recording_capability()
            if not capability.available:
                raise RuntimeError(capability.reason)
            return audio.start_recording(self.root, source_label=capability.source,
                                         on_level=lambda peak: GLib.idle_add(self._level, peak),
                                         on_error=lambda msg: GLib.idle_add(self._record_error, msg))
        def started(session, error):
            self._busy = False
            if error:
                Toast.show(self.host, f"Recording could not start: {error}", kind="error")
                if self._saving_close:
                    self.close()
                return
            self.session = session
            self._draft = False
            self.recording = True
            self.record_paused = False
            self._sync_recording_activity()
            self.record_started = time.monotonic()
            self.record_elapsed = 0
            self.live_peaks = []
            self.live_words = []
            self.split.set_show_content(True)
            self._render()
            self._start_timer()
            if self._saving_close:
                self._record()
        self._submit(work, started)

    def _start_fixture_recording(self, *, static: bool = False) -> None:
        self._draft = False
        self.recording = True
        self.record_paused = False
        self.record_elapsed = 1.4 if static else 0.0
        self.record_started = time.monotonic() - self.record_elapsed
        self.live_peaks = [0.2, 0.28, 0.36, 0.44, 0.5, 0.25, 0.43, 0.68, 0.48,
                           0.75, 0.8, 0.62, 0.49, 0.55, 0.7, 0.47, 0.35]
        self.live_words = ["Reminder"] if static else []
        self.split.set_show_content(True)
        self._render()
        if not static:
            self._start_timer()

    def _finish_fixture_recording(self) -> None:
        self.recording = False
        self.record_paused = False
        duration = max(2, self.record_elapsed)
        said = " ".join(self.live_words)
        title = " ".join(self.live_words[:5]).rstrip(":,.") if said else "New memo"
        words = tuple(Word(word, "You", 0, (index + .5) / len(self.live_words) * duration * .96)
                      for index, word in enumerate(self.live_words))
        memo = Memo(f"fixture-{len(self.memos)}", title, "Just now", "Today",
                    duration, "Studio", False, (), (("You", said),) if said else (),
                    words, tuple(self.live_peaks))
        self.memos.insert(0, memo)
        self.current_id = memo.id
        self._render()
        Toast.show(self.host, "Saved and transcribed", kind="saved")

    def _pause_recording(self) -> None:
        if not self.recording:
            return
        was_paused = self.record_paused
        if self.fixture:
            self.record_paused = not self.record_paused
        elif self.session:
            try:
                self.session.pause() if not self.record_paused else self.session.resume()
                self.record_paused = not self.record_paused
            except (AttributeError, RuntimeError) as error:
                Toast.show(self.host, f"Recording could not pause: {error}", kind="error")
        if self.record_paused != was_paused:
            if self.record_paused:
                self.record_pause_started = time.monotonic()
            else:
                self.record_started += time.monotonic() - self.record_pause_started
        self._sync_recording_activity()
        self._render_detail()
        self._render_bar()

    def _level(self, peak: float) -> bool:
        if self.recording:
            self.live_peaks.append(peak)
            self.live_peaks = self.live_peaks[-180:]
            self.deck.show_recording(self.record_elapsed, tuple(self.live_peaks))
        return GLib.SOURCE_REMOVE

    def _record_error(self, message: str) -> bool:
        self.recording = False
        self.record_paused = False
        self.session = None
        self._close_recording_activity()
        self._render()
        Toast.show(self.host, f"Recording stopped: {message}", kind="error")
        return GLib.SOURCE_REMOVE

    def _play(self) -> None:
        memo = self._find()
        if memo is None or self.recording:
            return
        if self.fixture:
            self.playing = not self.playing
            self.deck.set_playing(self.playing)
            if self.playing:
                self._start_timer()
            return
        path = self.paths.get(memo.id)
        if path is None:
            return
        try:
            if self.playback is None:
                self.playback = audio.start_playback(path, self.root,
                    on_error=lambda msg: GLib.idle_add(self._playback_error, msg),
                    on_end=lambda: GLib.idle_add(self._playback_end))
                self.playback.set_rate(self.speed)
            self.playback.pause() if self.playback.playing else self.playback.play()
            self.playing = self.playback.playing
            self.deck.set_playing(self.playing)
            self._start_timer()
        except (OSError, ValueError, RuntimeError) as error:
            Toast.show(self.host, f"Playback could not start: {error}", kind="error")

    def _playback_error(self, message: str) -> bool:
        self._stop_playback()
        Toast.show(self.host, f"Playback stopped: {message}", kind="error")
        return GLib.SOURCE_REMOVE

    def _playback_end(self) -> bool:
        self.playing = False
        self.deck.set_playing(False)
        return GLib.SOURCE_REMOVE

    def _stop_playback(self) -> None:
        if self.playback:
            self.playback.stop()
            self.playback = None
        self.playing = False

    def _seek(self, seconds: float) -> None:
        memo = self._find()
        if memo is None or self.recording:
            return
        self.play_position = max(0, min(memo.duration, seconds))
        if self.playback:
            try:
                self.playback.seek(self.play_position)
            except (OSError, ValueError, RuntimeError) as error:
                Toast.show(self.host, f"Could not seek: {error}", kind="error")
        self.deck.set_position(self.play_position, memo.duration)
        self.transcript.set_position(self.play_position, playing=self.playing)

    def _skip(self, delta: float) -> None:
        self._seek(self.play_position + delta)

    def _start_timer(self) -> None:
        if not self._timer:
            self._timer = GLib.timeout_add(100, self._tick)

    def _tick(self) -> bool:
        if self._closed:
            self._timer = 0
            return GLib.SOURCE_REMOVE
        if self.recording and not self.record_paused:
            self.record_elapsed = time.monotonic() - self.record_started
            if self.fixture:
                # Fixture audio remains in memory; the displayed peaks are the sample's.
                index = len(self.live_peaks)
                self.live_peaks.append(min(.95, .14 + .48 * abs(math.sin(index * .31))
                                            + .18 * abs(math.sin(index * .89))))
                self.live_peaks = self.live_peaks[-180:]
                self.deck.show_recording(self.record_elapsed, tuple(self.live_peaks))
                spoken = "Reminder for tomorrow: pick up the loft keys from Theo before ten".split()
                have = min(len(spoken), max(0, int((self.record_elapsed - 1.2) / .4) + 1))
                if have > len(self.live_words):
                    self.live_words = spoken[:have]
                    self.transcript.show_live(tuple(self.live_words))
            else:
                self.deck.clock.set_text(format_duration(self.record_elapsed))
                self.deck.clock.set_unit(f".{int(self.record_elapsed * 10) % 10}")
            self._update_recording_time()
            return GLib.SOURCE_CONTINUE
        if self.playing:
            memo = self._find()
            if memo:
                self.play_position = (self.play_position + 0.1 * self.speed) if self.fixture else self.playback.position
                self.play_position = min(self.play_position, memo.duration)
                self.deck.set_position(self.play_position, memo.duration)
                self.transcript.set_position(self.play_position, playing=True)
                if self.play_position >= memo.duration:
                    self.playing = False
                    self.deck.set_playing(False)
                    self._timer = 0
                    return GLib.SOURCE_REMOVE
                return GLib.SOURCE_CONTINUE
        self._timer = 0
        return GLib.SOURCE_REMOVE

    def _key(self, _controller, key, _code, modifiers) -> bool:
        focused = self.get_focus()
        if isinstance(focused, (Gtk.Entry, Gtk.Text, Gtk.SearchEntry)):
            return False
        if key in (Gdk.KEY_r, Gdk.KEY_R) and not modifiers:
            self._record() if self.recording else self._new_recording()
            return True
        if key == Gdk.KEY_space and not modifiers:
            self._pause_recording() if self.recording else self._play()
            return True
        if key == Gdk.KEY_Escape and self.recording:
            self._record()
            return True
        return False

    def _about(self) -> None:
        Adw.AboutDialog(application_name="Memos", application_icon=APP_ID,
                        developer_name="Project Luma").present(self)

    def do_close_request(self) -> bool:
        if self._busy and not self.recording:
            self._saving_close = True
            return True
        if self.recording and not self.fixture:
            self._saving_close = True
            self._record()
            return True
        self._closed = True
        self._close_recording_activity()
        self._stop_playback()
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self.executor.shutdown(wait=False, cancel_futures=True)
        return False


class VoiceMemosApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        GLib.set_application_name("Memos")
        install_appkit()
        install_lumaui()
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(
            str(Path(__file__).resolve().parent))
        local = Path(__file__).resolve().parents[1] / "style/memo.css"
        sheet = os.environ.get("LUMA_MEMO_STYLE_PATH")
        add_style_sheet(sheet or str(local if local.exists() else Path("/usr/share/prairie-core/memo.css")))

    def do_activate(self) -> None:
        (self.props.active_window or VoiceMemosWindow(self)).present()


def main() -> int:
    return VoiceMemosApplication().run([])


if __name__ == "__main__":
    raise SystemExit(main())
