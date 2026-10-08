# SPDX-License-Identifier: Apache-2.0
"""Phone: simulator v71 on LumaUI, with one native voice state model.

The shared frame, islands, contact actions, menus, dialogs, toasts and bars
belong to the kit. Phone owns its dial pad, call media and call controls,
and voicemail card, as GLOBAL-HANDOFF §2 permits. All colours and CSS
metrics use kit tokens. Fixture mode reads only its explicitly supplied JSON.

Desktop: the sidebar keeps recent calls visible; the bar switches the main
panel between the keypad and vertical Recents, Contacts and Voicemail lists.
Picking an entry opens its details. Phone width (v71 "Phone", second pass): Recents,
Contacts and Voicemail are list-first pages with a large title; the bar is
one Search and the four places as glyphs; calls are laid out like phone calls.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import quote
import sys
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk, Pango
from luma_appkit import lumaui_tokens as visual_tokens

from luma_appkit import (AZIndex, ActionCenter, AppWindow, BarAction, BarModes, BarSearch, Card, ListFirst, SwipeAction, SwipeRow, Command, CommandGroup,
    CommandRegistry, ContactActions, ContentLitHeader, DestructiveDialog, DetailsItem, Island,
    EmptyState, ListEmptyState, ModeSwitch, NavigationSidebar, Person,
    NavigationRow, SidebarRow, RowLead, Favourite, FavouritesStrip, AvatarStack, PersonAvatar, ScrollView, SidebarFoot, StackedButton, StackedButtons, Toast,
    ToastHost, VoiceClip, DialKey, add_style_sheet, bar_menu, apply_type, icons, install_lumaui, media_context)
from luma_appkit.action_bubble import MenuItem
from luma_appkit.action_center import BarChip, SEPARATOR, make_control
from luma_appkit.structure_adapt import window_tier
from .phone_backend import CallPhase, CallSession, PhoneCapability
from .phone_fixture import FixtureSource, PhonePerson, dial_display, digits, matching_person, search_people, source_from_environment, visible_people


# The contained preview supplies its own ID so Gio cannot activate the installed Phone.
APPLICATION_ID = os.environ.get("LUMA_PHONE_APPLICATION_ID", "org.projectluma.Phone")
# The standard contained-preview launcher overrides this public module constant.
APP_ID = APPLICATION_ID
PLACES = (("pad", "Keypad", "grid-3x3"), ("recents", "Recents", "history"),
          ("contacts", "Contacts", "users"), ("vm", "Voicemail", "voicemail"))
LIST_TITLES = {"recents": "Recents", "contacts": "Contacts", "vm": "Voicemail", "pad": "Recents"}
# v71: 0 has no "+" (holding it still types one); * and # are centred glyphs.
KEYS = (("1", ""), ("2", "ABC"), ("3", "DEF"), ("4", "GHI"), ("5", "JKL"),
        ("6", "MNO"), ("7", "PQRS"), ("8", "TUV"), ("9", "WXYZ"), ("*", ""), ("0", ""), ("#", ""))
KEY_GLYPHS = {"*": "asterisk", "#": "hash"}
KEY_NAMES = {"*": "Star", "#": "Pound"}


def named(widget, name, css=None):
    widget.set_name(name)
    if css:
        widget.add_css_class(css)
    return widget


def box(*, vertical=False, spacing=0, **kwargs):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                   spacing=spacing, **kwargs)


def label(text, role="body", *, muted=False, weight=None, **kwargs):
    return apply_type(Gtk.Label(label=text, **kwargs), role, muted=muted, weight=weight)


def button(icon, title, callback, name, *, css=None, sensitive=True, size=20):
    control = named(Gtk.Button(tooltip_text=title, sensitive=sensitive), name, css)
    control.set_child(icons.image(icon, pixel_size=size))
    control.update_property([Gtk.AccessibleProperty.LABEL], [title])
    control.connect("clicked", lambda _: callback())
    return control


def _highlight(text, query):
    """Bold every word of `text` that starts with `query` (v71 hl())."""
    escaped = GLib.markup_escape_text(text)
    if not query:
        return escaped
    words, out = text.split(" "), []
    for word in words:
        if word.casefold().startswith(query.casefold()):
            out.append("<b>" + GLib.markup_escape_text(word[:len(query)]) + "</b>" + GLib.markup_escape_text(word[len(query):]))
        else:
            out.append(GLib.markup_escape_text(word))
    return " ".join(out)


class CallPhoto(Gtk.Widget):
    """Phone-only media canvas. Cover cropping follows v70's face focus."""
    def __init__(self, picture, *, focus=(.5, .5), wash=False, effects=False):
        super().__init__(hexpand=True, vexpand=True, can_target=False,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.picture, self.focus, self.wash, self.effects = picture, focus, wash, effects

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        if not self.picture or width <= 0 or height <= 0:
            return
        image_w, image_h = self.picture.get_width(), self.picture.get_height()
        area_w, area_h = (width * 1.2, height * 1.4) if self.wash else (width * 1.06, height * 1.06)
        left, top = (-width * .1, -height * .2) if self.wash else (-width * .03, -height * .03)
        scale = max(area_w / image_w, area_h / image_h)
        image_w, image_h = image_w * scale, image_h * scale
        snapshot.push_clip(Graphene.Rect().init(0, 0, width, height))
        if self.wash:
            snapshot.push_opacity(.55)
        if self.wash:
            # TODO(kit-request phone-05-frame-and-media-tokens.md): 80px wash.
            snapshot.push_blur(visual_tokens.LIT_HEADER["blur"])

        def paint():
            snapshot.save()
            snapshot.translate(Graphene.Point().init(left + (area_w - image_w) * self.focus[0],
                                                     top + (area_h - image_h) * self.focus[1]))
            self.picture.snapshot(snapshot, image_w, image_h)
            snapshot.restore()

        paint()
        if self.effects and not self.wash:
            # v70 keeps the face clear and softens the edges of the feed.
            snapshot.push_mask(Gsk.MaskMode.ALPHA)
            stops = []
            for offset, alpha in ((0, 0), (.6, 0), (.95, 1), (1, 1)):
                stop = Gsk.ColorStop()
                stop.offset = offset
                stop.color = Gdk.RGBA(red=1, green=1, blue=1, alpha=alpha)
                stops.append(stop)
            snapshot.append_radial_gradient(Graphene.Rect().init(0, 0, width, height),
                Graphene.Point().init(width * .5, height * .45), width * .3, height * .5,
                0, 1, stops)
            snapshot.pop()
            # TODO(kit-request phone-05-frame-and-media-tokens.md): effect blur token.
            snapshot.push_blur(visual_tokens.LIT_HEADER["blur"])
            paint()
            snapshot.pop()
            snapshot.pop()
        if self.wash:
            snapshot.pop()
            snapshot.pop()
        snapshot.pop()


@dataclass
class CallView:
    kind: str
    people: tuple[str, ...]
    started: float = 0
    muted: bool = False
    camera_off: bool = False
    sharing: bool = False
    effects: bool = False
    keypad: bool = False
    output: str = ""
    connected: bool = False
    call_id: str = ""


class PhoneWindow(AppWindow):
    def __init__(self, application):
        self.fixture = source_from_environment()
        self.source = self.fixture
        self.photo_cache = {}
        self.voice = None
        self.audio_menu = None
        self.shell_surface = self.active_indicator = None
        self.closed = False
        self.generation = 0
        self.tab, self.selected, self.person, self.number, self.query = "pad", "r1", "PR", "", ""
        # Phone width: whether a list-first page has pushed its entry, and the bar's one search.
        self.is_phone, self.drill, self.bar_query = False, False, ""
        self.call = None
        self.video_idle_source = 0
        self.vm_playing = False
        self.vm_position = 0.0
        self.vm_source = self.call_timer_source = 0
        self.vm_clip = self.call_duration = self.call_timer_label = None
        self.call_chrome = ()
        self.az_index = None
        self.pending_search = os.environ.get("LUMA_PHONE_FIXTURE_SEARCH", "") if self.fixture else ""
        self.capability_ready = bool(self.fixture)
        self.requested_address = ""
        self.capability = PhoneCapability(bool(self.fixture), "Connecting to phone…")
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="phone-read")
        super().__init__(application=application, app_id=APP_ID, title="Phone", icon_name="org.projectluma.Phone",
                         commands=CommandRegistry((CommandGroup("", (
                             Command("phone.find", "Search people", self._focus_search, "search", shortcut=("Ctrl", "F")),
                             Command("phone.quit", "Quit Phone", self.close, "log-out", shortcut=("Ctrl", "Q")),)),)),
                         default_width=1180, default_height=740, minimum_width=360, minimum_height=480)
        self.sidebar = named(NavigationSidebar(variant="people", width="regular"), "pn-sidebar")
        # v71: Favorites sit in an inset well labelled Favorites (Recents and Contacts only).
        self.favourites = named(FavouritesStrip([], heading="Favorites",
            on_open=lambda item: self._select_person(item.key)), "pn-favourites")
        self.favourite_strip = self.favourites
        self.sidebar.append_header(self.favourites)
        self.foot = named(SidebarFoot(search="Search people and numbers", on_search=self._search), "pn-foot")
        self.sidebar.append_footer(self.foot)
        self.sidebar.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.person_page = named(box(vertical=True, halign=Gtk.Align.FILL), "pn-person", "pn-person")
        self.person_page.set_margin_top(56)
        self.person_page.set_margin_start(28)
        self.person_page.set_margin_end(28)
        self.person_page.set_margin_bottom(110)
        self.island = named(Island(), "pn-island")
        self.content_stack = named(Gtk.Stack(vexpand=True, hexpand=True, hhomogeneous=False, vhomogeneous=False), "pn-body")
        self.island.append(self.content_stack)
        self.host = ToastHost(self.island)
        self.center = ActionCenter().attach(self.host)
        self.center.show_bar([])
        self.places = named(ModeSwitch(PLACES, current=self.tab, label="Phone", on_change=self._switch), "pn-places")
        for key, control in self.places.buttons.items():
            control.set_name("pn-place-" + key)
        # At phone width the four places are the bar's glyph tabs, Voicemail with its count (v71 pnBarPhone).
        self.phone_places = None
        self.results = named(box(vertical=True, spacing=4), "pn-results", "pn-results")
        # The phone bar's one search for the whole app (v71 pnBarPhone): names and numbers.
        self.bar_search = BarSearch("Search", label="Search people and numbers", keep=True,
                                    on_change=self._bar_search)
        self.call_center = ActionCenter().attach(self.host)
        media_context(self.call_center)
        self.self_preview = named(Gtk.AspectFrame(ratio=1.6, obey_child=False, width_request=200, height_request=125,
                                                halign=Gtk.Align.END, valign=Gtk.Align.END), "pn-self", "pn-self")
        self.self_preview.set_overflow(Gtk.Overflow.HIDDEN)
        self.self_preview.set_cursor_from_name("grab")
        self.self_preview.set_margin_end(16)
        self.self_preview.set_margin_bottom(96)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", lambda *_: self._drag_self_begin())
        drag.connect("drag-update", lambda _, dx, dy: self._drag_self_update(dx, dy))
        drag.connect("drag-end", lambda _, dx, dy: self._snap_self(dx, dy))
        self.self_preview.add_controller(drag)
        # The place's list and the picked entry: side by side on a computer; on a phone v71's list-first
        # stack (K-NAV ListFirst), the list full screen under its large title and the entry pushed over it.
        self.host.set_hexpand(True)
        self.list_first = ListFirst(self.sidebar, self.host, title="", on_back=self._listed, push_on_activate=False)
        # The window's own layer: at phone width the bar and toasts float over the
        # list-first page as well as a pushed one (v71 #pn-bar over .pnlistview).
        self.frame = named(ToastHost(self.list_first), "pn-frame")
        self.set_body(self.frame)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 820px"))
        narrow.add_setter(self.sidebar, "width-request", 240)
        self.add_breakpoint(narrow)
        # v71 @container win (max-width: 700px): the sidebar steps aside; the island keeps the window.
        compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 700px"))
        compact.add_setter(self.sidebar, "visible", False)
        compact.add_setter(self.self_preview, "width-request", 130)
        compact.add_setter(self.self_preview, "height-request", 81)
        compact.add_setter(self.person_page, "margin-start", 16)
        compact.add_setter(self.person_page, "margin-end", 16)
        self.add_breakpoint(compact)
        # A phone (the last matching breakpoint wins): the sidebar is the list-first page again.
        phone = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(f"max-width: {visual_tokens.PHONE_MAX_WIDTH}px"))
        phone.add_setter(self.sidebar, "visible", True)
        phone.add_setter(self.person_page, "margin-start", 16)
        phone.add_setter(self.person_page, "margin-end", 16)
        self.add_breakpoint(phone)
        # v71 "redraw on crossing the phone width": Phone decides its phone shape when it draws.
        self.tiers = window_tier(self)
        self.tiers.connect("tier-changed", lambda _watch, tier: self._tier_changed(tier))
        keyboard = Gtk.EventControllerKey()
        keyboard.connect("key-pressed", self._key_pressed)
        self.add_controller(keyboard)
        self.connect("close-request", self._close_requested)
        if self.fixture:
            initial = self.fixture.initial
            self.tab, self.selected = initial["tab"], initial["selected"]
            self.person, self.number = initial["person"], initial["number"]
            self.places.set_current(self.tab)
            self._all()
            if os.environ.get("LUMA_PHONE_PREVIEW_CONTACTS") == "1":
                from .phone_data import PreviewContactsSource
                self._read(lambda: PreviewContactsSource.load(self.fixture), self._preview_contacts_loaded)
        else:
            self.sidebar.list.append(Gtk.ListBoxRow(child=ListEmptyState("Loading calls…"), selectable=False))
            self._pad()
            self._bar()
            GLib.idle_add(self._start_live)

    def _start_live(self):
        if self.closed:
            return False
        from .phone_control import VoiceController
        from .phone_data import BlockList
        self.block_list = BlockList()
        self.voice = VoiceController(dispatch=GLib.idle_add, on_state=self._native_state,
                                     on_capability=self._capability, on_error=lambda msg: self._toast(msg, error=True),
                                     is_blocked=self.block_list.contains,
                                     on_provider=lambda provider: setattr(self.get_application(), "call_provider", provider))
        from .phone_system import TransientShellSurface, ActiveCallIndicator
        self.shell_surface = TransientShellSurface(APP_ID)
        def indicator():
            return ActiveCallIndicator(application_id=APP_ID, on_return=lambda: GLib.idle_add(self.present),
                on_mute=self._notification_mute,
                on_end=lambda: GLib.idle_add(self._end_call))
        def installed(value):
            self.active_indicator = value
            if self.call:
                self._publish_shell()
        self._read(indicator, installed)
        application = self.get_application()
        if hasattr(application, "call_provider"):
            self.voice.start(application.call_provider)
        else:
            self.voice.start()
        self._reload()
        return False

    def _preview_contacts_loaded(self, source):
        self.source = source
        self.photo_cache.clear()
        self._all()

    def _read(self, operation, then):
        future = self.executor.submit(operation)
        def done(result):
            def publish():
                if self.closed:
                    return False
                try:
                    value = result.result()
                except Exception as error:
                    self._toast(str(error), error=True)
                else:
                    then(value)
                return False
            GLib.idle_add(publish)
        future.add_done_callback(done)

    def _reload(self):
        from .phone_data import LiveSource
        self.generation += 1
        generation = self.generation
        def loaded(source):
            if generation != self.generation:
                return
            self.source = source
            self.photo_cache.clear()
            if source.calls:
                self.selected = source.calls[0].uid
            if source.people and self.person not in {p.uid for p in source.people}:
                self.person = source.people[0].uid
            self._all()
        self._read(lambda: LiveSource().load(), loaded)

    def _capability(self, value):
        self.capability = value
        self.capability_ready = True
        requested, self.requested_address = self.requested_address, ""
        if requested:
            self.set_dial_address(requested)
            self._start_call("voice")
        if self.tab == "pad" and not self.call:
            self._pad()

    def _person(self, uid):
        if self.source:
            return next((person for person in self.source.people if person.uid == uid), None)
        return None

    def _picture(self, uid, *, face=True):
        key = (id(self.source), uid, face)
        if key not in self.photo_cache:
            person = self._person(uid)
            picture = None
            data = self.source.photo(person) if person else None
            if data:
                picture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
            if self.fixture and person and person.face and face:
                face_path = self.fixture._asset("phone-v70/face-" + person.uid + ".jpg")
                if face_path.is_file():
                    picture = Gdk.Texture.new_from_filename(str(face_path))
            self.photo_cache[key] = picture
        return self.photo_cache[key]

    def _avatar(self, uid, size):
        person = self._person(uid)
        if person is None:
            # TODO(kit-request phone-04-sidebar-and-places.md): semantic
            # phone-glyph avatar at sidebar and hero sizes.
            glyph = icons.image("phone", pixel_size=int(size * .45))
            glyph.set_valign(Gtk.Align.CENTER)
            slot = Gtk.CenterBox(halign=Gtk.Align.CENTER)
            slot.set_size_request(76, 76)
            slot.set_center_widget(glyph)
            return named(slot, "pn-unknown", "pn-unknown")
        return PersonAvatar(person.name, size, picture=self._picture(uid), hue=person.hue)

    def _group_avatar(self, people, size):
        # TODO(kit-request phone-04-sidebar-and-places.md): 84px hero tier.
        stack = AvatarStack([(self._person(uid).name, self._picture(uid)) for uid in people], size="header")
        if size == 84:
            stack.set_valign(Gtk.Align.CENTER)
            slot = Gtk.CenterBox(halign=Gtk.Align.CENTER)
            slot.set_size_request(-1, size)
            slot.set_center_widget(stack)
            return slot
        return stack

    # ── width ─────────────────────────────────────────────────────────────

    def _tier_changed(self, tier):
        phone = tier == "phone"
        if phone != self.is_phone:
            self.is_phone = phone
            self.bar_query = ""
            self.bar_search.set_text("")
            if self.call:
                self._call_view()
            else:
                self._all()
        if self.pending_search:
            # Fixture review states: the search typed in (the conformance harness cannot type).
            text, self.pending_search = self.pending_search, ""
            if self.is_phone:
                self.bar_search.set_text(text)
                self._bar_search(text)
            else:
                self.foot.entry.set_text(text)

    def _layer(self):
        """Where the bar, toasts and confirms float: the island, or the whole window at phone width."""
        return self.frame if self.is_phone else self.host

    # ── drawing ───────────────────────────────────────────────────────────

    def _put(self, widget):
        old = self.content_stack.get_visible_child()
        self.content_stack.add_named(widget, str(time.monotonic_ns()))
        self.content_stack.set_visible_child(widget)
        if old:
            self.content_stack.remove(old)

    def _all(self):
        self._side()
        self._main()
        self._bar()
        self._layout()

    def _listing(self):
        """A list-first page is showing (phone width, a list place, nothing pushed, no call)."""
        return self.is_phone and self.tab != "pad" and not self.drill and self.call is None

    def _layout(self):
        """What a phone shows: the place's list, or the pushed entry (v71 pnList); a computer shows both."""
        listing = self._listing()
        # The centered keypad and call stages paint the complete phone screen.
        self.set_phone_bleed(self.is_phone and (self.tab == "pad" or self.call is not None))
        self.list_first.set_title(LIST_TITLES[self.tab])
        self.sidebar.footer.set_visible(not self.is_phone)
        if self.is_phone:
            if listing:
                self.list_first.show_list()
            else:
                self.list_first.show_detail()
            # The Keypad and a call are places of their own, not something pushed: no ‹ over them.
            self.list_first.set_back_label(LIST_TITLES[self.tab])
            self.list_first.back_button.set_visible(self.drill and self.tab != "pad" and self.call is None)
        if self.az_index is not None:
            self.az_index.set_visible(listing and self.tab == "contacts")

    def _listed(self):
        """The list-first ‹: back up to the place's list."""
        self.drill = False
        self._stop_vm()
        self._side()
        self._main()
        self._layout()

    def _back(self):
        """Esc or Backspace on a pushed page: up one level, to the list it came from."""
        self._listed()

    def _favourites_strip(self):
        items = [Favourite(person.name.split()[0], person=person.name, picture=self._picture(person.uid), key=person.uid)
                 for uid in self.source.favourites if (person := self._person(uid))]
        self.favourite_strip.set_items(items)
        for item, control in zip(items, self.favourite_strip.buttons):
            control.set_name("pn-favourite-" + item.key)
            control.update_property([Gtk.AccessibleProperty.LABEL], [item.person])
        self.favourites.set_visible(bool(items) and self.tab != "vm")

    def _side(self):
        """Desktop keeps recent calls; phone width lists the selected place."""
        if not self.source:
            return
        self._favourites_strip()
        self.sidebar.clear()
        if self.az_index is not None:
            self.frame.remove_overlay(self.az_index)
            self.az_index = None
        query = self.query
        mark = not self.is_phone
        if not self.is_phone:
            self._recent_rows(self.sidebar.list, query=query)
            if not self.sidebar.list.get_first_child():
                self.sidebar.list.append(Gtk.ListBoxRow(selectable=False, activatable=False,
                    child=ListEmptyState("No matching calls" if query else "No calls yet")))
            return
        if query:
            for person in visible_people(self._contacts(), query):
                self._side_row(person.uid, person.name, person.phone, (person.uid,), "", "",
                               mark and self.tab == "contacts" and self.person == person.uid,
                               lambda uid=person.uid: self._select_person(uid), "pn-person-" + person.uid)
        elif self.tab in {"pad", "recents"}:
            for call in self.source.calls:
                person = self._person(call.people[0])
                title = call.group or (person.name if person else call.number)
                summary = "Video" if call.kind == "video" else "Voice"
                summary += " · likely spam" if call.spam else " · " + call.duration if call.duration else " · missed"
                icon = "phone-missed" if call.direction == "missed" else "phone-incoming" if call.direction == "in" else "phone-outgoing"
                self._side_row(call.uid, title, summary, call.people[:2], call.when.split(",")[0], icon,
                               mark and self.selected == call.uid,
                               lambda uid=call.uid: self._select(uid, "recents"),
                               "pn-recent-" + call.uid, missed=call.direction == "missed", removable=True)
        elif self.tab == "vm":
            for vm in self.source.voicemails:
                person = self._person(vm.person)
                snippet = vm.transcript[:44] + "…"
                # v71: the date, and the length under it.
                self._side_row(vm.uid, person.name if person else vm.person, snippet, (vm.person,),
                               vm.when.split(",")[0], "", mark and self.selected == vm.uid,
                               lambda uid=vm.uid: self._select(uid, "vm"), "pn-vm-" + vm.uid,
                               trail=f"0:{vm.duration:02d}")
        else:
            # One address book: telephone and verified Luma contacts, A–Z with a count.
            people = visible_people(self._contacts(), "")
            current = ""
            for person in people:
                letter = person.name[:1].upper()
                if letter != current:
                    current = letter
                    self.sidebar.append_section(letter)
                row = self._side_row(person.uid, person.name, person.phone or ("@" + person.handle if person.luma_account else ""), (person.uid,), "", "",
                                     mark and self.person == person.uid,
                                     lambda uid=person.uid: self._select_person(uid), "pn-person-" + person.uid)
                row.pn_letter = letter
            count = named(label(f"{len(people)} contacts", "caption", muted=True), "pn-count")
            self.sidebar.list.append(Gtk.ListBoxRow(child=count, selectable=False, activatable=False))
            if self.is_phone:
                # v71 lAZ: A to Z down the right edge, the list in its own lane beside it.
                self.az_index = named(AZIndex(self.sidebar.list, key=lambda row: getattr(row, "pn_letter", None)),
                                      "pn-az")
                self.frame.add_overlay(self.az_index)
                self.az_index.set_visible(self._listing())
        if not self.sidebar.list.get_first_child() or (
                self.tab == "contacts" and not visible_people(self._contacts(), query)):
            self.sidebar.clear()
            title = ("No people match" if query else "No voicemail yet" if self.tab == "vm"
                     else "No contacts yet" if self.tab == "contacts" else "No calls yet")
            detail = ("Try another name or number." if query else
                      "Your voicemail will appear here." if self.tab == "vm" else
                      "People with a phone number or verified Luma username appear here." if self.tab == "contacts" else
                      "Your recent calls will appear here.")
            empty = EmptyState(title, detail, "lumaui-phone-symbolic", compact=True)
            empty.set_name("pn-sidebar-empty")
            self.sidebar.list.append(Gtk.ListBoxRow(child=empty, selectable=False, activatable=False))

    def _contacts(self):
        return getattr(self.source, "contacts", self.source.people)

    def _side_row(self, uid, title, subtitle, people, when, icon, selected, activate, name, *,
                  missed=False, removable=False, trail=None, destination=None):
        if len(people) > 1:
            lead = RowLead.group([(self._person(uid).name, self._picture(uid)) for uid in people], size="medium")
        elif person := self._person(people[0]):
            lead = RowLead.face(person.name, size="medium", picture=self._picture(person.uid), hue=person.hue)
        else:
            lead = RowLead.glyph("phone")
        row = named(SidebarRow(title, lead=lead, subtitle=subtitle, subtitle_icon=icon or None,
                               attention="missed" if missed else False, meta=when, trail=trail), name)
        row.connect("activate", lambda _: activate())
        if self.is_phone:
            row.set_size_request(-1, 64)
        if removable and self.fixture:
            # v71 Recents: swipe left to Remove (red), with Undo (the kit's swipe row works at phone width).
            # Live call history is read-only today, so only the sample offers it.
            content = row.get_child()
            row.set_child(None)
            row.set_child(SwipeRow(content, end=SwipeAction("trash-2", "red", lambda _row: self._remove_call(uid),
                                                            label="Remove")))
        destination = self.sidebar.list if destination is None else destination
        destination.append(row)
        if selected:
            destination.select_row(row)
        return row

    def _recent_rows(self, destination, *, query=""):
        for call in self.source.calls:
            person = self._person(call.people[0])
            title = call.group or (person.name if person else call.number)
            if query and query.casefold() not in (title + " " + call.number).casefold():
                continue
            summary = "Video" if call.kind == "video" else "Voice"
            summary += " · likely spam" if call.spam else " · " + call.duration if call.duration else " · missed"
            icon = "phone-missed" if call.direction == "missed" else "phone-incoming" if call.direction == "in" else "phone-outgoing"
            self._side_row(call.uid, title, summary, call.people[:2], call.when.split(",")[0], icon,
                self.drill and self.tab == "recents" and self.selected == call.uid,
                lambda uid=call.uid: self._select(uid, "recents"), "pn-recent-" + call.uid,
                missed=call.direction == "missed", destination=destination)

    def _place_list(self):
        page = box(vertical=True, spacing=12)
        page.set_margin_top(24); page.set_margin_bottom(24)
        page.set_margin_start(24); page.set_margin_end(24)
        page.append(label(LIST_TITLES[self.tab], "title-1"))
        rows = named(Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE), "pn-main-list")
        rows.add_css_class("backgroundless")
        if self.tab == "recents":
            self._recent_rows(rows, query=self.query)
        elif self.tab == "contacts":
            for person in visible_people(self._contacts(), self.query):
                self._side_row(person.uid, person.name, person.phone, (person.uid,), "", "", False,
                    lambda uid=person.uid: self._select_person(uid), "pn-person-" + person.uid, destination=rows)
        else:
            for vm in self.source.voicemails:
                person = self._person(vm.person)
                self._side_row(vm.uid, person.name if person else vm.person, vm.transcript[:44], (vm.person,),
                    vm.when.split(",")[0], "", False, lambda uid=vm.uid: self._select(uid, "vm"),
                    "pn-vm-" + vm.uid, trail=f"0:{vm.duration:02d}", destination=rows)
        if not rows.get_first_child():
            rows.append(Gtk.ListBoxRow(selectable=False, activatable=False,
                child=ListEmptyState("No matching contacts" if self.query and self.tab == "contacts"
                    else "No voicemail yet" if self.tab == "vm" else "No contacts yet" if self.tab == "contacts" else "No calls yet")))
        page.append(rows)
        self._put(ScrollView(Adw.Clamp(maximum_size=760, child=page)))

    def _remove_call(self, uid):
        calls = list(self.source.calls)
        index = next((i for i, call in enumerate(calls) if call.uid == uid), None)
        if index is None:
            return
        removed = calls.pop(index)
        self.source.calls = tuple(calls)
        self._side()

        def undo():
            restored = list(self.source.calls)
            restored.insert(index, removed)
            self.source.calls = tuple(restored)
            self._side()
        self._toast("Removed from Recents", undo=undo)

    def _bar(self):
        """Desktop: the four places as a switch. Phone: Search, then the places as glyphs (v71 pnBarPhone)."""
        if self.call is not None:
            self.center.set_visible(False)
            return
        self.center.set_visible(True)
        layer = self._layer()
        if self.center.host is not layer:
            self.center.attach(layer)
        if not self.is_phone:
            self.center.show_bar([], modes=self.places)
            return
        count = len(self.source.voicemails) if self.source else 0
        # A ModeSwitch in a phone bar is the kit's glyph-only tabs: chosen glyph lit, count in the corner.
        self.phone_places = named(BarModes([(key, title, icon, count if key == "vm" and count else None)
                                            for key, title, icon in PLACES],
                                           current=self.tab, label="Phone", on_change=self._switch), "pn-places")
        for key, control in self.phone_places.buttons.items():
            control.set_name("pn-place-" + key)
        self.center.show_bar([self.bar_search, self.phone_places], fill=True)
        search = self.center.bar_row.get_first_child()
        if search is not None:
            search.set_name("pn-search")

    def _bar_search(self, text):
        """One search for the whole app: typing grows matches above the bar (v71 pnSearchHits)."""
        self.bar_query = text
        self.requested_address = ""
        if not text.strip():
            if self.center.grown == "search":
                self.center.fold_panel()
            return
        self._fill_results(text)
        if self.center.grown != "search":  # growing the same key again would fold it
            self.center.grow("search", self.results)

    def _fill_results(self, text):
        """The grown search panel: a typed number to call, then up to five people, each with Message, Video and Call."""
        panel = self.results
        while child := panel.get_first_child():
            panel.remove(child)
        number = digits(text)
        if len(number) > 2 and number == "".join(ch for ch in text if ch not in " ()+.-"):
            panel.append(self._result_row("#" + number, dial_display(number), "Call this number"))
        hits = search_people(self._contacts(), text)
        for person in hits:
            panel.append(self._result_row(person.uid, person.name, person.phone, query=text.strip()))
        if not hits and len(number) <= 2:
            panel.append(named(label(f"No one matches “{text.strip()}”", "body", muted=True, xalign=0), "pn-no-match"))

    def _result_row(self, who, title, subtitle, *, query=""):
        row = named(box(spacing=8), "pn-result-" + who.lstrip("#"), "pn-result")
        row.set_size_request(-1, 58)
        lead = self._avatar(who if not who.startswith("#") else "?", 40)
        open_person = named(Gtk.Button(hexpand=True), "pn-result-open-" + who.lstrip("#"), "pn-result-open")
        person_line = box(spacing=12)
        person_line.append(lead)
        words = box(vertical=True, valign=Gtk.Align.CENTER)
        name = label("", "list_title", xalign=0, ellipsize=Pango.EllipsizeMode.END)
        name.set_markup(_highlight(title, query))
        words.append(name)
        words.append(label(subtitle, "meta", muted=True, xalign=0))
        person_line.append(words)
        open_person.set_child(person_line)
        if not who.startswith("#"):
            open_person.connect("clicked", lambda _b: self._select_person(who))
        row.append(open_person)
        for kind, icon, title_text in (("message", "message-square", "Message"), ("video", "video", "Video call"),
                                       ("voice", "phone", "Call")):
            control = button(icon, title_text, lambda kind=kind: self._quick(kind, who),
                             f"pn-result-{kind}-" + who.lstrip("#"), css="pn-round")
            control.set_size_request(42, 42)
            if kind == "voice":
                control.add_css_class("pn-call")
            row.append(control)
        return row

    def _quick(self, kind, who):
        """A result's own Message, Video or Call (v71 data-pnqmsg / data-pnqcall)."""
        self.bar_query = ""
        self.bar_search.set_text("")
        if self.center.grown == "search":
            self.center.fold_panel()
        if kind == "message":
            name = dial_display(who[1:]) if who.startswith("#") else self._person(who).name.split()[0]
            self._toast("Opening Messages with " + name)
            return
        if who.startswith("#"):
            self.tab, self.number, self.drill = "pad", who[1:], False
        else:
            self.tab, self.person, self.selected, self.drill = "contacts", who, "", True
        self.places.set_current(self.tab)
        self._start_call(kind)

    def _search(self, text):
        """The desktop sidebar's search: people and numbers, on the Contacts place (v71 data-pnq)."""
        self.query = text
        self.requested_address = ""
        if text:
            self.tab = "contacts"
            self.drill = False
            self.places.set_current("contacts")
        self._side()
        if not self.is_phone:
            self._main()

    def _focus_search(self):
        if self.is_phone:
            self.bar_search.focus()
            return
        self.foot.entry.grab_focus()

    def _switch(self, key):
        """Pick a place and open its list without selecting a person implicitly."""
        self.requested_address = ""
        self._stop_vm()
        self.tab, self.drill = key, False
        self.bar_query = ""
        self.bar_search.set_text("")
        if self.center.grown == "search":
            self.center.fold_panel()
        if self.is_phone and self.source and key == "recents" and self.source.calls:
            self.selected = self.source.calls[0].uid
        if self.is_phone and self.source and key == "vm" and self.source.voicemails:
            self.selected = self.source.voicemails[0].uid
        self.places.set_current(key)
        self._all()

    def _select(self, uid, tab="recents"):
        self._stop_vm()
        self.tab = tab
        self.selected = uid
        self.drill = True
        self.places.set_current(tab)
        self._all()

    def _select_person(self, uid):
        """A person from Contacts, a favourite or a search result. On a phone's Recents the page pushes over Recents."""
        self.person = uid
        if self.is_phone and self.tab == "recents":
            self.selected = ""
        else:
            self.tab = "contacts"
        self.drill = True
        self.bar_query = ""
        self.bar_search.set_text("")
        if self.center.grown == "search":
            self.center.fold_panel()
        self.places.set_current(self.tab)
        self._all()

    def _main(self):
        if self.call:
            self._call_view()
        elif self.tab == "pad":
            self._pad()
        elif not self.is_phone and not self.drill:
            self._place_list()
        elif self._listing():
            return  # the list-first page is the sidebar; nothing is pushed yet
        else:
            self._person_view()

    def _keys(self, *, in_call=False):
        """The dial pad: 76 keys on a computer, 80 at phone width; in a call 56, or 76 on a phone (v71)."""
        grid = named(Gtk.Grid(row_spacing=(14 if self.is_phone else 10) if in_call else 16,
                              column_spacing=(22 if self.is_phone else 18) if in_call else (26 if self.is_phone else 24),
                              halign=Gtk.Align.CENTER), "pn-keys", "pn-keys")
        for index, (digit, letters) in enumerate(KEYS):
            control = named(DialKey(digit, legend=letters if not in_call else "",
                                    icon=KEY_GLYPHS.get(digit) if not in_call else None,
                                    variant="tone" if in_call else "key", phone=self.is_phone,
                                    label=KEY_NAMES.get(digit, digit) + (" " + letters if letters else ""),
                                    on_activate=lambda digit=digit: self._digit(digit)),
                            ("pn-tone-" if in_call else "pn-key-") + digit)
            if digit == "0" and not in_call:
                control.set_tooltip_text("Hold for +")
                hold = Gtk.GestureLongPress()
                hold.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
                def held(gesture, *args):
                    gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                    self._plus()
                hold.connect("pressed", held)
                control.add_controller(hold)
            grid.attach(control, index % 3, index // 3, 1, 1)
        return grid

    def _pad(self):
        """Keypad, centred in the space above the bar (v71 .pnpad)."""
        page = named(box(vertical=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                         vexpand=True), "pn-pad", "pn-pad")
        page.set_margin_top(20 if self.is_phone else 24)
        page.set_margin_bottom(120 if self.is_phone else 24)
        page.set_margin_start(20)
        page.set_margin_end(20)
        display = named(label(dial_display(self.number) if self.number else "Type a number",
                              "dial" if self.number else "dial-prompt",
                              muted=not self.number, valign=Gtk.Align.CENTER), "pn-number", "pn-number")
        display.set_size_request(-1, 60 if self.is_phone else 52)
        page.append(display)
        match = named(box(spacing=8, halign=Gtk.Align.CENTER), "pn-match")
        match.set_size_request(-1, 26)
        person = matching_person(self.source.people, self.number) if self.source else None
        if person:
            match.append(self._avatar(person.uid, 22))
            match.append(label(person.name))
        page.append(match)
        keys = self._keys()
        keys.set_margin_top(18)
        page.append(keys)
        # Video, then the call key (green on every phone), then delete once there is a number.
        actions = named(Gtk.Grid(column_spacing=26 if self.is_phone else 24, column_homogeneous=True,
                                 halign=Gtk.Align.CENTER), "pn-pad-actions")
        actions.set_margin_top(22)
        for column, (kind, icon, title) in enumerate((("video", "video", "Video call"), ("voice", "phone", "Call"))):
            available = bool(self.number) and (bool(self.fixture) or (self.capability.available and kind == "voice"))
            control = named(DialKey(icon=icon, label=title,
                                    variant="call" if kind == "voice" else "action", phone=self.is_phone,
                                    on_activate=lambda kind=kind: self._start_call(kind)), "pn-call-" + kind)
            control.set_sensitive(available)
            control.set_tooltip_text(title)
            if not self.fixture:
                control.set_tooltip_text(title if self.capability.available else self.capability.reason)
            actions.attach(control, column, 0, 1, 1)
        delete = button("delete", "Delete", self._delete_digit, "pn-delete", css="pn-plain",
                        sensitive=bool(self.number), size=22)
        delete.set_size_request(48, 48)
        delete.set_halign(Gtk.Align.CENTER)
        delete.set_valign(Gtk.Align.CENTER)
        delete.set_opacity(1 if self.number else 0)
        slot = Gtk.CenterBox()
        slot.set_size_request(76, 76)
        slot.set_center_widget(delete)
        actions.attach(slot, 2, 0, 1, 1)
        page.append(actions)
        if not self.fixture and not self.capability.available and self.capability.no_modem:
            readiness = named(label("", "caption", muted=True, wrap=True, justify=Gtk.Justification.CENTER),
                              "pn-call-readiness")
            readiness.set_markup('To make calls, <a href="luma-connect:">pair your phone in Connect</a>.')
            readiness.set_margin_top(14)
            def open_connect(_label, uri):
                if uri == "luma-connect:":
                    self._open_connect()
                return True
            readiness.connect("activate-link", open_connect)
            page.append(readiness)
        self._put(ScrollView(page))

    def _open_connect(self):
        Gio.AppInfo.launch_default_for_uri("luma-connect:", None)

    def _selection(self):
        if not self.source:
            return None, (), None
        vm = next((vm for vm in self.source.voicemails if vm.uid == self.selected), None) if self.tab == "vm" else None
        recent = next((call for call in self.source.calls if call.uid == self.selected), None) if self.tab == "recents" else None
        people = (vm.person,) if vm else recent.people if recent else (self.person,)
        return recent, people, vm

    def _person_view(self):
        recent, people, vm = self._selection()
        person = self._person(people[0]) if people else None
        group = len(people) > 1
        if person is None and recent is None:
            self._put(ListEmptyState("No voicemail" if self.tab == "vm" else "No calls" if self.tab == "recents" else "No contacts"))
            return
        page = self.person_page
        parent = page.get_parent()
        if parent:
            parent.set_child(None)
        while page.get_first_child():
            page.remove(page.get_first_child())
        hero = self._group_avatar(people, 84) if group else self._avatar(people[0], 112)
        hero.set_halign(Gtk.Align.CENTER)
        page.append(hero)
        title = recent.group if group else person.name if person else recent.number
        heading = named(label(title, "hero", justify=Gtk.Justification.CENTER), "pn-person-name")
        heading.set_margin_top(16)
        page.append(heading)
        subtitle = (", ".join(self._person(uid).name for uid in people) + " and you" if group else
                    "mobile · " + person.phone if person and person.phone else
                    "@" + person.handle if person and person.luma_account else "")
        if person is None and recent.spam and self.fixture:
            subtitle = f'Reported as spam by {self.source.spam[recent.uid]["reports"]} people'
        subline = box(spacing=6, halign=Gtk.Align.CENTER)
        if person is None and recent.spam:
            subline.append(icons.image("triangle-alert", pixel_size=14))
        subline.append(label(subtitle, "body", muted=True, justify=Gtk.Justification.CENTER))
        subline.set_margin_top(4)
        page.append(subline)
        if person and not group:
            actions = ContactActions(Person(person.name, phone=person.phone, email=person.email,
                                           username=person.handle, online=person.online), handler=self._contact_action)
            for action, control in actions.buttons.items():
                control.set_name({"call":"pn-call-voice", "video":"pn-call-video"}.get(action, "pn-" + action))
            if self.fixture:
                # v70 lContactActs enables Mail for an on-Luma username;
                # the fixture handler handles it in memory without a URI.
                actions.buttons["email"].set_sensitive(bool(person.handle or person.email))
                if not person.phone:
                    actions.buttons["call"].set_sensitive(False)
                    actions.buttons["video"].set_sensitive(False)
            else:
                actions.buttons["video"].set_sensitive(False)
                actions.buttons["call"].set_sensitive(bool(person.phone) and self.capability.available)
                actions.buttons["message"].set_sensitive(bool(person.phone or (person.luma_account and person.handle)))
        else:
            if group:
                controls = [StackedButton("message-square", "Message", on_click=lambda: self._toast("Opening Launch crew in Messages"))]
                for kind, icon, text in (("voice", "phone", "Call"), ("video", "video", "Video")):
                    controls.append(named(StackedButton(icon, text, on_click=lambda kind=kind: self._start_call(kind)), "pn-call-" + kind))
                actions = StackedButtons(controls)
            else:
                actions = StackedButtons([named(StackedButton("phone", "Call back", on_click=lambda: self._start_call("voice")), "pn-call-voice"),
                                          named(StackedButton("shield", "Block", danger=True, on_click=self._block), "pn-block")])
        actions.set_margin_top(22)
        page.append(Adw.Clamp(maximum_size=376, child=actions))
        if vm:
            page.append(self._voicemail(vm))
        # v71 .pnhist: "Together" above its card.
        self.together_heading = label("Together", "label", muted=True, xalign=0)
        self.together_heading.set_margin_top(30)
        self.together_heading.set_margin_start(4)
        self.together_heading.set_margin_bottom(8)
        self.together_heading.set_visible(False)
        page.append(self.together_heading)
        self.together = named(Card(recessed=True), "pn-together", "pn-together")
        self.together.set_visible(False)
        page.append(self.together)
        if person and not group:
            token = (self.generation, self.tab, self.selected, self.person)
            def fill(items):
                if token != (self.generation, self.tab, self.selected, self.person) or self.call:
                    return
                if items:
                    self.together.set_visible(True)
                    self.together_heading.set_visible(True)
                    for item in items:
                        row = named(DetailsItem(item.title, item.duration,
                                                icon=item.icon, when=item.when),
                                    "pn-history-row", "pn-history-row")
                        if item.missed:
                            row.add_css_class("missed")
                        self.together.append(row)
            if self.fixture:
                fill(self.source.together(person.uid))
            else:
                self._read(lambda source=self.source, uid=person.uid: source.together(uid), fill)
        clamp = Adw.Clamp(maximum_size=560, tightening_threshold=560, child=page)
        light = ContentLitHeader(name=person.name if person else title)
        light.set_visible(False)
        if person:
            data = self.source.photo(person)
            if data and not group:
                light.set_visible(True)
                focus = tuple(person.face[:2]) if person.face else (.5, .5)
                light.set_source(picture=Gdk.Texture.new_from_bytes(GLib.Bytes.new(data)), name=person.name, focus=focus, hue=person.hue)
        backdrop = box(vertical=True, vexpand=True)
        backdrop.append(light)
        overlay = Gtk.Overlay(child=backdrop)
        overlay.add_overlay(ScrollView(clamp))
        self._put(overlay)

    def _contact_action(self, action, person):
        if action in {"call", "video"}:
            self._start_call("video" if action == "video" else "voice")
            return True
        if self.fixture:
            self._toast("Opening " + person.name + (" in Messages" if action == "message" else " in Mail"))
            return True
        selected = self._person(self.person)
        if action == "message" and selected and selected.luma_account and selected.handle:
            # The installed Messages owner resolves the normal username link;
            # no device token or new network authority enters Phone.
            try:
                Gio.AppInfo.launch_default_for_uri("luma-messages://u/" + quote(selected.handle, safe=""), None)
            except GLib.Error:
                self._toast("Messages could not open this Luma contact", error=True)
            return True
        return False

    def _block(self):
        recent, _, _ = self._selection()
        def blocked(_):
            if self.fixture:
                self._toast("Blocked", undo=lambda: self._toast("Block undone"))
            else:
                def saved(previous):
                    def undo():
                        self._read(lambda: self.block_list.set_blocked(recent.number, previous),
                                   lambda _: self._toast("Block undone"))
                    self._toast("Blocked", undo=undo)
                self._read(lambda: self.block_list.set_blocked(recent.number, True), saved)
        # TODO(v71-kit DestructiveDialog.in_bar): on a phone the confirm rises inside the grown bar.
        dialog = DestructiveDialog.ask(self._layer(), title="Block " + recent.number + "?",
            body=("Calls and texts from it go straight to voicemail, and it isn’t told." if self.fixture else
                  "Incoming calls from this number are declined on this computer."), action="Block", icon="shield",
            on_confirm=blocked)
        dialog.set_name("pn-block-dialog")
        dialog.cancel_button.set_name("pn-block-cancel")
        dialog.action_button.set_name("pn-block-confirm")

    def _voicemail(self, vm):
        player = named(Card(recessed=True), "pn-voicemail", "pn-voicemail")
        player.set_margin_top(30)
        self.vm_clip = named(VoiceClip(vm.duration, position=self.vm_position, playing=self.vm_playing,
                                      label="Voicemail", time_mode="duration", on_toggle=self._play_vm, on_seek=self._seek_vm),
                             "pn-voicemail-clip")
        self.vm_clip.key.set_name("pn-voicemail-play")
        self.vm_clip.shape.set_name("pn-voicemail-position")
        player.append(self.vm_clip)
        transcript = label(vm.transcript, "body", wrap=True, xalign=0)
        transcript.set_margin_top(12)
        transcript.set_margin_bottom(4)
        player.append(transcript)
        player.append(label("Transcribed on this computer", "caption", xalign=0))
        return player

    def _play_vm(self, playing=None):
        if not self.fixture:
            return
        playing = not self.vm_playing if playing is None else playing
        if not playing:
            self._stop_vm()
        elif not self.vm_playing:
            self.vm_playing = True
            self.vm_source = GLib.timeout_add(250, self._vm_tick)
        self._main()

    def _vm_tick(self):
        _, _, vm = self._selection()
        if not self.vm_playing or not vm:
            return False
        self.vm_position += .25
        if self.vm_position >= vm.duration:
            self.vm_playing = False
            self.vm_position = 0
            self.vm_source = 0
            self._main()
            return False
        if self.vm_clip is not None:
            self.vm_clip.set_position(self.vm_position)
        return True

    def _seek_vm(self, position):
        _, _, vm = self._selection()
        if self.fixture and vm:
            self.vm_position = max(0, min(vm.duration, position))

    def _stop_vm(self, *, reset=True):
        if self.vm_source:
            GLib.source_remove(self.vm_source)
            self.vm_source = 0
        self.vm_playing = False
        if reset:
            self.vm_position = 0

    def _digit(self, value):
        self.requested_address = ""
        if self.call:
            if self.fixture:
                self._toast("Tone " + value)
            elif self.voice:
                self.voice.dtmf(value)
        else:
            self.number += value
            self._pad()

    def _plus(self):
        self.number += "+"
        self._pad()

    def _delete_digit(self):
        self.number = self.number[:-1]
        self._pad()

    def _start_call(self, kind):
        if self.tab == "pad":
            person = matching_person(self.source.people, self.number) if self.source else None
            people = (person.uid,) if person else ("dial",)
            address = self.number
        else:
            recent, people, _ = self._selection()
            person = self._person(people[0])
            address = person.phone if person else recent.number if recent else ""
        if not self.fixture:
            if kind != "voice" or not self.capability.available:
                self._toast("Video calls unavailable" if kind != "voice" else self.capability.reason, error=True)
                return
            if not address:
                self._toast("This contact has no telephone number", error=True)
                return
            self.voice.dial(address)
            return
        self.self_preview.set_halign(Gtk.Align.END)
        self.self_preview.set_valign(Gtk.Align.END)
        self.self_preview.set_margin_start(0)
        self.self_preview.set_margin_top(0)
        self.self_preview.set_margin_end(16)
        self.self_preview.set_margin_bottom(96)
        self.call = CallView(kind, people, time.monotonic(), output="buds", connected=False)
        self._all()
        self.call_timer_source = GLib.timeout_add(250, self._call_tick)

    def _native_state(self, session):
        if session.phase in {CallPhase.ENDED, CallPhase.FAILED}:
            self._end_view("Call failed" if session.phase is CallPhase.FAILED else "Call ended")
            self._reload()
            return
        if session.phase is CallPhase.IDLE:
            return
        person = next((p for p in self.source.people if digits(p.phone) == digits(session.address)), None) if self.source else None
        if not person:
            self.number = session.address
        if self.call is None or self.call.call_id != session.call_id:
            self.call = CallView("voice", (person.uid,) if person else ("dial",), call_id=session.call_id)
        elif person:
            self.call.people = (person.uid,)
        self.call.connected = session.connected_at > 0
        self._publish_shell()
        self._all()
        if not self.call_timer_source:
            self.call_timer_source = GLib.timeout_add(250, self._call_tick)

    def _publish_shell(self):
        if self.shell_surface:
            self.voice._task(lambda: self.shell_surface.set("immersive"), lambda _: None)
        if self.active_indicator and self.call:
            name, connected, muted = self._call_name(), self.voice.session.connected_at, self.call.muted
            self.voice._task(lambda: self.active_indicator.publish(name, connected, muted=muted), lambda _: None)

    def _call_name(self):
        if len(self.call.people) > 1:
            return "Launch crew"
        person = self._person(self.call.people[0])
        return person.name if person else dial_display(self.number)

    def _call_view(self):
        c = self.call
        root = named(Gtk.Overlay(), "pn-call-surface", "pn-call-surface")
        media_context(root)
        root.add_css_class(c.kind)
        self.sidebar.set_sensitive(False)
        self.sidebar.set_opacity(.45)
        self.call_chrome = ()
        self.call_timer_label = None
        phone = self.is_phone
        if c.kind == "voice":
            person = self._person(c.people[0])
            data = self.source.photo(person) if person else None
            if data:
                root.set_child(CallPhoto(Gdk.Texture.new_from_bytes(GLib.Bytes.new(data)), wash=True))
            if phone:
                root.add_overlay(self._voice_head_phone())
            else:
                page = box(vertical=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
                page.set_margin_bottom(60)
                page.append(self._avatar(c.people[0], 140))
                title = label(self._call_name(), "call-heading")
                title.set_margin_top(20)
                page.append(title)
                page.append(label("Connected" if c.connected else "Incoming call" if self._incoming() else "Calling…", "body"))
                if c.keypad:
                    grid = self._keys(in_call=True)
                    grid.set_margin_top(18)
                    page.append(grid)
                if data:
                    root.add_overlay(page)
                else:
                    root.set_child(page)
        else:
            tiles = (*c.people, "me") if len(c.people) > 1 else c.people
            grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True, row_spacing=6, column_spacing=6)
            for index, uid in enumerate(tiles):
                tile = self._video_tile(uid)
                grid.attach(tile, index % 2 if len(tiles) > 1 else 0, index // 2, 1, 1)
            root.set_child(grid)
            if len(c.people) == 1:
                parent = self.self_preview.get_parent()
                if parent:
                    parent.remove_overlay(self.self_preview)
                self.self_preview.set_child(self._video_tile("me", self_view=True))
                if phone:
                    # v71: your own picture top right at 104 × 150.
                    self.self_preview.set_ratio(104 / 150)
                    self.self_preview.set_size_request(104, 150)
                    self.self_preview.set_halign(Gtk.Align.END)
                    self.self_preview.set_valign(Gtk.Align.START)
                    self.self_preview.set_margin_start(0)
                    self.self_preview.set_margin_bottom(0)
                    self.self_preview.set_margin_end(16)
                    self.self_preview.set_margin_top(110)
                else:
                    self.self_preview.set_ratio(1.6)
                    collapsed = 0 < self.get_width() <= 700
                    self.self_preview.set_size_request(130 if collapsed else 200, 81 if collapsed else 125)
                root.add_overlay(self.self_preview)
            else:
                for edge in ("top", "bottom", "start", "end"):
                    getattr(grid, "set_margin_" + edge)(6)
            if phone:
                head = self._call_head_phone()
                root.add_overlay(head)
                self.call_chrome = (head,)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda *_: self._video_idle())
        root.add_controller(motion)
        if phone:
            controls = self._call_controls_phone()
            root.add_overlay(controls)
            self.call_chrome = (*self.call_chrome, controls)
            tap = Gtk.GestureClick()
            tap.connect("released", lambda *_: self._video_idle())
            root.add_controller(tap)
        self._put(root)
        self.center.set_visible(False)
        if phone:
            self.call_center.hide_bar()
            self._layout()
        else:
            self._call_bar()
        self._video_idle()

    def _incoming(self):
        return bool(self.voice and self.voice.session.phase is CallPhase.INCOMING)

    def _elapsed_text(self):
        c = self.call
        elapsed = int(time.monotonic() - c.started) if self.fixture else self.voice.session.elapsed()
        if self._incoming():
            return "Incoming call"
        return f"{elapsed // 60}:{elapsed % 60:02d}" if c.connected or self.fixture else "Calling…"

    def _timer_line(self):
        """The lock (end-to-end encrypted) and the running time (v71 phone calls)."""
        line = box(spacing=5, halign=Gtk.Align.CENTER)
        lock = icons.image("lock", pixel_size=13)
        lock.set_tooltip_text("End-to-end encrypted")
        line.append(lock)
        self.call_timer_label = named(label("Sharing your screen" if self.call.sharing else self._elapsed_text(),
                                            "call-timer"), "pn-timer")
        line.append(self.call_timer_label)
        return line

    def _voice_head_phone(self):
        """Voice call at phone width: the face (140), the name, the lock and the time; or the keypad."""
        c = self.call
        head = named(box(vertical=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.START), "pn-call-head", "pn-call-head")
        head.set_margin_top(70 if c.keypad else 110)
        if not c.keypad:
            face = self._avatar(c.people[0], 140)
            face.set_halign(Gtk.Align.CENTER)
            head.append(face)
        title = label(self._call_name(), "call-heading", justify=Gtk.Justification.CENTER)
        title.set_margin_top(20)
        head.append(title)
        line = self._timer_line()
        line.set_margin_top(4)
        head.append(line)
        if c.keypad:
            grid = self._keys(in_call=True)
            grid.set_margin_top(20)
            head.append(grid)
        return head

    def _call_head_phone(self):
        """Video call at phone width: the name and time over the top of the picture."""
        head = named(box(vertical=True, spacing=4, halign=Gtk.Align.CENTER, valign=Gtk.Align.START),
                     "pn-call-head", "pn-call-head")
        head.set_margin_top(60)
        head.append(label(self._call_name(), "hero-title"))
        head.append(self._timer_line())
        return head

    def _call_controls_phone(self):
        """v71 "calls laid out like phone calls": six round controls in two rows of three, End alone below.

        Voice: Mute, Keypad, Speaker; Add, Video, Message. Video: Mute, Camera, Flip,
        Share, Effects, Audio on a dark glass panel that fades after a few seconds.
        """
        c = self.call
        video = c.kind == "video"
        panel = named(box(vertical=True, spacing=22, valign=Gtk.Align.END), "pn-call-controls", "pn-call-controls")
        if video:
            panel.add_css_class("video")
        panel.set_margin_start(16)
        panel.set_margin_end(16)
        panel.set_margin_bottom(34)
        live = bool(self.fixture) or (self.voice is not None and self.voice.session.phase is CallPhase.ACTIVE)
        if self._incoming():
            row = Gtk.CenterBox()
            row.set_start_widget(self._end_button("phone-off", "Decline", lambda: self.voice.control("decline"), "pn-decline"))
            answer = button("phone", "Answer", lambda: self.voice.control("accept"), "pn-answer", css="pn-big", size=32)
            answer.add_css_class("pn-call")
            answer.set_size_request(76, 76)
            row.set_end_widget(answer)
            panel.append(row)
            return panel
        if video:
            controls = (("pn-mute", "mic-off" if c.muted else "mic", "Unmute" if c.muted else "Mute", c.muted,
                         lambda: self._toggle("muted"), live),
                        ("pn-camera", "video-off" if c.camera_off else "video", "Camera", c.camera_off,
                         lambda: self._toggle("camera_off"), True),
                        ("pn-flip", "refresh-ccw", "Flip", False, lambda: self._toast("Using the front camera"), bool(self.fixture)),
                        ("pn-share-screen", "monitor-up", "Share", c.sharing, lambda: self._toggle("sharing"), True),
                        ("pn-effects", "sparkles", "Effects", c.effects, lambda: self._toggle("effects"), True),
                        ("pn-audio", "headphones", "Audio", False, self._audio_menu, True))
        else:
            controls = (("pn-mute", "mic-off" if c.muted else "mic", "Unmute" if c.muted else "Mute", c.muted,
                         lambda: self._toggle("muted"), live),
                        ("pn-call-keypad", "grid-3x3", "Keypad", c.keypad, lambda: self._toggle("keypad"), True),
                        ("pn-audio", "volume-2", "Speaker", c.output in {"spk", "speaker"}, self._audio_menu, True),
                        ("pn-add-person", "user-plus", "Add", False, lambda: self._toast("Add someone to the call"), bool(self.fixture)),
                        ("pn-upgrade", "video", "Video", False, self._upgrade, bool(self.fixture)),
                        ("pn-message", "message-square", "Message", False, lambda: self._toast("Opening Messages"), bool(self.fixture)))
        grid = named(Gtk.Grid(column_homogeneous=True, row_spacing=18, column_spacing=10), "pn-call-grid")
        grid.set_visible(video or not c.keypad)
        for index, (name, icon, text, on, activate, sensitive) in enumerate(controls):
            grid.attach(self._round_control(name, icon, text, on, activate, sensitive), index % 3, index // 3, 1, 1)
        panel.append(grid)
        end_row = Gtk.CenterBox()
        if c.keypad and not video:
            hide = named(Gtk.Button(label="Hide"), "pn-hide", "pn-hide")
            hide.connect("clicked", lambda _b: self._toggle("keypad"))
            end_row.set_start_widget(hide)
        end_row.set_center_widget(self._end_button("phone-off", "End call", self._end_call, "pn-end"))
        panel.append(end_row)
        if video:
            # The glass panel's own inset (v71 .pnctrl2.v: 18 12 14).
            grid.set_margin_top(18)
            grid.set_margin_start(12)
            grid.set_margin_end(12)
            end_row.set_margin_bottom(14)
        return panel

    def _round_control(self, name, icon, text, on, activate, sensitive):
        control = named(Gtk.Button(sensitive=sensitive), name, "pn-control")
        if on:
            control.add_css_class("on")
        face = Gtk.CenterBox(halign=Gtk.Align.CENTER)
        face.add_css_class("pn-control-face")
        face.set_size_request(68, 68)
        face.set_center_widget(icons.image(icon, pixel_size=26))
        column = box(vertical=True, spacing=7)
        column.append(face)
        column.append(label(text, "button-small"))
        control.set_child(column)
        control.update_property([Gtk.AccessibleProperty.LABEL], [text])
        control.connect("clicked", lambda _b: activate())
        return control

    def _end_button(self, icon, title, activate, name):
        control = button(icon, title, activate, name, css="pn-end", size=32)
        control.set_size_request(76, 76)
        control.set_halign(Gtk.Align.CENTER)
        return control

    def _video_tile(self, uid, *, self_view=False):
        tile = named(Gtk.Overlay(hexpand=True, vexpand=True, overflow=Gtk.Overflow.HIDDEN), "pn-video-" + uid, "pn-video-tile")
        person = self._person(uid)
        data = self.fixture.photo(person) if person else None
        if uid == "me":
            data = (self.fixture.path.parent / "phone-v70/life-hero-home.webp").read_bytes()
        if data and not (uid == "me" and self.call.camera_off):
            focus = (.8, .3) if uid == "me" else tuple(person.face[:2]) if person and person.face else (.5, .5)
            tile.set_child(CallPhoto(Gdk.Texture.new_from_bytes(GLib.Bytes.new(data)), focus=focus, effects=self.call.effects and not self_view))
        else:
            tile.set_child(PersonAvatar("Nick", 40 if self_view else 64, hue=250) if uid == "me" else self._avatar(uid, 64))
        if len(self.call.people) > 1:
            tile.add_css_class("group")
            tile.add_css_class("speaking" if uid == self.call.people[0] else "quiet")
            name = named(box(spacing=6, halign=Gtk.Align.START, valign=Gtk.Align.END),
                         "pn-name-" + uid, "pn-name")
            name.set_size_request(-1, 26)
            name.set_margin_start(12)
            name.set_margin_bottom(12)
            text = label("You" if uid == "me" else person.name.split()[0], "caption")
            text.set_margin_start(10)
            text.set_margin_end(10)
            name.append(text)
            if uid == "me" and self.call.muted:
                text.set_margin_end(0)
                marker = icons.image("mic-off", pixel_size=13)
                marker.add_css_class("pn-muted-marker")
                marker.set_margin_end(10)
                name.append(marker)
            tile.add_overlay(name)
        return tile

    def _drag_self_begin(self):
        allocation = self.self_preview.get_allocation()
        self.self_origin = allocation.x, allocation.y

    def _drag_self_update(self, dx, dy):
        parent = self.self_preview.get_parent()
        x = max(12, min(parent.get_width() - self.self_preview.get_width() - 12, self.self_origin[0] + dx))
        y = max(12, min(parent.get_height() - self.self_preview.get_height() - 12, self.self_origin[1] + dy))
        self.self_preview.set_halign(Gtk.Align.START)
        self.self_preview.set_valign(Gtk.Align.START)
        self.self_preview.set_margin_end(0)
        self.self_preview.set_margin_bottom(0)
        self.self_preview.set_margin_start(int(x))
        self.self_preview.set_margin_top(int(y))

    def _snap_self(self, dx, dy):
        parent = self.self_preview.get_parent()
        right = self.self_origin[0] + dx > parent.get_width() / 2
        bottom = self.self_origin[1] + dy > parent.get_height() / 2
        self.self_preview.set_halign(Gtk.Align.END if right else Gtk.Align.START)
        self.self_preview.set_valign(Gtk.Align.END if bottom else Gtk.Align.START)
        self.self_preview.set_margin_start(0 if right else 16)
        self.self_preview.set_margin_end(16 if right else 0)
        self.self_preview.set_margin_top(0 if bottom else 64)
        self.self_preview.set_margin_bottom(96 if bottom else 0)

    def _video_idle(self):
        """A video call's controls fade after three seconds and return on a tap or a move (v71 pnIdle)."""
        if self.video_idle_source:
            GLib.source_remove(self.video_idle_source)
            self.video_idle_source = 0
        if self.is_phone:
            for widget in self.call_chrome:
                widget.set_opacity(1)
                widget.set_can_target(True)
        else:
            self.call_center.set_visible(True)
        surface = self._find(self.content_stack, "pn-call-surface")
        if surface:
            surface.set_cursor(None)
        if self.call and self.call.kind == "video":
            def hide():
                self.video_idle_source = 0
                if self.call and self.call.kind == "video":
                    if self.is_phone:
                        for widget in self.call_chrome:
                            widget.set_opacity(0)
                            widget.set_can_target(False)
                    else:
                        self.call_center.set_visible(False)
                    current = self._find(self.content_stack, "pn-call-surface")
                    if current:
                        current.set_cursor_from_name("none")
                return False
            self.video_idle_source = GLib.timeout_add(3000, hide)

    def _call_bar(self):
        c = self.call
        actions = [BarAction("mic-off" if c.muted else "mic", tooltip="Unmute" if c.muted else "Mute",
                             on_activate=lambda: self._toggle("muted"), active=c.muted,
                             sensitive=bool(self.fixture) or self.voice.session.phase is CallPhase.ACTIVE)]
        names = ["pn-mute"]
        if c.kind == "video":
            actions += [BarAction("video-off" if c.camera_off else "video", tooltip="Turn camera on" if c.camera_off else "Turn camera off", on_activate=lambda: self._toggle("camera_off"), active=c.camera_off),
                        BarAction("monitor-up", tooltip="Stop sharing" if c.sharing else "Share screen", on_activate=lambda: self._toggle("sharing"), active=c.sharing),
                        BarAction("sparkles", tooltip="Background effects on" if c.effects else "Background effects", on_activate=lambda: self._toggle("effects"), active=c.effects)]
            names += ["pn-camera", "pn-share-screen", "pn-effects"]
        else:
            actions += [BarAction("grid-2x2", tooltip="Keypad", on_activate=lambda: self._toggle("keypad"), active=c.keypad),
                        BarAction("video", tooltip="Switch to video", sensitive=bool(self.fixture), on_activate=self._upgrade)]
            names += ["pn-call-keypad", "pn-upgrade"]
        actions += [BarAction("headphones", tooltip="Audio", on_activate=self._audio_menu),
                    BarAction("user-plus", tooltip="Add person", sensitive=bool(self.fixture), on_activate=lambda: self._toast("Add someone to the call")),
                    BarAction("phone-off", "End", tooltip="End call", danger=True, on_activate=self._end_call)]
        names += ["pn-audio", "pn-add-person", "pn-end"]
        if self.voice and self.voice.session.phase is CallPhase.INCOMING:
            actions = [BarAction("phone", "Answer", on_activate=lambda: self.voice.control("accept")),
                       BarAction("phone-off", "Decline", danger=True, on_activate=lambda: self.voice.control("decline"))]
            names = ["pn-answer", "pn-decline"]
        elapsed = int(time.monotonic() - c.started) if self.fixture else self.voice.session.elapsed()
        duration = f"{elapsed // 60}:{elapsed % 60:02d}" if c.connected or self.fixture else "Calling…"
        items = []
        item_names = []
        incoming = bool(self.voice and self.voice.session.phase is CallPhase.INCOMING)
        boundaries = {0, 4 if c.kind == "video" else 1, len(actions) - 1}
        for index, (action, name) in enumerate(zip(actions, names)):
            if not incoming and index in boundaries:
                items.append(SEPARATOR)
                item_names.append(None)
            items.append(action)
            item_names.append(name)
        self.call_center.show_bar([BarChip(self._call_name(), meta="Sharing your screen" if c.sharing else duration,
                                          lead=self._group_avatar(c.people[:2], 22) if len(c.people) > 1 else self._avatar(c.people[0], 26)), *items])
        child = self.call_center.bar_row.get_first_child()
        self.call_duration = child
        child = child.get_next_sibling()
        for name in item_names:
            if name is not None:
                child.set_name(name)
            child = child.get_next_sibling()

    def _call_tick(self):
        if not self.call or self.closed:
            self.call_timer_source = 0
            return False
        elapsed = int(time.monotonic() - self.call.started) if self.fixture else self.voice.session.elapsed()
        if self.fixture:
            self.call.connected = elapsed > 0
            if self.call.kind == "video" and len(self.call.people) > 1:
                speaking = self.call.people[(elapsed // 3) % len(self.call.people)]
                for uid in (*self.call.people, "me"):
                    tile = self._find(self.content_stack, "pn-video-" + uid)
                    if tile:
                        if uid == speaking:
                            tile.add_css_class("speaking")
                        else:
                            tile.remove_css_class("speaking")
        if self.call_timer_label is not None and not self.call.sharing:
            self.call_timer_label.set_label(f"{elapsed // 60}:{elapsed % 60:02d}" if self.call.connected or self.fixture else "Calling…")
        child = self.call_duration.get_first_child() if self.call_duration is not None and not self.is_phone else None
        while child:
            if not self.call.sharing and isinstance(child, Gtk.Label) and child.has_css_class("lumaui-bar-subject-meta"):
                child.set_label(f"{elapsed // 60}:{elapsed % 60:02d}" if self.call.connected or self.fixture else "Calling…")
                break
            child = child.get_next_sibling()
        return True

    def _notification_mute(self, value):
        call = self.call
        GLib.idle_add(lambda: self._set_muted(value)
                      if call is not None and self.call is call else False)

    def _set_muted(self, value):
        if self.closed or not self.call:
            return False
        value = bool(value)
        if not self.fixture:
            call = self.call
            def confirmed(value):
                if not self.closed and self.call is call:
                    call.muted = value
                    self._publish_shell()
                    self._call_view()
            self.voice.mute(value, confirmed=confirmed)
        else:
            self.call.muted = value
            self._call_view()
        return False

    def _toggle(self, field):
        if self.closed or not self.call:
            return
        value = not getattr(self.call, field)
        if field == "muted":
            self._set_muted(value)
            return
        setattr(self.call, field, value)
        self._call_view()
        if field == "sharing" and value:
            self._toast("Sharing the Launch deck window. Pick another any time")

    def _upgrade(self):
        self.call.kind = "video"
        self._call_view()

    def _audio_menu(self):
        """Where the call's sound goes: the kit's menu (v71 lMenu), rising from the bar on a phone."""
        if self.closed or not self.call:
            return
        call = self.call
        if self.audio_menu is not None and hasattr(self.audio_menu, "close"):
            self.audio_menu.close()
        outputs = self.source.audio_outputs if self.fixture else (
            {"id":"speaker", "icon":"speaker", "label":"This computer’s speakers" if self.voice.provider else "Speaker"},
            {"id":"earpiece", "icon":"smartphone", "label":"Your phone"})
        rows = [MenuItem(output["label"], icon=output["icon"], selected=call.output == output["id"],
                         on_activate=lambda route=output["id"], call=call: self._audio(route) if self.call is call else None)
                for output in outputs]
        control = self._find(self.content_stack if self.is_phone else self.call_center, "pn-audio")
        self.audio_menu = bar_menu(control, rows, label="Audio")

    @staticmethod
    def _find(widget, name):
        if widget.get_name() == name:
            return widget
        child = widget.get_first_child()
        while child:
            found = PhoneWindow._find(child, name)
            if found:
                return found
            child = child.get_next_sibling()
        return None

    def _audio(self, route):
        if self.closed or not self.call:
            return
        if self.audio_menu is not None and hasattr(self.audio_menu, "close"):
            self.audio_menu.close()
        self.audio_menu = None
        if self.fixture:
            self.call.output = route
            self._toast({"buds":"Audio on Fable Buds", "spk":"Audio on this computer’s speakers", "usb":"Audio on the studio mic and speakers"}[route])
        else:
            call = self.call
            def confirmed(route):
                if not self.closed and self.call is call:
                    call.output = route
            self.voice.audio(route, confirmed=confirmed)

    def _end_call(self):
        if not self.call:
            return False
        if self.fixture:
            elapsed = int(time.monotonic() - self.call.started)
            self._end_view(f"Call ended · {elapsed // 60}:{elapsed % 60:02d}")
        else:
            self.voice.control("hangup")

    def _end_view(self, message):
        if self.audio_menu is not None and hasattr(self.audio_menu, "close"):
            self.audio_menu.close()
            self.audio_menu = None
        if self.call_timer_source:
            GLib.source_remove(self.call_timer_source)
            self.call_timer_source = 0
        if self.video_idle_source:
            GLib.source_remove(self.video_idle_source)
            self.video_idle_source = 0
        if self.shell_surface:
            self.voice._task(self.shell_surface.clear, lambda _: None)
        if self.active_indicator:
            self.voice._task(self.active_indicator.withdraw, lambda _: None)
        self.sidebar.set_sensitive(True)
        self.sidebar.set_opacity(1)
        self.call = None
        self.call_chrome = ()
        self.call_timer_label = None
        self.call_center.hide_bar()
        self._all()
        self._toast(message)

    def _toast(self, message, *, error=False, undo=None):
        if not self.closed:
            Toast.show(self._layer(), message, kind="error" if error else "done", undo=undo)

    def set_dial_address(self, value):
        self.number = value.removeprefix("tel:").split("?", 1)[0]
        self.tab, self.drill = "pad", False
        self.places.set_current("pad")
        self._all()

    def start_dial_address(self, value):
        self.set_dial_address(value)
        if not self.capability_ready:
            self.requested_address = self.number
        else:
            self._start_call("voice")

    def restore_native_call(self):
        pass  # VoiceController restores from the authoritative native service.

    def _key_pressed(self, controller, keyval, keycode, state):
        focus = self.get_focus()
        if isinstance(focus, (Gtk.Text, Gtk.Entry)):
            return False
        if keyval in {Gdk.KEY_Escape, Gdk.KEY_BackSpace} and self.is_phone and self.drill and self.tab != "pad" and not self.call:
            self._back()  # a pushed page goes back up to its list
            return True
        if self.call and not self.call.keypad:
            return False
        if self.tab != "pad" and not self.call:
            return False
        character = chr(Gdk.keyval_to_unicode(keyval))
        if character in "0123456789*#" and character:
            self._digit(character)
            return True
        if character == "+" and not self.call:
            self.number += "+"
            self._pad()
            return True
        if keyval == Gdk.KEY_BackSpace and not self.call:
            self._delete_digit()
            return True
        if keyval in {Gdk.KEY_Return, Gdk.KEY_KP_Enter} and self.number and not self.call:
            self._start_call("voice")
            return True
        return False

    def _close_requested(self, _):
        self.closed = True
        if self.audio_menu is not None and hasattr(self.audio_menu, "close"):
            self.audio_menu.close()
            self.audio_menu = None
        self.generation += 1
        self._stop_vm()
        if self.call_timer_source:
            GLib.source_remove(self.call_timer_source)
        if self.video_idle_source:
            GLib.source_remove(self.video_idle_source)
        if self.shell_surface:
            self.shell_surface.clear()
        if self.active_indicator:
            self.active_indicator.withdraw()
        if self.voice:
            self.voice.close()
            application = self.get_application()
            if hasattr(application, "call_provider") and application.call_provider is self.voice.provider:
                del application.call_provider
        self.executor.shutdown(wait=False, cancel_futures=True)
        return False


class PhoneApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.add_main_option("call", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Call supplied tel: address", None)
        self.pending_address = ""
        self.pending_start_call = False

    def do_startup(self):
        Adw.Application.do_startup(self)
        install_lumaui()
        path = os.environ.get("LUMA_PHONE_STYLE_PATH", str(Path(__file__).parents[1] / "style/phone.css"))
        if not Path(path).is_file():
            path = "/usr/share/prairie-core/phone.css"
        add_style_sheet(path)

    def do_activate(self):
        window = self.props.active_window or PhoneWindow(self)
        window.present()
        if self.pending_address:
            address, self.pending_address = self.pending_address, ""
            window.start_dial_address(address) if self.pending_start_call else window.set_dial_address(address)

    def do_command_line(self, command_line):
        self.pending_address = next((arg for arg in command_line.get_arguments()[1:] if arg.startswith("tel:")), "")
        self.pending_start_call = command_line.get_options_dict().contains("call")
        self.activate()
        return 0


def main():
    return PhoneApplication().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
