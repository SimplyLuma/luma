# SPDX-License-Identifier: Apache-2.0

"""Contacts, the LumaUI reference app (simulator v71).

Every visible part is a LumaUI part used through its semantic API: the app
says what is on screen, and the kit decides how it looks and moves. What is
left here is what an address book actually is: who is in it, how the list is
narrowed, one person's card, and what you share with them.

    window        AppWindow (frame, title row, identity pill: the kit's), its body a ListFirst:
    ├ list        ToastHost around a NavigationSidebar: My card (AccountCard), letter sections,
    │ │           a NavigationRow per person
    │ ├ foot      SidebarFoot (computer): search people, Lists, Add a person
    │ └ bar       ActionCenter (phone): Search, Lists, Add a person
    └ card        ToastHost around an Island
      ├ light     ContentLitHeader: their photo or hue, behind the top of the page
      ├ corner    CornerPill (computer): Share · Edit · ⋯ (Block, Delete), or Cancel · Done while editing
      ├ back      ListFirst's floating ‹ (phone): to the list
      ├ page      PersonAvatar, HeroTitleField, @handle + StatusPill, ContactActions, then Cards:
      │           Contact · Together, What <they> see (or Not on Luma) · Private note
      └ bar       ActionCenter (phone): Edit · Share · Favorite · ⋯, or Cancel · Done while editing

On a phone (the kit's phone tier, under 560) Contacts is list-first, as v71
is: it opens on the list under a large "Contacts", picking someone pushes
their card, and the title island's ‹ returns. The corner pill and the sidebar
foot give way to the two bars, and the bars grow into every panel (Lists,
Add a person, Share, ⋯, a confirmation); on a computer the same panels are
the kit's menus, sheets and dialogs at their buttons.

People come from one ContactsSource (contacts_data): the address book,
Messages (read only) and the sharing store, or a JSON fixture
(LUMA_CONTACTS_FIXTURE). The writes v71 adds (favourite, lists, block, link a
duplicate, your own card) are offered only where the source makes them
(`ContactsSource.can`). This module owns no styling: style/contacts.css holds
only this app's layout. Every element v71 has carries a stable widget name
(WIDGET_NAMES) for tools/lumaui-conform.

Every part is the kit's v71 part (KIT-STATUS.md); what v71 shows that the kit
does not offer yet is marked TODO(v71-kit …) with its kit request.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import threading
import time
from dataclasses import replace
from typing import Callable
from urllib.parse import quote

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from luma_appkit import (  # noqa: E402
    AccountCard, ActionCenter, AppWindow, AZIndex, BarAction, BarSearch, BarTile, BarTiles, Card, Command, CommandGroup,
    CommandRegistry, ContactActions, ContentLitHeader, CornerPill, DestructiveDialog, DetailsItem, EmptyState,
    FactRow, FileCard, HeroTitleField, Island, ListEmptyState, ListFirst, MenuSection, NavigationRow,
    NavigationSidebar, PanelField, PanelHeading, PanelRow, ParagraphField, Person, PersonAvatar, RichMenuItem,
    ScrollView, SharePanel, ShareSheet, ShareSubject, ShareTarget, SidebarFoot, StatusPill, TextButton,
    Toast, ToastHost, add_style_sheet, apply_type, hue_class, icons, install_appkit, install_lumaui,
    lumaui_tokens, modifier_text, panel_list, person_hue,
)
from luma_appkit.action_bubble import FloatingMenu, MenuItem  # noqa: E402
from luma_appkit.bar_share import PANEL_TARGETS  # noqa: E402
from .contacts_data import ContactsSource, Me, source_from_environment  # noqa: E402
from .contacts_import import MAX_VCARD_BYTES  # noqa: E402
from .qr_code import QUIET_ZONE, matrix as qr_matrix  # noqa: E402
from .contacts_together import TogetherItem  # noqa: E402
from .eds_backend import (  # noqa: E402
    FAVOURITE_CATEGORIES, LOCAL_ADDRESS_BOOK, AddressBookName, ContactRecord, _contact_vcard, import_contacts_vcard,
)


APP_ID = "org.projectluma.Contacts"
ICON_NAME = APP_ID
# Contact and Together sit side by side from this window width (v70 900).
TWO_COLUMN_MIN_WIDTH = 900
# The page reads as a column, not a spread (v70 .cpad: 760 wide, two cards side by side).
PAGE_MAX_WIDTH = 760
# Together is never shorter than this, however little Contact holds (v70 .ctog).
TOGETHER_MIN_HEIGHT = 200
# Stacked under Contact, Together sizes to its items up to this (v70 .ctog .tgl at 900).
TOGETHER_STACKED_MAX = 320
# The page's margins on a computer and on a phone (v70 .cpad: 44/32/…, 70/16/… under 720).
PAGE_MARGINS = {"top": (44, 70), "start": (32, 16), "end": (32, 16), "bottom": (32, 24)}
# On a phone (the device, not a narrow window) the card's light runs under the clock, and the hero starts 170
# from the top of the screen (v71: the status bar and Back's row to 100, then .cpad's 70).
PHONE_DEVICE_PAGE_TOP = 170
# Your code: 150 in a phone's grown bar (v71 .ccodep .qr); on a computer a white tile, 160 with the code
# 12 in, 16 round (.mycode .qr).
CODE_SIZE, CODE_TILE, CODE_TILE_INSET, CODE_TILE_RADIUS = 150, 160, 12, 16
# How many people Add a person suggests as you type a username (v71 Contacts' add panel).
ADD_MATCHES = 3
# How many faces Share offers (v71 "Send ___'s card to").
SHARE_PEOPLE = 5
CODE_POP_WIDTH = 240                      # v71 .codepop

SIDEBAR = lumaui_tokens.SIDEBAR
# The face at the top of a person's page (v70 .chero .av).
HERO_FACE = 116

ALL = "all"
FAVOURITES = "favourites"
ME = "me"

#: Widget names (Gtk.Widget.set_name) for the elements v71 has, so a tool can find them.
#: The controls the gate opens (ct-lists, ct-add, ct-share, ct-more, ct-edit) carry one name in either
#: tier: the corner's or the foot's on a computer, the bar's on a phone.
WIDGET_NAMES = ("ct-sidebar", "ct-my-card", "ct-row", "ct-foot", "ct-island", "ct-light", "ct-corner", "ct-hero",
                "ct-avatar", "ct-name", "ct-handle", "ct-status", "ct-work", "ct-actions", "ct-contact",
                "ct-together", "ct-sees", "ct-invite", "ct-note", "ct-list-bar", "ct-card-bar", "ct-lists",
                "ct-add", "ct-add-field", "ct-add-results", "ct-share", "ct-favourite", "ct-more", "ct-edit",
                "ct-code", "ct-back", "ct-index")


# ── What an address book is (no GTK below this line until the widgets) ──────


def _digits(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def contact_matches(record: ContactRecord, query: str) -> bool:
    """Whether a contact answers a search, as the person types it.

    Every word of the query has to appear in the name, the phone number or the
    email address, ignoring case. A query that reads as a number is also
    matched on digits alone, so "555 01" finds "(555) 010-1234" however either
    was punctuated.
    """
    folded = query.casefold().strip()
    if not folded:
        return True
    text = " ".join((record.name or "", record.email or "", record.phone or "",
                     record.organization or "", record.handle or "")).casefold()
    phone_digits = _digits(record.phone or "")
    query_digits = _digits(folded)
    if query_digits and not any(character.isalpha() for character in folded):
        if query_digits in phone_digits or folded in text:
            return True
    return all(word in text or (word.isdigit() and word in phone_digits)
               for word in folded.split())


def in_list(record: ContactRecord, key: str) -> bool:
    """Whether a contact belongs to what the sidebar foot's filter shows."""
    if key == ALL:
        return True
    if key == FAVOURITES:
        return record.favourite
    kind, name = filter_parts(key)
    return name == record.book if kind == "book" else name in record.categories


def filter_key(kind: str, name: str) -> str:
    """A list's key: "list-" or "book-", then its name in hex. The key becomes part of a menu's action name,
    which takes only letters, digits, "-" and "." (a ":" or a space aborts GLib), and a list is named freely."""
    return f"{kind}-{name.encode('utf-8').hex()}"


def filter_parts(key: str) -> tuple[str, str]:
    """(kind, name) from filter_key's key."""
    kind, _, encoded = key.partition("-")
    try:
        return kind, bytes.fromhex(encoded).decode("utf-8")
    except ValueError:
        return kind, ""


def list_filters(records, names: dict[str, str] | None = None) -> list[tuple[str, str, str]]:
    """Lists (v71, the foot's filter and the phone bar's Lists): everyone, favorites, each list;
    each address book when there are several."""
    records, names = list(records), names or {}
    books = sorted({record.book for record in records if record.book}, key=str.casefold)
    lists = sorted({category for record in records for category in record.categories
                    if category.casefold() not in FAVOURITE_CATEGORIES},
                   key=lambda key: list(names).index(key) if key in names else len(names))
    return [(ALL, "All contacts", "users"),
            *(((FAVOURITES, "Favorites", "star"),) if any(record.favourite for record in records) else ()),
            *((filter_key("list", key), names.get(key, key), "list") for key in lists),
            *((filter_key("book", name), name, "book") for name in (books if len(books) > 1 else ()))]


def filter_counts(records, filters) -> dict[str, int]:
    """How many people each list shows (v71's counts beside every list)."""
    records = list(records)
    return {key: sum(in_list(record, key) for record in records) for key, _label, _icon in filters}


def user_lists(records, names: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """The lists a person can be added to (v71 ⋯ › Add to list): (category, name)."""
    return [(filter_parts(key)[1], label) for key, label, _icon in list_filters(records, names)
            if filter_parts(key)[0] == "list"]


_EMAIL = re.compile(r"\S+@\S+\.\S+")
_PHONE = re.compile(r"[+\d][\d\s().-]{5,}")


def add_query(text: str) -> tuple[str, str]:
    """What Add a person has been given (v71): ("email" | "phone", the text) for a new card,
    ("handle", the username without @) to look up, or ("", "") while it is too short to say."""
    value = text.strip()
    if _EMAIL.search(value):
        return "email", value
    if _PHONE.fullmatch(value):
        return "phone", value
    handle = value.removeprefix("@").casefold()
    return ("handle", handle) if len(handle) > 1 else ("", "")


def add_matches(records, handle: str, limit: int = ADD_MATCHES) -> list[ContactRecord]:
    """The people whose @username starts with what is typed, A to Z, at most `limit`."""
    found = (record for record in records if record.handle and record.handle.casefold().startswith(handle))
    return sorted(found, key=lambda record: (record.name or "").casefold())[:limit]


def letter(record: ContactRecord) -> str:
    first = (record.name or "").strip()[:1].upper()
    return first if first.isalpha() else "#"


def work_line(record: ContactRecord | Me) -> str:
    """Where they work, then what they do there (v70 .cwork: "Luma · Design")."""
    return " · ".join(part for part in (record.organization, record.role) if part)


def row_subtitle(record: ContactRecord) -> str:
    """The list's second line (v70): their @username, "Phone contact" when they aren't on Luma,
    or how to reach them when nobody knows yet; never the name again."""
    if record.handle:
        return f"@{record.handle}"
    for line in (work_line(record), record.phone, record.email):
        if line and _digits(line) != _digits(record.name) and line != record.name:
            return line
    return ""


def reach_line(record: ContactRecord) -> str:
    """Under the name: their @username, or the number they're reached on."""
    return f"@{record.handle}" if record.handle else record.phone or record.email


def when(timestamp: int, now: float | None = None) -> str:
    """A time today, a weekday this week, a date before that (v70 Together)."""
    moment = time.localtime(timestamp)
    today = time.localtime(now if now is not None else time.time())
    days = (time.mktime(today[:3] + (0, 0, 0, 0, 0, -1)) - time.mktime(moment[:3] + (0, 0, 0, 0, 0, -1))) / 86400
    if days < 1:
        return time.strftime("%-I:%M %p", moment)
    if days < 7:
        return time.strftime("%A", moment)
    return time.strftime("%b %-d" if moment.tm_year == today.tm_year else "%b %-d, %Y", moment)


#: Edit mode's fields, in the card's order: (field, label, placeholder).
EDIT_FIELDS = (("name", "Name", "Name", "name", "user"), ("phone", "Mobile", "Add mobile", "phone", "phone"),
               ("email", "Email", "Add email", "email", "mail"),
               ("handle", "Luma username", "@username", "text", "at-sign"),
               ("birthday", "Birthday", "Add birthday", "text", "cake"),
               ("address", "Address", "Add address", "text", "map-pin"),
               ("work", "Work", "Add work", "text", "briefcase"))


def shown_fields(record: ContactRecord | None) -> dict[str, str]:
    """Each editable field as the card shows it, so a save can send only what changed."""
    if record is None:
        return {key: "" for key in (*(field for field, *_rest in EDIT_FIELDS), "note")}
    return {"name": record.name, "phone": record.phone, "email": record.email, "birthday": record.birthday,
            "address": record.address, "work": work_line(record), "note": record.note, "handle": record.handle}


def with_fields(record: ContactRecord | None, uid: str, values: dict[str, str], book: str) -> ContactRecord:
    """The card as it reads after a save, before the address book's own copy arrives."""
    base = record or ContactRecord(uid, "", book=book)
    organization, _, role = values.get("work", work_line(base)).partition(" · ")
    account = values.get('luma_account', base.luma_account if values.get('handle', base.handle) == base.handle else '')
    return replace(base, uid=uid, name=values.get("name", base.name), phone=values.get("phone", base.phone),
                   handle=values.get("handle", base.handle),
                   email=values.get("email", base.email), birthday=values.get("birthday", base.birthday),
                   address=values.get("address", base.address), note=values.get("note", base.note),
                   role=role.strip(), organization=organization.strip(),
                   luma_account=account, on_luma=True if account else None)


def book_place(label: str) -> str:
    """An address book's name as the end of a sentence ("removed from your Luma account")."""
    places = {LOCAL_ADDRESS_BOOK.label: LOCAL_ADDRESS_BOOK.place, "Luma account": "your Luma account"}
    return places.get(label, label)


def first_name(record: ContactRecord | Me) -> str:
    return (record.name or "").split(" ")[0] or "them"


def my_link(me: Me) -> str:
    """Your profile link as it reads (v71 "simplyluma.com/@nick")."""
    return me.link or (f"simplyluma.com/@{me.handle}" if me.handle else "")


# ── The window ──────────────────────────────────────────────────────────────


class ContactsWindow(AppWindow):
    def __init__(self, application: Adw.Application, source: ContactsSource | None = None) -> None:
        self.source = source or source_from_environment()
        self.all_records: dict[str, ContactRecord] = {}
        self.records: dict[str, ContactRecord] = {}
        self.selected: ContactRecord | None = None
        self.search_text = ""
        self.list_key = ALL
        self.address_book: AddressBookName = LOCAL_ADDRESS_BOOK
        self.editing = False                     # the card is in edit mode
        self.importing = False
        self._reloading = False
        self._fields: dict[str, Gtk.Widget] = {}   # each field has .text
        self._pending_delete: tuple[ContactRecord, int] | None = None   # (record, timeout id)
        self._together_for = ""                  # whose Together is loading; late answers are dropped
        self._load_generation = 0                # the newest address-book read; older answers are dropped
        self.loaded = False                      # the first read has arrived
        self.phone = False                       # the kit's phone tier: list-first, the bars for the corner and foot
        self._saving = False                     # one save at a time: the card's controls wait for it
        self._filters: list[tuple[str, str, str]] = []   # Lists: (key, label, icon)
        self._panel: Gtk.Widget | None = None    # the open panel (a kit menu or sheet): one at a time
        self._add_results: Gtk.Box | None = None  # Add a person's matches, while its panel is open
        super().__init__(
            application=application, app_id=APP_ID, title="Contacts", icon_name=ICON_NAME,
            commands=self._application_commands(),
            default_width=1180, default_height=740, minimum_width=360, minimum_height=420,
        )
        # The list and a card: side by side on a computer; on a phone v71's list-first stack (K-NAV
        # ListFirst), the list full screen under a large "Contacts", a card pushed over it, and the card's
        # title island ‹ back to the list.
        sidebar, detail = self._build_sidebar(), self._build_detail()
        # The sidebar's outside is the kit's (v70 272 from the window edge to the island);
        # the window body's own gutter is already on its left.
        sidebar.set_size_request(_sidebar_pane("width"), -1)
        detail.set_hexpand(True)
        self.list_first = ListFirst(sidebar, detail, title="Contacts", on_back=self._listed)
        # Back on a phone is ListFirst's floating ‹ (v71 .phback: 44 square, no title; the hero names them).
        self.list_first.back_button.set_name("ct-back")
        self.set_body(self.list_first)
        self._add_breakpoints()
        self.connect("close-request", lambda *_: self._flush_delete(wait=True) and False)
        # The address book answers slowly (seconds, for an account that syncs),
        # so the window shows at once and fills in when it has. A two-pane
        # window then opens on someone: an empty card reads as a failure.
        self._show_empty()
        self._reload(then=self._open_first)

    def _open_first(self) -> None:
        """The card a two-pane window opens on. A phone opens on the list (v71 list-first), the card behind it."""
        wanted = getattr(self.source, "selected", None)      # a fixture names the card it opens on
        edit = getattr(self.source, "edit", False)
        if wanted == ME and self.source.me() is not None:
            self._show_me(edit=edit)
        elif wanted in self.records:
            self._show_contact(self.records[wanted], edit=edit)
            self._select_uid(wanted)
        else:
            first = next(iter(self.records.values()), None)
            if first is not None and self.selected is None and not self.editing:
                self._show_contact(first)
                self._select_uid(first.uid)
        self._show_page(self._opens_on_card())

    def _opens_on_card(self) -> bool:
        """Whether a window that becomes a phone shows the card rather than the list. v71 is list-first:
        the list, unless a card is being edited (or a fixture asks: LUMA_CONTACTS_OPEN, LUMA_CONTACTS_EDIT)."""
        return (not self.phone or self.editing or bool(getattr(self.source, "open", False))
                or bool(getattr(self.source, "edit", False)))

    def _push(self) -> None:
        """Someone was picked: their card comes forward (pushed over the list on a phone)."""
        self.list_first.show_detail()

    def _show_page(self, card: bool) -> None:
        if card:
            self.list_first.show_detail()
        else:
            self.list_first.show_list()

    @property
    def listing(self) -> bool:
        """A phone showing the list (a computer shows both)."""
        return self.phone and self.list_first.showing == "list"

    def _add_breakpoints(self) -> None:
        # Only one breakpoint applies at a time (the last added that matches),
        # so the phone one repeats the narrow one's setters.
        def stacked(breakpoint_: Adw.Breakpoint) -> Adw.Breakpoint:
            # Contact over Together.
            for row in self.rows_of_cards:
                breakpoint_.add_setter(row, "orientation", Gtk.Orientation.VERTICAL)
                breakpoint_.add_setter(row, "homogeneous", False)
            breakpoint_.add_setter(self.together_scroll, "propagate-natural-height", True)
            return breakpoint_

        narrow = stacked(Adw.Breakpoint.new(Adw.BreakpointCondition.parse(
            f"max-width: {TWO_COLUMN_MIN_WIDTH - 1}px")))
        # A narrow window narrows the sidebar too (v70 236 below 900).
        narrow.add_setter(self.list_host, "width-request", _sidebar_pane("narrow_width"))
        narrow.add_setter(self.sidebar, "width-request", _sidebar_pane("narrow_width") - SIDEBAR["gutter"])
        self.add_breakpoint(narrow)
        # The kit's phone tier (under 560, lumaui_tokens.PHONE_MAX_WIDTH): list first, the bars at the thumb.
        phone = stacked(Adw.Breakpoint.new(Adw.BreakpointCondition.parse(
            f"max-width: {lumaui_tokens.PHONE_MAX_WIDTH}px")))
        # ListFirst lays the list out full width on a phone.
        phone.add_setter(self.list_host, "width-request", -1)
        phone.add_setter(self.sidebar, "width-request", -1)
        # Back holds the page's top on a phone, so the hero starts lower (v70 .cpad at 720).
        for side, (desktop, handheld) in PAGE_MARGINS.items():
            if side == "top" and self.phone_device:
                handheld = PHONE_DEVICE_PAGE_TOP
            self.page.set_property(f"margin-{side}", desktop)
            phone.add_setter(self.page, f"margin-{side}", handheld)
        # The card paints under the clock on a phone (v71 #c-light from the top of the screen); the page
        # insets itself (PHONE_DEVICE_PAGE_TOP). The list is not in the island, so it keeps the frame's inset.
        self.set_phone_bleed(True)
        # Crossing the phone width redraws the window in the other shape (v71 "redraw on crossing").
        phone.connect("apply", lambda _b: self._set_phone(True))
        phone.connect("unapply", lambda _b: self._set_phone(False))
        self.add_breakpoint(phone)

    def _set_phone(self, phone: bool) -> None:
        """Into or out of the phone tier: the foot and the corner give way to the bars, and back."""
        if phone == self.phone:
            return
        self.phone = phone
        self._close_panel()
        if phone and self.loaded:
            self._show_page(self._opens_on_card())
        if self.foot is not None:
            self._sync_foot_controls()
        self._refresh_card_chrome()
        self._refresh_list_bar()

    # ── Commands (menus, shortcuts and buttons all run these) ───────────────

    def _application_commands(self) -> CommandRegistry:
        return CommandRegistry((
            CommandGroup("", (
                Command("contacts.new", "New contact", self._new_contact, "user-plus", shortcut=("Ctrl", "N")),
                Command("contacts.find", "Search people", self._focus_search, "search", shortcut=("Ctrl", "F")),
                Command("contacts.import", "Import vCard…", self._import_vcard, "download"),
            )),
            CommandGroup("", (
                Command("contacts.about", "About Contacts", self._show_about, "info"),
                Command("contacts.quit", "Quit Contacts", self.close, "log-out", shortcut=("Ctrl", "Q")),
            )),
        ))

    def _card_commands(self, record: ContactRecord) -> CommandRegistry:
        """⋯ in the corner on a computer (v71 .nmorepop): Block and Delete contact, each confirmed.
        (A phone's ⋯ is `_more_items`.) Block is offered only where the source makes it."""
        commands = []
        if self.source.can("block"):
            commands.append(Command("contact.block", "Block", lambda: self._confirm_block(record), "shield",
                                    destructive=True))
        commands.append(Command("contact.delete", "Delete contact", lambda: self._confirm_delete(record),
                                "trash-2", destructive=True))
        return CommandRegistry((CommandGroup("", tuple(commands)),))

    # ── Sidebar: the people, then the foot ──────────────────────────────────

    def _build_sidebar(self) -> Gtk.Widget:
        self.sidebar = NavigationSidebar()
        self.sidebar.set_name("ct-sidebar")
        self.my_card: AccountCard | None = None
        self.rows = self.sidebar.list
        # The kit's sidebar draws no scrollbar over its rows (contacts-06, f70daffbf): rows are v71's 252.
        self.rows.connect("row-activated", self._row_activated)
        self.rows.connect("row-selected", self._row_selected)
        self.list_empty = ListEmptyState("Loading contacts…")
        # The address book can take seconds to answer; say so until it has.
        self.rows.append(Gtk.ListBoxRow(selectable=False, activatable=False, child=self.list_empty))
        self.foot: SidebarFoot | None = None
        # The list page's bar (a phone's Search · Lists · Add a person), and its toasts.
        self.list_host = ToastHost(self.sidebar)
        # A to Z down the list's right edge on a phone, the list in its own lane (v71 lAZ; K-ROWS AZIndex
        # shows itself under 560 with six or more letters, and follows the rows as they change).
        self.index = AZIndex(self.rows, key=lambda row: getattr(getattr(row, "record", None), "name", ""))
        self.index.set_name("ct-index")
        self.list_host.add_overlay(self.index)
        self.list_bar = ActionCenter().attach(self.list_host)
        self.list_bar.set_name("ct-list-bar")
        # v71 .srch.keep: on the list's bar the search stays a field, taking the bar's spare width.
        self.bar_search = BarSearch("Search", label="Search people", keep=True, on_change=self._search_changed)
        return self.list_host

    def _build_foot(self, filters: list[tuple[str, str, str]]) -> None:
        """The foot on a computer (v71 lFoot): search, Lists (only when there are lists) and Add a person."""
        search_text = self.foot.entry.get_text() if self.foot is not None else ""
        if self.foot is not None:
            self.foot.get_parent().remove(self.foot)
            self.foot.heading.get_parent().remove(self.foot.heading)
        self._filters = filters
        lists = len(filters) > 1
        self.foot = SidebarFoot(
            search="Search people", on_search=self._search_changed,
            filters=filters if lists else (), filter=self.list_key if lists else None,
            on_filter=self._filter_changed,
            add=("Add a person", "user-plus", lambda: self._open_add(self.foot.add_button)))
        # v71's computer shows Show and the lists, without counts (the kit's filter menu); a phone's Lists has them.
        self.foot.set_name("ct-foot")
        self._sync_foot_controls()
        self.search = self.foot.entry
        self.search.set_text(search_text)
        self.sidebar.append_header(self.foot.heading)
        self.sidebar.append_footer(self.foot)

    def _sync_foot_controls(self) -> None:
        """Only the active presentation owns the semantic names used to invoke its actions."""
        self.foot.set_visible(not self.phone)
        if self.foot.filter_button is not None:
            self.foot.filter_button.set_name("ct-desktop-lists" if self.phone else "ct-lists")
        self.foot.add_button.set_name("ct-desktop-add" if self.phone else "ct-add")

    def _reload(self, then: Callable[[], None] | None = None, *, wait: bool = False) -> None:
        """Read the address book again off the main loop, then show what the search and list allow.

        `wait=True` reads in place (tests). A read that finishes after a newer
        one started is dropped, so a slow answer never overwrites a fresh one.
        """
        self._load_generation += 1
        generation = self._load_generation
        if wait:
            self._loaded(generation, *self.source.load(), then)
            return

        def read() -> None:
            records, book = self.source.load()
            GLib.idle_add(self._loaded, generation, records, book, then)

        threading.Thread(target=read, daemon=True, name="contacts-load").start()

    def _loaded(self, generation: int, records, book: AddressBookName, then) -> bool:
        if generation != self._load_generation:
            return GLib.SOURCE_REMOVE
        self.loaded = True
        pending = self._pending_delete[0].uid if self._pending_delete else None
        self.all_records = {record.uid: record for record in records if record.uid != pending}
        self.address_book = book
        self._apply_records()
        if not self.source.rehearsal:
            self._discover_luma_contacts(tuple(self.all_records.values()))
        if then is not None:
            then()
        return GLib.SOURCE_REMOVE

    def _discover_luma_contacts(self, records):
        """Enrich only positive, opt-in Hub matches without delaying the list."""
        if getattr(self, '_luma_discovery_busy', False):
            return
        from .connect_sync import load_identity
        if load_identity() is None:
            return
        checked = getattr(self, '_luma_discovery_checked', {})
        self._luma_discovery_checked = checked
        candidates = [r for r in records if not r.luma_account and (r.handle or r.email or r.phone)
                      and time.monotonic() - checked.get(r.uid, -3600) >= 600][:500]
        if not candidates:
            return
        self._luma_discovery_busy = True
        for record in candidates:
            checked[record.uid] = time.monotonic()
        def worker():
            import hashlib
            from concurrent.futures import ThreadPoolExecutor
            from .collaboration import PeopleDirectory
            changed = False
            try:
                directory = PeopleDirectory()
                identifiers = [('email', r.email) for r in candidates if r.email]
                identifiers += [('phone', r.phone) for r in candidates if r.phone.startswith('+') and r.phone[1:].isdigit()]
                matches = directory.discover(identifiers[:500]) if identifiers else []
                by_hash = {p['hash']: p for p in matches}
                def resolve(record):
                    if record.handle:
                        try: return record, directory.lookup(record.handle)
                        except Exception: return record, None
                    for value in (record.email.strip().lower(), record.phone.strip()):
                        if value:
                            person = by_hash.get(hashlib.sha256(('luma-discovery-v1:' + value).encode()).hexdigest())
                            if person and person.get('handle'): return record, person
                    return record, None
                with ThreadPoolExecutor(max_workers=4) as pool:
                    resolved = list(pool.map(resolve, candidates))
                latest = {r.uid: r for r in self.source.load()[0]}
                for old, person in resolved:
                    current = latest.get(old.uid)
                    if person and current and (current.handle, current.email, current.phone) == (old.handle, old.email, old.phone):
                        self.source.update(old.uid, {'handle': person['handle'], 'luma_account': person['account']})
                        changed = True
            except Exception:
                # Offline/opt-out is not evidence of "not on Luma".
                pass
            GLib.idle_add(self._luma_discovery_finished, changed)
        threading.Thread(target=worker, daemon=True, name='contacts-luma-discovery').start()

    def _luma_discovery_finished(self, changed):
        self._luma_discovery_busy = False
        if changed:
            self._reload()
        return GLib.SOURCE_REMOVE

    def _apply_records(self) -> None:
        filters = list_filters(self.all_records.values(), self.source.lists)
        if self.list_key not in {key for key, _label, _icon in filters}:
            self.list_key = ALL
        if self.foot is None or filters != self._filters:
            self._build_foot(filters)
        self._render_list()
        self._refresh_list_bar()

    def _render_list(self) -> None:
        """The list, narrowed to the search and the list. Filters what is loaded; never reloads."""
        self._reloading = True
        # The empty label is reused across asynchronous loads and search edits.
        # Removing a row from the list does not detach that row's child.
        empty_holder = self.list_empty.get_parent()
        if isinstance(empty_holder, Gtk.ListBoxRow):
            empty_holder.set_child(None)
        self.sidebar.clear()
        self._show_my_card()
        records = sorted(
            (record for record in self.all_records.values()
             if in_list(record, self.list_key) and contact_matches(record, self.search_text)),
            key=lambda record: (record.name or "").strip().casefold())
        self.records = {record.uid: record for record in records}
        current = ""
        for record in records:
            if letter(record) != current:
                current = letter(record)
                self.sidebar.append_section(current)
            row = NavigationRow(record.name, subtitle=row_subtitle(record),
                                icon_widget=PersonAvatar(record.name, SIDEBAR["face"], picture=_picture(record.photo),
                                                         hue=record.hue))
            row.set_name("ct-row")
            row.record = record
            self.sidebar.append_row(row)
        if not records:
            self.list_empty.set_label(
                f"No contacts match “{self.search_text}”.\nTry a name, a number or an email address."
                if self.search_text else
                f"No contacts yet. Press {modifier_text('Ctrl')}+N to add the first one.")
            holder = Gtk.ListBoxRow(selectable=False, activatable=False, child=self.list_empty)
            self.rows.append(holder)
        if self.selected == ME:
            pass
        elif self.selected is not None and not self.editing:
            fresh = self.all_records.get(self.selected.uid)
            if fresh is None:
                self._show_empty()
            else:
                # The card stays while the list narrows around it.
                self._show_contact(fresh)
                self._select_uid(fresh.uid)
        elif self.selected is None and not self.editing:
            self._show_empty()
        self._reloading = False

    def _show_my_card(self) -> None:
        """My card at the top of the list (v70 .cmecard), when there is a card for you."""
        me = self.source.me()
        if self.my_card is not None:
            holder = self.my_card.get_parent()
            if isinstance(holder, Gtk.ListBoxRow):
                holder.set_child(None)
                if holder.get_parent() is self.rows:
                    self.rows.remove(holder)
            elif holder is not None:
                holder.remove(self.my_card)
            self.my_card = None
        if me is None or self.search_text or self.list_key != ALL:
            return
        caption = f"My card · @{me.handle}" if me.handle else "My card"
        self.my_card = AccountCard(me.name, caption=caption, picture=_picture(me.photo),
                                   on_activate=self._open_me, selected=self.selected == ME, hue=me.hue, recessed=True)
        self.my_card.set_name("ct-my-card")
        holder = Gtk.ListBoxRow(child=self.my_card, selectable=False, activatable=False)
        holder.add_css_class("ct-account-row")
        self.rows.insert(holder, 0)

    def _open_me(self) -> None:
        self.rows.unselect_all()
        self._show_me()
        self._push()

    def _select_uid(self, uid: str) -> None:
        child = self.rows.get_first_child()
        while child is not None:
            if getattr(child, "record", None) is not None and child.record.uid == uid:
                self.rows.select_row(child)
                return
            child = child.get_next_sibling()

    def _search_changed(self, text: str) -> None:
        text = text.strip()
        if text != self.search_text:
            self.search_text = text
            # The two fields are one search: the foot's on a computer, the bar's on a phone.
            if self.foot is not None and self.foot.entry.get_text().strip() != text:
                self.foot.entry.set_text(text)
            if self.bar_search.text.strip() != text:
                self.bar_search.set_text(text)
            self._render_list()

    def _filter_changed(self, key: str) -> None:
        self.list_key = key
        self._render_list()
        self._refresh_list_bar()

    def _focus_search(self) -> None:
        self.list_first.show_list()
        if self.phone:
            self.bar_search.focus()
        else:
            self.search.grab_focus()

    def _row_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if getattr(row, "record", None) is not None:
            self._show_contact(row.record)
            self._push()

    def _row_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if self._reloading or row is None or getattr(row, "record", None) is None:
            return
        if not (self.editing and self.selected is not None and self.selected.uid == row.record.uid):
            self._show_contact(row.record)

    # ── Detail: the corner, then the page ──────────────────────────────────

    def _build_detail(self) -> Gtk.Widget:
        island = Island()
        island.set_hexpand(True)
        island.set_name("ct-island")
        self.corner_slot = Adw.Bin(halign=Gtk.Align.END, valign=Gtk.Align.START)

        self.page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # Hero: face, name, what they do, then the shared contact actions.
        self.hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.hero.add_css_class("ct-hero")
        self.hero.set_name("ct-hero")
        self.page.append(self.hero)
        # Two rows of two cards (v70 .cgrid), each stacked in a narrow window:
        # Contact beside Together, then What they see (or Not on Luma) beside the Private note.
        self.grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=False)
        self.grid.add_css_class("ct-grid")
        self.rows_of_cards = []
        for _ in range(2):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True, vexpand=False)
            row.add_css_class("ct-card-row")
            self.rows_of_cards.append(row)
            self.grid.append(row)
        self.contact_card = _card("ct-contact")
        self.together_card, self.together_list = self._build_together()
        self.sees_card = _card("ct-sees")
        self.note_card = _card("ct-note")
        self.rows_of_cards[0].append(self.contact_card)
        self.rows_of_cards[0].append(self.together_card)
        self.rows_of_cards[1].append(self.sees_card)
        self.rows_of_cards[1].append(self.note_card)
        self.page.append(self.grid)

        clamp = Adw.Clamp(maximum_size=PAGE_MAX_WIDTH, tightening_threshold=PAGE_MAX_WIDTH, child=self.page)
        self.scroll = ScrollView(clamp)
        # The person's light sits behind the page, at the top of the island (v70 #c-light).
        self.light = ContentLitHeader()
        self.light.picture.set_name("ct-light")
        backdrop = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True, hexpand=True)
        backdrop.append(self.light)
        overlay = Gtk.Overlay(child=backdrop)
        overlay.add_overlay(self.scroll)
        overlay.add_overlay(self.corner_slot)

        self.card_stack = Gtk.Stack(vexpand=True, hexpand=True)
        self.card_stack.add_named(overlay, "card")
        self.empty_state = EmptyState(
            "Your contacts", "Choose someone to see their card.", "luma-empty-contacts-symbolic",
            primary=("New contact", self._new_contact))
        self.fresh_state = EmptyState(
            "No contacts yet", "Add the first one, or bring them in from a vCard file.",
            "luma-empty-contacts-symbolic", primary=("New contact", self._new_contact),
            secondary=("Import vCard…", self._import_vcard))
        self.card_stack.add_named(Gtk.Box(), "loading")
        self.card_stack.add_named(self.empty_state, "empty")
        self.card_stack.add_named(self.fresh_state, "fresh")
        island.append(self.card_stack)
        # Toasts centre on this island, above anything at its foot: the card's bar on a phone.
        self.card_host = ToastHost(island)
        self.card_bar = ActionCenter().attach(self.card_host)
        self.card_bar.set_name("ct-card-bar")
        return self.card_host

    def _build_together(self) -> tuple[Gtk.Widget, Gtk.Box]:
        """Together: as tall as Contact beside it, never shorter than 200; its list scrolls inside (#26).

        The card never takes its height from its items: its list sits in a
        scroller whose natural height is not propagated, so the row's height is
        set by the Contact card and Together fills it however late its items
        arrive. The list fades out at the card's foot (v70 .ctog .tgl).
        Stacked in a narrow window it sizes to its items, up to 320.
        """
        items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        items.add_css_class("ct-together-list")
        self.together_scroll = ScrollView(items, fade_bottom=True)
        self.together_scroll.set_propagate_natural_height(False)
        self.together_scroll.set_max_content_height(TOGETHER_STACKED_MAX)
        card = _card("ct-together")
        card.set_valign(Gtk.Align.FILL)
        card.set_size_request(-1, TOGETHER_MIN_HEIGHT)
        card.append(_heading("Together"))
        card.append(self.together_scroll)
        return card, items

    def _set_corner(self, pill: CornerPill | None, edit: bool = False) -> None:
        self.corner = pill
        self.corner_slot.set_child(pill)
        if pill is not None:
            pill.set_name("ct-corner")
            for key, name in (("share", "ct-share"), ("more", "ct-more"), ("actions.0", "ct-edit")):
                if key in pill.controls:
                    pill.controls[key].set_name(name)
            if edit:
                pill.edit(self._cancel_edit, self._finish_edit)

    # ── The card's controls: the corner on a computer, the bar on a phone ──

    def _refresh_card_chrome(self) -> None:
        """Share · Edit · ⋯ (or Cancel · Done) for what the card shows: in the corner pill on a computer,
        in the card's bar on a phone (v71: the management actions move from the corner to the bar)."""
        showing = self.card_stack.get_visible_child_name() == "card" and not self._saving
        if not showing:
            self._set_corner(None)
            self.card_bar.hide_bar()
        elif self.phone:
            self._set_corner(None)
            self._show_card_bar()
        else:
            self.card_bar.hide_bar()
            self._set_corner(self._corner(), self.editing)

    def _corner(self) -> CornerPill:
        if self.selected == ME:
            edit = [("pencil", "Edit", lambda: self._show_me(edit=True))] if self.source.can("edit-me") else []
            return CornerPill(share=self._open_my_code, actions=edit, labelled=True,
                              primary="Edit" if edit else None)
        record = self.selected
        return CornerPill(share=(lambda button: self._open_share(button, record)) if record else None,
                          actions=[("pencil", "Edit", lambda: self._show_contact(record, edit=True))],
                          more=self._card_commands(record) if record else None,
                          labelled=True, primary="Edit")

    def _show_card_bar(self) -> None:
        """The card's bar on a phone (v71 #c-phbar): Edit (the key, in words), Share, Favorite, ⋯;
        your own card is Edit and Share; editing is Cancel and Done. Share and ⋯ grow the bar.
        v71 spans it across the 16 gutter (show_bar fill): Edit takes the rest, Cancel and Done share it."""
        if self.editing:
            self.card_bar.show_bar([BarAction("", "Cancel", fill=True, on_activate=self._cancel_edit),
                                    BarAction("", "Done", primary=True, fill=True, on_activate=self._finish_edit)],
                                   fill=True)
            return
        names: dict[int, str] = {}
        items: list[BarAction] = []
        if self.selected == ME:
            if self.source.can("edit-me"):
                items.append(BarAction("pencil", "Edit", primary=True, keep_label=True,
                                       on_activate=lambda: self._show_me(edit=True)))
            items.append(BarAction("share-2", tooltip="Share my card", key="share",
                                   panel=lambda: self._code_panel(share=True)))
        else:
            record = self.selected
            items.append(BarAction("pencil", "Edit", primary=True, keep_label=True,
                                   on_activate=lambda: self._show_contact(record, edit=True)))
            items.append(BarAction("share-2", tooltip="Share contact", key="share",
                                   panel=lambda: self._share_panel(record)))
            if self.source.can("favourite"):
                items.append(BarAction("star", tooltip="Remove from favorites" if record.favourite
                                       else "Add to favorites", active=record.favourite,
                                       on_activate=lambda: self._toggle_favourite(record)))
                names[id(items[-1])] = "ct-favourite"
            items.append(BarAction("ellipsis", tooltip="More", key="more",
                                   panel=lambda: panel_list(self._more_items(record), label="More")))
            names[id(items[-1])] = "ct-more"
        for item in items:
            names.setdefault(id(item), {"pencil": "ct-edit", "share-2": "ct-share"}.get(item.icon, ""))
        self.card_bar.show_bar(items, fill=True)
        _name_bar_items(self.card_bar, names)

    def _refresh_list_bar(self) -> None:
        """The list's bar on a phone (v71): Search, Lists (lit while one is chosen), Add a person."""
        if not self.phone:
            self.list_bar.hide_bar()
            return
        items: list[object] = [self.bar_search]
        names: dict[int, str] = {}
        if self.foot is not None and self.foot.filter_button is not None:
            label = next((name for key, name, _icon in self._filters if key == self.list_key), "All contacts")
            items.append(BarAction("list-filter", tooltip=f"Lists: {label}", active=self.list_key != ALL,
                                   key="lists", panel=self._lists_panel))
            names[id(items[-1])] = "ct-lists"
        items.append(BarAction("user-plus", tooltip="Add a person", on_activate=self._grow_add))
        names[id(items[-1])] = "ct-add"
        self.list_bar.show_bar(items, fill=True)
        _name_bar_items(self.list_bar, names)

    def _arrange_cards(self, *, mine: bool) -> None:
        """Someone's card: Contact beside Together, then What they see beside the note. Yours: Contact beside
        What people see (v71 .cgrid holds what is shown, two to a row)."""
        first, second = self.rows_of_cards
        wanted = first if mine else second
        if self.sees_card.get_parent() is not wanted:
            self.sees_card.get_parent().remove(self.sees_card)
            if mine:
                wanted.append(self.sees_card)
            else:
                wanted.prepend(self.sees_card)

    def _clear(self, box: Gtk.Box, keep: int = 0) -> None:
        child = box.get_first_child()
        for _ in range(keep):
            child = child.get_next_sibling() if child is not None else None
        while child is not None:
            following = child.get_next_sibling()
            box.remove(child)
            child = following

    def _show_empty(self) -> None:
        self.selected = None
        self.editing = False
        self._fields = {}
        self.card_stack.set_visible_child_name(
            "loading" if not self.loaded else "empty" if self.all_records else "fresh")
        self._refresh_card_chrome()

    def _show_contact(self, record: ContactRecord | None, *, edit: bool = False) -> None:
        """One person's card, or the new-contact card when `record` is None."""
        self.selected = record
        self.editing = edit
        self._fields = {}
        self.card_stack.set_visible_child_name("card")
        if self.my_card is not None:
            self.my_card.remove_css_class("on")
        name = record.name if record else "New contact"
        picture = _picture(record.photo) if record else None
        person = Person(name, phone=record.phone if record else "", email=record.email if record else "",
                        username=record.handle if record else "", picture=picture)

        # The corner (computer) or the bar (phone): Share, Edit (the key), ⋯; Cancel · Done while editing.
        self._refresh_card_chrome()

        light, focus = self.source.light(record) if record else (b"", (0.5, 0.5))
        actions = ContactActions(person, handler=self._contact_action) if record and not edit else None
        if actions is not None:
            # The shared action row assumes a handler can place a Luma video call; Contacts has no video
            # provider yet, so a real source does not offer a dead action (the fixture rehearses it, as Phone's).
            if not self.source.rehearsal:
                actions.buttons["video"].set_sensitive(False)
                actions.buttons["video"].set_tooltip_text("Video calling is not available on this device")
            actions.buttons["call"].set_sensitive(bool(record.phone))
        self._fill_hero(name, record.name if record else "", picture, _picture(light), focus, edit,
                        hue=record.hue if record else None,
                        reach=reach_line(record) if record else "",
                        status=True if record is not None and record.on_luma is True else None,
                        work=work_line(record) if record else "",
                        actions=actions)
        self._arrange_cards(mine=False)
        self._fill_contact_card(record, edit)
        self._load_together(record)
        self._fill_sees_card(record, edit)
        self._fill_note_card(record, edit)
        self.scroll.get_vadjustment().set_value(0)
        if edit:
            self.name_field.grab_focus()

    def _show_me(self, *, edit: bool = False) -> None:
        """Your own card (v71 My card): what people see of you, and your link; Edit where the source can."""
        me = self.source.me()
        if me is None:
            return
        edit = edit and self.source.can("edit-me")
        self.selected = ME
        self.editing = edit
        self._fields = {}
        self.card_stack.set_visible_child_name("card")
        if self.my_card is not None:
            self.my_card.add_css_class("on")
        self._arrange_cards(mine=True)
        picture = _picture(me.photo)
        self._fill_hero(me.name, me.name, picture, picture, (0.5, 0.5), edit, hue=me.hue,
                        reach=f"@{me.handle}" if me.handle else me.phone, status=True if me.handle else None,
                        work=work_line(me), actions=None)
        card = self.contact_card
        self._clear(card)
        card.append(_heading("Contact"))
        mine = {"phone": me.phone, "email": me.email, "work": work_line(me)}
        for key, label, placeholder, purpose, icon in EDIT_FIELDS:
            if key not in mine:
                continue
            if edit:
                field = FactRow(icon, label, mine[key], editable=True, placeholder=placeholder, purpose=purpose)
                self._fields[key] = field
                card.append(field)
            elif mine[key]:
                card.append(FactRow(icon, label, mine[key]))
        if edit:
            self._fields["name"] = self.name_field
        self.together_card.set_visible(False)
        self._together_for = ""
        card = self.sees_card
        self._clear(card)
        card.set_visible(not edit)
        card.append(_heading("What people see"))
        handle = f"<b>@{GLib.markup_escape_text(me.handle)}</b>" if me.handle else "your number"
        card.append(_paragraph(f"Your name, photo and {handle}. Your phone number and email stay hidden "
                               "unless you share them with someone.", prose=True))
        link = my_link(me)
        if link:
            card.append(FactRow("link", "Your link", link))
        self.note_card.set_visible(False)
        self.scroll.get_vadjustment().set_value(0)
        self._refresh_card_chrome()
        if edit:
            self.name_field.grab_focus()

    def _fill_hero(self, name: str, title: str, picture, light, focus, edit: bool, *, hue: int | None,
                   reach: str, status: bool | None, work: str, actions: Gtk.Widget | None) -> None:
        """Their light, face, name (its own editor while editing), how to reach them, what they do."""
        hue = hue if hue is not None else person_hue(name)
        self.light.set_source(picture=light, name=name, focus=focus, hue=hue)
        hue_class(self.page, hue)                 # the page's icons wear their hue (v70 --ch)
        self._clear(self.hero)
        avatar = PersonAvatar(name, HERO_FACE, picture=picture, hue=hue)
        avatar.set_name("ct-avatar")
        self.hero.append(avatar)
        self.name_field = HeroTitleField(title, editable=edit, placeholder="Name")
        self.name_field.set_name("ct-name")
        self.hero.append(self.name_field)
        if reach or status is True:
            line = Gtk.Box(halign=Gtk.Align.CENTER)
            line.add_css_class("ct-reach")
            if reach:
                handle = apply_type(Gtk.Label(label=reach), "lead")
                handle.set_name("ct-handle")
                line.append(handle)
            if status is True:
                pill = StatusPill("on-luma")
                pill.set_name("ct-status")
                line.append(pill)
            self.hero.append(line)
        if work:
            label = apply_type(Gtk.Label(label=work, justify=Gtk.Justification.CENTER, wrap=True), "body", muted=True)
            label.set_name("ct-work")
            label.add_css_class("ct-work")
            self.hero.append(label)
        if actions is not None:
            # Four equal tiles, never wider than the kit's row (v70 .lcacts: 376).
            actions.set_name("ct-actions")
            row = Adw.Clamp(maximum_size=lumaui_tokens.STACK["row_max_width"], child=actions)
            row.add_css_class("ct-actions")
            self.hero.append(row)

    def _fill_sees_card(self, record: ContactRecord | None, edit: bool) -> None:
        """What they see of you, with the one thing you choose to share; or, not on Luma, an invite."""
        card = self.sees_card
        self._clear(card)
        card.set_visible(record is not None and record.on_luma is True and not edit)
        if record is None or record.on_luma is not True or edit:
            return
        first = first_name(record)
        card.set_name("ct-sees")
        card.append(_heading(f"What {first} sees"))
        me = self.source.me()
        available = (("mobile", "phone", "Share my mobile number", self.source.sharing.share_mobile),
                     ("email", "mail", "Share my email address", self.source.sharing.share_email))
        for field, icon, label, getter in available:
            if me is None or not getattr(me, "phone" if field == "mobile" else field):
                continue
            switch = Gtk.Switch(active=getter(record.uid), valign=Gtk.Align.CENTER)
            switch.update_property([Gtk.AccessibleProperty.LABEL], [f"{label} with {first}"])
            switch.connect("notify::active", lambda sw, _p, key=field: None if getattr(
                sw, "_reverting", False) else self._share_detail(record, key, sw.get_active(), sw))
            row = Gtk.Box()
            row.add_css_class("ct-share-row")
            row.append(_glyph(icon))
            row.append(apply_type(Gtk.Label(label=label, xalign=0, hexpand=True), "body"))
            row.append(switch)
            card.append(row)

    def _share_detail(self, record: ContactRecord, field: str, shared: bool, switch: Gtk.Switch) -> None:
        try:
            self.source.sharing.set_shared(record.uid, field, shared)
        except OSError as error:
            switch._reverting = True
            switch.set_active(not shared)
            switch._reverting = False
            self._toast(f"That wasn’t saved: {error.strerror or error}", kind="error")
            return
        first = first_name(record)
        detail = "number" if field == "mobile" else "email address"
        self._toast(f"{first} can now see your {detail}" if shared else f"Your {detail} is hidden again",
                   kind="done" if shared else "undone")

    def _fill_note_card(self, record: ContactRecord | None, edit: bool) -> None:
        """The private note (vCard NOTE): only you see it; it is edited with the rest of the card."""
        card = self.note_card
        self._clear(card)
        card.set_visible(record is not None or edit)
        card.append(_heading("Private note"))
        if edit:
            field = ParagraphField(record.note if record else "", placeholder="Add a private note",
                                   label="Private note")
            field.add_css_class("ct-note-editor")
            field.set_size_request(-1, 96)
            self._fields["note"] = field
            card.append(field)
        elif record is not None and record.note:
            card.append(_paragraph(record.note, markup=False))
        lock = Gtk.Box(halign=Gtk.Align.START)
        lock.add_css_class("ct-note-private")
        lock.append(_glyph("lock"))
        lock.append(apply_type(Gtk.Label(label="Only you can see this"), "caption"))
        card.append(lock)

    def _fill_contact_card(self, record: ContactRecord | None, edit: bool) -> None:
        card = self.contact_card
        self._clear(card)
        card.append(_heading("Contact"))
        if edit:
            # The same facts, each its own field (v70: every row is shown while editing).
            shown = shown_fields(record)
            self._fields["name"] = self.name_field
            for key, label, placeholder, purpose, icon in EDIT_FIELDS[1:]:
                field = FactRow(icon, label, shown[key], editable=True, placeholder=placeholder, purpose=purpose)
                self._fields[key] = field
                card.append(field)
            return
        facts = [(value, label, icon) for value, label, icon in (
            (record.phone, "Mobile", "phone"),
            (record.email, "Email", "mail"),
            (f"@{record.handle}" if record.handle else "", "Luma username", "at-sign"),
            (record.birthday, "Birthday", "cake"),
            (record.address, "Address", "map-pin"),
            (work_line(record), "Work", "briefcase"),
        ) if value]
        for value, label, icon in facts:
            card.append(FactRow(icon, label, value))
        if not facts:
            card.append(_paragraph("Only their @username. That’s all you need to reach someone on Luma."
                                   if record.handle else "No phone number or email on this card yet.",
                                   muted=True, markup=False))

    # ── Together: loaded off the main loop, applied only if still current ───

    def _load_together(self, record: ContactRecord | None) -> None:
        self._clear(self.together_list)
        self.together_card.set_visible(record is not None)
        if record is None:
            return
        token = self._together_for = f"{record.uid}:{time.monotonic()}"

        def read() -> None:
            items = self.source.together(record)
            GLib.idle_add(self._together_loaded, token, record, items)

        threading.Thread(target=read, daemon=True, name="contacts-together").start()

    def _together_loaded(self, token: str, record: ContactRecord, items: tuple[TogetherItem, ...]) -> bool:
        if token != self._together_for:
            return GLib.SOURCE_REMOVE          # someone else's card is showing now
        self._clear(self.together_list)
        for item in items:
            if item.kind == "file":
                widget = FileCard(item.path, name=item.title, size=item.size or None,
                                  content_type=item.content_type or None, subtitle=item.subtitle)
            else:
                widget = DetailsItem(item.title, item.subtitle,
                                     icon=item.icon or TOGETHER_ICONS.get(item.kind, "message-square"),
                                     when=item.when or when(item.timestamp),
                                     on_activate=(lambda: self._launch_uri(f"sms:{record.phone}"))
                                     if item.kind == "conversation" and record.phone else None)
            self.together_list.append(widget)
        if not items:
            self.together_list.append(_paragraph(
                f"Nothing shared yet. Conversations, notes and files you share with {first_name(record)} "
                "will gather here.", muted=True, markup=False))
        return GLib.SOURCE_REMOVE

    # ── Editing (the existing save path, unchanged) ─────────────────────────

    def _new_contact(self, fields: dict[str, str] | None = None) -> None:
        """The new card, ready to edit; `fields` fills what Add a person was given (a phone or an email)."""
        self._flush_delete()
        self.rows.unselect_all()
        self._show_contact(None, edit=True)
        for key, value in (fields or {}).items():
            if key in self._fields:
                field = self._fields[key]
                (field if isinstance(field, HeroTitleField) else field.entry).set_text(value)
        self._push()
        self.name_field.grab_focus()

    def _cancel_edit(self) -> None:
        if self.selected == ME:
            self._show_me()
        elif self.selected is not None:
            self._show_contact(self.selected)
        else:
            self._show_empty()
            first = next(iter(self.records.values()), None)
            if first is not None:
                self._show_contact(first)
                self._select_uid(first.uid)

    def _finish_edit(self) -> None:
        """Save what changed, and only that: the rest of the card stays as it is (update_contact).

        The address book is slow to answer, so the save runs off the main
        loop; the card shows the new values as soon as it has succeeded.
        """
        values = {key: field.text.strip() for key, field in self._fields.items()}
        if not values.get("name"):
            self._refresh_card_chrome()          # still editing: Cancel · Done again
            self.name_field.grab_focus()
            self._toast("Enter a name for the contact.", kind="warning")
            return
        if self.selected == ME:
            self._save_me(values)
            return
        if values.get("handle"):
            from .messages_luma import handle_from
            handle = handle_from(values["handle"])
            if handle is None:
                self._toast("Enter a valid Luma username, such as @alex.", kind="warning")
                self._fields["handle"].entry.grab_focus()
                return
            values["handle"] = handle
        record = self.selected
        changes = {key: value for key, value in values.items() if value != shown_fields(record)[key]}
        person = getattr(self, "_resolved_luma_person", None)
        if person and person.get("handle", "").casefold() == values.get("handle", "").casefold():
            changes["luma_account"] = person["account"]
            values["luma_account"] = person["account"]
        if record is not None and not changes:
            self._show_contact(record)
            return
        busy = self._toast("Saving…", busy=True)
        self._saving = True                      # one save at a time
        self._refresh_card_chrome()

        def save() -> None:
            try:
                if not self.source.rehearsal and changes.get('handle') and 'luma_account' not in changes:
                    from .connect_sync import load_identity
                    if load_identity() is not None:
                        from .collaboration import PeopleDirectory
                        matched = PeopleDirectory().lookup(changes['handle'])
                        changes['luma_account'] = matched['account']
                        values['luma_account'] = matched['account']
                uid = self.source.update(record.uid, changes) if record else self.source.create(changes)
                error = ""
            except (ValueError, RuntimeError, OSError, GLib.Error) as failure:
                uid, error = "", getattr(failure, "message", None) or str(failure) or "The contact wasn’t saved."
            GLib.idle_add(self._saved, busy, record, values, uid, error)

        threading.Thread(target=save, daemon=True, name="contacts-save").start()

    def _saved(self, busy: Toast, record: ContactRecord | None, values: dict[str, str], uid: str, error: str) -> bool:
        busy.dismiss()
        self._saving = False
        if error:
            self._show_contact(record, edit=True)
            self._toast(error, kind="error")
            return GLib.SOURCE_REMOVE
        saved = with_fields(record, uid, values, self.address_book.label)
        self.all_records[uid] = saved
        self.editing = False
        self.selected = saved
        self._clear_search()
        self._apply_records()
        self._show_contact(saved)
        self._select_uid(uid)
        self._toast("Saved" if record else f"Added {saved.name}", kind="saved" if record else "added")
        self._reload()                            # the address book's own copy, when it answers
        return GLib.SOURCE_REMOVE

    def _save_me(self, values: dict[str, str]) -> None:
        """Your own card (a write only the fixture makes yet: ContactsSource.can("edit-me"))."""
        me = self.source.me()
        shown = {"name": me.name, "phone": me.phone, "email": me.email, "work": work_line(me)}
        changes = {key: value for key, value in values.items() if value != shown.get(key)}
        if changes:
            try:
                self.source.update_me(changes)
            except (NotImplementedError, OSError, ValueError) as error:
                self._toast(f"Your card wasn’t saved: {error}", kind="error")
                return
        self._show_my_card()
        self._show_me()
        if changes:
            self._toast("Saved", kind="saved")

    def _clear_search(self) -> None:
        self.search_text = ""
        self.search.set_text("")
        self.bar_search.set_text("")

    # ── Panels: Lists, Add a person, your code, Share, ⋯ ─────────────────────
    # On a phone the bar grows into each (K-BAR: ActionCenter.grow, BarAction(panel=)); on a computer
    # the foot and the corner open the kit's menus and sheets at their buttons.

    def _popup(self, menu: Gtk.Widget, anchor: Gtk.Widget) -> None:
        """A computer's panel: a kit FloatingMenu at its button, one at a time."""
        self._close_panel()
        self._panel = menu
        menu.popup(anchor, align="start")

    def _close_panel(self) -> None:
        menu, self._panel = self._panel, None
        if menu is not None and getattr(menu, "is_open", False):
            menu.close()
        for center in (self.list_bar, self.card_bar):
            if center.grown is not None:
                center.fold_panel()
        self._add_results = None

    def _lists_panel(self) -> Gtk.Widget:
        """Lists on a phone (v71): Show, then everyone, favorites and each list with its count, the shown one
        checked. Picking one narrows the list and folds the bar."""
        counts = filter_counts(self.all_records.values(), self._filters)
        rows = [PanelRow(label, icon=icon, count=counts.get(key), selected=key == self.list_key,
                         on_activate=lambda key=key: self.foot.set_filter(key, notify=True))
                for key, label, icon in self._filters]
        return panel_list(["Show", *rows], label="Lists")

    def _grow_add(self) -> None:
        """Add a person on a phone (v71): the field replaces the bar's row (with ✕); its matches, "Add ___ as a
        new contact", and Show my code and Scan a code rise above it."""
        field = PanelField("user-plus", "Username, phone or email", on_change=self._add_typed,
                           on_submit=self._add_submit)
        field.set_name("ct-add-field")
        field.entry.update_property([Gtk.AccessibleProperty.LABEL], ["Add a person"])
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.append(PanelHeading("Add a person"))
        self._add_results = self._new_add_results()
        panel.append(self._add_results)
        panel.append(BarTiles([BarTile("scan", "Show my code", lambda: self.list_bar.grow("code", self._code_panel())),
                               BarTile("camera", "Scan a code", self._scan_code)], columns=2))
        self.list_bar.grow("add", panel, entry=field, anchor=_find_named(self.list_bar, "ct-add"),
                           on_fold=lambda: setattr(self, "_add_results", None))

    def _open_add(self, anchor: Gtk.Widget) -> None:
        """Add a person on a computer (v71 .caddpop): the field and its matches, then Show my code and Scan a code."""
        field = PanelField("user", "Username, phone or email", on_change=self._add_typed,
                           on_submit=self._add_submit)
        field.set_name("ct-add-field")
        field.entry.update_property([Gtk.AccessibleProperty.LABEL], ["Add a person"])
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        section.append(field)
        results = self._new_add_results()
        section.append(results)
        rows: list[object] = ["Add a person", MenuSection(section), None,
                              RichMenuItem("Show my code", icon="scan", subtitle="Someone scans it to add you",
                                           on_activate=lambda: self._open_my_code(anchor)),
                              RichMenuItem("Scan a code", icon="camera", subtitle="Add someone from their code",
                                           on_activate=self._scan_code)]
        self._popup(FloatingMenu(rows, label="Add a person", title="Add a person", width="wide"), anchor)
        self._add_results = results
        GLib.idle_add(lambda: (field.grab_focus(), False)[1])

    @staticmethod
    def _new_add_results() -> Gtk.Box:
        results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        results.set_name("ct-add-results")
        return results

    def _add_typed(self, text: str) -> None:
        """Live answers as Add a person is typed into (v71): a phone or an email offers a new card with it;
        a username lists the people it starts, or says no one on Luma has it."""
        self._add_lookup_generation = getattr(self, "_add_lookup_generation", 0) + 1
        self._add_current_query = text
        self._resolved_luma_person = None
        results = self._add_results
        if results is None:
            return
        self._clear(results)
        kind, value = add_query(text)
        if kind in ("handle", "email", "phone") and not self.source.rehearsal:
            self._lookup_add_person(results, kind, value, self._add_lookup_generation)
        if kind in ("email", "phone"):
            fields = {kind: value}
            if self.phone:
                button = TextButton(f"Add {value} as a new contact", icon="user-plus", style="key",
                                    on_click=lambda: self._add_new(fields))
                button.set_hexpand(True)
                results.append(button)
            else:
                row = RichMenuItem("New contact", icon="mail" if kind == "email" else "phone", subtitle=value,
                                   on_activate=lambda: self._add_new(fields))
                results.append(row.menu_widget(self._close_panel))
        elif kind == "handle":
            found = add_matches(self.all_records.values(), value)
            for record in found:
                face = PersonAvatar(record.name, 28, picture=_picture(record.photo), hue=record.hue)
                results.append(PanelRow(record.name, lead=face, note=f"@{record.handle}",
                                        on_activate=lambda record=record: self._add_open(record)))
            if not found:
                results.append(PanelRow(f"Add @{value}", icon="user-plus",
                                        subtitle="Save their Luma username in a new contact",
                                        on_activate=lambda: self._add_new({"handle": value})))

    def _lookup_add_person(self, results, kind, value, generation):
        def worker():
            from .collaboration import PeopleDirectory
            try:
                directory = PeopleDirectory()
                people = [directory.lookup(value)] if kind == "handle" else directory.discover([(kind, value)])
            except Exception:
                people = []
            GLib.idle_add(self._add_person_found, results, generation, people)
        threading.Thread(target=worker, daemon=True, name="contacts-luma-lookup").start()

    def _add_person_found(self, results, generation, people):
        if results is not self._add_results or generation != self._add_lookup_generation:
            return GLib.SOURCE_REMOVE
        from .collaboration import public_name
        for person in people:
            if not person.get("account") or not person.get("handle"):
                continue
            self._resolved_luma_person = person
            name = public_name(person)
            row = PanelRow(name, lead=PersonAvatar(name, 28, hue=person.get("hue")),
                           subtitle="On Luma · @" + person["handle"],
                           on_activate=lambda person=person: self._add_luma_person(person))
            row.set_name("ct-add-luma-person")
            results.prepend(row)
        return GLib.SOURCE_REMOVE

    def _add_luma_person(self, person):
        from .collaboration import public_name
        self._resolved_luma_person = person
        fields = {"name": public_name(person), "handle": person["handle"]}
        kind, value = add_query(getattr(self, "_add_current_query", ""))
        if kind in ("email", "phone"):
            fields[kind] = value
        self._add_new(fields)

    def _add_submit(self, text: str) -> None:
        """Return takes the first answer: the new card, or the first match."""
        kind, value = add_query(text)
        person = getattr(self, "_resolved_luma_person", None)
        resolved_current = text == getattr(self, "_add_current_query", "") and kind in ("email", "phone")
        if person and (resolved_current or (kind == "handle" and person.get("handle", "").casefold() == value.casefold())):
            self._add_luma_person(person)
            return
        if kind in ("email", "phone"):
            self._add_new({kind: value})
        elif kind == "handle":
            found = add_matches(self.all_records.values(), value, 1)
            if found:
                self._add_open(found[0])
            else:
                self._add_new({"handle": value})

    def _add_new(self, fields: dict[str, str]) -> None:
        self._close_panel()
        self._clear_search()
        self._new_contact(fields)

    def _add_open(self, record: ContactRecord) -> None:
        """Someone already in your contacts: their card (v71 "Open")."""
        self._close_panel()
        self._show_contact(record)
        self._select_uid(record.uid)
        self._push()

    def _scan_code(self) -> None:
        """Scan a code: the camera reads it."""
        self._close_panel()
        self._launch_core_app("prairie-camera")

    def _code_card(self, me: Me, *, copy: bool) -> Gtk.Widget:
        """Your code (v71 .ccodep, .mycode): the code, @you, your link, and Copy link."""
        link = my_link(me)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER)
        card.add_css_class("ct-code")
        code = _qr_code(f"https://{link}" if link and "://" not in link else link, f"Your code, {link}",
                        tile=not self.phone)
        code.set_name("ct-code")
        card.append(code)
        if not self.phone:
            # v71 .codepop .mycode: 240 wide (the menu's 5 padding, then 16 12 8 less the menu's own inset), the code 10 above @you,
            # @you at 15/700, the link at 12/400 in the quiet ink, a small Copy link 8 below.
            card.set_size_request(CODE_POP_WIDTH - 2 * 5 - 2 * 12, -1)
            card.set_margin_top(12)
            card.set_margin_start(12)
            card.set_margin_end(12)
            card.set_margin_bottom(4)
            card.set_spacing(4)
            code.set_margin_bottom(10)
        else:
            # v71 .ccodep (the grown bar): 14 8 round the code, @you and your link at 13 in the quiet ink.
            for side, value in (("top", 14), ("bottom", 14), ("start", 8), ("end", 8)):
                card.set_property(f"margin-{side}", value)
            card.set_spacing(4)
        if me.handle:
            card.append(apply_type(Gtk.Label(label=f"@{me.handle}"),
                                   "section-title" if self.phone else "title-2", weight=700))
        if link:
            card.append(apply_type(Gtk.Label(label=link, selectable=True), "caption" if not self.phone else "body",
                                   muted=True, weight=400))
            if copy:
                button = TextButton("Copy link", icon="link", style="fill", small=not self.phone,
                                    on_click=lambda: self._copy(link, "Copied"))
                button.set_halign(Gtk.Align.CENTER)
                button.set_margin_top(9 if not self.phone else 6)
                card.append(button)
        return card

    def _code_panel(self, *, share: bool = False) -> Gtk.Widget:
        """Your code as the grown bar on a phone: from Add a person (with Copy link), or as My card's Share,
        where Messages, Email and Copy link follow it (v71)."""
        me = self.source.me()
        if me is None:
            return Gtk.Box()
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.append(self._code_card(me, copy=not share))
        if share:
            link = my_link(me)
            panel.append(BarTiles([BarTile("message-square", "Messages", lambda: self._send_card("messages", me)),
                                   BarTile("mail", "Email", lambda: self._send_card("mail", me)),
                                   BarTile("link", "Copy link", lambda: self._copy(link, "Copied")),
                                   BarTile("radio-tower", "Nearby", lambda: self._toast(self._share_nearby(me)))]))
        return panel

    def _open_my_code(self, anchor: Gtk.Widget) -> None:
        """Your code on a computer (v71 .codepop): from My card's Share, or Add a person's Show my code."""
        me = self.source.me()
        if me is not None:
            self._popup(FloatingMenu([MenuSection(self._code_card(me, copy=True))], label="Your code",
                                     title="Your code"), anchor)

    def _share_panel(self, record: ContactRecord) -> Gtk.Widget:
        """Share on a phone (v71): "Send ___'s card to", their faces, then Messages, Email and Copy link."""
        def chosen(choice: str, value: object) -> str | None:
            if choice == "send-to" and isinstance(value, Person):
                return self._send_card("messages", record, value)
            if choice in ("messages", "mail"):
                return self._send_card(choice, record)
            if choice == "copy-link":
                link = f"https://simplyluma.com/@{record.handle}" if record.handle else ""
                if link:
                    self.get_clipboard().set(link)
                    return "Link copied"
                self.get_clipboard().set(self._card_text(record))
                return "Contact card copied"
            if choice == "nearby":
                return self._share_nearby(record)
            return None

        return SharePanel(people=self._share_people(record), on_choice=chosen, targets=PANEL_TARGETS,
                          heading=f"Send {first_name(record)}’s card to")

    def _share_people(self, contact: ContactRecord | Me) -> list[Person]:
        """Who Share offers (v71: five faces), never the person being shared."""
        return [Person(record.name, phone=record.phone, email=record.email, username=record.handle,
                       picture=_picture(record.photo), hue=record.hue)
                for record in self.source.share_people(list(self.all_records.values()))
                if not isinstance(contact, ContactRecord) or record.uid != contact.uid][:SHARE_PEOPLE]

    def _more_items(self, record: ContactRecord) -> list[MenuItem]:
        """⋯ on a phone (v71): Add to list, Link a duplicate, then Block and Delete contact in red, each confirmed
        in the bar. A write the address book does not make yet is not offered (ContactsSource.can)."""
        first = first_name(record)
        items = []
        if self.source.can("lists") and user_lists(self.all_records.values(), self.source.lists):
            items.append(MenuItem("Add to list", icon="list-plus", on_activate=lambda: self.card_bar.grow(
                "add-to-list", self._add_to_list_panel(record), anchor=_find_named(self.card_bar, "ct-more"))))
        if self.source.can("link-duplicate"):
            items.append(MenuItem("Link a duplicate", icon="merge", on_activate=lambda: self._link_duplicate(record)))
        if self.source.can("block"):
            items.append(MenuItem(f"Block {first}", icon="shield", danger=True,
                                  on_activate=lambda: self._confirm_block(record)))
        items.append(MenuItem("Delete contact", icon="trash-2", danger=True,
                              on_activate=lambda: self._confirm_delete(record)))
        return items

    def _add_to_list_panel(self, record: ContactRecord) -> Gtk.Widget:
        """⋯ › Add to list: each list, checked when they are on it; a tap adds or removes them."""
        rows = [PanelRow(name, icon="list", selected=key in record.categories,
                         on_activate=lambda key=key: self._toggle_list(record, key))
                for key, name in user_lists(self.all_records.values(), self.source.lists)]
        return panel_list(["Add to list", *rows], label="Add to list")

    # ── v71's new writes: offered only where the source makes them (ContactsSource.can) ──

    def _toggle_favourite(self, record: ContactRecord) -> None:
        favourite = not record.favourite
        if not self.source.rehearsal:
            pending = getattr(self, '_favorite_pending', set())
            self._favorite_pending = pending
            if record.uid in pending:
                return
            pending.add(record.uid)
            def save():
                try:
                    self.source.set_favourite(record.uid, favourite)
                    error = ''
                except Exception as failure:
                    error = str(failure) or 'The favorite choice was not saved.'
                def finished():
                    pending.discard(record.uid)
                    if not self.get_visible():
                        return GLib.SOURCE_REMOVE
                    if error:
                        self._toast(error, kind='error')
                    else:
                        self._reload()
                        self._toast(f'{first_name(record)} is a favorite' if favourite else 'Removed from favorites', kind='done')
                    return GLib.SOURCE_REMOVE
                GLib.idle_add(finished)
            threading.Thread(target=save, daemon=True, name='contacts-favorite').start()
            return
        if not self._write(lambda: self.source.set_favourite(record.uid, favourite), "Favorites"):
            return
        self._changed(replace(record, favourite=favourite))
        self._toast(f"{first_name(record)} is a favorite" if favourite else "Removed from favorites", kind="done")

    def _toggle_list(self, record: ContactRecord, key: str) -> None:
        lists = tuple(c for c in record.categories if c != key) if key in record.categories \
            else (*record.categories, key)
        if not self._write(lambda: self.source.set_lists(record.uid, lists), "Lists"):
            return
        name = dict(user_lists(self.all_records.values(), self.source.lists)).get(key, key)
        self._changed(replace(record, categories=lists))
        self._toast(f"Added {first_name(record)} to {name}" if key in lists else f"Removed from {name}", kind="done")

    def _link_duplicate(self, record: ContactRecord) -> None:
        if self._write(lambda: self.source.link_duplicate(record.uid), "Linking"):
            self._toast("Merged with duplicate", kind="done")

    def _confirm_block(self, record: ContactRecord) -> None:
        """Block, confirmed (v71)."""
        self._confirm(title=f"Block {record.name}?",
                      body=f"{first_name(record)} can’t message or call you, and isn’t told. You can unblock them here.",
                      action="Block", icon="shield", on_confirm=lambda: self._block(record))

    def _block(self, record: ContactRecord) -> None:
        if self._write(lambda: self.source.block(record.uid), "Blocking"):
            self._toast(f"{first_name(record)} is blocked", kind="done")

    def _write(self, write: Callable[[], None], what: str) -> bool:
        """One of v71's new writes; a source that does not make it says so instead of pretending."""
        try:
            write()
        except NotImplementedError:
            self._toast(f"{what} can’t be changed here yet.", kind="warning")
            return False
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            self._toast(f"That wasn’t saved: {error}", kind="error")
            return False
        return True

    def _changed(self, record: ContactRecord) -> None:
        """A card changed in place (favorite, lists): the list, the counts and the card follow."""
        self.all_records[record.uid] = record
        if self.selected is not None and self.selected != ME and self.selected.uid == record.uid:
            self.selected = record
        filters = list_filters(self.all_records.values(), self.source.lists)
        if filters != self._filters:
            self._build_foot(filters)
        self._render_list()
        self._refresh_card_chrome()

    def _copy(self, text: str, said: str) -> None:
        self.get_clipboard().set(text)
        self._close_panel()
        self._toast(said, kind="done")

    def _send_card(self, where: str, contact: ContactRecord | Me, person: Person | None = None) -> str | None:
        """Send a card: Messages opens (with that person, when there is one) and the card is ready to paste;
        Email opens a message with it. Returns what the toast says."""
        handle = contact.handle
        link = my_link(contact) if isinstance(contact, Me) else (f"simplyluma.com/@{handle}" if handle else "")
        card = "\n".join(part for part in (contact.name, f"@{handle}" if handle else "", contact.phone,
                                            contact.email, link) if part)
        self._close_panel()
        if where == "mail":
            uri = f"mailto:?subject={quote(f'Contact: {contact.name}')}&body={quote(card)}"
            return "Opening Mail" if self._launch_uri(uri) else None
        self.get_clipboard().set(card)
        if person is not None and person.username:
            self._launch_core_app("prairie-messages", f"luma-messages://u/{person.username}")
        elif person is not None and person.phone:
            self._launch_core_app("prairie-messages", f"sms:{person.phone}")
        else:
            self._launch_core_app("prairie-messages")
        said = f"Card copied for {person.name.split(' ')[0]}" if person is not None else "Card copied"
        if isinstance(contact, Me):
            self._toast(f"{said}. Paste it in Messages.", kind="done")
        return f"{said}. Paste it in Messages."

    def _toast(self, message: str, **options) -> Toast:
        """A toast where the person is looking: the list on a phone showing it, otherwise the card."""
        return Toast.show(self.list_host if self.listing else self.card_stack, message, **options)

    # ── Phone structure: Back and the A–Z index (K-NAV and K-ROWS parts) ──

    def _listed(self) -> None:
        """Back to the list (the title island's ‹): anything grown from the card folds."""
        if self.card_bar.grown is not None:
            self.card_bar.fold_panel()

    # ── Delete: confirmed, then undoable for the toast's lifetime ───────────

    def _confirm_delete(self, record: ContactRecord) -> None:
        self._confirm(title=f"Delete {record.name}?",
                      body=f"Their card is removed from {book_place(record.book) or self.address_book.place}. "
                           "Your conversations with them stay in Messages.",
                      action="Delete", icon="trash-2", on_confirm=lambda: self._delete(record))

    def _confirm(self, *, title: str, body: str, action: str, icon: str, on_confirm: Callable[[], None]) -> None:
        """A destructive confirm: inside the grown bar on a phone (v71 rule 9), the kit's dialog on a computer."""
        if self.phone and self.card_bar.state in ("bar", "double"):
            DestructiveDialog.in_bar(self.card_bar, title=title, body=body, action=action, on_confirm=on_confirm,
                                     anchor=_find_named(self.card_bar, "ct-more"))
        else:
            DestructiveDialog.ask(self.card_stack, title=title, body=body, action=action, icon=icon,
                                  on_confirm=lambda _checked: on_confirm())

    def _delete(self, record: ContactRecord) -> None:
        """Hide the card now; remove it from the address book when Undo is no longer offered.

        If the app ends before then, the close handler removes it; if it dies,
        the contact is simply not deleted, which is the safe direction.
        """
        self._flush_delete()
        timeout = GLib.timeout_add(lumaui_tokens.MOTION["toast_undo_ms"] + 500, self._flush_delete)
        self._pending_delete = (record, timeout)
        self.all_records.pop(record.uid, None)
        self.selected = None
        self.list_first.show_list()              # a phone goes back to the list (v71)
        self._render_list()
        self._toast(f"{record.name} deleted" if self.phone else "Contact deleted", kind="deleted",
                    undo=lambda: self._undo_delete(record))

    def _undo_delete(self, record: ContactRecord) -> None:
        if self._pending_delete is None or self._pending_delete[0].uid != record.uid:
            return
        GLib.source_remove(self._pending_delete[1])
        self._pending_delete = None
        self.all_records[record.uid] = record
        self.selected = record
        self._render_list()
        self._select_uid(record.uid)

    def _flush_delete(self, *, wait: bool = False) -> bool:
        """Remove the card whose Undo has expired; in place when the window is closing."""
        pending, self._pending_delete = self._pending_delete, None
        if pending is None:
            return GLib.SOURCE_REMOVE
        record, timeout = pending
        if GLib.main_context_default().find_source_by_id(timeout):
            GLib.source_remove(timeout)

        def remove() -> str:
            try:
                self.source.delete(record.uid)
                return ""
            except (ValueError, RuntimeError, OSError, GLib.Error) as error:
                return getattr(error, "message", None) or str(error)

        if wait:
            remove()
            return GLib.SOURCE_REMOVE
        threading.Thread(target=lambda: GLib.idle_add(self._delete_finished, record, remove()),
                         daemon=True, name="contacts-delete").start()
        return GLib.SOURCE_REMOVE

    def _delete_finished(self, record: ContactRecord, error: str) -> bool:
        if error:
            self.all_records[record.uid] = record
            self._render_list()
            self._toast(f"{record.name} wasn’t deleted: {error}", kind="error")
        return GLib.SOURCE_REMOVE

    # ── Import (unchanged behaviour) ────────────────────────────────────────

    def _import_vcard(self) -> None:
        if self.importing:
            return
        dialog = Gtk.FileDialog(title="Import vCard")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        cards = Gtk.FileFilter(name="vCard files")
        cards.add_mime_type("text/vcard")
        cards.add_mime_type("text/x-vcard")
        cards.add_pattern("*.vcf")
        filters.append(cards)
        dialog.set_filters(filters)
        dialog.open(self, None, self._import_chosen)

    def _import_chosen(self, dialog, result) -> None:
        try:
            file = dialog.open_finish(result)
        except GLib.Error as error:
            if not error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                self._toast(f"Contacts weren’t imported: {error.message}", kind="error")
            return
        if file is None or self.importing:
            return
        self.importing = True
        self.fresh_state.secondary_button.set_sensitive(False)

        def read_and_import():
            try:
                stream = file.read(None)
                try:
                    chunks, size = [], 0
                    while size <= MAX_VCARD_BYTES:
                        chunk = stream.read_bytes(min(65536, MAX_VCARD_BYTES + 1 - size), None).get_data()
                        if not chunk:
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                    data = b"".join(chunks)
                finally:
                    stream.close(None)
                if len(data) > MAX_VCARD_BYTES:
                    raise ValueError("Choose a vCard file smaller than 8 MiB.")
                uids = import_contacts_vcard(data.decode("utf-8-sig"))
            except Exception as error:
                GLib.idle_add(self._import_finished, (), str(error))
            else:
                GLib.idle_add(self._import_finished, uids, "")
        threading.Thread(target=read_and_import, daemon=True, name="contacts-import").start()

    def _import_finished(self, uids: tuple[str, ...], error: str) -> bool:
        self.importing = False
        self.fresh_state.secondary_button.set_sensitive(True)
        if error:
            self._toast(f"Contacts weren’t imported: {error}", kind="error")
            return GLib.SOURCE_REMOVE
        self._clear_search()

        def show_first() -> None:
            first = self.records.get(uids[0]) if uids else None
            if first is not None:
                self._show_contact(first)
                self._select_uid(first.uid)
                self._push()
                self._toast(f"Imported {len(uids)} contact" + ("" if len(uids) == 1 else "s"), kind="added")

        self._reload(then=show_first)
        return GLib.SOURCE_REMOVE

    # ── Plumbing ────────────────────────────────────────────────────────────

    def _open_share(self, button: Gtk.Widget, contact: ContactRecord | Me) -> None:
        """Use the system share sheet for a contact card, with working choices."""
        name, phone, email = contact.name, contact.phone, contact.email
        handle = contact.handle
        link = (contact.link if isinstance(contact, Me) else "") or (
            f"https://simplyluma.com/@{handle}" if handle else "")
        card = "\n".join(part for part in (name, f"@{handle}" if handle else "", phone, email) if part)
        # The sheet's head (v71 lShare): their face, their name and their link.
        picture = _picture(contact.photo)
        subject = ShareSubject(name, link.removeprefix("https://") or "Luma contact", kind="contact",
                               picture=picture, person=Person(name, phone=phone, email=email, username=handle,
                                                              picture=picture, hue=contact.hue))

        def chosen(choice: str, value: object) -> str | None:
            if choice == "send-to" and isinstance(value, Person):
                return self._send_card("messages", contact, value)
            if choice == "target" and value == "messages":
                return self._send_card("messages", contact)
            if choice == "copy-card":
                self.get_clipboard().set(card)
                return "Contact card copied"
            if choice == "copy-link" and link:
                self.get_clipboard().set(link)
                return "Profile link copied"
            if choice == "save":
                self._save_contact_card(name, phone, email)
                return "Choose where to save the contact"
            if choice == "target" and value == "mail":
                uri = f"mailto:?subject={quote(f'Contact: {name}')}&body={quote(card)}"
                return "Opening Mail" if self._launch_uri(uri) else None
            if choice == "target" and value == "nearby":
                return self._share_nearby(contact)
            if choice == "target":
                target = next((target for target in sheet.targets if target.key == value), None)
                return self._share_to_app(target, contact) if target is not None else None
            return None

        # Send it to someone (v71 lShare: a row of faces that scrolls), then the kit's targets (Messages, Mail,
        # Nearby, Notes, Write, Ari) and the copies. The computer's sheet; on a phone the bar grows into
        # SharePanel instead (_share_panel).
        people = [Person(record.name, phone=record.phone, email=record.email, username=record.handle,
                         picture=_picture(record.photo), hue=record.hue)
                  for record in self.source.sheet_people(list(self.all_records.values()))
                  if not isinstance(contact, ContactRecord) or record.uid != contact.uid]
        sheet = ShareSheet.present(button, document=subject, people=people, on_choice=chosen)

    def _share_nearby(self, contact: ContactRecord | Me) -> str:
        """Nearby (v71 offers it in every share). Luma has no Nearby sending yet, so a real source says so."""
        if not self.source.rehearsal:
            return "Nearby sharing isn’t on this computer yet"
        return "Sharing your card with Nearby" if isinstance(contact, Me) else \
            f"Sharing {first_name(contact)}’s card with Nearby"

    def _share_to_app(self, target: ShareTarget, contact: ContactRecord | Me) -> str | None:
        """Notes, Write, Ari: the card as a vCard file, opened in that app (v71 lShare's app tiles)."""
        if self.source.rehearsal:
            return {"notes": "Added to a new note", "write": "Opened in Write",
                    "ari": "Ari has it. Ask what you like."}.get(target.key, f"Opened in {target.label}")
        if not target.app_id:
            return f"{target.label} isn’t installed"
        folder = os.path.join(GLib.get_user_cache_dir(), "luma-contacts", "shared")
        os.makedirs(folder, exist_ok=True)
        safe = re.sub(r"[^\w .-]+", "", contact.name).strip() or "Contact"
        path = os.path.join(folder, f"{safe}.vcf")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_contact_vcard(contact.name, contact.phone, contact.email))
        os.chmod(path, 0o600)
        from luma_appkit.application_directory import launch
        def accepted(ok, error):
            self._toast('Opened in ' + target.label if ok else error or 'Could not share contact',
                        kind='done' if ok else 'error')
        launch(target.app_id + '.desktop', [Gio.File.new_for_path(path)],
               context=self.get_display().get_app_launch_context(), callback=accepted)
        return None

    def _save_contact_card(self, name: str, phone: str, email: str) -> None:
        dialog = Gtk.FileDialog(title="Save contact card", initial_name=f"{name}.vcf")

        def saved(source: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                file = source.save_finish(result)
                file.replace_contents(_contact_vcard(name, phone, email).encode("utf-8"), None, False,
                                      Gio.FileCreateFlags.REPLACE_DESTINATION, None)
            except GLib.Error as error:
                if not error.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                    self._toast(f"Contact card wasn’t saved: {error.message}", kind="error")
                return
            self._toast("Contact card saved", kind="done")

        dialog.save(self, None, saved)

    def _contact_action(self, action: str, person: Person) -> bool:
        """Hand off to a specific conversation, real call, or addressed email."""
        if action == "video" and self.source.rehearsal:
            self._toast(f"Video calling {person.name.split(' ')[0]}…")
            return True
        if action == "message":
            if person.username:
                return self._launch_core_app("prairie-messages", f"luma-messages://u/{person.username}")
            if person.phone:
                return self._launch_core_app("prairie-messages", f"sms:{person.phone}")
        if action == "call" and person.phone:
            return self._launch_core_app("prairie-phone", "--call", f"tel:{person.phone}")
        if action == "email" and person.email:
            self._launch_uri(f"mailto:{quote(person.email, safe='@')}")
            return True
        return False

    def _launch_core_app(self, executable: str, *arguments: str) -> bool:
        path = shutil.which(executable)
        command = [path, *arguments] if path else [sys.executable, "-m", f"prairie_apps.{executable.removeprefix('prairie-')}",
                                                   *arguments]
        try:
            Gio.Subprocess.new(command, Gio.SubprocessFlags.NONE)
        except GLib.Error as error:
            self._toast(f"Couldn’t open {executable.removeprefix('prairie-').title()}: {error.message}",
                       kind="error")
            return True
        return True

    def _launch_uri(self, uri: str) -> bool:
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error:
            self._toast("No app here can open that yet", kind="warning")
            return False
        return True

    def _show_about(self) -> None:
        Adw.AboutDialog(application_name="Contacts", application_icon=ICON_NAME,
                        developer_name="Project Luma").present(self)




#: Together's icon for each kind of thing shared (v70).
TOGETHER_ICONS = {"conversation": "message-square", "note": "file-text", "album": "image"}


# ── Helpers: the bar's names, your code ─────────────────────────────────────


def _name_bar_items(center: ActionCenter, names: dict[int, str]) -> None:
    """Name the bar's controls (the gate finds them, and panels open from them): `names` maps id(item) → name."""
    child = center.bar_row.get_first_child()
    while child is not None:
        name = names.get(id(getattr(child, "bar_item", None)))
        if name:
            child.set_name(name)
        child = child.get_next_sibling()


def _find_named(root: Gtk.Widget | None, name: str) -> Gtk.Widget | None:
    if root is None:
        return None
    if root.get_name() == name:
        return root
    child = root.get_first_child()
    while child is not None:
        found = _find_named(child, name)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _qr_code(text: str, label: str, *, tile: bool) -> Gtk.Widget:
    """Your code, Contacts' own surface; the modules come from prairie_apps.qr_code (libqrencode, as Messages'
    linking codes). On a computer it is v71's .mycode .qr: dark modules on a white tile (160, 12 in, 16 round),
    as a scanner wants in either mode; on a phone's grown bar, .ccodep .qr: 150, the modules in the bar's ink."""
    modules = qr_matrix(text) if text else None
    if modules:
        # The tile's own white is most of the quiet zone a scanner needs; on the bar's glass there is none to keep.
        trim = QUIET_ZONE - 1 if tile else QUIET_ZONE
        modules = [row[trim:len(row) - trim] for row in modules[trim:len(modules) - trim]]
    size = CODE_TILE if tile else CODE_SIZE
    area = Gtk.DrawingArea(content_width=size, content_height=size, halign=Gtk.Align.CENTER,
                           accessible_role=Gtk.AccessibleRole.IMG)
    area.update_property([Gtk.AccessibleProperty.LABEL], [label])
    if not modules:
        return area

    def draw(widget: Gtk.DrawingArea, cr, width: int, height: int) -> None:
        inset = 0
        if tile:
            _rounded(cr, 0, 0, width, height, CODE_TILE_RADIUS)
            cr.set_source_rgb(1, 1, 1)
            cr.fill()
            inset = CODE_TILE_INSET
        cells = len(modules)
        cell = (min(width, height) - 2 * inset) / cells
        left, top = (width - cell * cells) / 2, (height - cell * cells) / 2
        if tile:
            cr.set_source_rgb(0x11 / 255, 0x11 / 255, 0x11 / 255)
        else:
            ink = widget.get_color()
            cr.set_source_rgba(ink.red, ink.green, ink.blue, ink.alpha)
        for y, row in enumerate(modules):
            for x, dark in enumerate(row):
                if dark:
                    _rounded(cr, left + x * cell, top + y * cell, cell, cell, cell * 0.3)
        cr.fill()

    area.set_draw_func(draw)
    return area


def _rounded(cr, x: float, y: float, width: float, height: float, radius: float) -> None:
    import math
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _picture(data: bytes) -> Gdk.Texture | None:
    if not data:
        return None
    try:
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
    except GLib.Error:
        return None


def _sidebar_pane(key: str) -> int:
    """The sidebar pane's width: the kit's outside less the window body's gutter on its left."""
    return SIDEBAR[key] - SIDEBAR["gutter"]


def _card(name: str) -> Card:
    card = Card(recessed=True)
    card.set_name(name)
    card.set_valign(Gtk.Align.START)
    return card


def _heading(text: str) -> Gtk.Label:
    """A card's small heading (v70 .ccard h6): the type scale's label role."""
    return apply_type(Gtk.Label(label=text, xalign=0), "label")


def _paragraph(text: str, *, muted: bool = False, markup: bool = True, prose: bool = False) -> Gtk.Label:
    """A card's paragraph (v70 .cp, or .cempty when muted)."""
    label = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
    if markup:
        label.set_markup(text)
    else:
        label.set_text(text)
    label.add_css_class("ct-paragraph")
    if prose:
        # v71 .cp: 0 4 10 inside its card, in the second ink.
        label.add_css_class("ct-prose")
        label.set_margin_start(4)
        label.set_margin_end(4)
        label.set_margin_bottom(10)
    return apply_type(label, "caption" if muted else "body")


def _glyph(name: str) -> Gtk.Widget:
    return icons.image(name)


class ContactsApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        install_appkit()
        install_lumaui()
        _install_contacts_style()

    def do_activate(self) -> None:
        (self.props.active_window or ContactsWindow(self)).present()


def _install_contacts_style() -> None:
    if Gdk.Display.get_default() is None:
        return
    # The kit owns the sheet, so it reloads with the appearance.
    add_style_sheet(os.environ.get("LUMA_CONTACTS_STYLE_PATH", "/usr/share/prairie-core/contacts.css"))


def main() -> int:
    return ContactsApplication().run([])


if __name__ == "__main__":
    raise SystemExit(main())
