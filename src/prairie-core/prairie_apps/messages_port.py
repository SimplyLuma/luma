# SPDX-License-Identifier: Apache-2.0
"""Messages' v70 surface, composed from LumaUI parts.

The service and message store remain the authority.  This module turns their
records into the same view used by the in-memory conform fixture; it never
opens a second real store or sends from fixture mode.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
import math
import os
import time
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from luma_appkit import (  # noqa: E402
    ActionCenter, ActionEditor, AddRow, AppWindow, BarAction, BarChip, BarEntry, BarThumbnail,
    BarContext, ContactActions, ContentLitHeader, CornerPill,
    Card, CommandRegistry, DestructiveDialog, DetailsPane, DetailsRow, EmptyState, EventCard, FileCard,
    DetailsPhotos, GroupFace, Island, IslandSplitView, ListEmptyState, MessageBubble, MessageRun, NavigationRow, NavigationSidebar,
    RowLead, SidebarRow, apply_sidebar_variant,
    Person, PersonAvatar, PlaceCard, ScrollView, SidebarFoot, SongCard,
    SPACER, SwipeAction, SwipeRow, BarTile, BarTiles, StackedButton, StackedButtons, TitleIsland, Toast, ToastHost, VoiceClip, apply_type, icons,
)
from luma_appkit.action_bubble import FloatingMenu, MenuItem  # noqa: E402
from luma_appkit.bar_items import BarSearch  # noqa: E402
from luma_appkit.bar_panel import PanelField, PanelRow  # noqa: E402
from luma_appkit.action_center import make_control  # noqa: E402
from luma_appkit import lumaui  # noqa: E402
from luma_appkit import lumaui_tokens as tokens  # noqa: E402


APP_ID = "org.projectluma.Messages"
WIDGET_NAMES = (
    "msg-sidebar", "msg-foot", "msg-island", "msg-light", "msg-identity",
    "msg-corner", "msg-history", "msg-bar", "msg-details", "msg-intro",
)


def _picture(source: str | Path | bytes | None) -> Gdk.Texture | None:
    if not source:
        return None
    try:
        if isinstance(source, bytes):
            return Gdk.Texture.new_from_bytes(GLib.Bytes.new(source))
        return Gdk.Texture.new_from_filename(str(source))
    except (GLib.Error, OSError, ValueError):
        return None


def _live_photo_texture(path: Path, content_type: str = "") -> Gdk.Texture | None:
    """Decode a stored photo near its displayed size, with EXIF orientation."""
    from . import messages_photos as photos

    width, height = photos.photo_dimensions(path, content_type)
    if not width:
        return None
    shown = photos.display_size(width, height, max_width=600, max_height=450)
    pixels = photos.decode(path, *shown)
    return photos.texture(pixels) if pixels is not None else None


def _label(text: str, role: str, **props) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0, **props)
    apply_type(label, role)
    return label


def _unique_participants(participants: tuple[dict, ...]) -> dict[str, dict]:
    """The helper may list the same phone under multiple network IDs."""
    from .messages_backend import normalize_address

    unique: dict[str, dict] = {}
    seen: set[tuple[str, str]] = set()
    for index, person in enumerate(participants):
        phone = str(person.get("phone") or "").strip()
        try:
            phone = normalize_address(phone) if phone else ""
        except ValueError:
            pass
        handle = str(person.get("handle") or "").lstrip("@").casefold()
        identity = (("phone", phone) if phone else ("handle", handle) if handle else
                    ("id", str(person.get("id"))) if person.get("id") else
                    ("row", str(index)))
        if not identity[1] or identity in seen:
            continue
        seen.add(identity)
        unique[str(person.get("id") or f"row:{index}")] = person
    return unique


class _PhotoClip(Gtk.Box):
    """Keep a decoded photo inside the message bubble's rounded image edge."""

    __gtype_name__ = "LumaMessagesPhotoClip"

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        bounds = Graphene.Rect().init(0, 0, self.get_width(), self.get_height())
        rounded = Gsk.RoundedRect()
        # The photo's corners follow its bubble's in the run (v71 .k-photo .bub.mshaped .mph):
        # 3 inside the bubble's own, never under 4.
        bubble = self.get_ancestor(MessageBubble)
        free, join = tokens.MESSAGE_BUBBLE["radius"], 5
        corners = []
        for name in ("tl", "tr", "br", "bl"):
            joined = bubble is not None and bubble.has_css_class("run-" + name)
            size = max(4, (join if joined else free) - 3)
            corners.append(Graphene.Size().init(size, size))
        rounded.init(bounds, *corners)
        snapshot.push_rounded_clip(rounded)
        Gtk.Box.do_snapshot(self, snapshot)
        snapshot.pop()


class MessagesSurface:
    """One adaptive view for a live MessagesWindow or a memory-only fixture."""

    def __init__(self, owner: AppWindow, fixture: Any = None) -> None:
        from .messages_preferences import ConversationPreferences

        self.owner, self.fixture = owner, fixture
        self.preferences = None if fixture else ConversationPreferences()
        self.current: dict | None = None
        self.selected_message: dict | None = None
        self.reply: dict | None = None
        self.query = ""
        self._drafts: dict[str, str] = {}
        self._rich_drafts: dict[str, tuple[str, list[tuple[str, int, int]]]] = {}
        self._fixture_reply_index: dict[str, int] = {}
        self.bar_entry: BarEntry | None = None
        self._forward_text: str | None = None
        self._playing_voice: int | None = None
        self._voice_source = 0
        self._voice_at: dict[int, int] = {}
        self._quick_pop: Gtk.Popover | None = None
        self._quick_hide_source = 0
        self._phone_mode = False
        self._navigated = False
        self._new_field: PanelField | None = None
        self._new_rows: Gtk.Box | None = None
        self._columns: list[Adw.Clamp] = []
        self._photos: list[Gtk.Picture] = []
        self._photo_clamps: list[Adw.Clamp] = []
        self._links: list[Gtk.Box] = []
        self._message_rows: dict[str, Gtk.Widget] = {}
        self._message_bubbles: dict[str, Gtk.Widget] = {}
        self._scroll_source = 0
        self._end_pending = False  # a new thread's follow-the-end is still settling
        self._rendered_thread_id: str | None = None
        self._compose_scroll_source = 0
        self._editor_scroll_origin = 0.0
        self._editor_was_at_end = False
        self._rows: dict[str, Gtk.ListBoxRow] = {}
        self._entry_by_id: dict[str, dict] = {}
        self.pending: dict | None = None
        self._build()

    def _build(self) -> None:
        self.sidebar = NavigationSidebar()
        self.sidebar.set_phone_title("Messages")
        apply_sidebar_variant(self.sidebar, "people")
        self.sidebar.set_name("msg-sidebar")
        self.sidebar.list.connect("row-activated", self._row_activated)
        self.foot = SidebarFoot(search="Search people and messages", on_search=self._search,
                                add=("New message", "square-pen", self.new_message))
        self.foot.set_name("msg-foot")
        self.foot.add_button.set_name("msg-new")
        self.sidebar.append_footer(self.foot)

        self.details = DetailsPane("Details", on_close=self._details_closed)
        self.details.sheet.set_name("msg-details")
        self.island = Island()
        self.island.set_name("msg-island")
        self.light = ContentLitHeader(name="Messages")
        self.light.set_name("msg-light")
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, spacing=12)
        self.back = CornerPill(actions=(("chevron-left", "Conversations", self._show_conversations),))
        self.back.set_name("msg-back")
        self.back.set_visible(False)
        self.header.append(self.back)
        self.identity = self._identity("Messages", "Conversation", None)
        self.identity.set_name("msg-identity")
        self.header.append(self.identity)
        self.header.append(Gtk.Box(hexpand=True))
        # v71 .macts: Call, Video, Pin (lit while pinned), Details.
        self.corner = CornerPill(
            actions=(("phone", "Call", self._call), ("video", "Video call", self._video)),
            states=(("pin", "Pin", False, self._pin_toggled),), info=self.details,
            order=("people", "actions", "states", "info"))
        self.corner.set_name("msg-corner")
        self.corner.controls["info"].set_name("msg-info")
        self.header.append(self.corner)
        # The conform gate's way back to the list (a phone's ‹); never shown.
        self.show_list = Gtk.Button(visible=False)
        self.show_list.set_name("msg-show-list")
        self.show_list.connect("clicked", lambda _button: self._show_conversations())
        self.header.append(self.show_list)
        # ...and its way into the open conversation on a phone (list-first); a desktop shows it already.
        self.phone_open = Gtk.Button(visible=False)
        self.phone_open.set_name("msg-phone-open")
        self.phone_open.connect("clicked", lambda _button: self._phone_open_current())
        self.header.append(self.phone_open)
        self.body.append(self.header)
        self.history = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START,
                               hexpand=True, spacing=2)
        self.history.set_name("msg-history")
        self.history.set_margin_start(26)
        self.history.set_margin_end(26)
        self.history.set_margin_top(0)
        self.history.set_margin_bottom(69)
        self.history_clamp = Adw.Clamp(maximum_size=760, tightening_threshold=760, child=self.history)
        self.scroller = ScrollView(self.history_clamp, fade_top_start=52, fade_top_end=104,
                                   fade_top_always=True)
        # A reader at the end stays there while late content (photos, link cards)
        # or a grown bar changes the thread's height.
        self._stick_end = True
        # Only the reader (wheel, touch, scrollbar, keys) or a deliberate jump lets go
        # of the end; a hidden page's or a relayout's reset to 0 does not.
        self._reader_scrolled_at = 0.0
        touched = lambda *_a: setattr(self, "_reader_scrolled_at", time.monotonic())
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        wheel.connect("scroll", lambda *a: (touched(), False)[1])
        self.scroller.add_controller(wheel)
        drag = Gtk.GestureDrag()
        drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        drag.connect("drag-update", touched)
        self.scroller.add_controller(drag)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", lambda *a: (touched(), False)[1])
        self.scroller.add_controller(keys)
        adjustment = self.scroller.get_vadjustment()
        adjustment.connect("value-changed", self._thread_scrolled)
        adjustment.connect("notify::upper", self._thread_resized)
        adjustment.connect("notify::page-size", self._thread_resized)
        self.body.append(self.scroller)
        layered = Gtk.Overlay(child=self.light, hexpand=True, vexpand=True)
        layered.add_overlay(self.body)
        layered.set_measure_overlay(self.body, True)
        self.empty = EmptyState('Your messages, together',
                                'Link Google Messages, or connect with your Luma friends.',
                                icons.icon_name('message-square'),
                                primary=('Link Google Messages', self._link_google),
                                secondary=('Choose a Luma username', self._open_luma_account))
        self.empty.set_name('msg-empty')
        layered.add_overlay(self.empty)
        self.island.append(layered)
        self.host = ToastHost(self.island)
        self.center = ActionCenter()
        self.center.set_name("msg-bar")
        self.center.attach(self.host, over="frame")
        self.center.connect("state-changed", self._center_state_changed)
        self.content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)
        self.content.append(self.host)
        self.content.append(self.details)
        self.details.connect("notify::shown", lambda *_a: self._sync_details_gap())
        self.details.sheet.connect("notify::visible", lambda *_a: self._sync_details_gap())
        self.details.sheet.connect("notify::parent", lambda *_a: self._sync_details_gap())
        self._sync_details_gap()
        self.split = IslandSplitView(collapsed=False, show_content=True)
        self.split.set_sidebar_width_unit(Adw.LengthUnit.PX)
        self.split.set_min_sidebar_width(292)
        self.split.set_max_sidebar_width(292)
        # v71 phone: the list's foot is an action bar (Search, New message as the key) in the
        # sidebar's own layer; the desktop keeps the sidebar foot.
        self.sidebar_host = ToastHost(self.sidebar)
        self.list_center = ActionCenter()
        self.list_center.set_name("msg-list-bar")
        self.list_center.attach(self.sidebar_host, over="frame")
        self.split.set_sidebar(Adw.NavigationPage(child=self.sidebar_host, title="Messages"))
        self.split.set_content(Adw.NavigationPage(child=self.content, title="Conversation"))
        self.owner.set_body(self.split)
        # v71 phone: the title island replaces the header. ‹ (Conversations) | who | Call, Video;
        # tap who and it grows into the conversation's options (luma-next-71 msgHead, .misl).
        self.title_island = TitleIsland(lead="back", lead_label="Conversations",
                                        on_lead=self._show_conversations,
                                        grow=self._island_body, grows="details")
        self.title_island.set_name("msg-title-island")
        self.title_island.title_button.set_name("msg-island-who")
        self.title_island.lead_button.set_name("msg-island-back")
        self.island_call = self.title_island.add_trailing(TitleIsland.button("phone", "Call", self._call))
        self.island_call.set_name("msg-island-call")
        self.island_video = self.title_island.add_trailing(TitleIsland.button("video", "Video call", self._video))
        self.island_video.set_name("msg-island-video")
        self.title_island.float_over(self.owner)
        self.title_island.connect("grown", lambda _island, grown: self._island_grown(grown))
        self.split.connect("notify::show-content", lambda *_a: self._sync_island())
        self.split.connect("notify::collapsed", lambda *_a: self._sync_island())
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 899px"))
        narrow.add_setter(self.split, "min-sidebar-width", 76.0)
        narrow.add_setter(self.split, "max-sidebar-width", 76.0)
        self.owner.add_breakpoint(narrow)
        # v71's compact window hides the list at720, before the phone chrome
        # begins at560. Keep enough room for the thread and its Details pane.
        compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720px"))
        compact.add_setter(self.split, "collapsed", True)
        compact.add_setter(self.back, "visible", True)
        compact.add_setter(self.corner.controls["actions.0"], "visible", False)
        self.owner.add_breakpoint(compact)
        phone = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 559px"))
        phone.add_setter(self.split, "collapsed", True)
        phone.add_setter(self.split, "min-sidebar-width", 292.0)
        phone.add_setter(self.split, "max-sidebar-width", 292.0)
        phone.add_setter(self.history, "margin-start", 16)
        phone.add_setter(self.history, "margin-end", 16)
        phone.add_setter(self.header, "visible", False)
        phone.add_setter(self.history, "margin-top", 58 + self.owner.status_inset)
        # v71's phone header is the title island: ‹ | who | Call, Video. Pin and
        # Details move into the island's grown options (tap the name).
        phone.add_setter(self.corner.controls["states.0"], "visible", False)
        phone.add_setter(self.corner.controls["info"], "visible", False)
        self.owner.add_breakpoint(phone)
        self._phone_breakpoint = phone
        self.owner.connect("notify::current-breakpoint", self._adapt_to_width)
        self.render_sidebar()
        first = self.fixture.document.get("selected") if self.fixture else None
        entries = self._entries()
        if first is None and entries:
            first = entries[0]["id"]
        if first is not None:
            self.open(first)
        else:
            self.render_thread()
            self._composer()

    def _link_google(self) -> None:
        if self.fixture:
            return
        self.owner._show_accounts()
        from .messages_accounts import available_networks, network
        item = network('gmessages')
        if item in available_networks(self.owner.helper_directory):
            self.owner.accounts_dialog._show_network(item)
        else:
            self.owner.accounts_dialog._show_networks()

    def _open_luma_account(self) -> None:
        if not self.fixture:
            self.owner._show_luma_profile()

    def _entries(self) -> list[dict]:
        if self.fixture:
            data = self.fixture.document
            people = data["people"]
            result = []
            for conversation in data["conversations"]:
                key = conversation.get("person")
                person = people.get(key, {})
                name = conversation.get("name") or person.get("name") or conversation["address"]
                last = conversation["messages"][-1]
                from .messages_fixture import preview
                result.append({"id": conversation["id"], "name": name,
                               "address": conversation["address"], "record": None,
                               "service": None, "person": key, "group": conversation.get("group", ()),
                               "group_people": people,
                               "search_text": " ".join((name, person.get("handle", ""),
                                                        *(message.get("text", "") for message in conversation["messages"])
                                                        )).casefold(),
                               "sms": conversation.get("sms", False), "unread": conversation.get("unread", 0),
                               "pinned": conversation.get("pinned", False), "muted": conversation.get("muted", False),
                               "preview": "• • •" if conversation.get("typing") else
                                          preview(last, people, group=bool(conversation.get("group"))),
                               "when": (("SMS · " if conversation.get("sms") else "") +
                                        (last.get("at", "") if last.get("day") in (None, "Today") else last["day"])),
                               "hue": conversation.get("hue", person.get("hue")), "meta": conversation})
            return sorted(result, key=lambda entry: not entry["pinned"])
        result = []
        from .messages_backend import normalize_address
        contacts_by_phone = {}
        contacts_by_handle = {}
        for contact in self.owner.contacts:
            if contact.phone:
                try:
                    contacts_by_phone[normalize_address(contact.phone)] = contact
                except ValueError:
                    pass
            if contact.handle:
                contacts_by_handle[contact.handle.lstrip("@").casefold()] = contact
        try:
            preferences = self.preferences.all_flags()
        except (OSError, ValueError):
            preferences = {}
        for service in self.owner.services:
            for record in service.store.threads(self.query):
                flags = self.owner._luma_flags(service, record)
                pref = preferences.get(service.key(record.address), {})
                try:
                    phone_key = normalize_address(record.address)
                except ValueError:
                    phone_key = None
                contact = (contacts_by_phone.get(phone_key) if phone_key else None) or contacts_by_handle.get(
                    record.address.lstrip("@").casefold())
                person_data = ({"name": contact.name, "handle": contact.handle,
                                "phone": contact.phone, "photo_bytes": contact.photo,
                                "hue": contact.hue} if contact else {})
                group = self.owner._is_group(service, record)
                reader = getattr(service.store, "conversation_participants", None)
                participants = reader(record.address) if group and callable(reader) else ()
                group_people = _unique_participants(participants)
                result.append({"id": service.key(record.address), "name": record.display_name,
                               "address": record.address, "record": record, "service": service,
                               "person": None, "person_data": person_data,
                               "group": tuple(group_people), "is_group": group,
                               "group_people": group_people,
                               "sms": service.native, "unread": record.unread, "pinned": pref.get("pinned", False),
                               "muted": pref.get("muted", False), "preview": " ".join(record.preview.split()),
                               "request": bool(flags.get("request")),
                               "when": datetime.fromtimestamp(record.updated).strftime("%-I:%M %p") if record.updated else "",
                               "hue": person_data.get("hue"), "meta": flags})
        if self.pending:
            if any(entry["id"] == self.pending["id"] for entry in result):
                self.pending = None
            else:
                result.append(self.pending)
        return sorted(result, key=lambda entry: (
            not entry["pinned"], -(entry["record"].updated if entry["record"] else 0)))

    def _asset(self, relative: str | None) -> Path | None:
        return self.fixture.asset(relative) if self.fixture and relative else None

    def _person(self, key: str | None) -> dict:
        if self.fixture:
            return self.fixture.people.get(key, {})
        if key is None and self.current:
            return self.current.get("person_data", {})
        return self.current.get("group_people", {}).get(key, {}) if self.current else {}

    def _face(self, name: str, key: str | None, size: int, person: dict | None = None) -> PersonAvatar:
        person = person if person is not None else self._person(key)
        return PersonAvatar(name, size, picture=_picture(person.get("photo_bytes") or
                                                    self._asset(person.get("photo"))),
                            hue=person.get("hue"))

    def _conversation_face(self, entry: dict, size: int) -> Gtk.Widget:
        if len(entry["group"]) < 2:
            return self._face(entry["name"], entry["person"], size, entry.get("person_data"))
        people = [(entry["group_people"].get(key, {}).get("name", key),
                   _picture(self._asset(entry["group_people"].get(key, {}).get("photo"))))
                  for key in entry["group"]]
        return GroupFace(people, size=size)

    def _sync_details_gap(self) -> None:
        # DetailsPane owns the shared side-sheet gutter. Adding a box gap as
        # well doubles the visible separation; the empty/drawer slot needs none.
        self.content.set_spacing(0)

    def render_sidebar(self) -> None:
        entries = self._entries()
        if self.fixture and self.query:
            entries = [entry for entry in entries if self.query.casefold() in entry["search_text"]]
        self._entry_by_id = {entry["id"]: entry for entry in entries}
        self.sidebar.clear()
        self._rows.clear()
        # v71: pinned conversations sort first and carry the pin mark; the list
        # has no Pinned / All Conversations headings (_entries sorts them).
        for entry in entries:
            self._append_sidebar_entry(entry)
        if self.query and not entries:
            hint = _label(f'No one matches “{self.query}”.', "caption", wrap=True)
            hint.set_margin_start(12)
            hint.set_margin_end(12)
            hint.set_margin_top(12)
            hint.set_name("msg-no-results")
            self.sidebar.list.append(Gtk.ListBoxRow(child=hint, selectable=False, activatable=False))
        elif not entries:
            self.sidebar.list.append(Gtk.ListBoxRow(child=ListEmptyState('No conversations yet'),
                                                   selectable=False, activatable=False))
        # A phone's list-first page shows no selected row (v71 `.win.phone #m-side .crow.on`).
        if self.current and self.current["id"] in self._rows and not self._phone_mode:
            self.sidebar.list.select_row(self._rows[self.current["id"]])

    def _append_sidebar_entry(self, entry: dict) -> None:
        if len(entry["group"]) >= 2:
            people = [(entry["group_people"].get(key, {}).get("name", key),
                       _picture(self._asset(entry["group_people"].get(key, {}).get("photo"))))
                      for key in entry["group"]]
            lead = RowLead.group(people)
        else:
            person = (self._person(entry["person"]) if self.fixture else
                      entry.get("person_data", {}))
            lead = RowLead.face(entry["name"], picture=_picture(person.get("photo_bytes") or
                                                                 self._asset(person.get("photo"))),
                                online=person.get("online", False))
        row = SidebarRow(entry["name"], lead=lead,
                         subtitle=("Message request · " if entry.get("request") else "") + entry["preview"],
                         meta=entry["when"],
                         attention=bool(entry["unread"] or entry.get("request")),
                         trail=entry["unread"] or (1 if entry.get("request") else None),
                         pinned=entry["pinned"] and not bool(entry["unread"]), muted=entry["muted"])
        row.set_name("msg-row-" + entry["id"].replace("|", "-"))
        row.entry_id = entry["id"]
        # v71 phone swipe rows: right = Pin (yellow), left = Mute (blue); the row's
        # own actions stay in the island and the details pane.
        line = row.get_child()
        if line is not None:
            row.set_child(None)
            row.set_child(SwipeRow(
                line,
                start=SwipeAction("pin", "yellow", lambda _r, key=entry["id"]: self._swiped(key, "pinned"),
                                  label="Unpin" if entry["pinned"] else "Pin"),
                end=SwipeAction("bell" if entry["muted"] else "bell-off", "blue",
                                lambda _r, key=entry["id"]: self._swiped(key, "muted"),
                                label="Unmute" if entry["muted"] else "Mute")))
        self.sidebar.list.append(row)
        self._rows[entry["id"]] = row

    def _search(self, value: str) -> None:
        self.query = value
        self.render_sidebar()

    def _row_activated(self, _box, row: Gtk.ListBoxRow) -> None:
        key = getattr(row, "entry_id", None)
        if key:
            self._navigated = True
            self.open(key)

    def _phone_open_current(self) -> None:
        if self._phone_mode and self.current is not None:
            self._navigated = True
            self.open(self.current["id"])

    def _list_bar(self) -> None:
        if not self._phone_mode:
            self.list_center.hide_bar()
            self.foot.set_visible(True)
            self.foot.add_button.set_name("msg-new")
            return
        self.foot.set_visible(False)
        self.foot.add_button.set_name("msg-foot-new")
        self.list_center.show_bar([
            BarSearch("Search", text=self.query, keep=True, on_change=self._search),
            BarAction("square-pen", tooltip="New message", primary=True, on_activate=self._phone_new),
        ], fill=True)
        new = self.list_center.bar_row.get_last_child()
        if new is not None:
            new.set_name("msg-new")

    def _phone_new(self) -> None:
        """v71 phone New message: suggestions above, the To: field as the bar's row, with ✕."""
        if self.list_center.grown == "new":
            self.list_center.fold_panel()
            return
        self._new_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._new_rows.set_name("msg-new-panel")
        self._new_field = PanelField("user", "To: name, username or phone", on_change=self._fill_new_rows,
                                     on_submit=lambda _text: self._pick_first_recipient())
        self._new_field.set_name("msg-new-search")
        self._fill_new_rows("")
        self.list_center.grow("new", self._new_rows, entry=self._new_field)

    def _fill_new_rows(self, query: str) -> None:
        rows = self._new_rows
        if rows is None:
            return
        while child := rows.get_first_child():
            rows.remove(child)
        rows.append(_label("New message", "label"))
        rows.append(_label("Find Luma friends by @username. Phone numbers match your contacts or send texts.",
                           "caption", wrap=True))
        self._new_candidates = self._recipients(query, limit=4 if self.fixture else 20)
        for candidate in self._new_candidates:
            row = PanelRow(candidate["name"], lead=self._face(candidate["name"], candidate["key"], 36),
                           subtitle=candidate["subtitle"],
                           on_activate=lambda chosen=candidate: self._pick_recipient(chosen))
            row.add_css_class("messages-recipient")
            rows.append(row)
        own = PanelRow("Your Luma username", icon="scan", subtitle="Show your code or share your link",
                       on_activate=self._show_own_code)
        own.set_name("msg-own-code")
        rows.append(own)

    def _recipients(self, query: str, *, limit: int = 20) -> list[dict]:
        """Who New message offers: Studio's people in the fixture; contacts, a Luma @handle or a
        typed number live (the controller resolves and opens them)."""
        query = query.casefold().strip()
        found: list[dict] = []
        if self.fixture:
            for key in ("TH", "AR", "CL", "NF", "SK"):
                person = self._person(key)
                name = person.get("name", key)
                if query and query not in (name + " " + person.get("handle", "")).casefold():
                    continue
                found.append({"name": name, "key": key, "address": "@" + person.get("handle", ""),
                              "handle": None, "subtitle": ("@" + person["handle"]) if person.get("handle")
                              else person.get("phone", "")})
            return found[:limit]
        from . import messages_luma as luma

        handle = luma.handle_from(query) if luma.looks_like_handle(query) else None
        if handle and self.owner._luma_service() is not None:
            found.append({"name": "@" + handle, "key": None, "address": "@" + handle, "handle": handle,
                          "subtitle": "Luma · End-to-end encrypted"})
        contacts = tuple(contact for contact in self.owner.contacts if contact.phone and
                         (not query or query in contact.name.casefold() or query in contact.phone or
                          query in contact.organization.casefold()))[:limit]
        for contact in contacts:
            found.append({"name": contact.name, "key": None, "address": contact.phone, "handle": None,
                          "subtitle": (contact.organization + " · " if contact.organization else "") + contact.phone})
        if query:
            try:
                from .messages_backend import normalize_address
                direct = normalize_address(query)
            except ValueError:
                direct = None
            if direct and not any(contact.phone == direct for contact in contacts):
                found.append({"name": direct, "key": None, "address": direct, "handle": None,
                              "subtitle": "Phone number"})
        return found[:limit]

    def _pick_first_recipient(self) -> None:
        if getattr(self, "_new_candidates", None):
            self._pick_recipient(self._new_candidates[0])

    def _pick_recipient(self, chosen: dict) -> None:
        self.list_center.fold_panel()
        if self.fixture:
            # An existing conversation opens directly (v71 data-mcvgo).
            match = next((entry for entry in self._entries() if entry.get("person") == chosen["key"]
                          and not entry["group"]), None)
            if match is not None:
                self._navigated = True
                self.open(match["id"])
            else:
                self._notice("Starting a conversation with " + chosen["name"])
            return
        from types import SimpleNamespace
        row = SimpleNamespace(address=chosen["address"], display_name=chosen["name"],
                              luma_handle=chosen["handle"], person_key=chosen["key"],
                              luma_service=self.owner._luma_service() if chosen["handle"] else None)
        self._navigated = True
        self.owner._recipient_activated(None, row)

    def open(self, key: str) -> None:
        entry = self._entry_by_id.get(key)
        if entry is None:
            return
        self._remember_draft()
        self.current = entry
        self.selected_message = None
        self.reply = None
        self.details.show(open=False, subject=entry["id"])
        if self.fixture:
            self.fixture.mark_read(entry["address"])
            entry["meta"]["unread"] = 0
        else:
            self.owner._open_thread(entry["record"], reveal=True, service=entry["service"])
        self._present_entry(entry)
        self.render_thread()
        self.render_details()
        self._composer()
        self.render_sidebar()
        self.split.set_show_content(True)
        if self._forward_text and self.bar_entry is not None:
            self.bar_entry.set_text(self._forward_text)
            self._drafts[entry["id"]] = self._forward_text
            self._forward_text = None

    def _present_entry(self, entry: dict) -> None:
        self.header.remove(self.identity)
        self.identity = self._identity(entry["name"], self._subtitle(entry), entry)
        self.identity.set_name("msg-identity")
        self.header.insert_child_after(self.identity, self.back)
        self.title_island.set_title(entry["name"], self._subtitle(entry))
        self.title_island.set_faces(self._conversation_face(entry, 30))
        if self.title_island.grown:
            self.title_island.grow_into(self._island_body())
        self._name_detail_controls()
        pin = self.corner.controls["states.0"]
        label = "Unpin" if entry["pinned"] else "Pin"
        if pin.get_active() != entry["pinned"]:
            pin.set_active(entry["pinned"])
        pin.set_tooltip_text(label)
        pin.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self.corner.controls["info"].update_property([Gtk.AccessibleProperty.LABEL], ["Details"])
        light_key = entry["person"] or (entry["group"][0] if entry["group"] else None)
        person = self._person(light_key)
        self.light.set_source(name=entry["name"], hue=entry.get("hue"),
                              picture=_picture(self._asset(person.get("light")) if self.fixture else
                                               person.get("photo_bytes")),
                              focus=tuple(person.get("focus", (0.5, 0.5))))

    def _name_detail_controls(self) -> None:
        # One "msg-info" in the tree: the corner's Details on a desktop, the
        # title island's who button on a phone, where it grows.
        island = getattr(self, "title_island", None)
        self.corner.controls["info"].set_name("msg-corner-info" if self._phone_mode else "msg-info")
        if island is not None:
            island.title_button.set_name("msg-info" if self._phone_mode else "msg-island-who")
        self._sync_island()

    def _sync_island(self) -> None:
        # The island floats over the window: only while a conversation is the page.
        island = getattr(self, "title_island", None)
        if island is None:
            return
        on_thread = self.current is not None and (not self.split.get_collapsed() or self.split.get_show_content())
        # The conversation light paints behind the physical phone clock.
        # The thread itself reserves the same status inset in its top padding.
        self.owner.set_phone_bleed(self._phone_mode and on_thread)
        if not on_thread and island.grown:
            island.fold()
        if self._phone_mode:
            island.set_visible(on_thread)

    def _island_grown(self, grown: bool) -> None:
        if grown and self.details.shown:
            self.details.close()

    def _island_body(self) -> Gtk.Widget | None:
        """The conversation, grown out of the title island (v71 .mislp): its options as tiles,
        who is in it (groups), the encryption facts inset, then its photos and files.
        Nothing repeats the island's own row."""
        entry = self.current
        if entry is None:
            return None
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body.add_css_class("messages-island-body")
        body.set_name("msg-island-body")
        group = bool(entry["group"] or entry.get("is_group"))
        options = [
            BarTile("pin", "Pinned" if entry["pinned"] else "Pin", self._pin, on=entry["pinned"], closes=False),
            BarTile("bell-off" if entry["muted"] else "bell", "Muted" if entry["muted"] else "Mute",
                    self._mute, on=entry["muted"], closes=False),
            BarTile("search", "Search", self._search_conversation),
        ]
        if not group:
            options.append(BarTile("user", "Contact", lambda: self._open_contact(entry["name"])))
        tiles = BarTiles(options, columns=len(options), chip=True)
        for button, name in zip(tiles.buttons, ("pin", "mute", "search", "contact")):
            button.set_name("msg-island-" + name)
        body.append(tiles)
        if entry["group"]:
            members = list(entry["group"])
            heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            heading.append(_label("People", "label"))
            heading.append(_label(str(len(members) + (1 if self.fixture else 0)), "small"))
            body.append(heading)
            people = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            for key in members:
                person = self._person(key)
                name = person.get("name", key)
                people.append(DetailsRow(name, "@" + person["handle"] if person.get("handle") else "",
                                         lead=self._face(name, key, 34),
                                         on_activate=lambda who=name: self._open_contact(who)))
            if self.fixture:
                people.append(DetailsRow("You", "@nick", lead=self._face("Nick", "me", 34)))
            body.append(people)
        facts = Card(recessed=True)
        facts.set_orientation(Gtk.Orientation.VERTICAL)
        facts.add_css_class("messages-island-facts")
        for key, value in self._encryption_facts(entry):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.add_css_class("messages-island-fact")
            row.append(_label(key, "body", hexpand=True))
            shown = _label(value, "body")
            shown.set_xalign(1)
            apply_type(shown, "body", weight=600)
            row.append(shown)
            facts.append(row)
        body.append(facts)
        photos = self._conversation_photos(4)
        if photos:
            body.append(_label("Photos and files", "label"))
            body.append(DetailsPhotos(photos, columns=4))  # v71 .mislph: an even four-up grid
        return body

    def _encryption_facts(self, entry: dict) -> list[tuple[str, str]]:
        if entry["sms"]:
            return [("Encryption", "Text message, not encrypted")]
        if self.fixture:
            return [("Encryption", "End-to-end"), ("Safety code", "48213 90552 17608")]
        from .messages_luma import is_luma
        return [("Encryption", "End-to-end" if is_luma(entry["service"]) else "Provider managed")]

    def _conversation_photos(self, limit: int) -> list:
        entry = self.current
        if entry is None:
            return []
        messages = self._messages()
        if self.fixture:
            photos = [self._asset(message.get("photo")) for message in messages if message.get("kind") == "photo"]
            photos.extend(self._asset(path) for path in entry["meta"].get("gallery", ()))
            return [path for path in photos if path][:limit]
        photos = []
        for message in reversed(messages):
            for attachment in message.get("attachments", ()):
                if len(photos) >= limit:
                    return photos
                if not attachment.content_type.startswith("image/"):
                    continue
                try:
                    path = entry["service"].store.attachment_path(attachment)
                except (AttributeError, OSError, ValueError):
                    continue
                if path is not None:
                    photos.append(path)
        return photos

    def _search_conversation(self) -> None:
        self.title_island.fold()
        self._notice("Search in this conversation")

    def _subtitle(self, entry: dict) -> str:
        typing = entry.get("meta", {}).get("typing")
        if typing:
            return self._person(typing).get("name", typing).split()[0] + " is typing…"
        if entry["sms"]:
            return "Text message"
        if entry.get("is_group") or entry["group"]:
            return (f"{len(entry['group']) + (1 if self.fixture else 0)} people"
                    if entry["group"] else "Group conversation")
        person = self._person(entry["person"])
        if person.get("online"):
            return "Active now"
        if person.get("handle"):
            return "@" + person["handle"]
        return (person.get("phone") or getattr(entry.get("service"), "label", "")) if not self.fixture else ""

    def _identity(self, name: str, subtitle: str, entry: dict | None) -> CornerPill:
        button = Gtk.Button(has_frame=False)
        button.update_property([Gtk.AccessibleProperty.LABEL], ["Details"])
        button.set_tooltip_text("Details")
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10,
                       margin_start=2, margin_end=8)
        line.append(self._conversation_face(entry, 36) if entry else self._face(name, None, 36))
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        title = _label(name, "body", ellipsize=Pango.EllipsizeMode.END)
        apply_type(title, "body", weight=650)
        labels.append(title)
        description = _label(subtitle, "caption", ellipsize=Pango.EllipsizeMode.END)
        apply_type(description, "caption", weight=400)
        labels.append(description)
        line.append(labels)
        button.set_child(line)
        button.connect("clicked", lambda _button: self.details.toggle())
        pill = CornerPill(people=button)
        pill.set_halign(Gtk.Align.START)
        pill.who_button = button
        return pill

    def _messages(self) -> list[dict]:
        if not self.current:
            return []
        entry = self.current
        if self.fixture:
            return list(entry["meta"]["messages"])
        service = entry["service"]
        result = []
        try:
            pinned_messages = self.preferences.pinned_messages(entry["id"])
        except (OSError, ValueError):
            pinned_messages = set()
        for record in self.owner._thread_messages(entry["address"], service):
            sender = getattr(service.provider, "sender", None)
            from_name = sender(record.uid) if record.direction == "incoming" and callable(sender) else ""
            reactions: dict[str, list[str]] = {}
            read_reactions = getattr(service.store, "reactions", None)
            if callable(read_reactions):
                for reaction in read_reactions(record.uid):
                    if reaction.state in {"sent", "received"}:
                        reactions.setdefault(reaction.emoji, []).append(reaction.sender)
            quote = record.quote
            result.append({"id": record.uid, "from": "me" if record.direction == "outgoing" else from_name,
                           "text": record.body, "at": datetime.fromtimestamp(record.timestamp).strftime("%-I:%M %p"),
                           "day": datetime.fromtimestamp(record.timestamp).strftime("%b %-d"),
                           "status": record.state if record.direction == "outgoing" else "",
            "attachments": record.attachments, "record": record,
            "media": service.store.media_parts(record.uid) if hasattr(service.store, "media_parts") else (),
                           "pinned": record.uid in pinned_messages,
                           "reactions": reactions,
                           "reply": quote.message_uid if quote else None,
                           "quote": {"from": quote.author, "text": quote.body} if quote else None})
        return result

    def render_thread(self, *, to_end: bool = False) -> None:
        self._dismiss_quick()
        selected = self.current is not None
        self.empty.set_visible(not selected)
        self.header.set_visible(selected and not self._phone_mode)
        self.scroller.set_visible(selected)
        self.title_island.set_visible(selected and self._phone_mode)
        # A redraw while the thread is still settling at its end (before its first
        # full allocation, its scroll value is still 0) keeps following the end.
        following = self._end_pending or self._stick_end
        if self._scroll_source:
            GLib.source_remove(self._scroll_source)
            self._scroll_source = 0
        self._end_pending = False
        thread_id = str(self.current["id"]) if self.current else None
        retained_scroll = None
        if not to_end and not following and thread_id is not None and thread_id == self._rendered_thread_id:
            adjustment = self.scroller.get_vadjustment()
            end = max(0, adjustment.get_upper() - adjustment.get_page_size())
            if end - adjustment.get_value() > 32:
                retained_scroll = adjustment.get_value()
        self._message_rows.clear()
        self._message_bubbles.clear()
        self._columns.clear()
        self._photos.clear()
        self._photo_clamps.clear()
        self._links.clear()
        while child := self.history.get_first_child():
            self.history.remove(child)
        if not self.current:
            self._rendered_thread_id = None
            return
        entry = self.current
        intro = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER,
                        spacing=6, margin_top=26, margin_bottom=30)
        intro.set_name("msg-intro")
        if entry["group"]:
            faces = Gtk.Fixed(width_request=188, height_request=56,
                              halign=Gtk.Align.CENTER)
            members = (*entry["group"], "me") if self.fixture else entry["group"]
            for index, key in enumerate(members):
                name = "Nick" if key == "me" else entry["group_people"].get(key, {}).get("name", key)
                faces.put(self._face(name, key, 56), index * 44, 0)
            intro.append(faces)
        else:
            intro.append(self._conversation_face(entry, 88))
        title = _label(entry["name"], "intro", halign=Gtk.Align.CENTER)
        title.set_margin_top(10)
        intro.append(title)
        person = self._person(entry["person"])
        subtitle = ((", ".join(entry["group_people"].get(key, {}).get("name", key).split()[0]
                                for key in entry["group"]) + (" and you" if self.fixture else "")) if entry["group"] else
                    ("@" + person["handle"] if person.get("handle") else person.get("phone", "")))
        if subtitle:
            intro.append(_label(subtitle, "body", halign=Gtk.Align.CENTER))
        security = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.CENTER,
                           spacing=6, margin_top=10)
        security.append(icons.image("message-square" if entry["sms"] else "lock"))
        if entry["sms"]:
            security.append(_label("Text messages aren’t encrypted.", "small"))
            invite = Gtk.Button(label=f"Invite {entry['name']} to Luma", has_frame=False)
            invite.connect("clicked", lambda _button: self._invite_to_luma())
            security.append(invite)
        else:
            from .messages_luma import is_luma
            encryption_note = ("End-to-end encrypted. Only the people here can read this."
                               if self.fixture or is_luma(entry["service"]) else
                               "Encryption is managed by this service.")
            security.append(_label(encryption_note, "small", wrap=True))
        intro.append(security)
        self.history.append(intro)
        messages = self._messages()
        previous = None
        current_day = None
        run: MessageRun | None = None
        for index, item in enumerate(messages):
            day = item.get("day")
            day_changed = bool(day and day != current_day)
            if day_changed:
                separator = _label(day, "caption", halign=Gtk.Align.CENTER)
                self.history.append(separator)
                current_day = day
            mine = item.get("from") == "me"
            same_above = previous is not None and previous.get("from") == item.get("from") and not day_changed
            next_item = messages[index + 1] if index + 1 < len(messages) else None
            same_below = (next_item is not None and next_item.get("from") == item.get("from") and
                          not (next_item.get("day") and next_item["day"] != current_day))
            if entry["group"] and not mine and not same_above:
                sender = _label(self._sender_name(item).split()[0], "label")
                sender.set_margin_start(48)
                sender.set_margin_top(10)
                sender.set_margin_bottom(1)
                lumaui.hue_tint(sender, self._person(item["from"]).get("hue"))
                self.history.append(sender)
            if run is None or not same_above:
                # v71: one sender's consecutive messages are one block (MessageRun shapes the corners).
                run = MessageRun(mine=mine)
                if mine or not entry["group"]:
                    run.set_margin_top(10)
                run.set_name("msg-run-" + str(item["id"]))
                self.history.append(run)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                          halign=Gtk.Align.END if mine else Gtk.Align.START,
                          spacing=8 if entry["group"] and not mine else 0)
            row.set_margin_top(0)
            row.set_name("msg-message-" + str(item["id"]))
            self._message_rows[str(item["id"])] = row
            if entry["group"] and not mine and not same_below:
                face = self._face(self._sender_name(item), item.get("from"), 28)
                face.set_valign(Gtk.Align.END)
                row.append(face)
            elif entry["group"] and not mine:
                row.append(Gtk.Box(width_request=28))
            content = self._message_content(item)
            body = item.get("text") or None
            # Real stores can contain an empty message record. The previous
            # view still represented it as an empty bubble; keep that record
            # visible without passing two absent values to MessageBubble.
            if content is None and body is None:
                content = _label("Message unavailable", "caption")
            selected = bool(self.selected_message and self.selected_message["id"] == item["id"])
            card = content.get_first_child() if isinstance(content, Gtk.Box) else None
            if (item.get("kind") in {"file", "event", "song", "place"} and card is not None
                    and card.get_next_sibling() is None and not body):
                # These shared cards already own their surface and run corners.
                content.remove(card)
                bubble = card
                if selected:
                    bubble.add_css_class("selected")
            else:
                bubble = MessageBubble(body if content is None else None, mine=mine,
                                       joined_above=same_above, joined_below=same_below,
                                       selected=selected, child=content, hue=entry.get("hue"),
                                       padded=bool(item.get("kind") == "voice" or item.get("reply") or item.get("quote") or item.get("link")))
            bubble.set_name("msg-bubble-" + str(item["id"]))
            self._message_bubbles[str(item["id"])] = bubble
            # Keep activation on the actual bubble: a button around it adds
            # padding and expands the hover target across the whole column.
            click = Gtk.GestureClick()
            click.connect("released", lambda _gesture, _count, x, y, message=item, target=bubble:
                          self._bubble_clicked(target, message, x, y))
            bubble.add_controller(click)
            bubble.set_focusable(True)
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", lambda _keys, key, _code, _state, message=item:
                         self._message_key(key, message))
            bubble.add_controller(keys)
            hover = Gtk.EventControllerMotion()
            hover.connect("enter", lambda _motion, x, y, button=bubble, message=item, own=mine:
                          self._show_quick(button, message, own, x, y))
            hover.connect("motion", lambda _motion, x, y, button=bubble:
                          self._dismiss_quick() if self._over_bubble_control(button, x, y) else None)
            hover.connect("leave", lambda *_args: self._hide_quick_soon())
            bubble.add_controller(hover)
            activation = Gtk.Button(visible=False)
            activation.set_name("msg-select-" + str(item["id"]))
            activation.connect("clicked", lambda _b, message=item: self._select_message(message))
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             halign=Gtk.Align.END if mine else Gtk.Align.START)
            column.set_hexpand(False)
            column.append(activation)
            if item.get("reactions"):
                reaction_flow = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                reaction_flow.append(bubble)
                # A 24px reaction overlaps the bubble by 6px and contributes
                # its remaining 18px to the thread flow, as in v71.
                reaction_flow.append(Gtk.Box(height_request=18))
                overlay = Gtk.Overlay(child=reaction_flow)
                reaction = self._reactions(item)
                overlay.add_overlay(reaction)
                overlay.set_clip_overlay(reaction, False)
                column.append(overlay)
            else:
                column.append(bubble)
            max_width = 300 if self._phone_mode else 520
            clamp = Adw.Clamp(maximum_size=max_width, tightening_threshold=max_width,
                              child=column, halign=Gtk.Align.END if mine else Gtk.Align.START)
            self._columns.append(clamp)
            row.append(clamp)
            run.append(row, shape=bubble)
            if mine and item.get("status") and not same_below:
                receipt = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.END)
                receipt.set_name("msg-receipt-" + str(item["id"]))
                if item["status"] in {"read", "delivered", "sent"}:
                    mark = icons.image("check-check" if item["status"] in {"read", "delivered"} else "check")
                    lumaui.hue_tint(mark, entry.get("hue"))
                    receipt.append(mark)
                receipt.append(_label(item["status"].capitalize() + " " + item.get("at", ""), "small"))
                self.history.append(receipt)
            previous = item
        typing = entry.get("meta", {}).get("typing")
        if typing:
            dots = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            dots.add_css_class("messages-typing")
            dots.set_name("msg-typing")
            for _ in range(3):
                dot = Gtk.Box()
                dot.add_css_class("messages-typing-dot")
                dots.append(dot)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.START)
            if entry["group"]:
                row.append(self._face(self._person(typing).get("name", typing), typing, 28))
            row.append(MessageBubble(child=dots))
            self.history.append(row)
        self._rendered_thread_id = thread_id
        if retained_scroll is not None:
            # Rebuilt rows do not have their final bounds until the next
            # allocation; restoring on idle can be undone by that allocation.
            self._scroll_source = GLib.timeout_add(100, self._finish_restore_scroll, retained_scroll)
        else:
            self._stick_end = True
            GLib.idle_add(self._scroll_if_following)
            # The phone drawer and rich cards can finish allocating after the
            # first idle. Follow the end only on a new thread or when the
            # reader was already at the bottom before the redraw.
            self._scroll_source = GLib.timeout_add(100, self._finish_scroll_to_end)
            self._end_pending = True

    def _finish_restore_scroll(self, value: float) -> bool:
        self._scroll_source = 0
        self._stick_end = False
        adjustment = self.scroller.get_vadjustment()
        adjustment.set_value(min(max(0, value), max(0, adjustment.get_upper() - adjustment.get_page_size())))
        return GLib.SOURCE_REMOVE

    def _finish_scroll_to_end(self) -> bool:
        self._scroll_source = 0
        self._end_pending = False
        return self._scroll_to_end()

    def _at_end(self) -> bool:
        adjustment = self.scroller.get_vadjustment()
        end = max(0, adjustment.get_upper() - adjustment.get_page_size())
        return self._end_pending or self._stick_end or end - adjustment.get_value() < 60

    def _follow_end(self) -> None:
        GLib.idle_add(self._scroll_if_following)
        if self._scroll_source:
            GLib.source_remove(self._scroll_source)
        self._scroll_source = GLib.timeout_add(100, self._finish_scroll_to_end)
        self._end_pending = True

    def _scroll_if_following(self) -> bool:
        # An allocation queued this idle before the reader moved up. Their
        # newer scroll intent wins over that older follow-the-end request.
        if self._stick_end or self._end_pending:
            self._scroll_to_end()
        return GLib.SOURCE_REMOVE

    def _scroll_to_end(self) -> bool:
        adjustment = self.scroller.get_vadjustment()
        adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        self._stick_end = True
        return GLib.SOURCE_REMOVE

    def _thread_scrolled(self, adjustment: Gtk.Adjustment) -> None:
        end = max(0, adjustment.get_upper() - adjustment.get_page_size())
        if end - adjustment.get_value() < 24:
            self._stick_end = True
        elif time.monotonic() - self._reader_scrolled_at < 0.6:
            self._stick_end = False
            self._end_pending = False
            if self._scroll_source:
                GLib.source_remove(self._scroll_source)
                self._scroll_source = 0

    def _thread_resized(self, *_args: object) -> None:
        # v70's desktop editor floats over the last message and places the thread itself.
        center = getattr(self, "center", None)
        if not self._stick_end or (not self._phone_mode and center is not None and center.state == "editor"):
            return
        GLib.idle_add(self._scroll_if_following)

    def _center_state_changed(self, _center: ActionCenter, state: str) -> None:
        if self._compose_scroll_source:
            GLib.source_remove(self._compose_scroll_source)
            self._compose_scroll_source = 0
        at_end = self._at_end()
        if state != "editor":
            self.history.set_margin_bottom(self._thread_foot())
            # On a phone the grown bar takes room from the thread (the kit's safe
            # area); a reader at the end, or a thread still settling there, stays there.
            if self._phone_mode and at_end:
                self._follow_end()
            return
        self._editor_scroll_origin = self.scroller.get_vadjustment().get_value()
        self._editor_was_at_end = at_end
        # v70 pads the thread by the expanded editor's height plus 30 px.
        # Wait for GTK's editor allocation before using that height.
        self._compose_scroll_source = GLib.timeout_add(60, self._settle_editor_scroll)

    def _settle_editor_scroll(self) -> bool:
        if self.center.state != "editor" or self.center.editor is None:
            self._compose_scroll_source = 0
            return GLib.SOURCE_REMOVE
        height = self.center.editor.get_height()
        if height <= 0:
            return GLib.SOURCE_CONTINUE
        self._compose_scroll_source = 0
        if not self._phone_mode:
            self.history.set_margin_bottom(height + 30)
        # v70 follows the end on a phone. Its desktop editor floats over the
        # last message and advances the visible conversation only 65 px.
        target = (None if self._phone_mode and self._editor_was_at_end else
                  self._editor_scroll_origin + (65 if self._editor_was_at_end else 0))
        if target is None:
            GLib.idle_add(self._scroll_if_following)
            finish, args = self._finish_scroll_to_end, ()
        else:
            GLib.idle_add(self._scroll_to_position, target)
            finish, args = self._finish_scroll_to_position, (target,)
        if self._scroll_source:
            GLib.source_remove(self._scroll_source)
        self._scroll_source = GLib.timeout_add(100, finish, *args)
        self._end_pending = target is None
        return GLib.SOURCE_REMOVE

    def _thread_foot(self) -> int:
        # A phone's one safe area (LumaUI) gives the thread its room above the bar;
        # a window keeps v70's measured room under the floating bar.
        return 0 if self._phone_mode else 69

    def _scroll_to_position(self, target: float) -> bool:
        adjustment = self.scroller.get_vadjustment()
        end = max(0, adjustment.get_upper() - adjustment.get_page_size())
        self._stick_end = end - target < 24
        adjustment.set_value(min(max(0, target), max(0, adjustment.get_upper() - adjustment.get_page_size())))
        return GLib.SOURCE_REMOVE

    def _finish_scroll_to_position(self, target: float) -> bool:
        self._scroll_source = 0
        return self._scroll_to_position(target)

    def _dismiss_quick(self) -> None:
        if self._quick_hide_source:
            GLib.source_remove(self._quick_hide_source)
            self._quick_hide_source = 0
        if self._quick_pop is not None:
            self._quick_pop.popdown()
            self._quick_pop.unparent()
            self._quick_pop = None

    def _hide_quick_soon(self) -> None:
        if self._quick_hide_source:
            GLib.source_remove(self._quick_hide_source)
        self._quick_hide_source = GLib.timeout_add(160, self._quick_hide)

    def _quick_hide(self) -> bool:
        self._quick_hide_source = 0
        self._dismiss_quick()
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _over_bubble_control(bubble: Gtk.Widget, x: float, y: float) -> bool:
        child = bubble.pick(x, y, Gtk.PickFlags.DEFAULT)
        while child is not None and child is not bubble:
            if isinstance(child, Gtk.Button):
                return True
            child = child.get_parent()
        return False

    def _show_quick(self, anchor: Gtk.Widget, item: dict, mine: bool,
                    x: float | None = None, y: float | None = None) -> None:
        if (self.selected_message is not None or
                (x is not None and y is not None and self._over_bubble_control(anchor, x, y))):
            return
        self._dismiss_quick()
        offered = ("❤️", "😂", "👍")
        if not self.fixture:
            supported = tuple(getattr(self.current["service"].provider, "reaction_emoji", ()) or ())
            offered = tuple(symbol for symbol in offered if symbol in supported)
        pop = Gtk.Popover(position=Gtk.PositionType.LEFT if mine else Gtk.PositionType.RIGHT,
                          has_arrow=False, autohide=False)
        pop.set_name("msg-quick-actions")
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        for emoji in offered:
            action = make_control(BarAction("", emoji, on_activate=lambda symbol=emoji: (
                self._dismiss_quick(), self._react_selected(item, symbol))), size="bubble")
            action.update_property([Gtk.AccessibleProperty.LABEL], ["React " + emoji])
            actions.append(action)
        actions.append(make_control(BarAction("reply", tooltip="Reply",
                                              on_activate=lambda: (self._dismiss_quick(), self._reply_to(item))),
                                    size="bubble"))
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_args: self._cancel_quick_hide())
        motion.connect("leave", lambda *_args: self._hide_quick_soon())
        actions.add_controller(motion)
        pop.set_child(actions)
        pop.set_parent(anchor)
        self._quick_pop = pop
        pop.popup()

    def _cancel_quick_hide(self) -> None:
        if self._quick_hide_source:
            GLib.source_remove(self._quick_hide_source)
            self._quick_hide_source = 0

    def _adapt_to_width(self, _window, _property) -> None:
        phone = self.owner.get_current_breakpoint() is self._phone_breakpoint
        if phone == self._phone_mode:
            return
        self._phone_mode = phone
        if self.center.state != "editor":
            self.history.set_margin_bottom(self._thread_foot())
        self._list_bar()
        if phone and not self._navigated:
            # List-first: a phone opens on the conversations, full screen.
            self.split.set_show_content(not bool(self._entries()))
        self._name_detail_controls()
        GLib.idle_add(lambda: (self._sync_island(), False)[1])
        self.history.set_margin_start(16 if phone else 26)
        self.history.set_margin_end(16 if phone else 26)
        max_width = 300 if phone else 520
        for clamp in self._columns:
            clamp.set_maximum_size(max_width)
            clamp.set_tightening_threshold(max_width)
        for picture in self._photos:
            picture.set_size_request(266 if phone else 300, 200 if phone else 225)
        for clamp in self._photo_clamps:
            clamp.set_maximum_size(266 if phone else 300)
            clamp.set_tightening_threshold(266 if phone else 300)
        for card in self._links:
            card.set_size_request(252 if phone else 278, -1)
        self.render_sidebar()
        self.render_thread()
        self._composer()
        GLib.idle_add(self._scroll_if_following)

    def _reactions(self, item: dict) -> Gtk.Widget:
        # The reaction pill is Messages-specific; its stylesheet uses kit
        # surface and metric tokens, while kit type roles supply its text.
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        card.add_css_class("messages-reactions")
        card.set_name("msg-reactions-" + str(item["id"]))
        mine = item.get("from") == "me"
        card.set_halign(Gtk.Align.END if mine else Gtk.Align.START)
        card.set_valign(Gtk.Align.END)
        if mine:
            card.set_margin_end(8)
        else:
            card.set_margin_start(8)
        for emoji, people in item["reactions"].items():
            button = Gtk.Button(has_frame=False)
            button.add_css_class("messages-reaction")
            if "me" in people:
                button.add_css_class("on")
            button.update_property([Gtk.AccessibleProperty.LABEL], [emoji])
            mark = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            mark.append(_label(emoji, "body"))
            if len(people) > 1:
                count = _label(str(len(people)), "label")
                count.add_css_class("messages-reaction-count")
                mark.append(count)
            button.set_child(mark)
            button.connect("clicked", lambda _button, symbol=emoji: self._react_selected(item, symbol))
            card.append(button)
        return card

    def _sender_name(self, item: dict) -> str:
        return self._person(item.get("from")).get("name") or item.get("from") or self.current["name"]

    def _append_photo(self, content: Gtk.Box, texture: Gdk.Texture) -> None:
        picture = Gtk.Picture.new_for_paintable(texture)
        picture.set_content_fit(Gtk.ContentFit.COVER)
        picture.set_size_request(266 if self._phone_mode else 300, 200 if self._phone_mode else 225)
        picture.set_can_shrink(True)
        self._photos.append(picture)
        width = 266 if self._phone_mode else 300
        clip = _PhotoClip()
        clip.append(picture)
        photo_clamp = Adw.Clamp(maximum_size=width, tightening_threshold=width,
                                child=clip, halign=Gtk.Align.START)
        self._photo_clamps.append(photo_clamp)
        content.append(photo_clamp)

    def _message_content(self, item: dict) -> Gtk.Widget | None:
        kind = item.get("kind")
        if not kind and not item.get("attachments") and not item.get("media") and not item.get("link") and not item.get("reply") and not item.get("markup"):
            return None
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        if item.get("reply") or item.get("quote"):
            quoted = next((message for message in self._messages() if message["id"] == item["reply"]), None)
            quoted = quoted or item.get("quote")
            if quoted is not None:
                quote = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                quote.set_name("msg-quote-" + str(item["id"]))
                quote.add_css_class("messages-quote")
                quote.add_css_class("mine" if item.get("from") == "me" else "theirs")
                quote.set_size_request(270, -1)
                quote_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                        margin_top=5, margin_bottom=5, margin_start=9, margin_end=9)
                quote_author = ("You" if quoted.get("from") == "me" else
                                self._sender_name(quoted).split()[0])
                quote_content.append(_label(quote_author, "label"))
                quote_excerpt = _label(quoted.get("text") or quoted.get("name") or
                                       quoted.get("kind", "Message"), "caption")
                quote_excerpt.add_css_class("messages-quote-caption")
                quote_content.append(quote_excerpt)
                quote.append(quote_content)
                jump = Gtk.Button(has_frame=False, child=quote)
                jump.add_css_class("messages-quote-jump")
                jump.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                                     [quote_author, "Jump to quoted message"])
                jump.connect("clicked", lambda _button, target=item["reply"]:
                             self._jump_to_message(target))
                content.append(jump)
        if kind == "photo":
            texture = _picture(self._asset(item.get("photo")))
            if texture is not None:
                self._append_photo(content, texture)
        elif kind == "file":
            name = item["name"]
            file_kind, app_name = {"presentations": ("Stage presentation", "Stage"),
                                   "editor": ("Markdown", "Write"),
                                   "documents": ("Write document", "Write"),
                                   "spreadsheets": ("Grid spreadsheet", "Grid")}.get(
                                       item.get("app"), ("File", "Files"))
            file_card = FileCard(str(self._asset("messages-v70/" + name)), name=name,
                                 kind=file_kind,
                                 thumbnail=(str(self._asset("messages-v70/stage.svg"))
                                            if app_name == "Stage" else None),
                                 on_open=lambda: self._notice("Opening in " + app_name))
            if item.get("size"):
                file_card.second_line.set_label(f"{item['size']} · {file_kind}")
            if file_card.open_button is not None:
                file_card.open_button.update_property([Gtk.AccessibleProperty.LABEL],
                                                      ["Open in " + app_name])
                file_card.open_button.set_tooltip_text("Open in " + app_name)
            content.append(file_card)
        elif kind == "event":
            card = item["card"]
            start = datetime(2026, 9, int(card["day"]), 14)
            content.append(EventCard(card["title"], start, when=card["when"], where=card["where"],
                                     hue=card.get("hue"),
                                     on_add=lambda: self._notice("Added to Calendar")))
        elif kind == "song":
            card = item["card"]
            song = SongCard(card["title"], card["artist"], card.get("album"),
                            artwork=_picture(self._asset(card.get("photo"))),
                            on_play=lambda: self._notice("Playing in Tide"))
            song.row.get_last_child().update_property([Gtk.AccessibleProperty.LABEL], ["Play in Tide"])
            content.append(song)
        elif kind == "place":
            card = item["card"]
            content.append(PlaceCard(card["name"], card["address"], eta=card.get("eta"),
                                     on_directions=lambda: self._notice("Directions open in Maps")))
        elif kind == "voice":
            peaks = [min(1.0, (5 + round(abs(math.sin(index * 1.7 + item["id"]) * 13)
                                      + index % 3 * 2)) / 24) for index in range(34)]
            clip = VoiceClip(item["duration"], peaks=peaks,
                             position=self._voice_at.get(item["id"], 0),
                             playing=self._playing_voice == item["id"],
                             on_toggle=lambda _playing, message=item: self._play_voice(message),
                             on_seek=lambda position, message=item: self._seek_voice(message, position))
            clip.key.update_property([Gtk.AccessibleProperty.LABEL],
                                     ["Pause" if self._playing_voice == item["id"] else "Play"])
            content.append(clip)
        if item.get("link"):
            link = item["link"]
            link_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            link_card.set_name("msg-link-" + str(item["id"]))
            link_card.add_css_class("messages-link")
            link_card.set_margin_top(8)
            link_card.set_margin_bottom(0)
            link_card.set_size_request(252 if self._phone_mode else 278, -1)
            self._links.append(link_card)
            link_inset = 6
            link_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10,
                               margin_start=6, margin_end=6,
                               margin_top=link_inset, margin_bottom=link_inset)
            if link.get("photo"):
                picture = Gtk.Picture.new_for_paintable(_picture(self._asset(link["photo"])))
                picture.set_content_fit(Gtk.ContentFit.COVER)
                picture.set_can_shrink(True)
                thumbnail = Gtk.ScrolledWindow(width_request=56, height_request=42,
                                                hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                vscrollbar_policy=Gtk.PolicyType.NEVER,
                                                propagate_natural_width=False,
                                                propagate_natural_height=False,
                                                can_focus=False)
                thumbnail.set_child(picture)
                link_row.append(thumbnail)
            description = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
            description.set_size_request(174 if self._phone_mode else 190, -1)
            title = _label(link["title"], "body", ellipsize=Pango.EllipsizeMode.END)
            apply_type(title, "body", weight=600)
            description.append(title)
            host = _label(link["host"], "caption")
            apply_type(host, "caption", weight=400)
            description.append(host)
            link_row.append(description)
            link_card.append(link_row)
            content.append(link_card)
        if item.get("attachments"):
            for attachment in item["attachments"]:
                try:
                    file_path = self.current["service"].store.attachment_path(attachment)
                except (AttributeError, ValueError):
                    file_path = None
                available = file_path is not None and file_path.is_file()
                if available and attachment.content_type.startswith("image/") and "svg" not in attachment.content_type:
                    texture = _live_photo_texture(file_path, attachment.content_type)
                    if texture is None:
                        part = next((part for part in item.get("media", ())
                                     if part.attachment_uid == attachment.uid), None)
                        texture = (_live_photo_texture(part.preview)
                                   if part is not None and part.preview is not None and part.preview.is_file() else None)
                    if texture is not None:
                        self._append_photo(content, texture)
                        continue
                content.append(FileCard(str(file_path) if available else attachment.name,
                                        name=attachment.name, size=attachment.size,
                                        on_open=(lambda path=file_path, kind=attachment.content_type:
                                                 self._open_live_file(path, kind))
                                        if available else lambda: self._notice("Attachment is unavailable")))
        for part in item.get("media", ()):
            if part.state == "done" and part.attachment_uid and any(
                    attachment.uid == part.attachment_uid for attachment in item.get("attachments", ())):
                continue
            content.append(self._media_card(item, part))
        if item.get("text"):
            body = _label(item["text"], "body", wrap=True)
            if item.get("markup"):
                body.set_markup(item["markup"])
            body.add_css_class("lumaui-message-text")
            if item.get("reply") or item.get("quote"):
                body.set_margin_top(6)
            if item.get("link"):
                content.insert_child_after(body, link_card.get_prev_sibling())
            else:
                content.append(body)
        return content

    def _media_card(self, item: dict, part: Any) -> Gtk.Widget:
        """Show a network attachment's preview and its actual transfer state."""
        from .messages import _media_status, _file_size
        status, can_retry = ((part.noun.capitalize() + " unavailable", False)
                             if part.state == "done" else _media_status(part))
        retry = (can_retry and part.state != "downloading" and
                 getattr(self.current["service"], "provider", None) is not None)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.set_name("msg-media-" + str(item["id"]) + "-" + str(part.part))
        card.add_css_class("messages-media-pending")
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        heading.append(icons.image("image" if part.mime.startswith("image/") else "file"))
        heading.append(_label(part.noun.capitalize() + " unavailable", "body"))
        card.append(heading)
        card.append(_label(status, "caption", wrap=True))
        if part.size:
            card.append(_label(_file_size(part.size), "caption"))
        if retry:
            again = Gtk.Button(label="Try Again", halign=Gtk.Align.START)
            again.connect("clicked", lambda _button: self._retry_live_media(item["record"], part))
            card.append(again)
        return card

    def _retry_live_media(self, record: Any, part: Any) -> None:
        provider = self.current["service"].provider
        retry = getattr(provider, "retry_media", None)
        if callable(retry):
            status = getattr(provider, "status", {})
            if status and status.get("network_state") != "connected":
                self._notice("Connect your phone, then try again")
                return
            capabilities = getattr(provider, "capabilities", {})
            if capabilities and not capabilities.get("media_fetch", False):
                self._notice("This connection cannot download photos")
                return
            retry(record.uid, part.part)
            self._notice("Trying to get the " + part.noun + " again…")

    def _open_live_file(self, path: Path, content_type: str) -> None:
        try:
            application = Gio.AppInfo.get_default_for_type(content_type, False)
            if application is not None:
                application.launch([Gio.File.new_for_path(str(path))], None)
            else:
                Gio.AppInfo.launch_default_for_uri(path.as_uri(), None)
        except (GLib.Error, OSError, ValueError) as error:
            self._notice(str(error))

    def _select_message(self, item: dict) -> None:
        self._remember_draft()
        self.selected_message = (None if self.selected_message and self.selected_message["id"] == item["id"]
                                 else item)
        self.render_thread()
        self._composer()

    def _bubble_clicked(self, bubble: Gtk.Widget, item: dict, x: float, y: float) -> None:
        # Quotes, previews and retry keys own their clicks. Selecting the
        # enclosing message would rebuild the thread and undo their action.
        target = bubble.pick(x, y, Gtk.PickFlags.DEFAULT)
        while target is not None and target is not bubble:
            if isinstance(target, Gtk.Button):
                return
            target = target.get_parent()
        self._select_message(item)

    def _message_key(self, key: int, item: dict) -> bool:
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self._select_message(item)
            return True
        return False

    def _jump_to_message(self, message_id: str | int | None) -> None:
        row = self._message_rows.get(str(message_id))
        if row is None:
            self._notice("Quoted message is no longer in this conversation")
            return
        if self._scroll_source:
            GLib.source_remove(self._scroll_source)
            self._scroll_source = 0
        self._end_pending = False
        origin = row.translate_coordinates(self.history, 0, 0)
        if origin is None:
            return
        adjustment = self.scroller.get_vadjustment()
        centered = origin[1] - (adjustment.get_page_size() - row.get_height()) / 2
        self._stick_end = False
        adjustment.set_value(min(max(0, centered),
                                 max(0, adjustment.get_upper() - adjustment.get_page_size())))
        bubble = self._message_bubbles[str(message_id)]
        was_selected = bubble.has_css_class("selected")
        bubble.add_css_class("selected")
        if not was_selected:
            GLib.timeout_add(1200, lambda: (bubble.remove_css_class("selected"), False)[1])

    def _remember_draft(self) -> None:
        if not self.current:
            return
        if self.center.state == "editor" and self.center.editor is not None:
            draft = self.center.editor.draft or ""
            self._drafts[self.current["id"]] = draft
            self._save_rich_draft(self.current["id"], draft, self.center.editor.text_view.get_buffer())
        elif self.bar_entry is not None:
            key, draft = self.current["id"], self.bar_entry.text
            self._drafts[key] = draft
            editor = self.center.editor
            if editor is not None and editor.text_view is not None and editor.draft == draft:
                self._save_rich_draft(key, draft, editor.text_view.get_buffer())

    def _save_rich_draft(self, key: str, draft: str, buffer: Gtk.TextBuffer) -> None:
        spans = []
        for name in ("message-bold", "message-italic", "message-strike", "message-link"):
            tag = buffer.get_tag_table().lookup(name)
            begin = None
            for offset in range(len(draft) + 1):
                marked = offset < len(draft) and tag in buffer.get_iter_at_offset(offset).get_tags()
                if marked and begin is None:
                    begin = offset
                elif not marked and begin is not None:
                    spans.append((name, begin, offset))
                    begin = None
        self._rich_drafts[key] = (draft, spans)

    def _draft_changed(self, key: str, value: str) -> None:
        self._drafts[key] = value
        saved = self._rich_drafts.get(key)
        if saved is not None and saved[0] != value:
            self._rich_drafts.pop(key, None)

    def _restore_rich_draft(self, buffer: Gtk.TextBuffer, key: str) -> None:
        saved = self._rich_drafts.get(key)
        if saved is None:
            return
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        if text != saved[0]:
            return
        for name, start, end in saved[1]:
            buffer.apply_tag_by_name(name, buffer.get_iter_at_offset(start), buffer.get_iter_at_offset(end))

    def _composer(self) -> None:
        if not self.current:
            self.center.hide_bar()
            return
        if self.current.get("request"):
            self.bar_entry = None
            self.center.show_bar((
                BarAction("shield-off", "Block", danger=True,
                          on_activate=lambda: self.owner._answer_luma_request("block")),
                BarAction("trash-2", "Delete", on_activate=lambda: self.owner._answer_luma_request("declined")),
                BarAction("check", "Accept", primary=True,
                          on_activate=lambda: self.owner._answer_luma_request("accepted")),
            ), context=BarContext("message-square", "{} wants to message you", self.current["name"]))
            return
        if self.selected_message and self._phone_mode:
            self._held_on_phone(self.selected_message)
            return
        if self.selected_message:
            self.bar_entry = None
            item = self.selected_message
            description = item.get("text") or item.get("name") or item.get("kind", "Message").capitalize()
            lead = (BarThumbnail(_picture(self._asset(item.get("photo"))), label="Photo")
                    if item.get("kind") == "photo" else
                    self._face(self._sender_name(item), item.get("from"), 22)
                    if self.current["group"] else
                    {"file": "file-text", "voice": "mic", "event": "calendar", "place": "map-pin",
                     "song": "music"}.get(item.get("kind"), "message-square"))
            if self.current["group"] and item.get("kind") == "file":
                pair = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4,
                               valign=Gtk.Align.CENTER)
                pair.append(lead)
                pair.append(icons.image("file-text"))
                lead = pair
            actions = [
                BarChip(description, lead=lead, on_dismiss=lambda: self._select_message(item)),
                BarAction("reply", None if self._phone_mode else "Reply", tooltip="Reply",
                          on_activate=lambda: self._reply_to(item)),
                BarAction("smile", None if self._phone_mode else "React", tooltip="React",
                          on_activate=self._react_popup),
            ]
            if item.get("text"):
                actions.append(BarAction("copy", None if self._phone_mode else "Copy", tooltip="Copy",
                                         on_activate=lambda: self._copy(item)))
            actions.append(BarAction("ellipsis", tooltip="More", on_activate=self._message_more))
            self.center.show_bar(actions)
            subject = self.center.bar_row.get_first_child()
            if subject is not None and self.current["group"]:
                subject.update_property([Gtk.AccessibleProperty.LABEL],
                                        [f"Selected: {self._sender_name(item).split()[0]}, {description}"])
            more = self.center.bar_row.get_last_child()
            if more is not None:
                more.set_name("msg-more")
            action = self.center.bar_row.get_first_child()
            while action is not None:
                if getattr(getattr(action, "bar_item", None), "icon", None) == "smile":
                    action.set_name("msg-react")
                    break
                action = action.get_next_sibling()
            return
        entry = self.current
        recipient = entry["name"] if entry["group"] or entry.get("is_group") else entry["name"].split()[0]
        if not self.fixture:
            if self.center.state == "editor":
                self.center.fold()
            if self.center.editor is not None:
                self.center.card.remove(self.center.editor)
                self.center.editor = None
        if self.fixture:
            editor = ActionEditor(
            "Reply" if self.reply else "Message", "reply" if self.reply else "message-square",
            summary="to {}", summary_emphasis=entry["name"],
            tools=(BarAction("bold", tooltip="Bold", on_activate=lambda: self._format_draft("bold")),
                   BarAction("italic", tooltip="Italic", on_activate=lambda: self._format_draft("italic")),
                   BarAction("strikethrough", tooltip="Strikethrough",
                             on_activate=lambda: self._format_draft("strike")),
                   BarAction("list", tooltip="Bulleted list", on_activate=lambda: self._format_draft("list")),
                   BarAction("link", tooltip="Link", on_activate=lambda: self._format_draft("link")),
                   SPACER,
                   BarAction("smile", tooltip="Emoji", on_activate=self._emoji_menu),
                   BarAction("paperclip", tooltip="Attach", on_activate=self._attach)),
            placeholder="Text message" if entry["sms"] else "Message " + recipient,
            primary=BarAction("send-horizontal", "Send", on_activate=self._send),
            submit_on_return=True,
            )
            if entry["id"] in self._drafts:
                editor.text_view.get_buffer().set_text(self._drafts[entry["id"]])
            buffer = editor.text_view.get_buffer()
            buffer.create_tag("message-bold", weight=Pango.Weight.BOLD)
            buffer.create_tag("message-italic", style=Pango.Style.ITALIC)
            buffer.create_tag("message-strike", strikethrough=True)
            buffer.create_tag("message-link", underline=Pango.Underline.SINGLE)
            saved = self._rich_drafts.get(entry["id"])
            if saved is not None and saved[0] == self._drafts.get(entry["id"], ""):
                for name, start, end in saved[1]:
                    buffer.apply_tag_by_name(name, buffer.get_iter_at_offset(start), buffer.get_iter_at_offset(end))
            buffer.connect("changed", self._restore_rich_draft, entry["id"])
            self.center.set_editor(editor)
        placeholder = "Text message" if entry["sms"] else "Message " + recipient
        self.bar_entry = BarEntry("compose", placeholder=placeholder, label=placeholder,
                                  text=self._drafts.get(entry["id"], ""),
                                  grows=bool(self.fixture),
                                  tools=(BarAction("smile", tooltip="Emoji",
                                                   on_activate=self._emoji_menu),),
                                  voice=self._voice,
                                  on_change=lambda value, key=entry["id"]: self._draft_changed(key, value),
                                  on_submit=self._send)
        # v71 phone: Attach grows the bar into its tiles; a desktop keeps the menu by the button.
        attach = (BarAction("plus", tooltip="Attach", panel=self._attach_tiles, key="attach")
                  if self._phone_mode else BarAction("plus", tooltip="Attach", on_activate=self._attach))
        items = [attach, self.bar_entry]
        context = BarContext("reply", "Replying to {}", self._sender_name(self.reply),
                             on_dismiss=self._cancel_reply) if self.reply else None
        self.center.show_bar(items, context=context, phone_control_size="small")
        if self.bar_entry.widget is not None:
            self.bar_entry.widget.set_name("msg-composer-entry")
            if self.bar_entry.widget.grow_button is not None:
                self.bar_entry.widget.grow_button.set_name("msg-expand")
            tool = self.bar_entry.widget.well.get_first_child()
            while tool is not None:
                if getattr(getattr(tool, "bar_item", None), "icon", None) == "smile":
                    tool.set_name("msg-emoji")
                    break
                tool = tool.get_next_sibling()
        attach = self.center.bar_row.get_first_child()
        if attach is not None:
            attach.set_name("msg-attach")

    def _held_on_phone(self, item: dict) -> None:
        """v71 phone, holding a message: the six reactions in a well, then what you do with it as tiles."""
        self.bar_entry = None
        mine = item.get("from") == "me"
        panel = Gtk.Grid(row_spacing=8)
        panel.set_name("msg-held")
        well = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
        well.add_css_class("messages-react-well")
        for emoji in self._offered_reactions():
            button = Gtk.Button(label=emoji, has_frame=False)
            button.add_css_class("messages-react-choice")
            if "me" in item.get("reactions", {}).get(emoji, ()):
                button.add_css_class("on")
            button.update_property([Gtk.AccessibleProperty.LABEL], ["React " + emoji])
            button.connect("clicked", lambda _b, symbol=emoji: (self._react_selected(item, symbol),
                                                                  self._select_message(item)))
            well.append(button)
        if well.get_first_child() is not None:
            panel.attach(well, 0, 0, 1, 1)
        tiles = [BarTile("reply", "Reply", lambda: self._reply_to(item))]
        if item.get("text"):
            tiles.append(BarTile("copy", "Copy", lambda: (self._copy(item), self._select_message(item))))
        tiles += [BarTile("forward", "Forward", self._forward_selected),
                  BarTile("pin", "Pin", lambda: (self._pin_selected(), self._select_message(item))),
                  BarTile("trash-2", "Unsend" if mine else "Delete", lambda: self._delete_selected(item),
                          danger=True)]
        actions = BarTiles(tiles, size="compact", columns=5)
        for button, tile in zip(actions.buttons, tiles):
            button.set_name("msg-held-" + ("delete" if tile.icon == "trash-2" else tile.icon))
        panel.attach(actions, 0, 1, 1, 1)
        # The two tiers replace the composer row; Esc (or tapping the message again) lets go.
        self.center.show_panel(panel, on_dismiss=lambda: self._select_message(item))

    def _offered_reactions(self) -> tuple[str, ...]:
        if self.fixture:
            return ("❤️", "😂", "👍", "🎉", "😮", "🙏")
        return tuple(getattr(self.current["service"].provider, "reaction_emoji", ()) or ())[:6]

    def _attach_tiles(self) -> Gtk.Widget:
        tiles = BarTiles((BarTile("image", "Photo", lambda: self._choose_attachment("photo")),
                          BarTile("file-text", "File", lambda: self._choose_attachment("file")),
                          BarTile("map-pin", "Location", self._share_location)), columns=3)
        for button, name in zip(tiles.buttons, ("photo", "file", "location")):
            button.set_name("msg-attach-" + name)
        return tiles

    def _reply_to(self, item: dict) -> None:
        self._remember_draft()
        self.reply, self.selected_message = item, None
        self._composer()
        if self.fixture:
            self.center.grow()

    def _format_draft(self, kind: str) -> None:
        editor = self.center.editor
        if editor is None or editor.text_view is None:
            return
        buffer = editor.text_view.get_buffer()
        selection = buffer.get_selection_bounds()
        if kind == "list":
            start = selection[0] if selection else buffer.get_iter_at_mark(buffer.get_insert())
            first = start.get_line()
            end = selection[1] if selection else start
            last = end.get_line() - (1 if selection and end.get_line_offset() == 0 and end.get_line() > first else 0)
            for line in range(last, first - 1, -1):
                line_start = buffer.get_iter_at_line(line)[1]
                probe = line_start.copy()
                probe.forward_chars(2)
                if buffer.get_text(line_start, probe, False) != "• ":
                    buffer.insert(line_start, "• ")
        elif selection:
            start, end = selection
            tag = buffer.get_tag_table().lookup("message-" + kind)
            if tag is not None:
                buffer.apply_tag(tag, start, end)
        editor.focus_content()

    def _rich_draft(self) -> tuple[str, str]:
        """Serialize controlled text tags for the v70 fixture and GTK label."""
        editor = self.center.editor
        if editor is None or editor.text_view is None:
            return "", ""
        buffer = editor.text_view.get_buffer()
        content = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        wrappers = {
            "message-bold": ("<strong>", "</strong>", "<b>", "</b>"),
            "message-italic": ("<em>", "</em>", "<i>", "</i>"),
            "message-strike": ("<s>", "</s>", "<s>", "</s>"),
            "message-link": ('<a href="https://simplyluma.com">', "</a>",
                             '<a href="https://simplyluma.com">', "</a>"),
        }
        html_parts, markup_parts = [], []
        prior: tuple[str, ...] = ()
        for index, character in enumerate(content):
            tags = {tag.get_property("name") for tag in buffer.get_iter_at_offset(index).get_tags()}
            active = tuple(name for name in wrappers if name in tags)
            common = 0
            while common < min(len(prior), len(active)) and prior[common] == active[common]:
                common += 1
            for name in reversed(prior[common:]):
                _, html_end, _, markup_end = wrappers[name]
                html_parts.append(html_end)
                markup_parts.append(markup_end)
            for name in active[common:]:
                html_start, _, markup_start, _ = wrappers[name]
                html_parts.append(html_start)
                markup_parts.append(markup_start)
            literal = "<br>" if character == "\n" else escape(character)
            html_parts.append(literal)
            markup_parts.append("\n" if character == "\n" else escape(character))
            prior = active
        for name in reversed(prior):
            _, html_end, _, markup_end = wrappers[name]
            html_parts.append(html_end)
            markup_parts.append(markup_end)
        return "".join(html_parts), "".join(markup_parts)

    def _cancel_reply(self) -> None:
        self._remember_draft()
        self.reply = None
        self._composer()

    def _send(self, submitted: str | None = None) -> None:
        draft = (self.center.editor.draft if self.center.state == "editor" and self.center.editor is not None else
                 self.bar_entry.text if self.bar_entry is not None else "")
        text = (submitted if submitted is not None else draft or "").strip()
        if not text:
            return
        if self.fixture:
            content = {"text": text}
            if self.center.state == "editor":
                html, markup = self._rich_draft()
                if html != escape(text).replace("\n", "<br>"):
                    content.update(html=html, markup=markup)
            self._fixture_send(content)
            return
        # The live MessageStore and its SMS/MMS transports persist a plain
        # body. Keep rich tags in the v70 fixture only; sending HTML here would
        # deliver literal markup to recipients.
        self.owner._set_composer_text(text)
        if not self.owner.send_button.get_sensitive():
            self._notice("This conversation cannot send right now")
            return
        self.owner._send_message()
        if self.center.state == "editor" and self.center.editor is not None:
            self.center.editor.clear()
            self.center.fold()
        if self.bar_entry is not None:
            self.bar_entry.clear()
        self._drafts.pop(self.current["id"], None)
        self._rich_drafts.pop(self.current["id"], None)
        self.render_thread(to_end=True)
        self.render_sidebar()

    def _fixture_send(self, content: dict) -> None:
        if self.current is None:
            return
        messages = self.current["meta"]["messages"]
        entry = self.current
        message = {"id": max((item["id"] for conversation in self.fixture.document["conversations"]
                              for item in conversation["messages"]), default=0) + 1,
                   "from": "me", "at": datetime.now().strftime("%-I:%M %p"),
                   "status": "sending", **content}
        if self.reply is not None:
            message["reply"] = self.reply["id"]
        messages.append(message)
        self.reply = None
        self._drafts.pop(self.current["id"], None)
        self._rich_drafts.pop(self.current["id"], None)
        if self.center.state == "editor" and self.center.editor is not None:
            self.center.editor.clear()
            self.center.fold()
        if self.bar_entry is not None:
            self.bar_entry.clear()
        self.render_thread(to_end=True)
        self.render_sidebar()
        self._composer()

        def update_status(status: str) -> bool:
            if self.owner.get_visible():
                message["status"] = status
                if self.current is entry:
                    self.render_thread()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(350, update_status, "sent" if entry["sms"] else "delivered")
        if entry["sms"]:
            return

        replies = {"crew": (("PR", "Perfect, I’ll flag anything that looks off"),
                             ("NF", "Sounds good 🙌")), "priya": (("PR", "On it"),),
                   "theo": (("TH", "See you then!"),), "nora": (("NF", "Ha, likewise"),),
                   "alex": (("AR", "Thanks!"),), "climb": (("CL", "👍"),)}
        choices = replies.get(entry["id"], ())
        if not choices:
            return
        reply_index = self._fixture_reply_index.get(entry["id"], 0)
        sender, response = choices[reply_index % len(choices)]
        self._fixture_reply_index[entry["id"]] = reply_index + 1

        def start_typing() -> bool:
            if self.owner.get_visible():
                message["status"] = "read"
                entry["meta"]["typing"] = sender
                if self.current is entry:
                    self._present_entry(entry)
                    self.render_thread()
                self.render_sidebar()
            return GLib.SOURCE_REMOVE

        def finish_reply() -> bool:
            if self.owner.get_visible():
                entry["meta"].pop("typing", None)
                messages.append({"id": max(item["id"] for item in messages) + 1,
                                 "from": sender, "text": response, "at": message["at"]})
                if self.current is entry:
                    self._present_entry(entry)
                    self.render_thread()
                else:
                    entry["meta"]["unread"] = entry["meta"].get("unread", 0) + 1
                self.render_sidebar()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(1300, start_typing)
        GLib.timeout_add(3200, finish_reply)

    def _copy(self, item: dict) -> None:
        self.owner.get_clipboard().set(item.get("text", ""))
        self._notice("Copied")

    def _message_more(self) -> None:
        item = self.selected_message
        anchor = self.center.bar_row.get_last_child()
        if item is None or anchor is None:
            return
        mine = item.get("from") == "me"
        FloatingMenu((
            MenuItem("Forward", icon="forward", on_activate=self._forward_selected),
            MenuItem("Unpin" if item.get("pinned") else "Pin", icon="pin", on_activate=self._pin_selected),
            None,
            MenuItem("Delete for everyone" if mine else "Delete for me", icon="trash-2",
                     on_activate=lambda: self._delete_selected(item)),
        ), label="Message actions").popup(anchor)

    def _forward_selected(self) -> None:
        if self.selected_message is None:
            return
        self._forward_text = self.selected_message.get("text") or self.selected_message.get("name")
        if self.fixture:
            self._notice("Forward: choose a conversation")
        self.new_message()

    def _pin_selected(self) -> None:
        if self.selected_message is None:
            return
        if self.fixture:
            item = self.selected_message
            item["pinned"] = not item.get("pinned", False)
            self._notice("Pinned to this conversation" if item["pinned"] else "Unpinned")
            return
        item = self.selected_message
        try:
            self.preferences.set_message_pinned(self.current["id"], str(item["id"]),
                                                not item.get("pinned", False))
        except (OSError, ValueError) as error:
            self._notice(f"Couldn’t update message Pin: {error}")
            return
        item["pinned"] = not item.get("pinned", False)
        self._notice("Pinned to this conversation" if item["pinned"] else "Unpinned")

    def _delete_selected(self, item: dict) -> None:
        if self.fixture:
            messages = self.current["meta"]["messages"]
            index = next((i for i, message in enumerate(messages) if message["id"] == item["id"]), None)
            if index is None:
                return
            removed = messages.pop(index)
            self.selected_message = None
            self.render_thread()
            self._composer()
            def undo() -> None:
                messages.insert(index, removed)
                self.render_thread()
                self.render_sidebar()
            Toast.show(self.host, "Deleted", undo=undo)
            return
        record = item.get("record")
        if record is not None:
            DestructiveDialog.ask(self.host, title="Delete this message?",
                                  body="It’s removed from this computer. It won’t unsend it.",
                                  action="Delete", on_confirm=lambda _checked: self._delete_selected_confirmed(record.uid))

    def _delete_selected_confirmed(self, uid: str) -> None:
        self.owner._delete_message(uid)
        self.selected_message = None
        self.render_thread()
        self._composer()
        self._notice("Message deleted")

    def _react_popup(self) -> None:
        item = self.selected_message
        if item is None:
            return
        anchor = self.center.bar_row.get_first_child()
        while anchor is not None and getattr(getattr(anchor, "bar_item", None), "icon", None) != "smile":
            anchor = anchor.get_next_sibling()
        if anchor is None:
            return
        offered = ("❤️", "😂", "👍", "🎉", "😮", "🙏")
        if not self.fixture:
            offered = tuple(getattr(self.current["service"].provider, "reaction_emoji", ()) or ())
        if not offered:
            self._notice("Reactions aren’t available here")
            return
        pop = Gtk.Popover(position=Gtk.PositionType.TOP, has_arrow=False)
        pop.set_name("msg-reaction-pop")
        choices = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        for emoji in offered:
            button = Gtk.Button(label=emoji, has_frame=False, width_request=36, height_request=36)
            button.update_property([Gtk.AccessibleProperty.LABEL], ["React " + emoji])
            button.connect("clicked", lambda _button, symbol=emoji: (
                pop.popdown(), self._react_selected(item, symbol)))
            choices.append(button)
        pop.set_child(choices)
        coordinates = anchor.translate_coordinates(self.owner, 0, 0)
        pop.set_parent(self.owner if coordinates is not None else anchor)
        if coordinates is not None:
            x, y = coordinates
            pointing = Gdk.Rectangle()
            pointing.x, pointing.y = round(x), round(y)
            pointing.width, pointing.height = anchor.get_width(), anchor.get_height()
            pop.set_pointing_to(pointing)
        if not self._phone_mode:
            pop.set_offset(80, 0)
        self._reaction_pop = pop
        pop.popup()

    def _react_selected(self, item: dict, emoji: str) -> None:
        if self.fixture:
            senders = item.setdefault("reactions", {}).setdefault(emoji, [])
            if "me" in senders:
                senders.remove("me")
                if not senders:
                    item["reactions"].pop(emoji)
            else:
                senders.append("me")
            self.render_thread()
            return
        record = item.get("record")
        if record is not None:
            self.owner._react(self.current["service"], record, emoji)

    def _attach(self) -> None:
        if self.center.state == "editor":
            tools_row = self.center.editor.get_first_child().get_next_sibling()
            anchor = tools_row.get_first_child() if tools_row is not None else None
            while anchor is not None and getattr(getattr(anchor, "bar_item", None), "icon", None) != "paperclip":
                anchor = anchor.get_next_sibling()
        else:
            anchor = self.center.bar_row.get_first_child()
        if anchor is None:
            return
        FloatingMenu(("Send",
                      MenuItem("Photo or video", icon="image",
                               on_activate=lambda: self._choose_attachment("photo")),
                      MenuItem("File", icon="file-text",
                               on_activate=lambda: self._choose_attachment("file")),
                      MenuItem("Location", icon="map-pin", on_activate=self._share_location)),
                     label="Send").popup(anchor)

    def _choose_attachment(self, kind: str) -> None:
        if self.fixture:
            if kind == "photo":
                self._fixture_send({"kind": "photo", "photo": "messages-v70/life-sync-terrace.webp"})
            else:
                self._fixture_send({"kind": "file", "name": "launch-plan.md", "size": "14 KB",
                                    "app": "editor"})
        else:
            self.owner._attachment_requested()

    def _share_location(self) -> None:
        if self.fixture:
            self._fixture_send({"text": "📍 Shared location · Oakland, CA"})
        else:
            self._notice("Location sharing is unavailable")

    def _emoji_menu(self) -> None:
        if self.center.state == "editor":
            tools_row = self.center.editor.get_first_child().get_next_sibling()
            anchor = tools_row.get_first_child() if tools_row is not None else None
            while anchor is not None and getattr(getattr(anchor, "bar_item", None), "icon", None) != "smile":
                anchor = anchor.get_next_sibling()
        elif self.bar_entry is not None and self.bar_entry.widget is not None:
            anchor = self.bar_entry.widget.well.get_last_child()
            while anchor is not None and getattr(getattr(anchor, "bar_item", None), "icon", None) != "smile":
                anchor = anchor.get_prev_sibling()
        else:
            return
        if anchor is None:
            return
        symbols = ("😀", "😂", "🥹", "😍", "🤔", "😎", "🙌", "👍", "🎉", "🔥", "❤️", "🙏")
        if self._phone_mode and self.center.state != "editor":
            # v71 phone: Emoji grows the bar into a 6-across grid (.emrow.memo2).
            grid = Gtk.Grid(column_homogeneous=True, hexpand=True)
            grid.set_name("msg-emoji-grid")
            grid.add_css_class("messages-emoji-grid")
            for index, symbol in enumerate(symbols):
                button = Gtk.Button(label=symbol, has_frame=False, hexpand=True)
                button.add_css_class("messages-emoji-choice")
                button.update_property([Gtk.AccessibleProperty.LABEL], ["Insert " + symbol])
                button.connect("clicked", lambda _b, emoji=symbol: (self.center.fold_panel(),
                                                                    self._insert_emoji(emoji)))
                grid.attach(button, index % 6, index // 6, 1, 1)
            self.center.grow("emoji", grid, anchor=anchor)
            return
        pop = Gtk.Popover(position=Gtk.PositionType.TOP, has_arrow=False)
        pop.set_name("msg-emoji-pop")
        grid = Gtk.Grid(column_spacing=2, row_spacing=2)
        for index, symbol in enumerate(symbols):
            button = Gtk.Button(label=symbol, has_frame=False, width_request=36, height_request=36)
            button.update_property([Gtk.AccessibleProperty.LABEL], ["Insert " + symbol])
            button.connect("clicked", lambda _button, emoji=symbol: (pop.popdown(), self._insert_emoji(emoji)))
            grid.attach(button, index % 6, index // 6, 1, 1)
        pop.set_child(grid)
        coordinates = anchor.translate_coordinates(self.owner, 0, 0)
        pop.set_parent(self.owner if coordinates is not None else anchor)
        if coordinates is not None:
            x, y = coordinates
            pointing = Gdk.Rectangle()
            pointing.x, pointing.y = round(x), round(y)
            pointing.width, pointing.height = anchor.get_width(), anchor.get_height()
            pop.set_pointing_to(pointing)
        if not self._phone_mode:
            pop.set_offset(60, 0)
        self._emoji_pop = pop
        pop.popup()

    def _insert_emoji(self, emoji: str) -> None:
        if self.center.state == "editor":
            self.center.editor.text_view.get_buffer().insert_at_cursor(emoji)
            self.center.editor.focus_content()
        elif self.bar_entry is not None:
            self.bar_entry.set_text(self.bar_entry.text + emoji)
            self.bar_entry.focus()

    def _voice(self) -> None:
        # v70's mic click only prompts "Hold to record"; it has no recording
        # handler. The live branch reports the unavailable recording action.
        if self.fixture:
            self._notice("Hold to record a voice message")
        else:
            self._notice("Voice recording isn't available yet.")

    def _invite_to_luma(self) -> None:
        if self.fixture:
            self._notice("Invite sent: they can keep their number")
            return
        service = self.owner._luma_service()
        if service is None:
            self._notice("Set up Luma Messages before sharing your profile link")
            return

        def loaded(identity, error) -> None:
            if error is not None:
                self._notice(getattr(error, "message", "Luma didn't answer."))
                return
            link = str((identity or {}).get("profile_link") or "")
            if not link:
                self.owner._show_luma_profile()
                return
            if self.bar_entry is not None:
                self.bar_entry.set_text("Message me on Luma: " + link)
                self.bar_entry.focus()
                self._notice("Review the invitation before sending")

        self.owner.luma_call(service, "luma.identity", {}, loaded)

    def _play_voice(self, item: dict) -> None:
        if self._voice_source:
            GLib.source_remove(self._voice_source)
            self._voice_source = 0
        if self._playing_voice == item["id"]:
            self._playing_voice = None
            self.render_thread()
            return
        self._playing_voice = item["id"]
        self.render_thread()

        def tick() -> bool:
            if self._playing_voice != item["id"]:
                self._voice_source = 0
                return GLib.SOURCE_REMOVE
            elapsed = self._voice_at.get(item["id"], 0) + 1
            self._voice_at[item["id"]] = elapsed
            if elapsed >= item["duration"]:
                self._voice_at[item["id"]] = 0
                self._playing_voice = None
                self._voice_source = 0
                self.render_thread()
                return GLib.SOURCE_REMOVE
            self.render_thread()
            return GLib.SOURCE_CONTINUE

        self._voice_source = GLib.timeout_add_seconds(1, tick)

    def _seek_voice(self, item: dict, position: float) -> None:
        self._voice_at[item["id"]] = round(position)
        self.render_thread()

    def render_details(self) -> None:
        self.details.clear()
        if not self.current:
            self.details.show(subject=None)
            return
        entry = self.current
        person = self._person(entry["person"])
        group = bool(entry["group"] or entry.get("is_group"))
        handle = person.get("handle") or str(entry["meta"].get("handle") or "").lstrip("@")
        subtitle = self._subtitle(entry) if group else (
            "@" + handle if handle else person.get("phone") or entry["address"])
        self.details.add_hero(entry["name"], subtitle,
                              lead=self._conversation_face(entry, 64 if group else 72))
        if not entry["group"] and not entry.get("is_group"):
            model = Person(entry["name"],
                           username=person.get("handle") or str(entry["meta"].get("handle") or "").lstrip("@"),
                           phone=person.get("phone") or (entry["address"] if entry["sms"] else ""),
                           online=person.get("online", False),
                           picture=_picture(person.get("photo_bytes") or self._asset(person.get("photo"))))
            # Keep ContactActions' reachability and desktop delivery commands,
            # presenting its actual buttons through the shared pane tile row.
            contacts = ContactActions(model, handler=self._contact_action)
            buttons = tuple(contacts.buttons.values())
            for button in buttons:
                contacts.remove(button)
            tiles = StackedButtons(buttons, size="tile")
            tiles.update_property([Gtk.AccessibleProperty.LABEL], [f"Contact {model.name}"])
            self.details.add(tiles)
        elif entry["group"] or entry.get("is_group"):
            self.details.add(StackedButtons((
                StackedButton("phone", "Call", on_click=self._call),
                StackedButton("video", "Video", on_click=self._video),
            ), size="tile"))
        self.details.add_section("Conversation",
                                 action=("Unmute" if entry["muted"] else "Mute", self._mute))
        messages = self._messages()
        facts = []
        if self.fixture:
            facts = [("Started", "Sep 2" if entry["group"] else "Aug 14"),
                     ("Notifications", "Muted" if entry["muted"] else "On"),
                     ("Encryption", "None (text message)" if entry["sms"] else "End-to-end")]
            if not entry["sms"]:
                facts.append(("Safety code", "48213 90552 17608"))
        elif messages:
            from .messages_luma import is_luma
            encryption = ("None (text message)" if entry["sms"] else
                          "End-to-end" if is_luma(entry["service"]) else "Provider managed")
            facts = [("Started", messages[0].get("day", "")),
                     ("Notifications", "Muted" if entry["muted"] else "On"),
                     ("Encryption", encryption)]
        self.details.add_facts(facts)
        if not self.fixture and entry["meta"].get("account") and not entry.get("request"):
            self.details.add_list((AddRow("Compare safety numbers", icon="shield-check",
                                          on_activate=self.owner._show_luma_safety),))
        if entry["group"]:
            self.details.add_section("People")
            rows = []
            for key in entry["group"]:
                p = self._person(key)
                name = p.get("name", key)
                row = DetailsRow(name, "@" + p["handle"] if p.get("handle") else "",
                                 lead=self._face(name, key, 32),
                                 on_activate=lambda person=name: self._open_contact(person),
                                 actions=(("message-square", "Message " + name.split()[0],
                                           lambda person=key: self._message_person(person)),
                                          ("phone", "Call " + name.split()[0], self._call),
                                          ("bell-off", "Mute " + name.split()[0] + " here",
                                           lambda person=key: self._mute_person(person))))
                row.button.set_tooltip_text("Open in Contacts")
                row.button.update_property([Gtk.AccessibleProperty.LABEL], ["Open in Contacts"])
                rows.append(row)
            if self.fixture:
                own = DetailsRow("You", "@nick", lead=self._face("Nick", "me", 32))
                own.button.set_sensitive(False)
                rows.append(own)
            self.details.add_list(rows)
            self.details.add_list((AddRow("Add people", on_activate=lambda: self._notice("Add people")),))
        self.details.add_section("Photos and files", action=("View all", lambda: self._notice("All photos and files")))
        if self.fixture:
            photos = [self._asset(message.get("photo")) for message in messages if message.get("kind") == "photo"]
            photos.extend(self._asset(path) for path in entry["meta"].get("gallery", ()))
            self.details.add_photos([path for path in photos if path])
            file_card = FileCard(str(self._asset("messages-v70/launch-deck.stage")), name="launch-deck.stage",
                                 thumbnail=str(self._asset("messages-v70/stage.svg")),
                                 on_open=lambda: self._notice("Opening in Stage"))
            file_card.second_line.set_label("18.4 MB · Stage presentation")
            self.details.add(file_card)
        else:
            photos = []
            files = []
            for message in reversed(messages):
                for attachment in message.get("attachments", ()):
                    try:
                        path = entry["service"].store.attachment_path(attachment)
                    except (AttributeError, OSError, ValueError):
                        continue
                    if path is None:
                        continue
                    if attachment.content_type.startswith("image/"):
                        if len(photos) < 6:
                            photos.append(path)
                    elif len(files) < 6:
                        files.append((path, attachment))
            if photos:
                self.details.add_photos(photos)
            for path, attachment in files:
                self.details.add(FileCard(str(path), name=attachment.name, size=attachment.size,
                                          on_open=lambda file=path, kind=attachment.content_type:
                                                  self._open_live_file(file, kind)))
        actions = []
        if group:
            actions.append(StackedButton("log-out", "Leave", on_click=self._leave))
        actions.append(StackedButton("trash-2", "Delete", danger=True, on_click=self._delete))
        action_group = StackedButtons(actions, size="tile") if len(actions) > 1 else actions[0]
        action_group.set_margin_top(16)
        self.details.add(action_group)

    def _contact_action(self, action: str, _person: Person) -> bool:
        if action == "message":
            self.details.close()
            self.center.grow()
            return True
        if action == "call":
            self._call()
            return True
        if action == "video":
            self._video()
            return True
        return False

    def _open_contact(self, name: str) -> None:
        if self.fixture:
            self._notice("Opening " + name + " in Contacts")
            return
        from luma_appkit.application_directory import launch
        launch('org.projectluma.Contacts.desktop', callback=lambda ok, error:
               self._notice(error or 'Contacts is unavailable') if not ok else None)

    def _message_person(self, key: str) -> None:
        person = self._person(key)
        if not person:
            return
        self.details.close()
        if self.fixture:
            match = next((entry for entry in self._entries() if entry.get("person") == key), None)
            if match:
                self.open(match["id"])
            else:
                self._notice("Starting a conversation with " + person["name"])
            return
        handle = person.get("handle")
        service = self.owner._luma_service()
        if handle and service is not None:
            self.owner.start_luma_conversation(service, handle,
                                                lambda error: error and self._notice(error.message))
            return
        self.new_message()

    def _mute_person(self, key: str) -> None:
        if self.fixture and self.current:
            muted = self.current["meta"].setdefault("muted_people", [])
            if key in muted:
                muted.remove(key)
            else:
                muted.append(key)
            self.render_details()
            self._notice("Muted here" if key in muted else "Unmuted here")
            return
        self._notice("Person mute is unavailable")

    def _details_closed(self) -> None:
        pass

    def _show_conversations(self) -> None:
        self.details.close()
        self.split.set_show_content(False)

    def _call(self) -> None:
        if not self.fixture and self.current:
            self.owner._call_current()

    def _video(self) -> None:
        self._notice("Video calling is not available on this device")

    def _pin_toggled(self, on: bool) -> None:
        # The corner's Pin is a toggle; drawing a conversation sets it without acting.
        if self.current is not None and on != self.current["pinned"]:
            self._pin()

    def _pin(self) -> None:
        if self.current:
            self._toggle_flag(self.current, "pinned")

    def _mute(self) -> None:
        if self.current:
            self._toggle_flag(self.current, "muted")

    def _toggle_flag(self, entry: dict, flag: str, *, undo: bool = False) -> bool:
        """Pin or mute one conversation (the island, the corner, a swipe), then say so (v71 toasts)."""
        value = not entry[flag]
        if self.fixture:
            entry["meta"][flag] = value
        else:
            try:
                self.preferences.set_flag(entry["id"], flag, value)
            except (OSError, ValueError) as error:
                self._notice(f"Couldn’t update {'Pin' if flag == 'pinned' else 'Mute'}: {error}")
                return False
        entry[flag] = value
        self.render_sidebar()
        if self.current is not None and self.current["id"] == entry["id"]:
            self.current[flag] = value
            if flag == "muted":
                self.render_details()
            self._present_entry(self.current)
        if flag == "pinned":
            message = f"{entry['name']} pinned" if value else f"{entry['name']} unpinned"
        else:
            message = "Muted. You won’t be notified." if value else "Unmuted"
        host = self.sidebar_host if self._phone_mode and not self.split.get_show_content() else self.host
        Toast.show(host, message, undo=(lambda: self._toggle_flag(self._entry_by_id.get(entry["id"], entry), flag))
                   if undo else None)
        return True

    def _swiped(self, entry_id: str, flag: str) -> None:
        entry = self._entry_by_id.get(entry_id)
        if entry is not None:
            self._toggle_flag(entry, flag, undo=True)

    def _leave(self) -> None:
        if not self.current:
            return
        DestructiveDialog.ask(self.host, title=f"Leave {self.current['name']}?",
                              body="You won’t get new messages. The others keep the conversation, and someone can add you back.",
                              action="Leave", icon="log-out",
                              on_confirm=lambda _checked, entry=self.current: self._leave_confirmed(entry))

    def _leave_confirmed(self, entry: dict) -> None:
        if not self.fixture:
            self._notice("Leaving groups is unavailable")
            return
        conversations = self.fixture.document["conversations"]
        index = next((i for i, item in enumerate(conversations) if item["id"] == entry["id"]), None)
        if index is None:
            return
        removed = conversations.pop(index)
        self.current = None
        self.render_sidebar()
        self.render_thread()
        self.render_details()
        self._composer()
        def undo() -> None:
            conversations.insert(index, removed)
            self.render_sidebar()
            self.open(entry["id"])
        Toast.show(self.host, "You left the conversation", undo=undo)

    def _delete(self) -> None:
        if not self.current:
            return
        entry = self.current
        DestructiveDialog.ask(self.host, title="Delete this conversation?",
                              body="It’s removed from this computer. Others in the conversation keep their copy.",
                              action="Delete", on_confirm=lambda _checked: self._delete_confirmed(entry))

    def _delete_confirmed(self, entry: dict) -> None:
        if self.fixture:
            conversations = self.fixture.document["conversations"]
            index = next((i for i, item in enumerate(conversations) if item["id"] == entry["id"]), None)
            if index is None:
                return
            removed = conversations.pop(index)
            self.current = None
            self.render_sidebar()
            self.render_thread()
            self.render_details()
            self._composer()
            def undo() -> None:
                conversations.insert(index, removed)
                self.render_sidebar()
                self.open(entry["id"])
            Toast.show(self.host, "Conversation deleted", undo=undo)
            return
        self.owner._delete_thread(entry["record"], entry["service"])
        self.current = None
        self.render_sidebar()
        self.render_thread()
        self.render_details()
        self._composer()

    def new_message(self) -> None:
        # This uses the controller's existing contact, phone and Luma account
        # resolution. The fixture has only Studio's three sample choices.
        if self._phone_mode:
            self._show_conversations()
            if self.list_center.grown != "new":
                self._phone_new()
            return
        previous = getattr(self, "_new_pop", None)
        if previous is not None:
            shown = previous.get_visible()
            previous.popdown()
            previous.unparent()
            self._new_pop = None
            if shown:
                return
        previous_code = getattr(self, "_code_pop", None)
        if previous_code is not None:
            previous_code.popdown()
            previous_code.unparent()
        pop = Gtk.Popover(position=Gtk.PositionType.TOP, autohide=True)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.set_size_request(286, -1)
        heading = _label("New message", "label")
        heading.set_margin_start(10)
        heading.set_margin_top(6)
        heading.set_margin_bottom(4)
        content.append(heading)
        search = Gtk.Entry(placeholder_text="Name, username or phone",
                           accessible_role=Gtk.AccessibleRole.SEARCH_BOX)
        search.set_name("msg-new-search")
        search.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, icons.icon_name("user"))
        search.set_margin_start(6)
        search.set_margin_end(6)
        search.set_margin_top(2)
        search.set_margin_bottom(8)
        content.append(search)
        help_text = _label("Find Luma friends by @username. Phone numbers match your contacts or send texts.",
                           "caption", wrap=True)
        help_text.set_margin_start(10)
        help_text.set_margin_end(10)
        help_text.set_margin_bottom(8)
        content.append(help_text)
        results = Gtk.ListBox()
        results.set_name("msg-new-results")
        result_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                           vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                           propagate_natural_height=True,
                                           max_content_height=5 * 48)
        result_scroll.set_child(results)
        content.append(result_scroll)

        def add(name: str, subtitle: str, key: str | None, address: str,
                *, handle: str | None = None) -> None:
            row = NavigationRow(name, subtitle=subtitle,
                                icon_widget=self._face(name, key, 28))
            row.set_size_request(-1, 48)
            row.add_css_class("messages-recipient")
            if handle:
                row.add_css_class("handle")
            elif name == address and address.startswith("+"):
                row.add_css_class("phone")
            row.address, row.display_name, row.luma_handle, row.person_key = address, name, handle, key
            results.append(row)

        def changed(_entry):
            while child := results.get_first_child():
                results.remove(child)
            query = search.get_text().casefold().strip()
            if self.fixture:
                for key in ("TH", "AR", "CL"):
                    person = self._person(key)
                    name = person.get('name')
                    if not name:
                        continue
                    if query and query not in (name + " " + person.get("handle", "")).casefold():
                        continue
                    add(name, "@" + person["handle"], key, "@" + person["handle"])
                return
            from . import messages_luma as luma

            handle = luma.handle_from(query) if luma.looks_like_handle(query) else None
            if handle and self.owner._luma_service() is not None:
                add("@" + handle, "Luma · End-to-end encrypted", None, "@" + handle, handle=handle)
            candidates = tuple(contact for contact in self.owner.contacts if contact.phone and
                               (not query or query in contact.name.casefold() or query in contact.phone or
                                query in contact.organization.casefold()))[:20]
            for contact in candidates:
                detail = (contact.organization + " · " if contact.organization else "") + contact.phone
                add(contact.name, detail, None, contact.phone)
            if query:
                try:
                    from .messages_backend import normalize_address
                    direct = normalize_address(query)
                except ValueError:
                    direct = None
                if direct and not any(contact.phone == direct for contact in candidates):
                    add(direct, "Phone number", None, direct)
        search.connect("changed", changed)
        def picked(_box, row):
            pop.popdown()
            if self.fixture:
                self._notice("Starting a conversation with " + row.display_name)
                return
            row.luma_service = self.owner._luma_service() if row.luma_handle else None
            self.owner._recipient_activated(results, row)
        results.connect("row-activated", picked)
        changed(search)
        divider = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        divider.set_margin_start(6)
        divider.set_margin_end(6)
        divider.set_margin_top(4)
        divider.set_margin_bottom(4)
        content.append(divider)
        own_code = NavigationRow("Your Luma username", subtitle="Show your code or share your link",
                                 icon_widget=icons.image("scan"))
        own_code.set_name("msg-own-code")
        own_code.set_size_request(-1, 48)
        own_list = Gtk.ListBox()
        own_list.append(own_code)
        own_list.connect("row-activated", lambda *_args: self._show_own_code())
        content.append(own_list)
        self._own_code_card = self._fixture_code_card() if self.fixture else None
        pop.set_child(content)
        coordinates = self.foot.add_button.translate_coordinates(self.owner, 0, 0)
        pop.set_parent(self.owner if coordinates is not None else self.foot.add_button)
        if coordinates is not None:
            x, y = coordinates
            anchor = Gdk.Rectangle()
            anchor.x, anchor.y = round(x), round(y)
            anchor.width = self.foot.add_button.get_width()
            anchor.height = self.foot.add_button.get_height()
            pop.set_pointing_to(anchor)
        self._new_pop = pop
        if not self._phone_mode:
            pop.set_offset(127, 0)
        pop.popup()
        search.grab_focus()

    def _fixture_code_card(self) -> Card:
        link = "simplyluma.com/@nick"
        card = Card(recessed=True)
        card.set_name("msg-own-code-card")
        card.set_orientation(Gtk.Orientation.VERTICAL)
        card.set_spacing(8)
        card.set_size_request(240, -1)
        card.append(Gtk.Box(height_request=10))
        picture = Gtk.Picture.new_for_filename(str(self._asset("messages-v70/nick-qr.svg")))
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        picture.set_can_shrink(True)
        picture.set_size_request(160, 160)
        picture.set_halign(Gtk.Align.CENTER)
        picture.update_property([Gtk.AccessibleProperty.LABEL], ["QR code for @nick"])
        thumbnail = Gtk.ScrolledWindow(width_request=160, height_request=160,
                                       hscrollbar_policy=Gtk.PolicyType.NEVER,
                                       vscrollbar_policy=Gtk.PolicyType.NEVER,
                                       propagate_natural_width=False,
                                       propagate_natural_height=False,
                                       can_focus=False, halign=Gtk.Align.CENTER)
        thumbnail.set_child(picture)
        card.append(thumbnail)
        card.append(_label("@nick", "title-2", halign=Gtk.Align.CENTER))
        card.append(_label(link, "caption", halign=Gtk.Align.CENTER))
        copy = make_control(BarAction("link", "Copy link",
                                      on_activate=lambda: self._copy_own_link(link)), size="tool")
        copy.set_halign(Gtk.Align.CENTER)
        card.append(copy)
        return card

    def _show_own_code(self) -> None:
        if not self.fixture:
            service = self.owner._luma_service()
            if service is None:
                self.owner._show_luma_profile()
                return
            self._close_new()

            def loaded(identity, error) -> None:
                if error is not None:
                    self._notice(getattr(error, "message", "Luma didn't answer."))
                    return
                identity = identity or {}
                handle = str(identity.get("handle") or "")
                link = str(identity.get("profile_link") or "")
                if not handle or not link:
                    self.owner._show_luma_profile()
                    return
                from .messages_accounts_ui import qr_widget
                card = Card(recessed=True)
                card.set_name("msg-own-code-card")
                card.set_orientation(Gtk.Orientation.VERTICAL)
                card.set_spacing(8)
                card.set_size_request(240, -1)
                card.append(Gtk.Box(height_request=10))
                qr = qr_widget(link, f"QR code for @{handle}")
                if isinstance(qr, Gtk.DrawingArea):
                    qr.set_content_width(160)
                    qr.set_content_height(160)
                qr.set_halign(Gtk.Align.CENTER)
                card.append(qr)
                card.append(_label("@" + handle, "title-2", halign=Gtk.Align.CENTER))
                card.append(_label(link.removeprefix("https://"), "caption", halign=Gtk.Align.CENTER))
                copy = make_control(BarAction("link", "Copy link",
                                              on_activate=lambda: self._copy_own_link(link)), size="tool")
                copy.set_halign(Gtk.Align.CENTER)
                card.append(copy)
                self._own_code_card = card
                self._present_own_code()

            self.owner.luma_call(service, "luma.identity", {}, loaded)
            return
        self._close_new()
        if getattr(self, "_own_code_card", None) is None:
            self._own_code_card = self._fixture_code_card()
        GLib.idle_add(self._present_own_code)

    def _close_new(self) -> None:
        pop = getattr(self, "_new_pop", None)
        if pop is not None:
            pop.popdown()
        if self.list_center.grown is not None:
            self.list_center.fold_panel()

    def _present_own_code(self) -> bool:
        if self._phone_mode and self._own_code_card is not None:
            card = self._own_code_card
            if card.get_parent() is not None:
                card.unparent()
            self.list_center.grow("code", card)
            return GLib.SOURCE_REMOVE
        pop = Gtk.Popover(position=Gtk.PositionType.TOP)
        pop.set_child(self._own_code_card)
        coordinates = self.foot.add_button.translate_coordinates(self.owner, 0, 0)
        pop.set_parent(self.owner if coordinates is not None else self.foot.add_button)
        if coordinates is not None:
            x, y = coordinates
            anchor = Gdk.Rectangle()
            anchor.x, anchor.y = round(x), round(y - 50 if not self._phone_mode else y)
            anchor.width = self.foot.add_button.get_width()
            anchor.height = self.foot.add_button.get_height()
            pop.set_pointing_to(anchor)
        if not self._phone_mode:
            pop.set_offset(96, 0)
        self._code_pop = pop
        pop.popup()
        return GLib.SOURCE_REMOVE

    def _copy_own_link(self, link: str) -> None:
        self.owner.get_clipboard().set(link)
        pop = getattr(self, "_code_pop", None)
        if pop is not None:
            pop.popdown()
        if self.list_center.grown == "code":
            self.list_center.fold_panel()
        self._notice("Link copied")

    def _notice(self, message: str) -> None:
        Toast.show(self.host, message)


class FixtureMessagesWindow(AppWindow):
    """The same visible Messages surface over Studio data held only in memory."""

    def __init__(self, application: Adw.Application) -> None:
        from .messages_fixture import FixtureMessageStore

        path = Path(os.environ["LUMA_MESSAGES_FIXTURE"])
        self.fixture_store = FixtureMessageStore(path)
        super().__init__(application=application, app_id=APP_ID + ".Fixture", title="Messages", icon_name=APP_ID,
                         commands=CommandRegistry(()),
                         default_width=1180, default_height=740, minimum_width=360, minimum_height=420)
        self.surface = MessagesSurface(self, self.fixture_store)
        self.connect("close-request", lambda *_: (self.fixture_store.close(), False)[1])

    def _save_state(self) -> None:
        """A conform fixture never writes the person's window state."""
        pass
