# SPDX-License-Identifier: Apache-2.0
"""Charlie's window, simulator v71 (`luma-next-71.html?app=charlie`), built from LumaUI parts.

    window        AppWindow (frame, title row and identity are the kit's); its body:
    ├ mailboxes   NavigationSidebar on the frame (All inboxes, Flagged, each account's boxes, Labels,
    │             Add account); hidden at 1060 and narrower, and while Details is open at 1320
    └ ListFirst   the list and the thread: side by side on a computer, list first on a phone
      ├ list      ToastHost around an Island: SidebarFoot (Search mail, New email) on top, the rows
      │ │         (SwipeRow on a phone: Archive, Flag), All · Unread (ModeSwitch) at its foot
      │ ├ title   TitleIsland (phone): ☰ | the mailbox and its account; grows into the mailboxes
      │ └ bar     ActionCenter (phone): Search, Unread, New email
      └ thread    ToastHost around an Island, then the DetailsPane
        ├ subject TitleIsland, no lead (computer): faces, subject, who · count; toggles Details
        ├ corner  CornerPill (computer): Archive, Flag, Details, ⋯
        ├ title   TitleIsland (phone): ‹ | faces, subject, who · count; grows into Information
        ├ body    the conversation, Messages-style: day, face, name · to · time, the bubble, files,
        │         photos, an event, designed mail on its paper
        └ bar     ActionCenter: the quick reply (one row on a computer; on a phone "Reply all ⌄",
                  Archive, Flag, ⋯ over the reply well), a picked message's or file's actions, and
                  the editor it grows into (reply, forward, new email)

The mail core (store, engine, model, mime) is unchanged; this module only reads it and calls
the writes Charlie already had (read state, flag, move, send). Every element v71 has carries a
stable widget name for tools/lumaui-conform. What v71 shows that the kit does not offer yet is
marked TODO(v71-kit …) with its kit request.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parseaddr
import os
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import parse_qs, unquote, urlparse

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402

from luma_appkit import (  # noqa: E402
    ActionCenter, ActionEditor, AddRow, AppWindow, BarAction, BarChip, BarEntry, BarPrompt, BarSearch,
    Command, CommandGroup, CommandRegistry, CornerPill, CountBadge, DetailsItem, DetailsPane, DetailsPhotos,
    DetailsRow, EventCard, FileCard, GroupFace, Island, ListFirst, MessageBubble, ModeSwitch,
    NavigationSidebar, PanelRow, PersonAvatar, SEPARATOR, SPACER, SidebarFoot, SidebarRow, RowLead,
    StackedButton, StackedButtons, SwipeAction, SwipeRow, TitleIsland, Toast, ToastHost, WidthWatch,
    apply_type, icons, panel_list,
)
from luma_appkit.lumaui_tokens import WINDOW
from luma_appkit.action_bubble import MenuItem  # noqa: E402

from . import APP_ID, __version__  # noqa: E402
from .mime import message_presentation  # noqa: E402
from .model import Attachment, Conversation, Draft, Message  # noqa: E402

#: v71's widths (luma-next-71 1281-1479): the mailboxes 212, the list 336 (260 at 820), and where they step aside.
NAV_WIDTH = 212
LIST_WIDTH = 336
LIST_NARROW_WIDTH = 260
NAV_HIDES_AT = 1060          # @container win (max-width: 1060px) .crnav { display: none }
NAV_HIDES_WITH_DETAILS = 1320
LIST_NARROWS_AT = 820
LIST_HIDES_AT = 760
PHONE_BELOW = 560            # the kit's phone tier

#: Boxes a mailbox column lists, v71 CRBOX: key, name, glyph.
BOXES = (("inbox", "Inbox", "inbox"), ("flagged", "Flagged", "star"), ("drafts", "Drafts", "file-text"),
         ("sent", "Sent", "send-horizontal"), ("archive", "Archive", "archive"), ("trash", "Trash", "trash-2"))
BOX_NAMES = {key: name for key, name, _icon in BOXES}

#: Senders that send designed mail and updates, not people (real mail; the fixture names its own).
_AUTOMATED = ("noreply", "no-reply", "donotreply", "notifications", "notification", "newsletter", "news",
              "receipts", "receipt", "orders", "order", "tickets", "letters", "updates", "mailer", "billing",
              "hello", "info", "team", "support", "alerts", "digest")

WIDGET_NAMES = {
    "nav": "cr-nav", "list": "cr-list", "main": "cr-main", "bar": "cr-bar", "details": "cr-det",
    "body": "cr-body", "list_bar": "cr-list-bar", "subject": "cr-subject", "corner": "cr-corner",
    "thread_island": "cr-thread-island", "list_island": "cr-list-island",
}


# ── times, the way v71 writes them ─────────────────────────────────────────

def _clock(moment: datetime) -> str:
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def _days(moment: datetime, now: datetime) -> int:
    return (now.date() - moment.astimezone(now.tzinfo).date()).days


def list_time(moment: datetime, now: datetime) -> str:
    """A row's time: the clock today, the weekday this week, else the date ("Sep 17")."""
    local = moment.astimezone(now.tzinfo)
    days = _days(local, now)
    if days <= 0:
        return _clock(local)
    if days < 7:
        return local.strftime("%a")
    return f"{local.strftime('%b')} {local.day}"


def message_time(moment: datetime, now: datetime) -> str:
    """Above a message: "9:41 AM" today, "Tue 4:12 PM" this week, else "Sep 19, 3:30 PM"."""
    local = moment.astimezone(now.tzinfo)
    days = _days(local, now)
    if days <= 0:
        return _clock(local)
    if days < 7:
        return f"{local.strftime('%a')} {_clock(local)}"
    return f"{local.strftime('%b')} {local.day}, {_clock(local)}"


def day_label(moment: datetime, now: datetime) -> str:
    """The thread's day line: Today, Yesterday, a weekday this week, else the date."""
    local = moment.astimezone(now.tzinfo)
    days = _days(local, now)
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 7:
        return local.strftime("%A")
    return f"{local.strftime('%b')} {local.day}"


def _join(names: list[str], last: str = "and") -> str:
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} {last} {names[-1]}"


# ── who is who ─────────────────────────────────────────────────────────────

@dataclass
class _Face:
    name: str
    address: str
    picture: Gdk.Paintable | None = None
    hue: float | None = None
    brand: tuple[str, str] | None = None


def _crop_face(path: Path, x: float, y: float, zoom: float, side: int = 96) -> Gdk.Paintable | None:
    """v71 PPL faces: the photo scaled `zoom` times the face, centred on (x, y) of the photo."""
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
    except GLib.Error:
        return None
    width, height = pixbuf.get_width(), pixbuf.get_height()
    crop = max(8, min(width, height, round(width / zoom)))
    left = int(min(max(0, x * width - crop / 2), width - crop))
    top = int(min(max(0, y * height - crop / 2), height - crop))
    square = pixbuf.new_subpixbuf(left, top, crop, crop).scale_simple(side, side, GdkPixbuf.InterpType.BILINEAR)
    return Gdk.Texture.new_for_pixbuf(square)


class People:
    """Names, first names and faces for addresses: the fixture's people, then what the mail says."""

    def __init__(self, window: "CharlieWindow") -> None:
        self.window = window
        self._names: dict[str, str] = {}
        self._faces: dict[str, Gdk.Paintable | None] = {}
        self._waiting: dict[str, list[Callable[[Gdk.Paintable], None]]] = {}

    @property
    def own(self) -> set[str]:
        return {account.address.casefold() for account in self.window.store.accounts()}

    def is_me(self, address: str) -> bool:
        return address.casefold() in self.own

    def learn(self, messages: Iterable[Message]) -> None:
        for message in messages:
            if message.sender_name and message.sender_address:
                self._names.setdefault(message.sender_address.casefold(), message.sender_name)

    def name(self, address: str) -> str:
        if self.is_me(address):
            return "You"
        fixture = self.window.fixture
        if fixture is not None and (person := fixture.person(address)) is not None:
            return person.name
        found = self._names.get(address.casefold())
        if found:
            return found
        name, plain = parseaddr(address)
        return name or (plain or address).split("@", 1)[0]

    def first(self, address: str) -> str:
        if self.is_me(address):
            return "you"
        fixture = self.window.fixture
        if fixture is not None and (person := fixture.person(address)) is not None and person.brand:
            return person.name
        return self.name(address).split(" ", 1)[0]

    def brand(self, address: str) -> tuple[str, str] | None:
        fixture = self.window.fixture
        if fixture is not None:
            person = fixture.person(address)
            return person.brand if person is not None else None
        return None

    def automated(self, address: str) -> bool:
        if self.brand(address) is not None:
            return True
        if self.window.fixture is not None:
            return False
        local = address.casefold().split("@", 1)[0]
        return any(local == word or local.startswith(word + "-") or local.startswith(word + ".")
                   for word in _AUTOMATED)

    def face(self, address: str) -> _Face:
        name = self.name(address) if not self.is_me(address) else self.window.my_name(address)
        fixture = self.window.fixture
        picture = self._faces.get(address.casefold())
        hue = None
        brand = None
        if fixture is not None and (person := fixture.person(address)) is not None:
            hue, brand = person.hue, person.brand
            if address.casefold() not in self._faces and person.face is not None:
                photo = fixture.photo(person.face[0])
                picture = _crop_face(photo, *person.face[1:]) if photo is not None else None
                self._faces[address.casefold()] = picture
        return _Face(name, address, picture, hue, brand)

    def avatar(self, address: str, size: int) -> Gtk.Widget:
        """A person's round face; a sender of designed mail gets its own mark."""
        face = self.face(address)
        if face.brand is not None:
            # TODO(v71-kit brand mark): v71 .crbrand is a rounded square in the sender's colour with its
            # monogram (kit request charlie-03); a face in the sender's name stands in.
            return PersonAvatar(face.brand[0], size, hue=_hex_hue(face.brand[1]))
        holder = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        holder.append(PersonAvatar(face.name, size, picture=face.picture, hue=face.hue))
        if face.picture is None and self.window.fixture is None and address:
            def loaded(picture: Gdk.Paintable, holder=holder) -> None:
                child = holder.get_first_child()
                if child is not None:
                    holder.remove(child)
                holder.append(PersonAvatar(face.name, size, picture=picture, hue=face.hue))
            self._load(address, loaded)
        return holder

    def entry(self, address: str) -> tuple[str, Gdk.Paintable | None]:
        face = self.face(address)
        return (face.brand[0] if face.brand else face.name), face.picture

    def _load(self, address: str, then: Callable[[Gdk.Paintable], None]) -> None:
        key = address.casefold()
        if key in self._faces:
            if self._faces[key] is not None:
                then(self._faces[key])
            return
        waiting = self._waiting.setdefault(key, [])
        waiting.append(then)
        if len(waiting) > 1:
            return

        def arrived(data: bytes | None) -> None:
            def show() -> bool:
                texture = None
                if data:
                    try:
                        texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
                    except GLib.Error:
                        texture = None
                self._faces[key] = texture
                for callback in self._waiting.pop(key, []):
                    if texture is not None:
                        callback(texture)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(show)

        self.window.app.avatar_loader.request(address, "", arrived)


def _hex_hue(colour: str) -> float:
    rgba = Gdk.RGBA()
    rgba.parse(colour)
    r, g, b = rgba.red, rgba.green, rgba.blue
    high, low = max(r, g, b), min(r, g, b)
    if high == low:
        return 0
    if high == r:
        hue = (g - b) / (high - low) % 6
    elif high == g:
        hue = (b - r) / (high - low) + 2
    else:
        hue = (r - g) / (high - low) + 4
    return hue * 60


# ── a conversation, read for the screen ────────────────────────────────────

class Thread:
    """A conversation as v71 shows it: who is in it, its subject, its state."""

    def __init__(self, conversation: Conversation, people: People) -> None:
        self.conversation = conversation
        self.people = people
        self.id = conversation.id
        self.messages = conversation.messages
        people.learn(self.messages)
        seen: list[str] = []
        for message in self.messages:
            for address in (message.sender_address, *message.recipients):
                key = address.casefold()
                if address and not people.is_me(address) and key not in (a.casefold() for a in seen):
                    seen.append(address)
        self.others = seen

    @property
    def subject(self) -> str:
        return self.messages[0].subject or "(No subject)"

    @property
    def latest(self) -> Message:
        return self.conversation.latest

    @property
    def unread(self) -> bool:
        return self.conversation.unread_count > 0

    @property
    def flagged(self) -> bool:
        return any(message.flagged for message in self.messages)

    @property
    def attachments(self) -> int:
        fixture = self.people.window.fixture
        total = 0
        for message in self.messages:
            total += len(message.attachments)
            if fixture is not None and message.id in fixture.messages:
                total += len(fixture.messages[message.id].photos)
        return total

    @property
    def account_id(self) -> str:
        return self.messages[0].account_id

    @property
    def brand(self) -> str | None:
        """The sender of designed mail this thread is from (v71 crIsBrand), or None."""
        first = self.messages[0]
        if not first.outgoing and self.people.automated(first.sender_address):
            return first.sender_address
        return None

    def names(self) -> str:
        """v71 crNames: one person's full name, or everyone's first names."""
        if len(self.others) == 1:
            return self.people.name(self.others[0])
        if not self.others:
            return "You"
        return ", ".join(self.people.first(a) for a in self.others)

    def reply_targets(self, mode: str, base: Message | None = None) -> list[str]:
        base = base or self.latest
        if mode == "all":
            return [a for a in self.others if not self.people.automated(a) or a == base.sender_address]
        if base.outgoing:
            return list(base.recipients[:1])
        return [base.sender_address]


# ── the list's rows ────────────────────────────────────────────────────────

def _label(text: str, role: str, *, muted: bool = False, weight: int | None = None, ellipsize: bool = True,
           xalign: float = 0) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=xalign)
    if ellipsize:
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_width_chars(1)
    apply_type(label, role, muted=muted, weight=weight)
    return label


def _font(label: Gtk.Label, size: float, weight: int) -> Gtk.Label:
    """v71 .crtx's exact size and weight (13.5/550 names, 13 subject, 12.5 snippet, 11.5 time)."""
    attributes = Pango.AttrList.from_string(f"0 4294967295 weight {weight}") or Pango.AttrList()
    attributes.insert(Pango.attr_size_new_absolute(int(size * Pango.SCALE)))
    label.set_attributes(attributes)
    return label


class MailRow(Gtk.ListBoxRow):
    """A conversation in the list (v71 `.crrow`): face, names · count · time, subject · clip · star · dot, snippet.

    TODO(v71-kit mail row): the kit's SidebarRow has two lines; mail has three (kit request charlie-02).
    The row wears the kit's row classes, so hover and the chosen chip are the kit's.
    """

    def __init__(self, thread: Thread, window: "CharlieWindow", *, sub: bool = False) -> None:
        super().__init__()
        self.thread = thread
        self.add_css_class("luma-navigation-row")
        self.add_css_class("lumaui-row")
        self.add_css_class("charlie-row")
        if sub:
            self.add_css_class("sub")
        people = window.people
        unread = thread.unread
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        line.add_css_class("charlie-row-line")
        brand = thread.brand
        if brand is not None:
            face = people.avatar(brand, 38)
        elif len(thread.others) > 1:
            face = GroupFace([people.entry(a) for a in thread.others[:2]], size=38, decorative=True)
        else:
            face = people.avatar(thread.others[0] if thread.others else thread.latest.sender_address, 38)
        face.set_valign(Gtk.Align.START)
        face.add_css_class("charlie-row-face")
        line.append(face)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        text.add_css_class("charlie-row-text")
        first = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        first.add_css_class("charlie-row-first")
        names = _label(thread.names(), "body", weight=700 if unread else 550)
        names.add_css_class("charlie-row-names")
        _font(names, 13.5, 700 if unread else 550)
        first.append(names)
        if len(thread.messages) > 1:
            first.append(CountBadge(len(thread.messages)))
        first.append(Gtk.Box(hexpand=True))
        when = _label(list_time(thread.latest.sent_at, window.now()), "caption", muted=not unread, ellipsize=False)
        when.add_css_class("charlie-row-time")
        _font(when, 11.5, 400)
        if unread:
            when.add_css_class("unread")
        first.append(when)
        text.append(first)
        second = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        second.add_css_class("charlie-row-second")
        subject = _label(thread.subject, "body", muted=not unread, weight=550 if unread else None)
        subject.add_css_class("charlie-row-subject")
        _font(subject, 13, 550 if unread else 400)
        second.append(subject)
        if thread.attachments:
            clip = icons.image("paperclip")
            clip.add_css_class("charlie-row-glyph")
            second.append(clip)
        if thread.flagged:
            star = icons.image("star")
            star.add_css_class("charlie-row-flag")
            second.append(star)
        second.append(Gtk.Box(hexpand=True))
        if unread:
            dot = Gtk.Box(valign=Gtk.Align.CENTER)
            dot.add_css_class("charlie-row-dot")
            second.append(dot)
        text.append(second)
        snippet = _label(window.snippet(thread), "caption", muted=True)
        snippet.add_css_class("charlie-row-snippet")
        _font(snippet, 12.5, 400)
        text.append(snippet)
        line.append(text)
        self.content = line
        self.set_child(line)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"{thread.names()}, {thread.subject}, {window.snippet(thread)}"])


class BundleRow(Gtk.ListBoxRow):
    """The Updates bundle (v71 `.crrow.bundle`): their marks, "Updates" and the unread count, their names."""

    def __init__(self, threads: list[Thread], window: "CharlieWindow", open_: bool) -> None:
        super().__init__()
        self.threads = threads
        self.add_css_class("luma-navigation-row")
        self.add_css_class("lumaui-row")
        self.add_css_class("charlie-row")
        self.add_css_class("bundle")
        people = window.people
        senders = [t.brand or t.latest.sender_address for t in threads]
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        line.add_css_class("charlie-row-line")
        face = GroupFace([people.entry(a) for a in senders[:2]], size=38, decorative=True)
        face.set_valign(Gtk.Align.START)
        line.append(face)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        text.add_css_class("charlie-row-text")
        first = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        first.add_css_class("charlie-row-first")
        first.append(_label("Updates", "body", weight=550))
        unread = sum(1 for t in threads if t.unread)
        if unread:
            first.append(CountBadge(unread, attention=True))
        first.append(Gtk.Box(hexpand=True))
        chevron = icons.image("chevron-up" if open_ else "chevron-down")
        chevron.add_css_class("charlie-row-glyph")
        first.append(chevron)
        text.append(first)
        text.append(_label(", ".join(people.name(a) for a in senders), "caption", muted=True))
        line.append(text)
        self.set_child(line)
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"Updates, {unread} unread"])
        self.update_state([Gtk.AccessibleState.EXPANDED], [GObject.Value(GObject.TYPE_INT, int(open_))])


# ── the thread ─────────────────────────────────────────────────────────────

class Lane(Gtk.Widget):
    """A message's column: at most `fraction` of the thread's width and `cap` pixels, to one side."""

    def __init__(self, child: Gtk.Widget, *, outgoing: bool, fraction: float, cap: int, fill: bool = False) -> None:
        super().__init__(hexpand=True)
        self.child, self.outgoing, self.fraction, self.cap = child, outgoing, fraction, cap
        self.fill = fill
        child.set_parent(self)

    def _width(self, available: int) -> int:
        return max(0, min(available, self.cap, round(available * self.fraction)))

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            minimum, natural, _b, _n = self.child.measure(orientation, -1)
            return min(minimum, 120), natural, -1, -1
        width = self._width(for_size) if for_size >= 0 else self.cap
        return self.child.measure(orientation, width)

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        lane = self._width(width)
        natural = self.child.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        child_width = min(lane, max(natural, 0)) if self.outgoing and not self.fill else lane
        x = width - child_width if self.outgoing else 0
        self.child.allocate(child_width, height, baseline,
                            Gsk.Transform.new().translate(Graphene.Point().init(x, 0)))

    def do_dispose(self) -> None:
        if self.child is not None:
            self.child.unparent()
            self.child = None
        super().do_dispose()


# ── the window ─────────────────────────────────────────────────────────────

class CharlieWindow(AppWindow):
    def __init__(self, application) -> None:
        from .application import guard_single_key_shortcuts
        self.app = application
        self.store = application.store
        self.fixture = getattr(application, "fixture", None)
        self.people = People(self)
        self.account_id: str | None = None      # None: every account
        self.folder = "inbox"
        self.unread_only = False
        self.keep: str | None = None            # an unread thread opened while Unread is on stays listed
        self.query = ""
        self.bundle_open = False
        self.thread: Thread | None = None
        self.composing: str | None = None       # "reply", "all", "fwd" or "new" while the editor is out
        self.reply_mode: str | None = None      # the quick reply's mode: None = v71's default
        self.reply_to: Message | None = None    # replying to one message, not the last
        self.selected_message: str | None = None
        self.selected_attachment: tuple[str, int] | None = None   # (message id, index)
        self.details_open = False
        self.phone = False
        self.width = 0
        self._threads: list[Thread] = []
        self._rows: dict[str, Gtk.ListBoxRow] = {}
        self._bubbles: dict[str, MessageBubble] = {}
        self._building = False
        registry = self._commands()
        test_width = os.environ.get("CHARLIE_TEST_WIDTH")
        super().__init__(application=application, app_id=APP_ID, title="Charlie", icon_name=APP_ID,
                         commands=registry, default_width=1180, default_height=740,
                         minimum_width=360, minimum_height=420,
                         **({"geometry_scope": f"visual-{test_width}"} if test_width else {}))
        if test_width:
            self.set_default_size(max(360, int(test_width)), int(os.environ.get("CHARLIE_TEST_HEIGHT", "740")))
        self.add_css_class("charlie-window")
        self.connect("close-request", self._minimize_on_close)
        self.guarded_shortcuts = guard_single_key_shortcuts(self, application, registry)
        self._build()
        # Declare the real adaptive navigation state to the native window.
        # Without a breakpoint Adw measures the current desktop split as the
        # window's minimum, so GTK clamps a phone resize before WidthWatch
        # can fold that split. Our declared 360×420 minimum then governs the
        # configure, and the same shared list/detail owner follows its width.
        navigation = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(
            f"max-width: {NAV_HIDES_AT}px"))
        navigation.add_setter(self.nav, "visible", False)
        self.add_breakpoint(navigation)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key_pressed)
        self.add_controller(keys)
        self._width_watch = WidthWatch(self, self._width_changed)
        self.refresh()
        self._open_initial()

    # ── time ──────────────────────────────────────────────────────────────

    def now(self) -> datetime:
        return self.fixture.now if self.fixture is not None else datetime.now().astimezone()

    def my_name(self, address: str) -> str:
        account = next((a for a in self.store.accounts() if a.address.casefold() == address.casefold()), None)
        return account.display_name if account is not None and self.fixture is None else "You"

    # ── commands ──────────────────────────────────────────────────────────

    def _commands(self) -> CommandRegistry:
        has = lambda: self.thread is not None  # noqa: E731
        return CommandRegistry((
            CommandGroup("Accounts", (
                Command("mail.all-inboxes", "All inboxes", lambda: self._choose("inbox", None), "inbox"),
                Command("mail.accounts", "Add account…", self._accounts, "plus"),
            )),
            CommandGroup(None, (
                Command("mail.compose", "New email", self._new_email, "square-pen", shortcut=("Ctrl", "N")),
                Command("mail.search", "Search mail", self._focus_search, "search", shortcut=("Ctrl", "F")),
                Command("mail.refresh", "Check for mail", self._check_for_mail, "refresh-cw"),
            )),
            CommandGroup(None, (
                Command("mail.reply", "Reply", lambda: self._compose("one"), "reply",
                        shortcut=("Ctrl", "R"), enabled=has),
                Command("mail.reply-all", "Reply all", lambda: self._compose("all"), "reply-all",
                        shortcut=("Ctrl", "Shift", "R"), enabled=has),
                Command("mail.forward", "Forward", lambda: self._compose("fwd"), "forward",
                        shortcut=("Ctrl", "L"), enabled=has),
                Command("mail.archive", "Archive", self._archive, "archive", shortcut=("E",), enabled=has),
                Command("mail.delete", "Delete thread", self._trash, "trash-2", shortcut=("Delete",),
                        enabled=has, destructive=True),
            )),
            CommandGroup(None, (
                Command("mail.about", "About Charlie", self._about, "info"),
                Command("mail.quit", "Quit Charlie", self.app.quit, "log-out", shortcut=("Ctrl", "Q")),
            )),
        ))

    def _minimize_on_close(self, _window: Gtk.Window) -> bool:
        """Keep mail services resident when the frame's close control is used."""
        if self.fixture is not None:
            return False
        self.minimize()
        return True

    # ── building ──────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.nav = self._build_nav()
        list_page = self._build_list()
        detail_page = self._build_thread()
        self.list_first = ListFirst(list_page, detail_page, on_back=self._back_to_list)
        self.list_first.split.set_spacing(WINDOW["gutter"])
        self.list_first.connect("showing", lambda _l, _s: self._sync_islands())
        self.list_first.attach_island(self.thread_island)
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)
        body.add_css_class("charlie-body")
        body.append(self.nav)
        body.append(self.list_first)
        self.set_body(body)
        # The phone's islands float over the window (top 52, left 12), each over its own page.
        self.list_island.float_over(list_page)
        self.thread_island.float_over(detail_page)

    def _build_nav(self) -> Gtk.Widget:
        """The mailboxes (v71 crNav), on the frame beside the list."""
        sidebar = NavigationSidebar(variant="destinations")
        sidebar.set_name(WIDGET_NAMES["nav"])
        sidebar.add_css_class("charlie-nav")
        sidebar.set_size_request(NAV_WIDTH, -1)
        sidebar.list.connect("row-activated", self._nav_activated)
        self.nav_sidebar = sidebar
        self.add_account = AddRow("Add account", icon="plus", on_activate=self._accounts)
        self.add_account.set_name("cr-add-account")
        sidebar.append_footer(self.add_account)
        return sidebar

    def _fill_nav(self) -> None:
        sidebar = self.nav_sidebar
        sidebar.clear()

        def row(account: str | None, box: str, title: str, icon: str, count: int | None, attention: bool) -> None:
            item = SidebarRow(title, lead=RowLead.icon(icon),
                              trail=count if count else None, attention=attention and bool(count))
            item.charlie_place = (account, box)
            sidebar.append_row(item)
            if account == self.account_id and box == self.folder:
                sidebar.list.select_row(item)

        row(None, "inbox", "All inboxes", "inbox", self._unread_threads(None, "inbox"), True)
        row(None, "flagged", "Flagged", "star", len(self._threads_in(None, "flagged")), False)
        for position, account in enumerate(self.store.accounts()):
            # TODO(v71-kit account heading): v71 .crnh is the account's dot, name and address
            # (kit request charlie-04); the kit's section heading carries the name.
            heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            heading.add_css_class("charlie-account-heading")
            heading.append(_label(account.display_name, "label", weight=650))
            heading.append(_label(account.address, "caption", muted=True))
            sidebar.list.append(Gtk.ListBoxRow(selectable=False, activatable=False, child=heading))
            row(account.id, "inbox", "Inbox", "inbox", self._unread_threads(account.id, "inbox"), True)
            row(account.id, "drafts", "Drafts", "file-text", self._draft_count(account.id), False)
            row(account.id, "sent", "Sent", "send-horizontal", None, False)
            row(account.id, "archive", "Archive", "archive", None, False)
            if position == 0:
                row(account.id, "trash", "Trash", "trash-2", None, False)
        if self.fixture is not None and self.fixture.labels:
            sidebar.append_section("Labels")
            for name, hue, note in self.fixture.labels:
                item = SidebarRow(name, lead=RowLead.dot(hue))
                item.charlie_label = note
                sidebar.append_row(item)

    def _build_list(self) -> Gtk.Widget:
        island = Island()
        island.add_css_class("charlie-list")
        island.set_size_request(LIST_WIDTH, -1)
        self.list_island_widget = island
        # Search mail and New email on top (v71 .crsh), a computer's.
        self.list_head = SidebarFoot(search="Search mail", on_search=self._search_changed,
                                     add=("New email", "square-pen", self._new_email), placement="header")
        self.list_head.set_name("cr-list-head")
        self.list_head.add_css_class("charlie-list-head")
        if getattr(self.list_head, "add_button", None) is not None:
            self.list_head.add_button.set_name("cr-new")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        content.add_css_class("charlie-list-content")
        self.list_content = content
        island.append(content)
        content.append(self.list_head)
        self.rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.rows.add_css_class("luma-navigation-list")
        self.rows.add_css_class("charlie-rows")
        self.rows.set_name("cr-rows")
        self.rows.connect("row-activated", self._row_activated)
        self.list_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.list_scroller.set_child(self.rows)
        content.append(self.list_scroller)
        # All · Unread at the foot (v71 .crtabs), a computer's; the count is the unread threads'.
        self.list_modes = ModeSwitch([("all", "All", "", None), ("unread", "Unread", "", 0)],
                                     current="all", fill=True, label="Show",
                                     on_change=lambda key: self._set_unread(key == "unread"))
        self.list_modes.set_name("cr-list-modes")
        self.list_modes.buttons["unread"].set_name("cr-unread")
        foot = Gtk.Box()
        foot.add_css_class("charlie-list-foot")
        foot.append(self.list_modes)
        self.list_foot = foot
        content.append(foot)
        island.set_hexpand(False)
        host = ToastHost(island)
        host.set_hexpand(False)   # the thread takes the room (rows ask for the width they're given)
        host.set_name(WIDGET_NAMES["list"])
        self.list_host = host
        # A phone's list: ☰ | the mailbox in the title island, the bar at the thumb (v71 phone.js label()).
        self.list_island = TitleIsland("All inboxes", "", lead="menu", grow=self.nav, grows="menu")
        self.list_island.set_name(WIDGET_NAMES["list_island"])
        self.list_island.connect("grown", self._list_island_grown)
        self.list_bar = ActionCenter().attach(host)
        self.list_bar.set_name(WIDGET_NAMES["list_bar"])
        self.list_search = BarSearch("Search", label="Search mail", keep=True,
                                     on_change=self._search_changed, on_close=self._refresh_list_bar)
        return host

    def _build_thread(self) -> Gtk.Widget:
        island = Island()
        island.add_css_class("charlie-main")
        island.set_hexpand(True)
        self.transcript = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.transcript.add_css_class("charlie-thread")
        clamp = Adw.Clamp(maximum_size=800, tightening_threshold=800, child=self.transcript)
        self.body_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.body_scroller.set_name(WIDGET_NAMES["body"])
        self.body_scroller.set_child(clamp)
        click = Gtk.GestureClick()
        click.connect("released", self._thread_clicked)
        self.transcript.add_controller(click)
        island.append(self.body_scroller)
        self.thread_island_widget = island
        host = ToastHost(island)
        host.set_name(WIDGET_NAMES["main"])
        self.thread_host = host
        # The subject pill and the corner float over the top of the thread (a computer's, v71 .flo).
        self.subject_pill = TitleIsland(lead=None, phone_only=False, on_title=self._toggle_details)
        self.subject_pill.set_name(WIDGET_NAMES["subject"])
        self.subject_pill.add_css_class("charlie-subject")
        host.add_overlay(self.subject_pill)
        host.set_measure_overlay(self.subject_pill, False)
        self.corner_slot = Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.START)
        self.corner_slot.add_css_class("charlie-corner")
        host.add_overlay(self.corner_slot)
        host.set_measure_overlay(self.corner_slot, False)
        self.corner: CornerPill | None = None
        self.bar = ActionCenter().attach(host)
        self.bar.set_name(WIDGET_NAMES["bar"])
        # A phone's thread: ‹ | faces, subject, who · count; it grows into Information.
        self.thread_island = TitleIsland(lead="back", lead_label="Back", on_lead=self._back_to_list,
                                         grow=self._info_panel, grows="details")
        self.thread_island.set_name(WIDGET_NAMES["thread_island"])
        self.details = DetailsPane("Details", on_close=self._details_closed)
        self.details.set_name(WIDGET_NAMES["details"])
        self.details.add_css_class("charlie-details")
        self.details.show(open=False, subject=None)
        self.details.connect("notify::shown", self._details_shown)
        page = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)
        page.add_css_class("charlie-page")
        page.append(host)
        page.append(self.details)
        return page

    # ── tiers ─────────────────────────────────────────────────────────────

    def _width_changed(self, width: int) -> None:
        if width <= 0:
            return
        self.width = width
        phone = width < PHONE_BELOW
        crossed = phone != self.phone
        self.phone = phone
        self._apply_widths()
        if crossed:
            # Crossing the phone width redraws the window in the other shape (v71 "redraw on crossing").
            self.thread_island.fold()
            self.list_island.fold()
            if phone and self.thread is not None and (self._opened_by_hand or self.composing == "new"):
                self.list_first.show_detail()
                GLib.idle_add(self._scroll_thread)
            elif phone:
                self.list_first.show_list()
            self.refresh(keep_scroll=True)
            self._show_thread_chrome()

    def _apply_widths(self) -> None:
        width, phone = self.width, self.phone
        nav_room = width > NAV_HIDES_AT and not (self.details_open and width <= NAV_HIDES_WITH_DETAILS)
        if not self.list_island.grown:
            self.nav.set_visible(nav_room and not phone)
        self.list_island_widget.set_size_request(LIST_NARROW_WIDTH if width <= LIST_NARROWS_AT else LIST_WIDTH, -1)
        list_shown = phone or width > LIST_HIDES_AT
        self.list_host.set_visible(list_shown or self.list_first.showing == "list")
        self.list_head.set_visible(not phone)
        self.list_foot.set_visible(not phone)
        if phone and self.details_open:
            self.details.show(open=False)
        self.subject_pill.set_visible(not phone and self.thread is not None and self.composing != "new")
        self.corner_slot.set_visible(not phone and self.thread is not None and self.composing != "new")
        lists = self.list_island_widget
        (lists.add_css_class if phone else lists.remove_css_class)("phone")
        (self.list_content.add_css_class if phone else self.list_content.remove_css_class)("phone")
        self._frame(phone)
        self._sync_islands()

    def _frame(self, phone: bool) -> None:
        """A phone's list and thread are the screen itself (v71 .phstack: no island around them)."""
        # Island draws the shared rim from its actual allocation. App spacing
        # belongs to its child, so the surface and rim keep the same bounds.
        self.list_content.set_margin_top(0 if phone else 12)
        self.list_content.set_margin_start(0 if phone else 8)
        self.list_content.set_margin_end(0 if phone else 8)
        for host, island, content in ((self.list_host, self.list_island_widget, self.list_content),
                                      (self.thread_host, self.thread_island_widget, self.body_scroller)):
            framed = content.get_parent() is island
            if framed == (not phone):
                continue
            parent = content.get_parent()
            if parent is host:
                # ToastHost is the toolkit's native GtkOverlay owner, whose
                # main child is released through set_child(), not Box.remove().
                host.set_child(None)
            elif parent is island:
                island.remove(content)
            elif parent is not None:
                raise RuntimeError("Charlie content has an unexpected native owner")
            if phone:
                host.set_child(content)
            else:
                island.append(content)
                host.set_child(island)

    def _sync_islands(self) -> None:
        listing = self.phone and self.list_first.showing == "list"
        self.list_island.set_visible(listing)
        self.thread_island.set_visible(self.phone and not listing and self.thread is not None
                                       and self.composing != "new")
        if self.phone:
            self._refresh_list_bar()

    _opened_by_hand = False

    # ── the list ──────────────────────────────────────────────────────────

    def _all_threads(self, account: str | None, folder: str) -> list[Thread]:
        store_folder = "inbox" if folder == "flagged" else folder
        conversations = self.store.conversations(store_folder, self.query if folder == self.folder else "", account)
        threads = [Thread(c, self.people) for c in conversations]
        if folder == "flagged":
            threads = [t for t in threads if t.flagged]
        elif folder in ("inbox", "archive", "trash"):
            # A conversation lives where its newest received message is (sent replies don't move it).
            threads = [t for t in threads if any(m.folder == folder for m in t.messages)]
        return threads

    def _threads_in(self, account: str | None, folder: str) -> list[Thread]:
        saved, self.query = self.query, "" if folder != self.folder else self.query
        try:
            return self._all_threads(account, folder)
        finally:
            self.query = saved

    def _unread_threads(self, account: str | None, folder: str) -> int:
        return sum(1 for t in self._threads_in(account, folder) if t.unread)

    def _draft_count(self, account: str | None) -> int:
        count = len(self._threads_in(account, "drafts"))
        if self.fixture is not None:
            count += sum(1 for d in self.fixture.drafts.values() if account in (None, d.account))
        return count

    def snippet(self, thread: Thread) -> str:
        latest = thread.latest
        text = latest.snippet or " ".join(latest.body_text.split())
        if not latest.snippet:
            text = text[:200]
        if latest.outgoing:
            return f"You: {text}"
        import re
        paragraphs = [p.strip() for p in latest.body_text.split("\n-- \n")[0].split("\n\n") if p.strip()]
        first = next((p for p in paragraphs if not re.match(r"^(hi|hey|hello|dear)\b.*,$", p, re.I)), None)
        return " ".join(first.split()) if first else text

    def _is_update(self, thread: Thread) -> bool:
        if self.fixture is not None:
            return any(m.id in self.fixture.updates for m in thread.messages)
        return thread.brand is not None

    def refresh(self, *, keep_scroll: bool = False) -> None:
        """Read the store again and draw the list, the mailboxes and the counts."""
        threads = self._all_threads(self.account_id, self.folder)
        if self.unread_only:
            threads = [t for t in threads if t.unread or t.id == self.keep]
        self._threads = threads
        adjustment = self.list_scroller.get_vadjustment()
        scroll = adjustment.get_value()
        while (child := self.rows.get_first_child()) is not None:
            self.rows.remove(child)
        self._rows.clear()
        current = self.thread.id if self.thread is not None else None
        people = [t for t in threads if not self._is_update(t)]
        updates = [t for t in threads if self._is_update(t)]
        if self.folder == "inbox" and not self.query and updates:
            order: list[object] = [*people[:2], ("bundle", updates)]
            if self.bundle_open:
                order += [("sub", t) for t in updates]
            order += people[2:]
        else:
            order = list(threads)
        for item in order:
            if isinstance(item, tuple) and item[0] == "bundle":
                row: Gtk.ListBoxRow = BundleRow(item[1], self, self.bundle_open)
                row.set_name("cr-updates")
                self.rows.append(row)
                continue
            sub = isinstance(item, tuple)
            thread = item[1] if sub else item
            mail = MailRow(thread, self, sub=sub)
            mail.set_name(f"cr-row-{thread.id[-8:]}")
            # A phone slides a row to act on it (v71 LumaUI swipe rows): right Archive, left Flag.
            line = mail.get_child()
            mail.set_child(None)
            mail.set_child(SwipeRow(line,
                                    start=SwipeAction("archive", "green", lambda _r, t=thread: self._archive(t),
                                                      label="Archive"),
                                    end=SwipeAction("star", "orange", lambda _r, t=thread: self._flag(t),
                                                    label="Flag")))
            self.rows.append(mail)
            self._rows[thread.id] = mail
            if thread.id == current and not self.phone:
                self.rows.select_row(mail)
        if not threads:
            box = BOX_NAMES.get(self.folder, "this mailbox")
            text = (f"Nothing matches “{self.query}”" if self.query else
                    "You’re all caught up" if self.unread_only else f"Nothing in {box}")
            empty = _label(text, "body", muted=True, ellipsize=False, xalign=0.5)
            empty.add_css_class("charlie-empty")
            self.rows.append(Gtk.ListBoxRow(selectable=False, activatable=False, child=empty))
        if keep_scroll:
            GLib.idle_add(lambda: (adjustment.set_value(scroll), False)[1])
        unread = sum(1 for t in self._all_threads(self.account_id, self.folder) if t.unread)
        self.list_modes.set_count("unread", unread)
        self._unread_count = unread
        self._fill_nav()
        self._label_list_island()
        self._refresh_list_bar()

    def _label_list_island(self) -> None:
        """☰ | the mailbox, and under it its account, or the unread count (v71 phone.js label())."""
        if self.account_id is None:
            title = "All inboxes" if self.folder == "inbox" else BOX_NAMES.get(self.folder, "Mail")
            account = None
        else:
            title = BOX_NAMES.get(self.folder, "Mail")
            account = next((a for a in self.store.accounts() if a.id == self.account_id), None)
        unread = self._unread_threads(self.account_id, "inbox") if self.folder == "inbox" else 0
        subtitle = account.display_name if account else (f"{unread} unread" if unread else "All accounts")
        self.list_island.set_title(title, subtitle)
        self.list_island.title_button.update_property([Gtk.AccessibleProperty.LABEL], [f"{title}. Mailboxes"])

    def _refresh_list_bar(self) -> None:
        if not self.phone:
            self.list_bar.hide_bar()
            return
        # Model updates must not replace the native focused search field.
        # ActionCenter owns its expanded field until close_search(); rebuild
        # the normal row through the item's on_close callback instead.
        if self.list_bar.searching:
            return
        unread = getattr(self, "_unread_count", 0)
        self.list_bar.show_bar([
            self.list_search,
            BarAction("mail-check", tooltip="Show all mail" if self.unread_only else "Show only unread",
                      active=self.unread_only, badge=unread or None,
                      on_activate=lambda: self._set_unread(not self.unread_only)),
            BarAction("square-pen", tooltip="New email", primary=True, on_activate=self._new_email),
        ])
        _name_bar(self.list_bar, {1: "cr-unread", 2: "cr-new"})

    def _search_changed(self, text: str) -> None:
        if text == self.query:
            return
        self.query = text
        self.refresh()

    def _focus_search(self) -> None:
        if self.phone:
            self.list_first.show_list()
            self.list_bar.open_search(self.list_search) if hasattr(self.list_bar, "open_search") else None
        elif self.list_head.entry is not None:
            self.list_head.entry.grab_focus()

    def _set_unread(self, only: bool) -> None:
        if only == self.unread_only:
            return
        self.unread_only = only
        self.keep = None
        self.list_modes.set_current("unread" if only else "all")
        self.refresh()

    def _toggle_bundle(self) -> None:
        self.bundle_open = not self.bundle_open
        self.refresh(keep_scroll=True)

    def _row_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if isinstance(row, BundleRow):
            self._toggle_bundle()
            # A bundle is not a page: stay on the list (ListFirst pushes on any activation).
            if self.phone:
                GLib.idle_add(lambda: (self.list_first.show_list(), False)[1])
            return
        if isinstance(row, MailRow):
            self._opened_by_hand = True
            self.open_thread(row.thread.id)

    def _nav_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        note = getattr(row, "charlie_label", None)
        if note is not None:
            self._toast(note)
            return
        place = getattr(row, "charlie_place", None)
        if place is None:
            return
        self._choose(place[1], place[0])
        self.list_island.fold()

    def _list_island_grown(self, _island: TitleIsland, grown: bool) -> None:
        if grown:
            self.nav.set_visible(True)   # borrowed into the island on a phone
        else:
            GLib.idle_add(lambda: (self._apply_widths(), False)[1])

    def _choose(self, folder: str, account: str | None) -> None:
        self.folder, self.account_id = folder, account
        self.bundle_open = False
        self.keep = None
        self.refresh()
        if self.fixture is not None and folder == "drafts":
            draft = next((d for d in self.fixture.drafts.values() if account in (None, d.account)), None)
            if draft is not None:
                self._new_email(subject=draft.subject, text=draft.draft)
                return
        first = next(iter(self._threads), None)
        if first is not None and not self.phone:
            self.open_thread(first.id)

    # ── opening a thread ──────────────────────────────────────────────────

    def _open_initial(self) -> None:
        wanted = os.environ.get("LUMA_CHARLIE_OPEN", "")
        if self.fixture is not None and wanted:
            match = next((t for t in self._all_threads(None, "inbox") if self._fixture_thread(t) == wanted), None)
            if match is not None:
                if self._is_update(match) and not self.bundle_open:
                    self.bundle_open = True
                    self.refresh()
                self._opened_by_hand = True
                self.open_thread(match.id)
                self._select_from_environment()
                if self.phone:
                    self.list_first.show_detail()
                return
        first = next(iter(self._threads), None)
        if first is not None:
            self.open_thread(first.id, push=False)
        else:
            self._show_thread_chrome()

    def _fixture_thread(self, thread: Thread) -> str:
        from .fixture_v71 import THREADS
        ids = {m.id.split(":")[-1] for m in thread.messages}
        for item in THREADS:
            if ids & {m.id for m in item.messages}:
                return item.id
        return ""

    def _select_from_environment(self) -> None:
        picked = os.environ.get("LUMA_CHARLIE_SELECT", "")
        if not picked or self.thread is None:
            return
        name, _, index = picked.partition(":")
        message = next((m for m in self.thread.messages if m.id.split(":")[-1] == name), None)
        if message is None:
            return
        if index:
            self._pick_attachment(message.id, int(index))
        else:
            self._pick_message(message.id)

    def open_thread(self, thread_id: str, *, push: bool = True) -> None:
        thread = next((t for t in self._threads if t.id == thread_id), None)
        if thread is None:
            thread = next((t for t in self._all_threads(self.account_id, self.folder) if t.id == thread_id), None)
        if thread is None:
            return
        if self.unread_only and thread.unread:
            self.keep = thread.id
        self.thread = thread
        self.composing = None
        self.reply_mode = None
        self.reply_to = None
        self.selected_message = None
        self.selected_attachment = None
        self.thread_island.fold()
        if thread.unread:
            unread = tuple(m for m in thread.messages if m.unread)
            if self.fixture is None:
                self.app.engine.mark_read(unread, True, self.store.server_configs())
            else:
                self.store.set_read(tuple(m.id for m in unread), True)   # in memory only
            self.refresh(keep_scroll=True)
            self.thread = next((t for t in self._threads if t.id == thread_id), thread)
        for key, row in self._rows.items():
            if key == thread_id and not self.phone:
                self.rows.select_row(row)
        self._render_thread()
        self._show_thread_chrome()
        if push and self.phone:
            self.list_first.show_detail()
        GLib.idle_add(self._scroll_thread)

    def _scroll_thread(self) -> bool:
        adjustment = self.body_scroller.get_vadjustment()
        # A phone opens a thread at its top; a computer at its newest message (v71 crOpen).
        adjustment.set_value(0 if self.phone else adjustment.get_upper())
        return GLib.SOURCE_REMOVE

    def _back_to_list(self) -> None:
        self.thread_island.fold()
        self.bar.fold()
        self.list_first.show_list()
        self.composing = None
        self._sync_islands()

    def open_message_id(self, value: str) -> None:
        for thread in self._all_threads(None, "inbox"):
            if any(m.id == value or m.message_id == value for m in thread.messages):
                self.account_id, self.folder = None, "inbox"
                self.refresh()
                self.open_thread(thread.id)
                return

    def open_mailto(self, uri: str) -> None:
        parsed = urlparse(uri)
        query = parse_qs(parsed.query)
        self._new_email(to=[unquote(parsed.path)] if parsed.path else [],
                        subject=query.get("subject", [""])[0], text=query.get("body", [""])[0])

    # ── the conversation ──────────────────────────────────────────────────

    def _render_thread(self) -> None:
        while (child := self.transcript.get_first_child()) is not None:
            self.transcript.remove(child)
        self._bubbles.clear()
        thread = self.thread
        lumaui_phone = self.phone
        (self.transcript.add_css_class if lumaui_phone else self.transcript.remove_css_class)("phone")
        if thread is None or self.composing == "new":
            if thread is None and self.composing != "new":
                empty = _label("Nothing open", "title-2", ellipsize=False, xalign=0.5)
                empty.add_css_class("charlie-empty")
                self.transcript.append(empty)
            return
        now = self.now()
        day = ""
        for message in thread.messages:
            label = day_label(message.sent_at, now)
            if label != day:
                day = label
                line = _label(label.upper(), "label", muted=True, ellipsize=False, xalign=0.5)
                line.add_css_class("charlie-day")
                self.transcript.append(line)
            self.transcript.append(self._message_row(thread, message))

    def _message_row(self, thread: Thread, message: Message) -> Gtk.Widget:
        people = self.people
        out = message.outgoing
        fixture_item = self.fixture.messages.get(message.id) if self.fixture is not None else None
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.add_css_class("charlie-message")
        row.add_css_class("out" if out else "in")
        row.set_name(f"cr-msg-{message.id.split(':')[-1]}")
        face_size = 30 if self.phone else 34
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.add_css_class("charlie-column")
        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.END if out else Gtk.Align.START)
        meta.add_css_class("charlie-meta")
        if not out:
            meta.append(_label(people.name(message.sender_address), "caption", weight=600, ellipsize=False))
        recipients = [a for a in message.recipients if a]
        if recipients and not message.body_html and (len(recipients) > 1 or out):
            meta.append(_label("to " + ", ".join(people.first(a) for a in recipients), "caption", muted=True))
        meta.append(_label(message_time(message.sent_at, self.now()), "caption", muted=True, ellipsize=False))
        column.append(meta)
        presentation = message_presentation(message)
        selected = self.selected_message == message.id
        if message.body_html and presentation.designed_html:
            from .application import HtmlMessagePreview
            paper = HtmlMessagePreview(presentation.html or message.body_html,
                                       lambda m=message: self.app.show_formatted_message(m),
                                       private=bool(fixture_item and fixture_item.blocked))
            bubble = MessageBubble(child=paper)
            bubble.add_css_class("paper")
        else:
            bubble = MessageBubble(child=self._message_text(message, fixture_item), mine=out)
        bubble.set_selected(selected)
        # WebKit has no useful intrinsic width. Shrink-wrapping this paper,
        # as we do ordinary text, allocates its native view zero pixels wide.
        # The Lane already owns the responsive fraction and maximum width.
        bubble.set_halign(Gtk.Align.FILL if message.body_html and presentation.designed_html
                          else Gtk.Align.END if out else Gtk.Align.START)
        bubble.set_name(f"cr-bubble-{message.id.split(':')[-1]}")
        pick = Gtk.GestureClick()
        pick.connect("released", lambda _g, n, _x, _y, m=message.id: n == 1 and self._pick_message(m))
        bubble.add_controller(pick)
        self._bubbles[message.id] = bubble
        column.append(bubble)
        photos = list(fixture_item.photos) if fixture_item is not None else []
        images = [a for a in presentation.inline_images] if fixture_item is None else []
        if photos or images:
            column.append(self._photos(message, photos, images))
        visible = [a for a in message.attachments if a.id not in {i.id for i in presentation.inline_images}]
        if visible:
            files = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.END if out else Gtk.Align.START)
            files.add_css_class("charlie-files")
            for index, attachment in enumerate(visible):
                files.append(self._file_card(message, index, attachment))
            column.append(files)
        if fixture_item is not None and fixture_item.event is not None:
            title, start, when, where = fixture_item.event
            card = EventCard(title, start, when=when, where=where, tone="work",
                             on_add=lambda: self._toast(f"Added “{title}” to Calendar"))
            card.set_halign(Gtk.Align.START)
            column.append(card)
        lane = Lane(column, outgoing=out, fraction=0.84 if self.phone else 0.84,
                    cap=640 if message.body_html else 620,
                    fill=bool(message.body_html and presentation.designed_html))
        if not out:
            face = people.avatar(message.sender_address, face_size)
            face.set_valign(Gtk.Align.END)
            face.add_css_class("charlie-face")
            row.append(face)
        row.append(lane)
        return row

    def _message_text(self, message: Message, fixture_item) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class("charlie-text")
        if fixture_item is not None:
            paragraphs = list(fixture_item.text)
            signature = fixture_item.signature
            quote = fixture_item.quote
        else:
            body = message_presentation(message).text or message.body_text or message.snippet
            body, _sep, signature = body.partition("\n-- \n")
            paragraphs = [p for p in body.split("\n\n") if p.strip()]
            quote = ""
        for paragraph in paragraphs or [""]:
            label = Gtk.Label(label=paragraph.strip("\n"), xalign=0, wrap=True, selectable=False,
                              wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=200)
            label.add_css_class("lumaui-message-text")
            box.append(label)
        if quote:
            toggle = Gtk.Button(label="•••", halign=Gtk.Align.START, tooltip_text="Show quoted text")
            toggle.add_css_class("charlie-quote-toggle")
            quoted = Gtk.Label(label=quote, xalign=0, wrap=True, visible=False)
            apply_type(quoted, "body", muted=True)
            quoted.add_css_class("charlie-quote")

            def flip(button: Gtk.Button, shown=quoted) -> None:
                shown.set_visible(not shown.get_visible())
                button.set_label("Hide quoted text" if shown.get_visible() else "•••")
            toggle.connect("clicked", flip)
            box.append(toggle)
            box.append(quoted)
        if signature.strip():
            sig = Gtk.Label(label=signature.strip(), xalign=0, wrap=True)
            apply_type(sig, "caption", muted=True)
            sig.add_css_class("charlie-signature")
            box.append(sig)
        return box

    def _photos(self, message: Message, photos: list[str], images: list[Attachment]) -> Gtk.Widget:
        # A message's own photos (v71 .crimgs): one wide, or a grid of two columns.
        grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=True, halign=Gtk.Align.START)
        grid.add_css_class("charlie-photos")
        grid.set_overflow(Gtk.Overflow.HIDDEN)
        count = len(photos) or len(images)
        grid.add_css_class(f"n{min(count, 4)}")
        paintables: list[Gdk.Paintable | None] = []
        for name in photos:
            path = self.fixture.photo(name) if self.fixture is not None else None
            paintables.append(Gdk.Texture.new_from_filename(str(path)) if path is not None else None)
        for image in images:
            try:
                paintables.append(Gdk.Texture.new_from_bytes(GLib.Bytes.new(image.data)))
            except GLib.Error:
                paintables.append(None)
        for index, paintable in enumerate(paintables[:4]):
            button = Gtk.Button()
            button.add_css_class("charlie-photo")
            picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
            if paintable is not None:
                picture.set_paintable(paintable)
            button.set_child(picture)
            button.update_property([Gtk.AccessibleProperty.LABEL], ["Photo"])
            button.connect("clicked", lambda _b, i=index, m=message.id: self._pick_attachment(m, -1 - i))
            columns = 1 if count == 1 else 2
            grid.attach(button, index % columns, index // columns, 1, 1)
        grid.set_size_request(360 if count == 1 else 440, 225 if count == 1 else (165 if count == 2 else 330))
        return grid

    def _file_card(self, message: Message, index: int, attachment: Attachment) -> Gtk.Widget:
        location = Gio.File.new_for_uri(f"file:///nonexistent/charlie/{GLib.uri_escape_string(attachment.filename, None, False)}")
        card = FileCard(location, name=attachment.filename, size=attachment.size or None,
                        content_type=attachment.content_type, compact=False,
                        selected=self.selected_attachment == (message.id, index),
                        on_open=lambda a=attachment: self._open_attachment(a))
        card.set_name(f"cr-file-{message.id.split(':')[-1]}-{index}")
        pick = Gtk.GestureClick()
        pick.connect("released", lambda _g, n, _x, _y, m=message.id, i=index: n == 1 and self._pick_attachment(m, i))
        card.add_controller(pick)
        return card

    def _thread_clicked(self, gesture: Gtk.GestureClick, _n: int, x: float, y: float) -> None:
        # Tapping empty space lets a picked message go (v71).
        target = self.transcript.pick(x, y, Gtk.PickFlags.DEFAULT)
        node = target
        while node is not None and node is not self.transcript:
            if isinstance(node, (MessageBubble, FileCard)) or node.has_css_class("charlie-photo"):
                return
            node = node.get_parent()
        if self.selected_message or self.selected_attachment:
            self._deselect()

    # ── picking ───────────────────────────────────────────────────────────

    def _pick_message(self, message_id: str) -> None:
        self.selected_message = None if self.selected_message == message_id else message_id
        self.selected_attachment = None
        for key, bubble in self._bubbles.items():
            bubble.set_selected(key == self.selected_message)
        self._refresh_bar()

    def _pick_attachment(self, message_id: str, index: int) -> None:
        picked = (message_id, index)
        self.selected_attachment = None if self.selected_attachment == picked else picked
        self.selected_message = None
        for bubble in self._bubbles.values():
            bubble.set_selected(False)
        self._render_thread()
        self._refresh_bar()

    def _deselect(self) -> None:
        had_file = self.selected_attachment is not None
        self.selected_message = self.selected_attachment = None
        for bubble in self._bubbles.values():
            bubble.set_selected(False)
        if had_file:
            self._render_thread()
        self._refresh_bar()

    def _picked(self) -> tuple[Message, int] | None:
        if self.selected_attachment is None or self.thread is None:
            return None
        message_id, index = self.selected_attachment
        message = next((m for m in self.thread.messages if m.id == message_id), None)
        return (message, index) if message is not None else None

    # ── the head, the corner, the islands ─────────────────────────────────

    def _faces(self, thread: Thread, size: int) -> Gtk.Widget:
        if thread.brand is not None:
            return self.people.avatar(thread.brand, size)
        if len(thread.others) == 1:
            return self.people.avatar(thread.others[0], size)
        from luma_appkit import AvatarStack
        return AvatarStack([self.people.entry(a) for a in thread.others[:3]], size="header" if size > 32 else "row")

    def _show_thread_chrome(self) -> None:
        thread = self.thread
        if thread is not None and self.composing != "new":
            n = len(thread.messages)
            count = f" · {n} messages" if n > 1 else ""
            if thread.brand is not None:
                desk_sub = thread.brand
                phone_sub = self.people.name(thread.brand)
            else:
                desk_sub = f"{_join([self.people.first(a) for a in thread.others])} and you{count}" \
                    if len(thread.others) > 1 else f"{self.people.name(thread.others[0])}{count}" if thread.others else count
                desk_sub = f"{', '.join(self.people.first(a) for a in thread.others)} and you{count}" \
                    if len(thread.others) > 1 else desk_sub
                phone_sub = f"{thread.names()}{count}"
            self.subject_pill.set_title(thread.subject, desk_sub)
            self.subject_pill.set_faces(self._faces(thread, 36))
            self.thread_island.set_title(thread.subject, phone_sub)
            self.thread_island.set_faces(self._faces(thread, 30))
            parent = {"inbox": "the inbox"}.get(self.folder, BOX_NAMES.get(self.folder, "Mail"))
            self.thread_island.set_lead("back", f"Back to {parent}")
            self.thread_island.title_button.update_property([Gtk.AccessibleProperty.LABEL],
                                                           [f"{thread.subject}. Details"])
            self.subject_pill.title_button.update_property([Gtk.AccessibleProperty.LABEL],
                                                          ["Who's in this conversation"])
            self._build_corner(thread)
            self._fill_details(thread)
        self._refresh_bar()
        self._apply_widths()

    def _build_corner(self, thread: Thread) -> None:
        if self.corner is not None:
            self.corner_slot.remove(self.corner)
        more = CommandRegistry((
            CommandGroup(None, (
                Command("thread.unread", "Mark unread", self._mark_unread, "mail"),
                Command("thread.move", "Move to…", self._move_to, "folder"),
                Command("thread.mute", "Mute this thread", self._mute, "bell"),
                Command("thread.print", "Print", self._print, "printer"),
            )),
            CommandGroup(None, (
                Command("thread.delete", "Delete thread", self._trash, "trash-2", destructive=True),
            )),
        ))
        self.corner = CornerPill(actions=[("archive", "Archive", self._archive)],
                                 states=[("star", "Flag", thread.flagged, lambda on: self._flag(on=on))],
                                 info=self.details, more=more, order=("actions", "states", "info", "more"))
        self.corner.set_name(WIDGET_NAMES["corner"])
        for key, name in (("more", "cr-more"), ("info", "cr-details")):
            control = self.corner.controls.get(key)
            if control is not None and not self.phone:
                control.set_name(name)
        info = self.corner.controls.get("info")
        if info is not None:   # v71 names it Details
            info.set_tooltip_text("Details")
            info.update_property([Gtk.AccessibleProperty.LABEL], ["Details"])
        self.corner_slot.append(self.corner)

    def _info_panel(self) -> Gtk.Widget:
        """What a thread is (v71 crInfoBody): who is in it, then Messages, Account, Attachments."""
        thread = self.thread
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class("charlie-info")
        if thread is None:
            return box
        people = list(thread.others)
        if len(people) > 1:
            people.append(self._my_address(thread))
        for address in people:
            box.append(DetailsRow(self.people.name(address), address, lead=self.people.avatar(address, 36)))
        account = next((a for a in self.store.accounts() if a.id == thread.account_id), None)
        facts = [("Messages", str(len(thread.messages))), ("Account", account.display_name if account else "Mail")]
        if thread.attachments:
            facts.append(("Attachments", str(thread.attachments)))
        rule = Gtk.Separator()
        rule.add_css_class("charlie-info-rule")
        box.append(rule)
        for name, value in facts:
            # TODO(v71-kit details facts): v71's dl rows (word left, value right, no glyph); kit request charlie-05.
            box.append(PanelRow(name, detail=value, sensitive=True, closes=False))
        return box

    def _my_address(self, thread: Thread) -> str:
        account = next((a for a in self.store.accounts() if a.id == thread.account_id), None)
        return account.address if account else ""

    def _fill_details(self, thread: Thread) -> None:
        pane = self.details
        pane.clear()
        n = len(thread.messages)
        account = next((a for a in self.store.accounts() if a.id == thread.account_id), None)
        pane.add_hero(thread.subject, f"{n} message{'s' if n != 1 else ''} · {account.display_name if account else 'Mail'}",
                      lead=self._faces(thread, 48))
        others = list(thread.others)
        if len(others) > 1:
            pane.add_section("People", count=len(others) + 1)
        else:
            pane.add_section("From")
        for address in others:
            first = self.people.first(address)
            actions = () if self.people.automated(address) else (
                ("mail", f"Email {first}", lambda a=address: self._new_email(to=[a])),
                ("message-square", f"Message {first}", lambda f=first: self._toast(f"Opening Messages with {f}")))
            pane.add(DetailsRow(self.people.name(address), address, lead=self.people.avatar(address, 32),
                                actions=actions))
        if len(others) > 1:
            mine = self._my_address(thread)
            pane.add(DetailsRow("You", mine, lead=self.people.avatar(mine, 32)))
        photos: list[Gdk.Paintable] = []
        files: list[tuple[Message, int, Attachment]] = []
        for message in thread.messages:
            item = self.fixture.messages.get(message.id) if self.fixture is not None else None
            for name in (item.photos if item is not None else ()):
                path = self.fixture.photo(name)
                if path is not None:
                    photos.append(Gdk.Texture.new_from_filename(str(path)))
            files += [(message, i, a) for i, a in enumerate(message.attachments)]
        if photos or files:
            pane.add_section("Attachments")
            if photos:
                pane.add(DetailsPhotos(photos[:3]))
            for message, index, attachment in files:
                pane.add(self._file_card(message, index, attachment))
        also = []
        for address in others:
            if self.people.automated(address):
                continue
            for other in self._threads_in(None, "inbox") + self._threads_in(None, "sent"):
                if other.id != thread.id and address in other.others and other not in also:
                    also.append(other)
        if also:
            who = "these people" if len(others) > 1 else self.people.first(others[0])
            pane.add_section(f"Also with {who}")
            for other in also[:3]:
                pane.add(DetailsItem(other.subject, list_time(other.latest.sent_at, self.now()), icon="mail",
                                     on_activate=lambda t=other.id: self.open_thread(t)))
        pane.show(subject=thread)
        pane.add(StackedButtons([StackedButton("archive", "Archive", on_click=self._archive),
                                 StackedButton("trash-2", "Delete", danger=True, on_click=self._trash)], small=True))

    def _toggle_details(self) -> None:
        self.details.show(open=not self.details_open, subject=self.thread)

    def _details_shown(self, pane: DetailsPane, _spec) -> None:
        self.details_open = bool(pane.get_property("shown"))
        self._apply_widths()

    def _details_closed(self) -> None:
        self.details_open = False
        self._apply_widths()

    # ── the bar ───────────────────────────────────────────────────────────

    def _mode(self) -> str:
        thread = self.thread
        if self.reply_mode:
            return self.reply_mode
        return "all" if thread is not None and len(thread.others) > 1 else "one"

    def _reply_names(self) -> list[str]:
        thread = self.thread
        if thread is None:
            return []
        return [self.people.first(a) for a in thread.reply_targets(self._mode(), self.reply_to)]

    def _refresh_bar(self) -> None:
        bar, thread = self.bar, self.thread
        if self.composing is not None:
            return
        if thread is None:
            bar.hide_bar()
            return
        if self.selected_attachment is not None and (picked := self._picked()) is not None:
            self._attachment_bar(*picked)
            return
        if self.selected_message is not None:
            self._message_bar()
            return
        if thread.brand is not None:
            if self.phone:
                # A newsletter's phone bar: the sender, then Archive, Flag, ⋯ (no reply row).
                # TODO(v71-kit bar subject): v71 .crwho2 (the sender's mark, name and address); BarChip stands in.
                bar.show_bar([BarChip(self.people.name(thread.brand), lead=self.people.avatar(thread.brand, 30)),
                              SPACER, *self._thread_actions()])
            else:
                bar.show_bar([BarAction("archive", "Archive", primary=True, on_activate=self._archive),
                              BarAction("reply", "Reply", on_activate=lambda: self._compose("one")),
                              BarAction("forward", "Forward", on_activate=lambda: self._compose("fwd")),
                              SEPARATOR, self._more_action()])
            _name_bar(bar, {0: "cr-bar-first"})
            return
        mode = self._mode()
        names = self._reply_names()
        prompt = f"Reply to {_join(names)}…" if mode != "fwd" else "Forward…"
        context = None
        if self.reply_to is not None:
            from luma_appkit import BarContext
            context = BarContext("reply", "Replying to {}’s message",
                                 emphasis=self.people.first(self.reply_to.sender_address),
                                 on_dismiss=self._clear_reply_to)
        self.bar.set_editor(self._reply_editor(mode))
        if self.phone:
            switch = BarAction("reply-all" if mode == "all" else "reply", "Reply all" if mode == "all" else "Reply",
                               dropdown=True, panel=self._mode_panel, key="mode")
            # TODO(v71-kit two-row prompt): v71's bottom row is the reply well (a reply glyph, "Reply to …",
            # tapping it grows the editor) and Attach (kit request charlie-01); a compose field stands in.
            entry = BarEntry("compose", placeholder=prompt, on_submit=self._quick_send,
                             tools=[BarAction("paperclip", tooltip="Attach", on_activate=self._attach_then_grow)])
            bar.show_bar([switch, SPACER, *self._thread_actions()], entry=entry, context=context)
            _name_bar(bar, {0: "cr-reply-mode", 4: "cr-more"})
            _label_bar(bar, {0: "Reply, reply all or forward"})
        else:
            first = names[0] if len(names) == 1 else "All"
            switch = BarAction("reply-all" if mode == "all" else "reply", first, dropdown=True,
                               panel=self._mode_panel, key="mode")
            bar.show_bar([switch, BarPrompt(prompt),
                          BarAction("paperclip", tooltip="Attach", on_activate=self._attach_then_grow)],
                         context=context)
            _name_bar(bar, {0: "cr-reply-mode", 1: "cr-quick-reply"})
            _label_bar(bar, {0: "Reply, reply all or forward"})

    def _thread_actions(self) -> list[object]:
        thread = self.thread
        return [BarAction("archive", tooltip="Archive", on_activate=self._archive),
                BarAction("star", tooltip="Unflag" if thread.flagged else "Flag", active=thread.flagged,
                          on_activate=lambda: self._flag()),
                self._more_action()]

    def _more_action(self) -> BarAction:
        return BarAction("ellipsis", tooltip="More", panel=self._more_panel, key="more")

    def _more_panel(self) -> Gtk.Widget:
        thread = self.thread
        rows: list[object] = []
        if self.phone:
            rows.append(MenuItem("Details", icon="info", on_activate=self._open_info))
        rows += [MenuItem("Mark unread", icon="mail", on_activate=self._mark_unread),
                 MenuItem("Move to…", icon="folder", on_activate=self._move_to),
                 MenuItem("Mute this thread", icon="bell-off", on_activate=self._mute)]
        if thread is not None and thread.brand is not None and self.fixture is not None:
            rows.append(MenuItem("Unsubscribe", icon="bell-off", on_activate=self._unsubscribe))
        rows += [MenuItem("Print", icon="printer", on_activate=self._print),
                 MenuItem("Delete thread", icon="trash-2", danger=True, on_activate=self._trash)]
        return panel_list(rows, label="Thread")

    def _mode_panel(self) -> Gtk.Widget:
        thread = self.thread
        mode = self._mode()
        base = self.reply_to or (thread.latest if thread else None)
        target = ""
        if thread is not None and base is not None:
            target = self.people.first(base.recipients[0] if base.outgoing and base.recipients else base.sender_address)
        rows = [MenuItem(f"Reply to {target}" if self.phone else "Reply", icon="reply", selected=mode == "one",
                         on_activate=lambda: self._set_mode("one"))]
        if thread is not None and len(thread.others) > 1:
            rows.append(MenuItem("Reply all", icon="reply-all", selected=mode == "all",
                                 on_activate=lambda: self._set_mode("all")))
        rows.append(MenuItem("Forward", icon="forward", selected=mode == "fwd",
                             on_activate=lambda: self._compose("fwd")))
        return panel_list(rows, label="Reply")

    def _set_mode(self, mode: str) -> None:
        self.reply_mode = mode
        GLib.idle_add(lambda: (self._refresh_bar(), False)[1])

    def _message_bar(self) -> None:
        thread = self.thread
        message = next((m for m in thread.messages if m.id == self.selected_message), None)
        if message is None:
            self.selected_message = None
            self._refresh_bar()
            return
        many = len([a for a in message.recipients if not self.people.is_me(a)]) + (0 if message.outgoing else 1) > 1
        reply = lambda mode: lambda: self._compose(mode, message)  # noqa: E731
        more = BarAction("ellipsis", tooltip="More", key="msg", panel=lambda: panel_list([
            MenuItem("Mark unread from here", icon="mail", on_activate=lambda: self._mark_unread(message)),
            MenuItem("Show original", icon="file-text", on_activate=lambda: self._show_original(message)),
            MenuItem("Print", icon="printer", on_activate=self._print),
            None,
            MenuItem("Delete message", icon="trash-2", danger=True, on_activate=lambda: self._delete_message(message)),
        ], label="Message"))
        if self.phone:
            items = [BarAction("reply", tooltip="Reply", on_activate=reply("one"))]
            if many:
                items.append(BarAction("reply-all", tooltip="Reply all", on_activate=reply("all")))
            items += [BarAction("forward", tooltip="Forward", on_activate=reply("fwd")),
                      BarAction("copy", tooltip="Copy text", on_activate=lambda: self._copy(message)),
                      more, BarAction("x", tooltip="Done", on_activate=self._deselect)]
            self.bar.show_bar(items, fill=True)
        else:
            who = "Your message" if message.outgoing else f"{self.people.first(message.sender_address)}’s message"
            items = [BarChip(who, lead=self.people.avatar(message.sender_address, 22), on_dismiss=self._deselect),
                     BarAction("reply", "Reply", primary=True, on_activate=reply("one"))]
            if many:
                items.append(BarAction("reply-all", "Reply all", on_activate=reply("all")))
            items += [BarAction("forward", "Forward", on_activate=reply("fwd")),
                      BarAction("copy", "Copy text", on_activate=lambda: self._copy(message)), SEPARATOR, more]
            self.bar.show_bar(items)

    def _attachment_bar(self, message: Message, index: int) -> None:
        photo = index < 0
        attachment = None if photo else (message.attachments[index] if index < len(message.attachments) else None)
        name = f"Photo {-index}" if photo else (attachment.filename if attachment else "Attachment")
        word = "View" if photo else "Open"
        open_ = lambda: self._open_attachment(attachment) if attachment else self._toast(f"Opening {name}")  # noqa: E731
        save = lambda: self._save_attachment(attachment) if attachment else self._toast("Saved to Downloads")  # noqa: E731
        reply = lambda: self._compose("one", message)  # noqa: E731
        forward = lambda: self._compose("fwd")  # noqa: E731
        if self.phone:
            self.bar.show_bar([BarAction("eye" if photo else "app-window", word, primary=True, keep_label=True,
                                         fill=True, on_activate=open_),
                               BarAction("download", tooltip="Save", on_activate=save),
                               BarAction("reply", tooltip="Reply", on_activate=reply),
                               BarAction("forward", tooltip="Forward", on_activate=forward),
                               BarAction("x", tooltip="Done", on_activate=self._deselect)], fill=True)
        else:
            self.bar.show_bar([BarChip(name, icon="image" if photo else "file", on_dismiss=self._deselect),
                               BarAction("", word, primary=True, on_activate=open_),
                               BarAction("download", "Save", on_activate=save), SEPARATOR,
                               BarAction("reply", "Reply", on_activate=reply),
                               BarAction("forward", "Forward", on_activate=forward)])

    # ── the editor: reply, forward, new email ─────────────────────────────

    def _tools(self) -> list[object]:
        def fmt(name: str) -> Callable[[], None]:
            return lambda: self._format(name)
        return [BarAction("bold", tooltip="Bold", on_activate=fmt("bold")),
                BarAction("italic", tooltip="Italic", on_activate=fmt("italic")),
                BarAction("underline", tooltip="Underline", on_activate=fmt("underline")), SEPARATOR,
                BarAction("list", tooltip="Bulleted list", on_activate=fmt("list")),
                BarAction("list-ordered", tooltip="Numbered list", on_activate=fmt("ordered")),
                BarAction("quote", tooltip="Quote", on_activate=fmt("quote")),
                BarAction("link", tooltip="Link", on_activate=fmt("link")), SPACER,
                BarAction("paperclip", tooltip="Attach", on_activate=self._attach)]

    def _reply_editor(self, mode: str) -> ActionEditor:
        names = [self.people.name(a) for a in self.thread.reply_targets(mode, self.reply_to)] if self.thread else []
        title, icon = {"one": ("Reply", "reply"), "all": ("Reply all", "reply-all"),
                       "fwd": ("Forward", "forward")}[mode]
        editor = ActionEditor(
            title, icon, summary=None if mode == "fwd" else "to {}", summary_emphasis=", ".join(names),
            modes=[("one", "Reply", "reply"), ("all", "Reply all", "reply-all"), ("fwd", "Forward", "forward")],
            mode=mode, on_mode=self._editor_mode,
            fields=[("To", "Name or email")] if mode == "fwd" else (),
            tools=self._tools(), placeholder="Add a note (optional)" if mode == "fwd" else "Write your reply",
            primary=BarAction("send-horizontal", "Send", on_activate=self._send_editor),
            on_discard=self._discarded)
        self._editor = editor
        return editor

    def _editor_mode(self, mode: str) -> None:
        self.reply_mode = mode
        draft = self._editor.draft if getattr(self, "_editor", None) is not None else None
        self.composing = mode
        editor = self._reply_editor(mode)
        self.bar.set_editor(editor)
        if draft and editor.text_view is not None:
            editor.text_view.get_buffer().set_text(draft)
        self.bar.grow()

    def _compose(self, mode: str, message: Message | None = None) -> None:
        if self.thread is None:
            return
        self.reply_mode = mode
        self.reply_to = message if message is not None and message is not self.thread.latest else None
        self.selected_message = self.selected_attachment = None
        for bubble in self._bubbles.values():
            bubble.set_selected(False)
        self.composing = mode
        self.bar.set_editor(self._reply_editor(mode))
        self.bar.grow()

    def _attach_then_grow(self) -> None:
        self._compose(self._mode())
        GLib.timeout_add(120, lambda: (self._attach(), False)[1])

    def _new_email(self, *, to: list[str] | None = None, subject: str = "", text: str = "") -> None:
        if self.fixture is None and not self.app.configured_accounts():
            self._accounts()
            return
        self.composing = "new"
        self.selected_message = self.selected_attachment = None
        self.thread_island.fold()
        self._new_to = Gtk.Entry(placeholder_text="" if to else "Name or email", hexpand=True)
        self._new_to.set_text(", ".join(to or []))
        self._new_to.set_name("cr-new-to")
        self._new_subject = Gtk.Entry(placeholder_text="What’s it about?", hexpand=True)
        self._new_subject.set_text(subject)
        self._new_subject.set_name("cr-new-subject")
        account = self.store.accounts()[0] if self.store.accounts() else None
        editor = ActionEditor(
            "New email", "square-pen", summary="from {}", summary_emphasis=account.address if account else "",
            fields=[("To", self._new_to), ("Subject", self._new_subject)], tools=self._tools(),
            placeholder="Write your email", primary=BarAction("send-horizontal", "Send", on_activate=self._send_editor),
            on_discard=self._discarded)
        if text and editor.text_view is not None:
            editor.text_view.get_buffer().set_text(text)
        self._editor = editor
        self.bar.set_editor(editor)
        self.bar.show_bar([BarPrompt("New email")])
        self._render_thread()
        self._apply_widths()
        if self.phone:
            self.list_first.show_detail()
        self.bar.grow()
        self._sync_islands()

    def _format(self, name: str) -> None:
        editor = getattr(self, "_editor", None)
        view = editor.text_view if editor is not None else None
        if view is None:
            return
        buffer = view.get_buffer()
        tags = {"bold": {"weight": Pango.Weight.BOLD}, "italic": {"style": Pango.Style.ITALIC},
                "underline": {"underline": Pango.Underline.SINGLE}}
        bounds = buffer.get_selection_bounds()
        if name in tags:
            tag = buffer.get_tag_table().lookup(name) or buffer.create_tag(name, **tags[name])
            if bounds:
                start, end = bounds
                (buffer.remove_tag if start.has_tag(tag) else buffer.apply_tag)(tag, start, end)
            return
        prefix = {"list": "• ", "ordered": "1. ", "quote": "> "}.get(name)
        if prefix is not None:
            mark = buffer.get_iter_at_mark(buffer.get_insert())
            mark.set_line_offset(0)
            buffer.insert(mark, prefix)
        elif name == "link" and bounds:
            self._toast("Paste the address to link")
        view.grab_focus()

    def _attach(self) -> None:
        if self.fixture is not None:
            self._toast("Filer opens as a picker")
            return
        dialog = Gtk.FileDialog(title="Attach")

        def chosen(file_dialog: Gtk.FileDialog, result) -> None:
            try:
                file_dialog.open_multiple_finish(result)
            except GLib.Error:
                return
            self._toast("Attachments are sent from the editor soon")
        dialog.open_multiple(self, None, chosen)

    def _discarded(self) -> None:
        was_new = self.composing == "new"
        self.composing = None
        self._toast("Draft discarded")
        if was_new:
            self._render_thread()
            if self.phone:
                self.list_first.show_list()
        GLib.idle_add(lambda: (self._show_thread_chrome(), False)[1])

    def _editor_text(self) -> str:
        editor = getattr(self, "_editor", None)
        if editor is None or editor.text_view is None:
            return ""
        buffer = editor.text_view.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True).strip()

    def _send_editor(self) -> None:
        text = self._editor_text()
        mode = self.composing
        thread = self.thread
        if mode == "new" or mode == "fwd":
            source = self._new_to.get_text() if mode == "new" else self._editor.field("To").get_text()
            recipients = [part.strip() for part in source.split(",") if part.strip()]
            if not recipients:
                self._toast("Add who it’s for first", kind="warning")
                return
        else:
            recipients = thread.reply_targets(mode, self.reply_to) if thread else []
        if not text and mode not in ("fwd",):
            self._editor.focus_content()
            return
        if mode == "new":
            account = self.store.accounts()[0].id if self.store.accounts() else self.app.default_account_id()
            draft = Draft(account, to=recipients, subject=self._new_subject.get_text(), body=text)
        else:
            base = self.reply_to or thread.latest
            prefix = "Fwd: " if mode == "fwd" else "Re: "
            subject = base.subject if base.subject.casefold().startswith(prefix.casefold().strip() .lower()) \
                else f"{prefix}{thread.subject}"
            body = text
            if mode == "fwd":
                body = f"{text}\n\n---------- Forwarded message ----------\n{base.body_text}"
            draft = Draft(base.account_id, to=recipients, subject=subject, body=body,
                          in_reply_to="" if mode == "fwd" else base.message_id,
                          references=[] if mode == "fwd" else list((*base.references, base.message_id)))
        self._deliver(draft, "Forwarded" if mode == "fwd" else "Sent")

    def _quick_send(self, text: str) -> None:
        thread = self.thread
        if thread is None or not text.strip():
            return
        base = self.reply_to or thread.latest
        draft = Draft(base.account_id, to=thread.reply_targets(self._mode(), self.reply_to),
                      subject=base.subject if base.subject.casefold().startswith("re:") else f"Re: {thread.subject}",
                      body=text.strip(), in_reply_to=base.message_id,
                      references=list((*base.references, base.message_id)))
        self._deliver(draft, "Sent")

    def _deliver(self, draft: Draft, done: str) -> None:
        def finished(error: Exception | None) -> None:
            if error is not None:
                self._toast(self.app.friendly_network_error(error), kind="error")
                return
            self.composing = None
            self.reply_to = None
            if getattr(self, "_editor", None) is not None:
                self._editor.clear()
            self.bar.fold()
            self._toast(done, kind="sent")
            self.refresh(keep_scroll=True)
            self._show_thread_chrome()

        if self.fixture is not None:
            finished(None)   # the fixture never sends
            return
        self.app.send_draft(draft, finished)

    # ── acting on a thread ────────────────────────────────────────────────

    def _target(self, thread: Thread | None) -> Thread | None:
        return thread if isinstance(thread, Thread) else self.thread

    def _archive(self, thread: Thread | None = None) -> None:
        self._move(self._target(thread), "archive", "Archived")

    def _trash(self, thread: Thread | None = None) -> None:
        self._move(self._target(thread), "trash", "Moved to Trash")

    def _move(self, thread: Thread | None, destination: str, done: str) -> None:
        if thread is None:
            return
        source = "inbox" if self.folder == "flagged" else self.folder
        ids = tuple(m.id for m in thread.messages if m.folder == source) or tuple(
            m.id for m in thread.messages if not m.outgoing)
        sources = {m.id: m.folder for m in thread.messages if m.id in ids}
        order = [t.id for t in self._threads]
        self.store.move_messages(ids, destination)
        was_open = self.thread is not None and self.thread.id == thread.id
        self.refresh(keep_scroll=True)
        if was_open:
            index = order.index(thread.id) if thread.id in order else 0
            following = [t for t in self._threads if t.id in order[index + 1:]] or self._threads[-1:]
            if following and not self.phone:
                self.open_thread(following[0].id, push=False)
            else:
                self.thread = None
                self._render_thread()
                self._show_thread_chrome()
                if self.phone:
                    self.list_first.show_list()

        def undo() -> None:
            for message_id, folder in sources.items():
                self.store.move_messages((message_id,), folder)
            self.refresh(keep_scroll=True)
        self._toast(done, kind="archived" if destination == "archive" else "deleted", undo=undo)

    def _flag(self, thread: Thread | None = None, *, on: bool | None = None) -> None:
        thread = self._target(thread)
        if thread is None:
            return
        value = (not thread.flagged) if on is None else on
        self.store.set_flagged(tuple(m.id for m in thread.messages), value)
        self.refresh(keep_scroll=True)
        if self.thread is not None and self.thread.id == thread.id:
            self.thread = next((t for t in self._all_threads(self.account_id, self.folder) if t.id == thread.id),
                               self.thread)
            self._refresh_bar()
        self._toast("Flagged" if value else "Unflagged", kind="done")

    def _mark_unread(self, message: Message | None = None) -> None:
        thread = self.thread
        if thread is None:
            return
        if message is not None:
            chosen = [m for m in thread.messages if m.sent_at >= message.sent_at and not m.outgoing]
        else:
            chosen = [m for m in thread.messages if not m.outgoing][-1:]
        if self.fixture is None:
            self.app.engine.mark_read(tuple(chosen), False, self.store.server_configs())
        self.store.set_read(tuple(m.id for m in chosen), False)
        self.refresh(keep_scroll=True)
        self._toast("Marked unread")

    def _move_to(self) -> None:
        # TODO(v71 Move to): v71 says "Pick a folder in the sidebar"; picking a mailbox while holding a thread
        # is the kit's held row (ActionCenter.hold), wired once the mailboxes take a drop.
        self._toast("Pick a folder in the sidebar")

    def _mute(self) -> None:
        # A new kind of write (the thread skips the inbox); the fixture shows it, real mail waits for Nick.
        self._toast("Muted. New replies skip your inbox")

    def _unsubscribe(self) -> None:
        thread = self.thread
        if thread is not None and thread.brand:
            self._toast(f"Unsubscribed from {self.people.name(thread.brand)}")

    def _print(self) -> None:
        self._toast("Sent to the printer")

    def _show_original(self, message: Message) -> None:
        self.app.show_formatted_message(message)

    def _delete_message(self, message: Message) -> None:
        folder = message.folder
        self.store.move_messages((message.id,), "trash")
        self.selected_message = None
        self.refresh(keep_scroll=True)
        thread_id = self.thread.id if self.thread else None
        if thread_id is not None:
            self.open_thread(thread_id, push=False)

        def undo() -> None:
            self.store.move_messages((message.id,), folder)
            self.refresh(keep_scroll=True)
        self._toast("Moved to Trash", kind="deleted", undo=undo)

    def _copy(self, message: Message) -> None:
        self.get_clipboard().set(message_presentation(message).text or message.body_text)
        self._deselect()
        self._toast("Copied", kind="copied")

    def _clear_reply_to(self) -> None:
        self.reply_to = None
        self._refresh_bar()

    def _open_info(self) -> None:
        self.bar.fold_panel()
        GLib.idle_add(lambda: (self.thread_island.grow_into(), False)[1])

    def _open_attachment(self, attachment: Attachment | None) -> None:
        if attachment is None:
            return
        if self.fixture is not None or not attachment.data:
            self._toast(f"Opening {attachment.filename}", kind="opening")
            return
        from .application import open_attachment
        open_attachment(self, attachment)

    def _save_attachment(self, attachment: Attachment | None) -> None:
        if attachment is None:
            return
        if self.fixture is not None or not attachment.data:
            self._toast("Saved to Downloads", kind="downloaded")
            return
        from .application import save_attachment
        save_attachment(self, attachment)

    # ── the rest ──────────────────────────────────────────────────────────

    def _toast(self, message: str, *, kind: str = "done", undo: Callable[[], None] | None = None) -> None:
        where = self.list_host if (self.phone and self.list_first.showing == "list") else self.thread_host
        Toast.show(where, message, kind=kind, undo=undo)

    def _accounts(self) -> None:
        if self.fixture is not None:
            self._toast("Sign in to your Luma account to add it")
            return
        self.app.show_accounts()

    def _about(self) -> None:
        self._toast(f"Charlie {__version__}")

    def accounts_changed(self, account_id: str | None) -> None:
        self.account_id, self.folder = account_id, "inbox"
        self.thread = None
        self.refresh()
        self._open_initial()

    def _check_for_mail(self) -> None:
        if self.fixture is not None:
            return
        self.app.call_mail_agent("CheckNow")
        self._sync_all()

    def _sync_all(self) -> None:
        if self.fixture is not None:
            return
        accounts = self.app.configured_accounts()
        if not accounts:
            return
        pending = {account.id for account in accounts}
        errors: list[Exception] = []

        def complete(account, error: Exception | None) -> None:
            pending.discard(account.id)
            if error:
                errors.append(error)
            if pending:
                return
            current = self.thread.id if self.thread else None
            self.refresh(keep_scroll=True)
            if current is not None and self.composing is None:
                self.thread = next((t for t in self._threads if t.id == current), self.thread)
            if errors:
                self._toast(self.app.friendly_network_error(errors[0]), kind="error")

        for account in accounts:
            self.app.sync_account(account, lambda error, value=account: complete(value, error))

    def _key_pressed(self, _controller, keyval, _keycode, _state) -> bool:
        name = Gdk.keyval_name(keyval) or ""
        if name == "Escape":
            if self.thread_island.grown:
                self.thread_island.fold()
                return True
            if self.selected_message or self.selected_attachment:
                self._deselect()
                return True
            if self.phone and self.list_first.showing == "detail" and self.composing is None:
                self._back_to_list()
                return True
        return False

    def refresh_open(self) -> None:
        self.refresh(keep_scroll=True)


def _label_bar(center: ActionCenter, labels: dict[int, str]) -> None:
    """v71's names for bar items whose words are not their name (the reply switcher)."""
    row = getattr(center, "bar_row", None)
    index, child = 0, row.get_first_child() if row is not None else None
    while child is not None:
        if index in labels:
            child.update_property([Gtk.AccessibleProperty.LABEL], [labels[index]])
            child.set_tooltip_text(labels[index])
        index += 1
        child = child.get_next_sibling()


def _name_bar(center: ActionCenter, names: dict[int, str]) -> None:
    """Stable names on the bar's row items, for tools/lumaui-conform."""
    row = getattr(center, "bar_row", None)
    if row is None:
        return
    index, child = 0, row.get_first_child()
    while child is not None:
        if index in names:
            child.set_name(names[index])
        index += 1
        child = child.get_next_sibling()
