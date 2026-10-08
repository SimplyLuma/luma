# SPDX-License-Identifier: Apache-2.0

"""Shared, responsive Luma Messages application.

The handset inbox and conversation destinations are adaptive arrangements of
the same widgets, store, and native ModemManager transport used on desktop.
Visible state is always derived from the authoritative store; unsupported rich
messaging features are never presented as working SMS capabilities.

One window shows every message service (ADR-022): this device's own messages
and each phone Luma Connect reaches. Each service keeps its own store and
transports; a conversation belongs to exactly one service, and everything done
in it goes through that service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import json
import sys
import threading
import tempfile
import time
import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402
from luma_appkit import IslandSplitView

from luma_appkit import (  # noqa: E402
    add_style_sheet,
    AppWindow,
    Avatar,
    Command,
    CommandGroup,
    CommandRegistry,
    EmptyState,
    ListEmptyState,
    Island,
    EventCard,
    FileCard,
    MessageBubble,
    NavigationRow,
    NavigationSidebar,
    PersonAvatar,
    PlaceCard,
    PresentationMode,
    SidebarFoot,
    SongCard,
    Lightbox,
    LightboxItem,
    ScrollView,
    command_popover,
    install_appkit,
    icons,
    lumaui_tokens,
    motion_duration,
)
from prairie_ui import install_theme  # noqa: E402

from .eds_backend import ContactRecord, load_contacts
from .messages_backend import (
    AttachmentRecord,
    MessageRecord,
    MessageStore,
    MessagingCapability,
    ModemMessagingTransport,
    ThreadRecord,
    TransportMessage,
    QuoteRecord,
    MAX_ATTACHMENT_BYTES,
    _parse_timestamp,
    incoming_transport_id,
    normalize_address,
    png_preview_dimensions,
)
from .messages_accounts import Account, AccountProvider, Accounts, AccountStore, KeyringLocked, helper_directory, network, remove_account
from . import messages_luma as luma
from .messages_cloud import CLOUD_LABEL, CloudHistory, is_cloud, merge_thread, merge_threads
from .messages_mms import MmsCapability, MmsMessagingTransport, read_mms_part
from .messages_fixture import FixtureMessageStore, FixtureTransport
from . import messages_photos as photos
from .phone_system import TransientShellSurface


APPLICATION_ID = "org.projectluma.Messages"
NATIVE_SERVICE = "native"
_messages_style_provider: Gtk.CssProvider | None = None


@dataclass(eq=False)
class MessageService:
    """One place messages come from and go through (ADR-022).

    ``provider`` is None for this device's own modem and MMS service; a phone
    reached through Luma Connect supplies its provider, whose transports never
    touch this device's modem.
    """

    id: str
    label: str
    store: MessageStore
    transport: object
    mms_transport: object
    provider: object | None = None
    capability: MessagingCapability = field(default_factory=lambda: MessagingCapability(False, "Checking cellular service…"))
    mms_capability: MmsCapability = field(default_factory=MmsCapability)
    incoming_revision: int = 0
    monitor: Gio.FileMonitor | None = None
    # A network account (ADR-023); its conversations are network ids, not phone numbers.
    account: Account | None = None

    @property
    def phone_numbers(self) -> bool:
        return self.account is None

    @property
    def native(self) -> bool:
        return self.id == NATIVE_SERVICE

    def key(self, address: str) -> str:
        """Names one conversation across services, for notifications and restore."""
        return address if self.native else f"{self.id}|{address}"

    def writer(self) -> MessageStore:
        """A second connection to this service's store, for work off the UI thread."""
        clone = getattr(self.store, 'clone', None)
        if callable(clone):
            return clone()
        return type(self.store)(self.store.path)


class _NoKeyring:
    """Stands in when libsecret is unavailable: accounts stop instead of storing secrets elsewhere."""

    def get(self, _account_id):
        raise KeyringLocked("Secret Service unavailable")

    def set(self, _account_id, _label, _value):
        raise KeyringLocked("Secret Service unavailable")

    def delete(self, _account_id):
        pass


def install_messages_theme() -> None:
    global _messages_style_provider
    display = Gdk.Display.get_default()
    if display is None or _messages_style_provider is not None:
        return
    # The kit owns this sheet, so it is reloaded when the surface
    # treatment changes (luma_appkit.add_style_sheet). Loaded through a
    # provider of its own it was not: whatever the palette was when the
    # application started stayed on screen, so switching to Glass left
    # this application painting Light colours next to windows that had
    # followed, which is one of the ways two windows came out different.
    provider = add_style_sheet(
        os.environ.get(
            "LUMA_MESSAGES_STYLE_PATH", "/usr/share/prairie-core/messages.css"
        )
    )
    _messages_style_provider = provider


_image_types: frozenset[str] | None = None


def _previewable_image_types() -> frozenset[str]:
    """Image types this system's pixbuf loaders decode (HEIC appears when a HEIF loader is installed).

    SVG is left out: an attachment is untrusted and is shown as a file instead.
    """
    global _image_types
    if _image_types is None:
        found = {"image/png", "image/jpeg", "image/webp", "image/gif"}
        for image_format in GdkPixbuf.Pixbuf.get_formats():
            found.update(kind for kind in image_format.get_mime_types() if kind.startswith("image/"))
        _image_types = frozenset(kind for kind in found if "svg" not in kind)
    return _image_types


# The gap between messages in a conversation, which the column's layout and the
# scroll anchoring both have to agree on.
MESSAGE_SPACING = 6
# Scroll anchoring can be traced when a reader reports the conversation moving.
_ANCHOR_DEBUG = os.environ.get("LUMA_MESSAGES_ANCHOR_DEBUG") == "1"
# How far from the bottom still counts as reading the newest messages.
SCROLL_END_SLACK = 24
# How far in from the bubble's corner a reaction pill sits.
REACTION_INSET = 10
# Pictures this far outside the visible part of a conversation give their pixels back.
PHOTO_KEEP_PAGES = 1.5


def _file_size(size: int) -> str:
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f} MB"
    if size >= 1000:
        return f"{size // 1000} KB"
    return f"{max(size, 0)} bytes"


def _media_status(part) -> tuple[str, bool]:
    """What an attachment still on its way says, and whether trying again can help."""
    noun = part.noun
    if part.state == "downloading":
        return f"Downloading {noun}…", False
    if part.state == "pending":
        if part.error in {"waiting_for_phone", ""}:
            # An incoming picture is never described as something the phone is
            # sending: it is on its way here, and nothing is being delivered.
            return f"Getting this {noun} from your phone…", True
        return f"The {noun} didn't download yet. Trying again…", True
    if part.error in {"no_full_size", "waiting_for_phone"}:
        # The wait ended without the file ever becoming available to fetch.
        return f"Your phone hasn't made this {noun} available to download yet.", True
    if part.error == "gone":
        # Google Messages only keeps attachments for a while.
        return f"Google Messages no longer has this {noun}. It's still on your phone.", True
    if part.error == "too_large":
        return f"This {noun} is too large to download here. Open it on your phone.", False
    if part.error in {"phone_download_failed", "phone_manual_download"}:
        return f"Your phone hasn't downloaded this {noun}. Open it on your phone, then try again.", True
    if part.error == "not_found":
        return f"Couldn't find this {noun} on your phone.", True
    return f"The {noun} didn't download.", True


def _accessible(widget: Gtk.Widget, label: str) -> None:
    widget.update_property([Gtk.AccessibleProperty.LABEL], [label])


def _moment(timestamp: int) -> str:
    value = datetime.fromtimestamp(timestamp)
    age = (datetime.now().date() - value.date()).days
    if age == 0:
        return value.strftime("%-I:%M %p")
    if age == 1:
        return "Yesterday"
    return value.strftime("%b %-d")


def _day_label(timestamp: int) -> str:
    """Name the day the way a person would.

    Today and yesterday have names; anything inside the past week is its
    weekday; older than that needs its date to be of any use.
    """
    day = datetime.fromtimestamp(timestamp).date()
    today = datetime.now().date()
    difference = (today - day).days
    if difference == 0:
        return "Today"
    if difference == 1:
        return "Yesterday"
    if 0 < difference < 7:
        return day.strftime("%A")
    if day.year == today.year:
        return day.strftime("%A, %B %-d")
    return day.strftime("%B %-d, %Y")


def _delivery_label(state: str, timestamp: int, receipt: str = "") -> str:
    labels = {
        "queued": "Queued",
        "sending": "Sending",
        "sent": "Sent",
        "failed": "Not delivered",
    }
    label = labels.get(state, "")
    if state == "sent" and receipt == "read":
        label = "Read"
    elif state == "sent" and receipt == "delivered":
        label = "Delivered"
    elif state == "sent":
        label = f"Sent {datetime.fromtimestamp(timestamp):%-I:%M %p}"
    return label


def _avatar(name: str, *, small: bool = False, medium: bool = False, group: bool = False) -> Avatar:
    """The kit's identity: initials, or a person, business or group glyph; never a stray character."""
    return Avatar(name, compact=small, medium=medium, group=group)


# Every glyph is Prairie's, by its standard name (Lucide geometry; see
# assets/icon-theme/Prairie/upstream/lucide). Nothing is drawn here.
ICONS = {
    "compose": "document-edit-symbolic",
    "accounts": "system-users-symbolic",
    "back": "luma-chevron-left-symbolic",
    "call": "call-start-symbolic",
    "video": "camera-video-symbolic",
    "attach": "mail-attachment-symbolic",
    "mic": "audio-input-microphone-symbolic",
    "send": "mail-send-symbolic",
    "heart": "emblem-favorite-symbolic",
}


def _icon_button(kind: str, label: str, css_class: str) -> Gtk.Button:
    button = Gtk.Button()
    button.set_valign(Gtk.Align.CENTER)
    button.set_halign(Gtk.Align.CENTER)
    button.add_css_class(css_class)
    image = Gtk.Image(icon_name=ICONS[kind], halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    image.add_css_class("messages-icon")
    button.set_child(image)
    _accessible(button, label)
    button.set_tooltip_text(label)
    return button


def _one_line(text: str) -> str:
    """A preview on a single line: line breaks and runs of spaces become one space."""
    return " ".join(text.split())


@dataclass(eq=False)
class _MessageBlock:
    """One message's widgets in the open conversation, and what they were drawn from."""
    context: tuple
    widgets: list
    anchor: Gtk.Widget
    slots: list
    sources: dict


class _ConversationView(Gtk.Widget, Gtk.Scrollable):
    """The open conversation's scrolling surface: places the rows and the scroll position in one step.

    A GtkViewport reads the scroll position before it places its child, so
    when a row above the reader grows (a picture arrives, a message rewraps
    on a resize) the rows are drawn once where the old position puts them and
    only corrected in the next frame: the conversation visibly jumps. This
    view instead lays the rows out, asks the window where the message being
    read has landed, and sets the position from that before anything is
    drawn, the way a native list keeps its scroll anchor. Nothing is painted
    between a height changing and the position that absorbs it.
    """

    __gtype_name__ = "PrairieMessagesConversationView"

    def __init__(self, child: Gtk.Widget, place: weakref.WeakMethod) -> None:
        super().__init__()
        self._hadjustment: Gtk.Adjustment | None = None
        self._vadjustment: Gtk.Adjustment | None = None
        self._hscroll_policy = Gtk.ScrollablePolicy.MINIMUM
        self._vscroll_policy = Gtk.ScrollablePolicy.MINIMUM
        self._handlers: dict[str, int] = {}
        self._place = place
        self._configuring = False
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self._child = child
        child.set_parent(self)

    def _adjustment_property(name: str):  # noqa: N805 - builds the two adjustment properties
        def getter(self) -> Gtk.Adjustment | None:
            return getattr(self, f"_{name}")

        def setter(self, adjustment: Gtk.Adjustment | None) -> None:
            old = getattr(self, f"_{name}")
            if old is adjustment:
                return
            if old is not None and name in self._handlers:
                old.disconnect(self._handlers.pop(name))
            setattr(self, f"_{name}", adjustment)
            if adjustment is not None:
                self._handlers[name] = adjustment.connect("value-changed", self._scrolled)
            self.queue_allocate()

        return GObject.Property(type=Gtk.Adjustment, getter=getter, setter=setter)

    def _policy_property(name: str):  # noqa: N805
        def getter(self) -> Gtk.ScrollablePolicy:
            return getattr(self, f"_{name}")

        def setter(self, policy: Gtk.ScrollablePolicy) -> None:
            setattr(self, f"_{name}", policy)
            self.queue_resize()

        return GObject.Property(type=Gtk.ScrollablePolicy, default=Gtk.ScrollablePolicy.MINIMUM,
                                getter=getter, setter=setter)

    hadjustment = _adjustment_property("hadjustment")
    vadjustment = _adjustment_property("vadjustment")
    hscroll_policy = _policy_property("hscroll_policy")
    vscroll_policy = _policy_property("vscroll_policy")
    del _adjustment_property, _policy_property

    def _scrolled(self, _adjustment: Gtk.Adjustment) -> None:
        if not self._configuring:
            self.queue_allocate()

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation: Gtk.Orientation, for_size: int):
        return self._child.measure(orientation, for_size)

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        child = self._child
        content = max(float(height), float(child.measure(Gtk.Orientation.VERTICAL, width)[1]))
        vertical = self._vadjustment
        shown = vertical.get_value() if vertical is not None else 0.0
        # Rows first, at the position the view had: every height is final now.
        child.allocate(width, round(content), -1, self._at(shown))
        place = self._place()
        target = place(content, float(height), shown) if place is not None else shown
        target = float(round(min(max(target, 0.0), max(0.0, content - height))))
        if target != shown:
            # Only the offset changes, so GTK moves the rows without laying them out again.
            child.allocate(width, round(content), -1, self._at(target))
        self._configuring = True
        try:
            if vertical is not None:
                vertical.configure(target, 0.0, content, height * 0.1, height * 0.9, float(height))
            if self._hadjustment is not None:
                self._hadjustment.configure(0.0, 0.0, float(width), width * 0.1, width * 0.9, float(width))
        finally:
            self._configuring = False

    @staticmethod
    def _at(value: float) -> Gsk.Transform:
        point = Graphene.Point()
        point.init(0.0, -value)
        return Gsk.Transform.new().translate(point)

    def do_dispose(self) -> None:
        for name, handler in list(self._handlers.items()):
            adjustment = getattr(self, f"_{name}")
            if adjustment is not None:
                adjustment.disconnect(handler)
        self._handlers.clear()
        if self._child is not None and self._child.get_parent() is self:
            self._child.unparent()
        self._child = None
        Gtk.Widget.do_dispose(self)


class ConversationRow(NavigationRow):
    """A conversation using the shared navigation row, with message state."""

    def __init__(self, record: ThreadRecord, service: MessageService, *, group: bool = False) -> None:
        encrypted = luma.is_luma(service)
        super().__init__(
            _one_line(record.display_name) or "Unknown",
            subtitle=_one_line(record.preview) or " ",
            icon_widget=_avatar(record.display_name, medium=True, group=group),
            trailing=str(record.unread) if record.unread else "",
        )
        self.set_hexpand(True)
        self.set_halign(Gtk.Align.FILL)
        self.set_size_request(-1, 64)
        self.record = record
        self.service = service
        self.group = group
        self.add_css_class("messages-conversation-row")
        self.add_css_class("unread" if record.unread else "read")
        line = self.get_child()
        labels = line.get_first_child().get_next_sibling()
        self.title = self._title_label
        self.title.add_css_class("messages-row-title")
        self.preview = labels.get_last_child()
        self.preview.add_css_class("messages-row-preview")
        if record.preview.startswith("Draft: "):
            self.preview.add_css_class("draft")
        if encrypted:
            lock = Gtk.Image(icon_name="luma-lock-symbolic", pixel_size=11, valign=Gtk.Align.CENTER)
            lock.add_css_class("messages-row-lock")
            line.insert_child_after(lock, line.get_first_child())
        self.dot = Gtk.Box(width_request=9, height_request=9, valign=Gtk.Align.CENTER, halign=Gtk.Align.END)
        self.dot.add_css_class("messages-unread-dot")
        self.dot.set_opacity(1 if record.unread else 0)
        line.append(self.dot)
        state = f", {record.unread} unread" if record.unread else ""
        moment = _moment(record.updated) if record.updated else "New conversation"
        kind = ", Luma, encrypted" if encrypted else ""
        _accessible(self, f"{record.display_name}{kind}{state}, {moment}. {_one_line(record.preview)}")


class MessagesWindow(AppWindow):
    def __init__(self, application: Adw.Application) -> None:
        self._fixture_path = os.environ.get("LUMA_MESSAGES_FIXTURE", "")
        self._fixture_mode = bool(self._fixture_path)
        # Acquire before constructing a window or opening any account store.
        # A missing owner is a repair screen, never an unmanaged fallback.
        self.agent = None if self._fixture_mode else self._connect_agent(application)
        if not self._fixture_mode:
            from .messages_app_client import sandboxed, HostServiceUnavailable
            if sandboxed() and self.agent is None:
                raise HostServiceUnavailable('Messages could not start its approved service. Your messages and accounts are preserved. Review application updates in Depot, then try again.')
        commands = CommandRegistry((
            CommandGroup(None, (
                Command("messages.new", "New message", self._show_new_message, "document-edit-symbolic", shortcut=("Ctrl", "N")),
                Command("messages.search", "Search conversations", lambda: self.search.grab_focus(), "system-search-symbolic", shortcut=("Ctrl", "F")),
                Command("messages.refresh", "Refresh", self._refresh, "view-refresh-symbolic"),
                Command("messages.accounts", "Accounts…", self._show_accounts, "system-users-symbolic", shortcut=("Ctrl", "comma")),
                Command("messages.luma", "Your Luma Profile…", self._show_luma_profile, "system-users-symbolic"),
            )),
            # A desktop gives its sidebar to search and the list, so the
            # filters the handset shows as chips live here instead. They are
            # the same three filters driving the same one list.
            CommandGroup(None, (
                Command("messages.filter.all", "Show all conversations", lambda: self._apply_filter("all"), "message-square"),
                Command("messages.filter.unread", "Show unread only", lambda: self._apply_filter("unread"), "mail"),
                Command("messages.filter.groups", "Show groups only", lambda: self._apply_filter("groups"), "users"),
            )),
            CommandGroup(None, (Command("messages.quit", "Quit Messages", self.quit, "log-out", shortcut=("Ctrl", "Q")),)),
        ))
        super().__init__(
            application=application, app_id=APPLICATION_ID + (".Fixture" if self._fixture_mode else ""), title="Messages",
            icon_name=APPLICATION_ID, commands=commands, default_width=920,
            default_height=700, minimum_width=360, minimum_height=480,
        )
        self.add_css_class("messages-window")
        if self.context.input_mode.value == "touch":
            self.add_css_class("messages-touch")
        self._service_problems: list[str] = []
        self.accounts = (Accounts(root=Path(":luma-messages-fixture:no-accounts:")) if self._fixture_mode
                         else getattr(application, "accounts", None) or Accounts())
        self.helper_directory = getattr(application, "helper_directory", None) or helper_directory()
        self.login_options = getattr(application, "login_options", None) or {}
        self._account_secrets = getattr(application, "account_secrets", None)
        # Network accounts are connected by the Messages agent (ADR-033) so they
        # keep working with this window closed; the window asks it for sends,
        # reactions and retries. Without the agent the window runs them itself.
        if not self._fixture_mode:
            from .messages_app_client import sandboxed, ApplicationAccounts
            if sandboxed():
                self.accounts = ApplicationAccounts(self.agent)
        self.services = ([MessageService(NATIVE_SERVICE, "This device", FixtureMessageStore(Path(self._fixture_path)),
                                         FixtureTransport(), FixtureTransport(),
                                         capability=MessagingCapability(True, ""))]
                         if self._fixture_mode else self._discover_services(application))
        # A phone's conversations are what a computer without a modem is for;
        # a device with its own messages opens on those.
        self.service = next((service for service in self.services if not service.native), self.services[0])
        for service in self.services:
            service.store.recover_interrupted()
            service.incoming_revision = service.store.external_revision()
        # Other devices' history from the Luma Hub: shown beside this device's
        # own conversations, never written here.
        from .messages_app_client import sandboxed
        self.cloud = (CloudHistory(reader=lambda: []) if self._fixture_mode else
                      CloudHistory(reader=lambda: json.loads(self.agent.call('ApplicationCloud')[0])) if sandboxed() else CloudHistory())
        self.content_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="messages-content")
        # Pictures decode off the main thread, into a cache bounded by bytes.
        self.picture_worker = ThreadPoolExecutor(max_workers=2, thread_name_prefix="messages-pictures")
        self.picture_cache = photos.TextureCache()
        self._photo_slots: list[tuple[Gtk.Picture, tuple, Path, int, int]] = []
        self._photo_sources: dict = {}
        self._lightbox_keys: list[tuple] = []
        self._photo_generation = 0
        self._photo_sync_source = 0
        self._rendered: tuple | None = None
        self._rendered_for: tuple | None = None
        self._blocks: dict[str, _MessageBlock] = {}
        self._block_order: list[str] = []
        self._stick_end = True
        self._anchor_uid: str | None = None
        self._anchor_offset = 0.0
        self._anchoring = False
        self.peer_presence: dict[str, tuple[float, str]] = {}
        self.presence_expiry = 0
        self.contacts: tuple[ContactRecord, ...] = ()
        self.current_address: str | None = None; self.current_name = ""; self.filter_mode = "all"
        self._reloading_threads = False
        self.poll_source = 0; self.polling = False; self.closed = False; self.store_refresh_source = 0
        self._loading_draft = False; self._compact_widgets: list[Gtk.Widget] = []
        self.composer_preedit = ""
        self._unlock_requested = False
        self._unlock_error = False
        unlock_action = Gio.SimpleAction.new("unlock-phone", None)
        unlock_action.connect("activate", self._unlock_phone)
        self.add_action(unlock_action)
        self.shell_surface = FixtureTransport() if self._fixture_mode else TransientShellSurface("org.projectluma.Messages.desktop")
        if not self._fixture_mode and sandboxed():
            self.agent.on_store_changed = self._host_store_changed
            self.agent.on_mailbox_error = lambda message: self._notice(message) if not self.closed else None
        if not self._fixture_mode and not sandboxed():
            for service in self.services:
                service.monitor = Gio.File.new_for_path(str(service.store.path.parent)).monitor_directory(Gio.FileMonitorFlags.NONE, None)
                service.monitor.connect("changed", self._store_changed)
        if self.context.presentation is PresentationMode.FULLSCREEN:
            self.maximize(); self.add_css_class("luma-fullscreen"); self.title_bar.set_visible(False)
            self.shell_surface.set("deep")
        self.split = IslandSplitView(collapsed=False, show_content=False)
        # v70's conversation list sits on the frame; only the conversation is an island.
        self.sidebar_island = self._build_sidebar()
        self.sidebar_island.set_name("msg-sidebar-pane")
        self.thread_island = Island()
        self.thread_island.add_css_class("messages-thread-island")
        self.thread_island.set_name("msg-island")
        self.thread_island.append(self._build_detail())
        self.split.set_sidebar(Adw.NavigationPage(child=self.sidebar_island, title="Messages"))
        self.split.set_content(Adw.NavigationPage(child=self.thread_island, title="Conversation"))
        self.split.set_sidebar_width_unit(Adw.LengthUnit.PX)
        self.split.set_min_sidebar_width(lumaui_tokens.SIDEBAR["width"] - lumaui_tokens.SIDEBAR["gutter"])
        self.split.set_max_sidebar_width(lumaui_tokens.SIDEBAR["width"] - lumaui_tokens.SIDEBAR["gutter"])
        self.toast_overlay = Adw.ToastOverlay(child=self.split); self.set_body(self.toast_overlay)
        compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 639px"))
        compact.add_setter(self.split, "collapsed", True); compact.add_setter(self.title_bar, "visible", False)
        for widget in self._compact_widgets:
            compact.add_setter(widget, "visible", True)
        # The title row and the filter chips are the handset's: it hides the
        # title bar, so the sidebar has to say what it is, and the chips are
        # its only way to filter. A desktop has the title bar and the app menu.
        compact.connect("apply", lambda *_: self._set_compact(True))
        compact.connect("unapply", lambda *_: self._set_compact(False))
        self.add_breakpoint(compact)
        self._set_compact(self.context.presentation is PresentationMode.FULLSCREEN)
        keys = Gtk.EventControllerKey(); keys.connect("key-pressed", self._key_pressed); self.add_controller(keys)
        if self._fixture_mode:
            GLib.idle_add(self._open_fixture_conversation)
        else:
            GLib.idle_add(self._restore_last_conversation)
        self.connect("close-request", self._close_requested)
        self.connect("notify::is-active", self._update_agent_viewing)
        if not self._fixture_mode:
            self._watch_wake()
        self._reload_threads()
        if not self._fixture_mode:
            for service in self.services:
                self._start_provider(service)
            GLib.idle_add(self._start_external_context_load)
        for problem in self._service_problems:
            GLib.idle_add(lambda message=problem: (self._notice(message), GLib.SOURCE_REMOVE)[1])

    # The conversation on screen decides which service these name.
    @property
    def store(self) -> MessageStore: return self.service.store

    def _save_state(self) -> None:
        """A conform fixture keeps window and selection state in this process."""
        if not getattr(self, "_fixture_mode", False):
            super()._save_state()

    def _open_fixture_conversation(self) -> bool:
        store = self.service.store
        selected = store.by_id.get(store.document.get("selected", ""))
        if selected:
            record = next((row for row in store.threads() if row.address == selected["address"]), None)
            if record is not None:
                self._open_thread(record, reveal=True)
        return GLib.SOURCE_REMOVE
    @property
    def transport(self): return self.service.transport
    @transport.setter
    def transport(self, value) -> None: self.service.transport = value
    @property
    def mms_transport(self): return self.service.mms_transport
    @mms_transport.setter
    def mms_transport(self, value) -> None: self.service.mms_transport = value
    @property
    def continuity(self): return self.service.provider
    @property
    def capability(self) -> MessagingCapability: return self.service.capability
    @capability.setter
    def capability(self, value: MessagingCapability) -> None: self.service.capability = value
    @property
    def mms_capability(self) -> MmsCapability: return self.service.mms_capability
    @mms_capability.setter
    def mms_capability(self, value: MmsCapability) -> None: self.service.mms_capability = value

    @property
    def native_service(self) -> MessageService:
        return next(service for service in self.services if service.native)

    def _discover_services(self, application) -> list[MessageService]:
        from .messages_app_client import sandboxed, ApplicationStore, NativeProvider
        if sandboxed():
            native = NativeProvider(self.agent)
            services = [MessageService(NATIVE_SERVICE, 'This device', ApplicationStore(self.agent, 'native'),
                                       native.sms_transport(), native.mms_transport(), native)]
            from .messages_app_client import PhoneProvider
            for phone in json.loads(self.agent.call('ApplicationPhones')[0]):
                provider = PhoneProvider(self.agent, phone['id'])
                services.append(MessageService(phone['id'], phone['label'], ApplicationStore(self.agent, phone['id']),
                    provider.sms_transport(), provider.mms_transport(), provider))
            for account in self.accounts.list(): services.append(self._account_service(account, services))
            return services
        services = [MessageService(NATIVE_SERVICE, "This device", MessageStore(),
                                   ModemMessagingTransport(), MmsMessagingTransport())]
        providers = []
        injected = getattr(application, "message_provider", None)
        if injected is not None:
            providers = [injected]
        else:
            try:
                from luma_continuity.message_provider import message_services
            except ImportError as error:
                # Luma Connect is optional; a saved phone without it is reported, not silently dropped.
                if (Path.home() / ".config/luma-connect/messages-phone.json").exists():
                    self._service_problems.append("Luma Connect isn't installed, so your phone's messages can't be shown.")
                if error.name not in {"luma_continuity", "luma_continuity.message_provider"}:
                    raise
            else:
                providers = message_services(
                    store_factory=MessageStore, dispatch=GLib.idle_add,
                    problem=lambda _error: self._service_problems.append(
                        "The phone saved for Messages couldn't be loaded. Choose it again in Luma Connect."))
        for provider in providers:
            peer = getattr(provider, "peer", "") or getattr(getattr(provider, "selection", None), "peer", "")
            label = getattr(provider, "label", "") or "Your phone"
            services.append(MessageService(
                f"phone:{peer}", label, MessageStore(provider.store_path), provider.sms_transport(), provider.mms_transport(),
                provider, capability=MessagingCapability(False, f"Connecting to {label}…")))
        try:
            # Every device signed in to Luma Connect has Luma Messages (ADR-051).
            from .messages_app_client import sandboxed
            if not sandboxed():
                luma.ensure_account(self.accounts, helper_dir=self.helper_directory)
        except (OSError, ValueError) as error:
            self._service_problems.append("Luma Messages couldn't be set up on this device.")
            print(f"messages: Luma account not added: {type(error).__name__}", file=sys.stderr)
        for account in self.accounts.list():
            try:
                services.append(self._account_service(account, services))
            except (KeyError, ValueError, OSError):
                continue  # an account for a network this build no longer knows
        return services

    @staticmethod
    def _connect_agent(application):
        from .messages_app_client import sandboxed
        if not sandboxed() and (getattr(application, "accounts", None) is not None or os.environ.get("LUMA_MESSAGES_AGENT") == "0"):
            return None  # tests that inject accounts run the helpers in this process
        from .messages_agent import AgentClient
        return AgentClient.connect()

    def _update_agent_viewing(self, *_args) -> None:
        """Tell the agent which conversation a person is looking at, so it isn't announced."""
        if self.agent is None or self.closed:
            return
        for service in self.services:
            viewing = getattr(service.provider, "viewing", None)
            if not callable(viewing) or not getattr(service.provider, "remote", False):
                continue
            shown = service is self.service and self.is_active() and self.get_visible()
            viewing(self.current_address if shown and self.current_address else None)

    @property
    def account_secrets(self):
        if self._account_secrets is None:
            try:
                from .messages_accounts import AccountSecrets
                self._account_secrets = AccountSecrets()
            except (ImportError, ValueError):
                self._account_secrets = _NoKeyring()
        return self._account_secrets

    def _account_service(self, account: Account, existing: list[MessageService]) -> MessageService:
        item = network(account.network)
        same = [s for s in existing if s.account is not None and s.account.network == account.network]
        label = item.name.removesuffix(" (unofficial client)") + (f" · {account.name or account.handle}" if same and not item.automatic else "")
        if self.agent is not None:
            from .messages_agent import RemoteAccountProvider
            provider = RemoteAccountProvider(account, self.accounts, self.agent, dispatch=GLib.idle_add,
                                             helper_dir=self.helper_directory)
        else:
            provider = AccountProvider(account, self.accounts, secrets=self.account_secrets, dispatch=GLib.idle_add,
                                       helper_dir=self.helper_directory)
        provider.label = label
        from .messages_app_client import ApplicationStore, sandboxed
        store = ApplicationStore(self.agent, account.id) if sandboxed() else AccountStore(provider.store_path)
        return MessageService(f"account:{account.id}", label, store, provider.sms_transport(),
                              provider.mms_transport(), provider, capability=MessagingCapability(False, f"Connecting to {item.name}…"),
                              account=account)

    def _start_provider(self, service: MessageService) -> None:
        if service.provider is None:
            return
        if service.account is not None:
            service.provider.on_message = lambda address, name, text, service=service: self._notify_service_arrival(service, address, name, text)
            service.provider.on_outbound_disabled = self._outbound_disabled
        service.provider.start(lambda state, service=service: self._service_changed(service, state))

    def _outbound_disabled(self, reason: str) -> None:
        if not self.closed:
            self._notice("Messages stopped sending to keep anything from going out twice. Review it in Accounts.")

    def _show_accounts(self, *_args) -> None:
        from .messages_accounts_ui import AccountsDialog
        self.accounts_dialog = AccountsDialog(self)
        self.accounts_dialog.present(self)

    def account_added(self, account: Account) -> None:
        """A finished sign-in becomes a service at once, without restarting Messages."""
        for service in list(self.services):
            if service.account is not None and service.account.id == account.id:
                self.remove_account_service(service, sign_out=False, forget=False)
        service = self._account_service(account, self.services)
        service.store.recover_interrupted()
        service.incoming_revision = service.store.external_revision()
        from .messages_app_client import sandboxed
        if not sandboxed():
            service.monitor = Gio.File.new_for_path(str(service.store.path.parent)).monitor_directory(Gio.FileMonitorFlags.NONE, None)
            service.monitor.connect("changed", self._store_changed)
        self.services.append(service)
        self._services_changed()
        self._start_provider(service)
        service.mms_transport.start(lambda path, properties, service=service: self._mms_message(service, path, properties),
                                    lambda capability, service=service: self._mms_capability_changed(service, capability))

    def remove_account_service(self, service: MessageService, *, sign_out: bool = True, forget: bool = True) -> None:
        """Removes a network account: signs out, deletes its keyring item and its stored chats."""
        if service not in self.services or service.account is None:
            return
        self.services.remove(service)
        if service is self.service:
            self.service = self.services[0]
            self._show_nothing_selected()
        if service.monitor is not None:
            service.monitor.cancel()
        service.mms_transport.stop()
        if getattr(service.provider, "remote", False):
            if forget:
                service.provider.remove(sign_out=sign_out)
            else:
                service.provider.close()
        elif forget:
            remove_account(service.account, self.accounts, secrets=self.account_secrets,
                           provider=service.provider if sign_out else None)
        else:
            service.provider.close()
        service.store.close()
        self._services_changed()

    def _services_changed(self) -> None:
        self.send_service_picker.get_model().splice(0, self.send_service_picker.get_model().get_n_items(),
                                                    [service.label for service in self.services])
        self.send_service_row.set_visible(len(self.services) > 1)
        self._reload_threads()

    def _service_named(self, service_id: str) -> MessageService | None:
        return next((service for service in self.services if service.id == service_id), None)

    def _conversation_from_key(self, key: str) -> tuple[MessageService | None, str]:
        service_id, separator, address = key.partition("|")
        if not separator:
            return None, key
        return self._service_named(service_id), address

    def _service_changed(self, service: MessageService, state) -> None:
        if self.closed: return
        service.capability = service.transport.inspect()
        if service is self.service:
            detail = {"offline": f"Waiting for {service.label}… Messages stay queued.",
                      "connecting": f"Connecting to {service.label}…",
                      "unavailable": f"{service.label} isn't available to Messages right now.",
                      "attention": "Some sends were not confirmed. Use Review send on the affected message."}.get(state["state"], service.capability.reason)
            self._set_transport_status(detail, service.capability.available)
            self._composer_changed(self.composer_buffer)
            self._render_messages()
        self._reload_threads()

    def _start_external_context_load(self) -> bool:
        for service in self.services:
            service.mms_transport.start(
                lambda path, properties, service=service: self._mms_message(service, path, properties),
                lambda capability, service=service: self._mms_capability_changed(service, capability))
        threading.Thread(target=self._load_external_context, daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _load_external_context(self) -> None:
        capabilities = {}
        for service in self.services:
            try: capabilities[service.id] = service.transport.inspect()
            except Exception: capabilities[service.id] = MessagingCapability(False, f"{service.label} isn't available right now.")
        # This device's contacts help address a new message through any service,
        # but only name conversations stored for this device (a phone cache keeps
        # the names its phone sent).
        GLib.idle_add(self._external_context_ready, capabilities, load_contacts())

    def _external_context_ready(
        self,
        capabilities: dict[str, MessagingCapability],
        contacts: tuple[ContactRecord, ...],
    ) -> bool:
        if self.closed:
            return GLib.SOURCE_REMOVE
        for service in self.services:
            if service.id in capabilities: service.capability = capabilities[service.id]
        self.contacts = contacts
        self._sync_contact_names()
        self._set_transport_status(self.capability.reason, self.capability.available)
        self._composer_changed(self.composer_buffer)
        self._reload_threads()
        self._poll_received()
        return GLib.SOURCE_REMOVE

    def _sync_contact_names(self) -> None:
        for contact in self.contacts:
            if not contact.phone:
                continue
            try: self.native_service.store.set_display_name(contact.phone, contact.name)
            except ValueError: continue

    def _set_compact(self, compact: bool) -> None:
        """Arrange the same widgets for a thumb or for a pointer."""
        self._compact = compact
        # Two panes side by side are two islands with the window's gutter
        # between them. Collapsed, the sidebar is the whole window and a
        # margin would only cut into it.
        self._sync_composer_chrome()

    def _build_sidebar(self) -> Gtk.Widget:
        self.sidebar_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, transition_duration=motion_duration(210), hexpand=True, vexpand=True)
        self.sidebar_stack.add_named(self._build_inbox(), "inbox")
        self.sidebar_stack.add_named(self._build_new_message(), "new")
        self.sidebar_stack.set_visible_child_name("inbox"); self.sidebar_stack.add_css_class("messages-sidebar")
        return self.sidebar_stack

    def _build_inbox(self) -> Gtk.Widget:
        # v70 puts the list on the frame and the search and New square in its foot.
        self.sidebar = NavigationSidebar()
        self.sidebar.set_name("msg-sidebar")
        self.luma_sidebar_box = self._build_luma_sidebar()
        self.sidebar.append_header(self.luma_sidebar_box)
        self.thread_list = self.sidebar.list
        self.thread_list.set_selection_mode(Gtk.SelectionMode.MULTIPLE)
        self.thread_list.connect("selected-rows-changed", self._selection_changed)
        self.thread_list.connect("row-activated", self._thread_activated)
        self.list_empty = EmptyState("No conversations yet", "Start a message to someone.",
            "luma-empty-message-symbolic", compact=True)
        self.list_empty.set_name("msg-sidebar-empty")
        self.foot = SidebarFoot(
            search="Search people and messages", on_search=lambda _text: self._reload_threads(),
            filters=(("all", "All", "message-square"), ("unread", "Unread", "mail-check"),
                     ("groups", "Groups", "users")),
            on_filter=self._apply_filter, add=("New message", "square-pen", self._show_new_message))
        self.foot.set_name("msg-foot")
        self.search = self.foot.entry
        self.sidebar.append_header(self.foot.heading)
        self.sidebar.append_footer(self.foot)
        self.selection_bar = self._build_selection_bar()
        self.sidebar.append_footer(self.selection_bar)
        return self.sidebar

    def _build_new_message(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True); root.add_css_class("messages-page")
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10); header.add_css_class("messages-thread-header")
        back = _icon_button("back", "Cancel new message", "messages-square-button"); back.connect("clicked", self._show_inbox); header.append(back)
        title = Gtk.Label(label="New message", xalign=0, hexpand=True); title.add_css_class("messages-thread-title"); header.append(title); root.append(header)
        recipient = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8); recipient.add_css_class("messages-recipient-field")
        label = Gtk.Label(label="To:"); label.add_css_class("messages-recipient-label"); recipient.append(label)
        self.recipient_search = Gtk.SearchEntry(placeholder_text="Name or phone number", hexpand=True)
        self.recipient_search.connect("search-changed", self._reload_recipients); self.recipient_search.connect("activate", self._recipient_activate_entry)
        recipient.append(self.recipient_search); root.append(recipient)
        # Which service a new conversation goes through. Existing conversations
        # keep theirs; this only appears when there is a choice to make.
        through = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8); through.add_css_class("messages-recipient-field")
        through_label = Gtk.Label(label="Send with"); through_label.add_css_class("messages-recipient-label"); through.append(through_label)
        self.send_service_picker = Gtk.DropDown.new_from_strings([service.label for service in self.services])
        self.send_service_picker.set_hexpand(True)
        _accessible(self.send_service_picker, "Send the new message with")
        through.append(self.send_service_picker)
        through.set_visible(len(self.services) > 1)
        self.send_service_row = through
        root.append(through)
        recent = Gtk.Label(label="SUGGESTED", xalign=0); recent.add_css_class("messages-section-label"); root.append(recent)
        self.recipient_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE); self.recipient_list.add_css_class("messages-recipient-list")
        self.recipient_list.connect("row-activated", self._recipient_activated)
        root.append(ScrollView(self.recipient_list))
        return root

    def _build_detail(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.detail_stack = Gtk.Stack(hexpand=True, vexpand=True)
        self.empty_state = EmptyState("Your messages",
            "Pick a conversation on the left, or start a new one.",
            "luma-empty-message-symbolic", primary=("New message", self._show_new_message))
        self.detail_stack.add_named(self.empty_state, "empty")
        thread = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True); thread.add_css_class("messages-thread")
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6); header.add_css_class("messages-thread-header")
        self.thread_back = _icon_button("back", "Back to conversations", "messages-square-button"); self.thread_back.set_visible(False)
        self._compact_widgets.append(self.thread_back); self.thread_back.connect("clicked", self._back_to_inbox); header.append(self.thread_back)
        self.thread_identity_cluster = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9, hexpand=True)
        self.thread_avatar_holder = Gtk.Box(); self.thread_identity_cluster.append(self.thread_avatar_holder)
        identity = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True, valign=Gtk.Align.CENTER)
        self.thread_title = Gtk.Label(label="Conversation", xalign=0); self.thread_title.set_ellipsize(Pango.EllipsizeMode.END); self.thread_title.add_css_class("messages-thread-title")
        self.thread_subtitle = Gtk.Label(label="", xalign=0); self.thread_subtitle.set_ellipsize(Pango.EllipsizeMode.END); self.thread_subtitle.add_css_class("messages-thread-subtitle")
        identity.append(self.thread_title); identity.append(self.thread_subtitle); self.thread_identity_cluster.append(identity); header.append(self.thread_identity_cluster)
        self.call_button = _icon_button("call", "Call", "messages-round-button")
        self.call_button.set_tooltip_text("Call")
        self.call_button.connect("clicked", self._call_current); header.append(self.call_button)
        # A video call is offered because the design offers it. It is disabled
        # and says why: this device has no video calling to place it through.
        self.video_button = _icon_button("video", "Video call", "messages-round-button")
        self.video_button.set_sensitive(False)
        self.video_button.set_tooltip_text("Video calling is not available on this device")
        header.append(self.video_button)
        # A Luma conversation's safety number, block and report (ADR-051).
        self.luma_button = Gtk.MenuButton(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.luma_button.add_css_class("messages-round-button")
        self.luma_button.set_child(Gtk.Image(icon_name="luma-info-symbolic"))
        self.luma_button.set_tooltip_text("Conversation details")
        _accessible(self.luma_button, "Conversation details")
        self.luma_button.set_create_popup_func(self._fill_luma_menu)
        self.luma_button.set_visible(False)
        header.append(self.luma_button)
        self.thread_header = header
        root.append(header)
        root.append(self.detail_stack)
        self.message_scroll = ScrollView()
        self.message_scroll.add_css_class("messages-thread-scroll")
        self.message_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=MESSAGE_SPACING, valign=Gtk.Align.START)
        self.message_box.add_css_class("messages-history")
        # The rows and the scroll position are placed together, anchored to the
        # message being read (see _place_view). No GtkViewport: it would also
        # scroll to any row that takes focus, so a click on a message's text
        # moved the conversation; keyboard focus is handled in _focus_moved.
        self.message_scroll.set_child(_ConversationView(self.message_box, weakref.WeakMethod(self._place_view)))
        from .messages_luma_ui import RequestBar, SafetyNotice
        self.luma_notice = SafetyNotice(self._show_luma_safety)
        self.luma_notice.set_visible(False)
        thread.append(self.luma_notice)
        thread.append(self.message_scroll)
        self.luma_request_bar = RequestBar(self._answer_luma_request)
        self.luma_request_bar.set_visible(False)
        thread.append(self.luma_request_bar)
        self.connect("notify::focus-widget", self._focus_moved)
        adjustment = self.message_scroll.get_vadjustment()
        adjustment.connect("value-changed", self._capture_anchor)
        adjustment.connect("value-changed", self._schedule_photo_sync)
        # A desktop expects to be able to drop a file into a conversation.
        # The drop is accepted and answered plainly, because this device has
        # no transport that can carry it — an affordance that silently does
        # nothing would be worse than one that says why.
        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect("drop", self._file_dropped)
        thread.add_controller(drop)
        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL); bottom.add_css_class("messages-composer-region")
        self.draft_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.draft_content.add_css_class("messages-draft-content")
        self.draft_content.set_visible(False)
        bottom.append(self.draft_content)
        self.transport_status = Gtk.Label(label=self.capability.reason, wrap=True); self.transport_status.add_css_class("messages-transport-status")
        self.unlock_phone_button = Gtk.Button(label="Unlock")
        self.unlock_phone_button.set_action_name("win.unlock-phone")
        self.unlock_phone_button.set_halign(Gtk.Align.START)
        _accessible(self.unlock_phone_button, "Unlock phone connection")
        self._set_transport_status(self.capability.reason, self.capability.available)
        bottom.append(self.transport_status)
        bottom.append(self.unlock_phone_button)
        composer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.END); composer.add_css_class("messages-composer")
        attach = _icon_button("attach", "Add attachment", "messages-composer-button"); attach.connect("clicked", self._attachment_requested); composer.append(attach)
        self.composer_view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, hexpand=True); self.composer_view.add_css_class("messages-composer-input")
        self.composer_view.set_top_margin(8); self.composer_view.set_bottom_margin(8); self.composer_view.set_left_margin(12); self.composer_view.set_right_margin(12)
        _accessible(self.composer_view, "Message"); self.composer_buffer = self.composer_view.get_buffer(); self.composer_buffer.connect("changed", self._composer_changed)
        self.composer_view.connect("preedit-changed", self._composer_preedit_changed)
        self.composer_view.connect("notify::has-focus", self._composer_focus_changed)
        self._composer_return_down = set()
        composer_keys = Gtk.EventControllerKey()
        composer_keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        composer_keys.connect("key-pressed", self._composer_key_pressed)
        composer_keys.connect("key-released", self._composer_key_released)
        self.composer_view.add_controller(composer_keys)
        input_overlay = Gtk.Overlay(hexpand=True)
        input_overlay.set_child(self.composer_view)
        self.composer_placeholder = Gtk.Label(label="Message", xalign=0, halign=Gtk.Align.FILL, valign=Gtk.Align.START)
        self.composer_placeholder.add_css_class("messages-composer-placeholder")
        self.composer_placeholder.set_margin_top(8); self.composer_placeholder.set_margin_start(13)
        self.composer_placeholder.set_can_target(False)
        input_overlay.add_overlay(self.composer_placeholder); composer.append(input_overlay)
        self.composer_action = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=motion_duration(120))
        voice = _icon_button("mic", "Record voice message", "messages-composer-button"); voice.connect("clicked", self._voice_requested)
        self.send_button = _icon_button("send", "Send message", "messages-send-button"); self.send_button.connect("clicked", self._send_message)
        self.composer_action.add_named(voice, "voice"); self.composer_action.add_named(self.send_button, "send"); self.composer_action.set_visible_child_name("voice")
        composer.append(self.composer_action); bottom.append(composer); thread.append(bottom)
        self.composer_region = bottom
        self.detail_stack.add_named(thread, "conversation")
        self.detail_stack.connect("notify::visible-child-name", self._sync_detail_state)
        self.detail_stack.set_visible_child_name("empty")
        self._sync_detail_state()
        return root

    def _sync_detail_state(self, *_args) -> None:
        active = self.detail_stack.get_visible_child_name() == "conversation"
        if not active and hasattr(self, "luma_request_bar"):
            self.luma_request_bar.set_visible(False)
            self.luma_notice.set_visible(False)
        child = self.thread_header.get_first_child()
        while child:
            child.set_opacity(1 if active else 0)
            child = child.get_next_sibling()
        self.thread_header.set_sensitive(active)
        self.thread_header.update_state([Gtk.AccessibleState.HIDDEN], [not active])
        self.composer_region.set_visible(active and not (hasattr(self, "luma_request_bar") and self.luma_request_bar.get_visible()))

    def _show_nothing_selected(self) -> None:
        self.current_address = None
        self.current_name = ""
        self.remember("conversation", "")
        self.detail_stack.set_visible_child_name("empty")
        self._reload_threads()

    @staticmethod
    def _children(container: Gtk.Widget):
        child = container.get_first_child()
        while child is not None:
            yield child
            child = child.get_next_sibling()

    @staticmethod
    def _clear(container: Gtk.Widget) -> None:
        while child := container.get_first_child(): container.remove(child)

    def _apply_filter(self, mode: str) -> None:
        """Filter the list from its sidebar-foot picker or the app menu."""
        self.filter_mode = mode
        if hasattr(self, "foot") and mode in {"all", "unread", "groups"}:
            self.foot.set_filter(mode)
        self._reload_threads()

    def _filter_changed(self, button: Gtk.ToggleButton, mode: str) -> None:
        if not button.get_active(): return
        self.filter_mode = mode
        if hasattr(self, "thread_list"): self._reload_threads()

    def _reload_threads(self) -> None:
        if self.closed:
            return
        self._reloading_threads = True
        empty_parent = self.list_empty.get_parent()
        if isinstance(empty_parent, Gtk.ListBoxRow):
            empty_parent.set_child(None)
        self._clear(self.thread_list)
        search = self.search.get_text() if hasattr(self, "search") else ""
        entries: list[tuple[MessageService, ThreadRecord]] = []
        for service in self.services:
            threads = service.store.threads(search)
            if service.native:
                threads = merge_threads(threads, self._cloud_history(service), service.store.thread, search)
            entries.extend((service, record) for record in threads)
        entries.sort(key=lambda entry: (
            not bool(self.store.conversations.get(entry[1].address, {}).get("pinned"))
            if self._fixture_mode else False, -entry[1].updated))
        # Luma conversations from people not yet accepted wait in Requests, not the inbox.
        requests = [entry for entry in entries if self._luma_flags(*entry).get("request")]
        entries = [entry for entry in entries if entry not in requests] if self.filter_mode != "requests" else requests
        self._sync_luma_sidebar(len(requests))
        all_records = tuple(record for _service, record in entries)
        unread_count = sum(1 for item in all_records if item.unread)
        self.foot.set_filter_counts({"all": len(all_records), "unread": unread_count,
                                     "groups": sum(self._is_group(*entry) for entry in entries)})
        shown = ([entry for entry in entries if entry[1].unread] if self.filter_mode == "unread" else
                 [entry for entry in entries if self._is_group(*entry)] if self.filter_mode == "groups" else entries)
        records = tuple(record for _service, record in shown)
        query = self.search.get_text().strip()
        if self.current_address and not self.store.thread(self.current_address) and self.filter_mode == "all":
            # An untouched recipient is UI state, not a fabricated stored message.
            if not any(service is self.service and item.address == self.current_address for service, item in shown) and (
                    not query or query.casefold() in self.current_name.casefold() or query in self.current_address):
                shown = [(self.service, ThreadRecord(self.current_address, self.current_name, "Say hello", 0, 0)), *shown]
                records = tuple(record for _service, record in shown)
        for service, record in shown:
            row = ConversationRow(record, service, group=self._is_group(service, record))
            row.set_name("msg-thread-row")
            current = service is self.service and record.address == self.current_address
            if current:
                row.add_css_class("current-conversation")
            self._install_thread_gesture(row)
            self.thread_list.append(row)
            if current:
                self.thread_list.select_row(row)
        self.list_empty.set_text(f"Nothing matches “{query}”" if query else
            "No unread conversations" if self.filter_mode == "unread" else
            "No message requests" if self.filter_mode == "requests" else
            "No group conversations" if self.filter_mode == "groups" else "No conversations yet",
            "Try another name or message." if query else
            "Start a message to someone." if self.filter_mode == "all" else "Conversations will appear here.")
        if not records:
            self.thread_list.append(Gtk.ListBoxRow(selectable=False, activatable=False, child=self.list_empty))
        fresh = not any(service.store.threads() for service in self.services)
        self.empty_state.set_text("No messages yet" if fresh else "Your messages",
            "When someone writes to you, it shows up here. You can also start the conversation." if fresh else
            "Pick a conversation on the left, or start a new one.")
        self._reloading_threads = False

    def _thread_activated(self, _list: Gtk.ListBox, row: ConversationRow) -> None:
        self._reloading_threads = True
        self.thread_list.unselect_all()
        self.thread_list.select_row(row)
        self._reloading_threads = False
        self._open_thread(row.record, reveal=True, service=row.service)

    def _selected_rows(self) -> list[ConversationRow]:
        return [row for row in self.thread_list.get_selected_rows() if isinstance(row, ConversationRow)]

    def _selection_changed(self, *_args) -> None:
        if self._reloading_threads:
            return
        selected = self._selected_rows()
        if not selected and self.current_address:
            self._show_nothing_selected()
        many = len(selected) > 1
        self.selection_bar.set_visible(many)
        if many:
            self.selection_label.set_label(f"{len(selected)} conversations selected")

    def _build_selection_bar(self) -> Gtk.Widget:
        """What to do with several conversations at once."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.add_css_class("messages-selection-bar")
        bar.set_visible(False)
        self.selection_label = Gtk.Label(label="", xalign=0, hexpand=True)
        self.selection_label.add_css_class("messages-selection-label")
        bar.append(self.selection_label)
        read = Gtk.Button(label="Mark read")
        read.add_css_class("messages-selection-action")
        read.set_tooltip_text("Mark the selected conversations as read")
        read.connect("clicked", lambda *_: self._mark_selected_read())
        bar.append(read)
        delete = Gtk.Button(label="Delete")
        delete.add_css_class("messages-selection-action")
        delete.add_css_class("destructive")
        delete.set_tooltip_text("Delete the selected conversations from this device")
        delete.connect("clicked", lambda *_: self._delete_selected())
        bar.append(delete)
        return bar

    def _mark_selected_read(self) -> None:
        for row in self._selected_rows():
            row.service.store.mark_read(row.record.address)
        self.thread_list.unselect_all()
        self._reload_threads()

    def _delete_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        dialog = Adw.AlertDialog(
            heading=f"Delete {len(rows)} conversations?",
            body="This removes the messages from this Luma device. It does not unsend them.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def answered(_dialog, response: str) -> None:
            if response != "delete":
                return
            for row in rows:
                service, record = row.service, row.record
                native_messages = service.store.thread(record.address)
                service.store.delete_thread(record.address)
                self._delete_native_copies(native_messages, service)
                if service is self.service and self.current_address == record.address:
                    self.current_address = None
                    self.detail_stack.set_visible_child_name("empty")
            self.thread_list.unselect_all()
            self._reload_threads()

        dialog.connect("response", answered)
        dialog.present(self)

    @staticmethod
    def _is_group(service: MessageService, record: ThreadRecord) -> bool:
        kind = getattr(service.store, "conversation_kind", None)
        return bool(kind) and kind(record.address) == "group"

    def _callable_number(self, service: MessageService, address: str) -> str:
        """The number a conversation can be called on, or "" (a short code, a group, a network id)."""
        if service.phone_numbers:
            number = address
        else:
            phone = getattr(service.store, "conversation_phone", None)
            number = phone(address) if phone else ""
        digits = "".join(character for character in number if character.isdigit())
        return number if len(digits) >= 7 else ""

    def _install_thread_gesture(self, row: ConversationRow) -> None:
        """Give a conversation its menu: right button, a long press, or the Menu key."""
        # Each handler finds its row through its controller. A closure over the
        # row would be held by the row's own controller, a cycle Python's
        # collector cannot see through GTK, so every list reload leaked every row.
        secondary = Gtk.GestureClick(button=3)
        secondary.connect("pressed", self._thread_secondary_pressed)
        row.add_controller(secondary)
        hold = Gtk.GestureLongPress(touch_only=True)
        hold.connect("pressed", self._thread_long_pressed)
        row.add_controller(hold)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._thread_menu_key)
        row.add_controller(keys)

    def _thread_secondary_pressed(self, gesture: Gtk.GestureClick, _presses: int, x: float, y: float) -> None:
        row = gesture.get_widget()
        if isinstance(row, ConversationRow):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._show_thread_actions(row, x, y)

    def _thread_long_pressed(self, gesture: Gtk.GestureLongPress, x: float, y: float) -> None:
        row = gesture.get_widget()
        if isinstance(row, ConversationRow):
            self._show_thread_actions(row, x, y)

    def _thread_menu_key(self, controller: Gtk.EventControllerKey, keyval: int, _code: int, state) -> bool:
        row = controller.get_widget()
        if isinstance(row, ConversationRow) and (
                keyval == Gdk.KEY_Menu or (keyval == Gdk.KEY_F10 and state & Gdk.ModifierType.SHIFT_MASK)):
            self._show_thread_actions(row)
            return True
        return False

    def thread_commands(self, record: ThreadRecord, service: MessageService) -> CommandRegistry:
        """What can be done to a conversation from the list; only what does something."""
        number = self._callable_number(service, record.address)
        can_unread = not record.unread and service.store.has_incoming(record.address)
        return CommandRegistry((
            CommandGroup(None, (
                Command("conversation.open", "Open", lambda: self._open_thread(record, reveal=True, service=service), "message-square"),
                Command("conversation.call", "Call", lambda: self._call_number(number), "phone", visible=lambda: bool(number)),
                Command("conversation.mark-read", "Mark as Read", lambda: self._mark_thread_read(record, service), "mail-check",
                        visible=lambda: bool(record.unread)),
                Command("conversation.mark-unread", "Mark as Unread", lambda: self._mark_thread_unread(record, service), "mail",
                        visible=lambda: can_unread),
            )),
            CommandGroup(None, (
                Command("conversation.delete", "Delete Conversation…", lambda: self._confirm_delete_thread(record, service), "trash-2",
                        destructive=True),
            )),
        ))

    def _show_thread_actions(self, row: ConversationRow, x: float | None = None, y: float | None = None) -> Gtk.Popover:
        """The kit's menu, anchored where the pointer is, or under the row from the keyboard."""
        menu = command_popover(self.thread_commands(row.record, row.service), variant="desktop")
        if x is None or y is None:
            # From the keyboard: under the row's avatar, first item focused.
            menu.present_at_pointer(row, 18, max(1, row.get_height() - 6))
            menu.child_focus(Gtk.DirectionType.TAB_FORWARD)
        else:
            menu.present_at_pointer(row, x, y)
        return menu

    def _mark_thread_unread(self, record: ThreadRecord, service: MessageService) -> None:
        service.store.mark_unread(record.address)
        self._reload_threads()

    def _call_number(self, number: str) -> None:
        if not number:
            return
        try:
            # Phone's registered call action. A plain tel: URI only opens the dialler.
            Gio.Subprocess.new(["/usr/bin/prairie-phone", "--call", f"tel:{number}"], Gio.SubprocessFlags.NONE)
        except GLib.Error:
            self._notice("Phone could not call this number.")

    def _mark_thread_read(self, record: ThreadRecord, service: MessageService) -> None:
        service.store.mark_read(record.address)
        self._reload_threads()

    def _confirm_delete_thread(self, record: ThreadRecord, service: MessageService) -> None:
        dialog = Adw.AlertDialog(
            heading=f"Delete the conversation with {record.display_name}?",
            body="This removes the messages from this Luma device. It does not unsend them." if service.native else
                 f"This removes the copy on this Luma device. The messages stay on {service.label}.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect(
            "response",
            lambda _dialog, response: self._delete_thread(record, service) if response == "delete" else None,
        )
        dialog.present(self)

    def _cloud_history(self, service: MessageService | None = None) -> dict:
        if self._fixture_mode:
            return {}
        service = service or self.service
        if not service.native:
            return {}  # Luma Cloud history is this device's own kind of conversation
        try:
            return self.cloud.by_address(service.store.canonical_address)
        except Exception:
            return {}  # a damaged or locked cache never takes Messages down

    def _thread_messages(self, address: str, service: MessageService | None = None) -> tuple[MessageRecord, ...]:
        service = service or self.service
        local = service.store.thread(address)
        try:
            canonical = service.store.canonical_address(address)
        except ValueError:
            return local
        return merge_thread(local, self._cloud_history(service).get(canonical, ()))

    def _delete_thread(self, record: ThreadRecord, service: MessageService) -> None:
        native_messages = service.store.thread(record.address)
        if not native_messages and self._thread_messages(record.address, service):
            self._notice("Luma Cloud history is removed from the device that sent it")
            return
        if service.store.delete_thread(record.address):
            self._delete_native_copies(native_messages, service)
            if service is self.service and self.current_address == record.address:
                self.current_address = None
                self.detail_stack.set_visible_child_name("empty")
            self._reload_threads()
    def _show_inbox(self, *_args) -> None: self.sidebar_stack.set_visible_child_name("inbox")

    def _default_send_service(self) -> MessageService:
        """The service a new conversation uses unless the person picks another."""
        remembered = self.recall("send-service")
        if isinstance(remembered, str) and (service := self._service_named(remembered)) is not None:
            return service
        return next((service for service in self.services if service.capability.available), self.service)

    def _show_new_message(self, *_args) -> None:
        self._show_nothing_selected()
        self.send_service_picker.set_selected(self.services.index(self._default_send_service()))
        self.sidebar_stack.set_visible_child_name("new"); self.recipient_search.set_text(""); self._reload_recipients(); self.recipient_search.grab_focus()
        if self.split.get_collapsed(): self.split.set_show_content(False)

    def _reload_recipients(self, *_args) -> None:
        self._clear(self.recipient_list); query = self.recipient_search.get_text().strip()
        self._recipient_search_generation = getattr(self, "_recipient_search_generation", 0) + 1
        generation = self._recipient_search_generation
        # A plain username is as useful as @username or a profile link. Phone
        # numbers are rejected by handle_from, and contact matches stay shown.
        handle = luma.handle_from(query)
        luma_service = self._luma_service()
        if handle and luma_service is not None:
            waiting = Gtk.ListBoxRow(selectable=False, activatable=False,
                child=ListEmptyState(f"Looking up @{handle}…"))
            self.recipient_list.append(waiting)
            def found(person, error):
                if self.closed or generation != self._recipient_search_generation:
                    return
                if waiting.get_parent() is not self.recipient_list:
                    return
                self.recipient_list.remove(waiting)
                if error is not None or not (person or {}).get("account"):
                    message = getattr(error, "message", "") or f"No Luma member named @{handle}."
                    self.recipient_list.prepend(Gtk.ListBoxRow(selectable=False, activatable=False,
                        child=ListEmptyState(message)))
                    return
                name = luma.public_person_name(person, handle)
                row = self._recipient_row(f"@{handle}", name, f"Luma · @{handle}")
                row.luma_handle = handle; row.luma_service = luma_service
                self.recipient_list.prepend(row)
            self.luma_call(luma_service, "luma.people", {"handle": handle}, found)
        candidates = tuple(contact for contact in self.contacts if contact.phone and (not query or query.casefold() in contact.name.casefold() or query in contact.phone or query.casefold() in contact.organization.casefold()))[:20]
        direct = None
        if query:
            try: direct = normalize_address(query)
            except ValueError: direct = None
        if direct and not any(normalize_address(item.phone) == direct for item in candidates): self.recipient_list.append(self._recipient_row(direct, direct, "Phone number"))
        for contact in candidates: self.recipient_list.append(self._recipient_row(contact.phone, contact.name, contact.organization or contact.phone))
        if self.recipient_list.get_first_child() is None:
            row = Gtk.ListBoxRow(selectable=False, activatable=False)
            message = Gtk.Label(label=("Sign in to Luma Connect to find members" if handle and luma_service is None else
                "Enter a Luma username or complete phone number" if query else "Search a Luma username or a contact"), wrap=True); message.add_css_class("messages-recipient-empty")
            row.set_child(message); self.recipient_list.append(row)

    def _recipient_row(self, address: str, name: str, subtitle: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow(); row.address = address; row.display_name = name
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, hexpand=True); content.add_css_class("messages-recipient-row"); content.append(_avatar(name))
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        title = Gtk.Label(label=name, xalign=0); title.add_css_class("messages-row-title")
        detail = Gtk.Label(label=subtitle, xalign=0); detail.add_css_class("messages-row-preview")
        labels.append(title); labels.append(detail); content.append(labels); row.set_child(content); return row

    def _recipient_activate_entry(self, *_args) -> None:
        row = self.recipient_list.get_row_at_index(0)
        if row is not None and getattr(row, "address", ""):
            self._recipient_activated(self.recipient_list, row)
        elif (handle := luma.handle_from(self.recipient_search.get_text())) and (service := self._luma_service()) is not None:
            self.start_luma_conversation(service, handle, lambda error: error and self._notice(error.message))

    def _recipient_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        address = getattr(row, "address", "")
        if not address: return
        if getattr(row, "luma_handle", None):
            def done(error) -> None:
                if error is not None:
                    self._notice(error.message)
            self.start_luma_conversation(row.luma_service, row.luma_handle, done)
            return
        name = getattr(row, "display_name", address)
        index = self.send_service_picker.get_selected()
        service = self.services[index] if 0 <= index < len(self.services) else self.service
        try:
            address = service.store.canonical_address(address)
            if name != address and service.native: service.store.set_display_name(address, name)
        except ValueError as error: self._notice(str(error)); return
        if len(self.services) > 1: self.remember("send-service", service.id)
        existing = next((item for item in service.store.threads() if item.address == address), None)
        self._open_thread(existing or ThreadRecord(address, name, "", 0, 0), reveal=True, service=service)
        self.sidebar_stack.set_visible_child_name("inbox"); self.composer_view.grab_focus()

    def _restore_last_conversation(self) -> bool:
        """Open the conversation this window was last showing.

        A desktop window is left open on something. Returning to a blank pane
        every launch makes a person find their place again for no reason.
        """
        key = self.recall("conversation")
        if not isinstance(key, str) or not key:
            return GLib.SOURCE_REMOVE
        service, address = self._conversation_from_key(key)
        service = service or self.native_service
        record = next(
            (item for item in service.store.threads() if item.address == address), None
        )
        if record is not None:
            self._open_thread(record, reveal=False, service=service)
        return GLib.SOURCE_REMOVE

    def _mark_opened_read(self, service: MessageService, address: str, received: tuple[str, ...]) -> None:
        if received:
            service.store.mark_read(address)

    def _open_thread(self, record: ThreadRecord, *, reveal: bool, service: MessageService | None = None) -> None:
        if service is not None and service is not self.service:
            self.service = service
            self._set_transport_status(self.capability.reason, self.capability.available)
        records = self.store.thread(record.address)
        received = tuple(message.uid for message in records
                         if message.direction == "incoming" and message.state == "received")
        unread_mms = [message.transport_id.removeprefix("mmsd:")
                      for message in records
                      if message.direction == "incoming" and message.state == "received"
                      and message.transport_id and message.transport_id.startswith("mmsd:")]
        if unread_mms:
            def mark_native_read():
                for path in unread_mms:
                    try:
                        self.mms_transport.mark_read(path)
                    except Exception:
                        # Local read state remains durable while the daemon is
                        # unavailable; no outgoing read receipt is invented.
                        pass
            self.content_worker.submit(mark_native_read)
        self.current_address = self.store.canonical_address(record.address); self.current_name = record.display_name
        self._mark_opened_read(self.service, self.current_address, received)
        self.thread_title.set_label(record.display_name)
        self._sync_peer_presence()
        self._clear(self.thread_avatar_holder)
        self.thread_avatar_holder.append(_avatar(record.display_name, group=self._is_group(self.service, record)))
        self.call_button.set_sensitive(bool(self._callable_number(self.service, record.address)))
        self._sync_luma_thread()
        if hasattr(self.service.provider, "conversation_opened"):
            self.service.provider.conversation_opened(self.current_address)
        self._update_agent_viewing()
        self.detail_stack.set_visible_child_name("conversation"); self._render_messages()
        self._loading_draft = True
        try:
            self._set_composer_text(self.store.draft(self.current_address))
            self._composer_changed(self.composer_buffer)
        finally:
            self._loading_draft = False
        if reveal: self.split.set_show_content(True)
        self.remember("conversation", self.service.key(self.current_address))
        self._render_draft_content()
        self._reload_threads()

    def _thread_signature(self, records) -> tuple:
        """Everything a conversation's drawing depends on; equal means nothing to redraw."""
        media_of = getattr(self.continuity, "media_parts", None)
        sender_of = getattr(self.continuity, "sender", None) or getattr(self.store, "sender", None)
        receipt_of = getattr(self.store, "receipt", None)
        rows = []
        for message in records:
            # A part's kind decides what its card says and whether it is drawn
            # as a picture at all, and a helper can revise it in place, so it
            # belongs here with the rest: a row is only redrawn when one of
            # these changes.
            media = tuple((part.part, part.state, part.error, part.mime, part.attachment_uid, part.preview, part.size)
                          for part in media_of(message.uid)) if media_of else ()
            rows.append((message.uid, message.body, message.state, message.send_status, message.direction, message.quote,
                         tuple((item.uid, item.name, item.content_type, item.size, item.storage_key)
                               for item in message.attachments), media,
                         tuple(self.store.reactions(message.uid)),
                         receipt_of(message.uid) if receipt_of and message.direction == "outgoing" else "",
                         sender_of(message.uid) if sender_of and message.direction == "incoming" else ""))
        # Day separators say "Today" and "Yesterday", so the date is part of the drawing.
        return (self.service.id, self.current_address, self.current_name if not records else "",
                datetime.now().date(), tuple(rows))

    def _render_messages(self, *, to_end: bool = False) -> None:
        """Draw the open conversation, keeping the reader where they were.

        Account status changes, reconnects, receipts, reactions, upserts and
        pictures arriving all redraw. Each message is a block of widgets keyed
        by its uid: a block whose drawing did not change is kept as it is, a
        changed one is replaced where it stands, and the list is never cleared
        while the same conversation stays open. Where the reader is comes from
        the scroll anchor (see _place_view), never from a pixel offset.
        """
        if not self.current_address:
            self._clear(self.message_box)
            self._rendered = None
            self._rendered_for = None
            self._blocks = {}
            self._block_order = []
            self._anchor_uid = None
            self._photo_slots = []
            self._photo_sources = {}
            return
        records = self._thread_messages(self.current_address)
        signature = self._thread_signature(records)
        previous = self._rendered
        if previous is not None and previous == signature and not to_end:
            self._sync_peer_presence()
            return
        same_conversation = self._rendered_for == signature[:2]
        if previous is None or not same_conversation:
            # Another conversation, or a forced redraw: nothing is reused. A
            # forced redraw of the same conversation keeps the reader's anchor,
            # which names a message, not a widget.
            self._clear(self.message_box)
            self._blocks = {}
            self._block_order = []
        self._rendered_for = signature[:2]
        self._photo_generation += 1
        self._rendered = signature
        self.message_box.set_valign(Gtk.Align.START if records else Gtk.Align.FILL)
        self.message_box.set_vexpand(not records)
        self.composer_placeholder.set_label(("Luma Message" if luma.is_luma(self.service) else "Message") if records else "Say hello")
        self._sync_peer_presence()
        sender_of = getattr(self.continuity, "sender", None) or getattr(self.store, "sender", None) or (lambda _uid: "")
        media_of = getattr(self.continuity, "media_parts", None)
        rows = signature[4]
        wanted: list[tuple[str, tuple, object]] = []
        previous_message: MessageRecord | None = None
        for index, message in enumerate(records):
            day = datetime.fromtimestamp(message.timestamp).date()
            new_day = previous_message is None or datetime.fromtimestamp(previous_message.timestamp).date() != day
            sender = sender_of(message.uid) if message.direction == "incoming" else ""
            show_sender = bool(sender) and (previous_message is None or previous_message.direction != message.direction
                                            or sender_of(previous_message.uid) != sender)
            breaks = previous_message is not None and (previous_message.direction != message.direction
                                                       or message.timestamp - previous_message.timestamp > 300)
            reactions = self.store.reactions(message.uid)
            receipt = self.store.receipt(message.uid) if message.direction == "outgoing" and hasattr(self.store, "receipt") else ""
            delivery = "Needs review" if message.send_status == "uncertain" else _delivery_label(message.state, message.timestamp, receipt)
            footer = bool(delivery) and message.direction == "outgoing" and (
                message.state in {"queued", "sending", "failed"} or (index == len(records) - 1 and not reactions))
            following = records[index + 1] if index + 1 < len(records) else None
            joins_below = bool(following and following.direction == message.direction
                               and following.timestamp - message.timestamp <= 300
                               and datetime.fromtimestamp(following.timestamp).date() == day)
            context = (rows[index], _day_label(message.timestamp) if new_day else "", breaks,
                       show_sender, delivery, footer, joins_below)
            wanted.append((message.uid, context, message))
            previous_message = message
        if not records:
            wanted.append(("\0intro", (self.current_name, self.current_address), None))
        contexts = {key: context for key, context, _message in wanted}
        keys = contexts.keys()
        self._keep_anchor_through(contexts)
        # Blocks that went away, or whose drawing changed, leave; everything else stays put.
        for key in list(self._block_order):
            block = self._blocks[key]
            if key not in keys or block.context != contexts[key]:
                for widget in block.widgets:
                    if widget.get_parent() is self.message_box:
                        self.message_box.remove(widget)
                if key not in keys:
                    del self._blocks[key]
        order: list[str] = []
        after: Gtk.Widget | None = None
        for key, context, message in wanted:
            block = self._blocks.get(key)
            if block is None or block.context != context:
                block = self._build_block(key, context, message, media_of)
                self._blocks[key] = block
            for widget in block.widgets:
                parent = widget.get_parent()
                if parent is None:
                    self.message_box.insert_child_after(widget, after)
                elif widget.get_prev_sibling() is not after:
                    self.message_box.reorder_child_after(widget, after)
                after = widget
            order.append(key)
        self._block_order = order
        self._blocks = {key: self._blocks[key] for key in order}
        self._photo_slots = [slot for key in order for slot in self._blocks[key].slots]
        self._photo_sources = {name: source for key in order for name, source in self._blocks[key].sources.items()}
        if to_end or not same_conversation:
            self._follow_end()
        self._schedule_photo_sync()
        self._sync_lightbox()

    def _build_block(self, key: str, context: tuple, message: MessageRecord | None, media_of) -> _MessageBlock:
        """The widgets for one message: its day separator, bubble, reactions and delivery line."""
        slots, sources = self._photo_slots, self._photo_sources
        self._photo_slots, self._photo_sources = [], {}
        try:
            widgets = self._message_widgets(context, message, media_of) if message is not None else [self._intro_widget()]
            anchor = next((widget for widget in widgets if widget.has_css_class("messages-bubble-row")), widgets[0])
            return _MessageBlock(context, widgets, anchor, self._photo_slots, self._photo_sources)
        finally:
            self._photo_slots, self._photo_sources = slots, sources

    def _fixture_message_widget(self, message: MessageRecord) -> Gtk.Widget | None:
        """Compose v70's sample media from owner cards, without a real store."""
        if not self._fixture_mode:
            return None
        item = self.store.meta.get(message.uid, {})
        kind = item.get("kind")
        if kind == "photo":
            picture = Gtk.Picture.new_for_filename(str(self.store.asset(item["photo"])))
            picture.set_can_shrink(True)
            picture.set_size_request(300, 180)
            picture.set_name("msg-photo")
            return picture
        if kind == "file":
            size = item.get("size", "")
            count = int(float(size.split(" ")[0]) * 1_000_000) if size.endswith(" MB") else None
            widget = FileCard(str(self.store.asset("messages-v70/" + item["name"])), name=item["name"],
                              size=count, kind="Stage presentation", compact=True,
                              on_open=lambda: self._notice("Opening in Stage"))
            widget.set_name("msg-file-card")
            return widget
        if kind == "event":
            card = item["card"]
            return EventCard(card["title"], datetime(2026, 9, 25, 14), where=card["where"],
                             when=card["when"], on_add=lambda: self._notice("Added to Calendar"))
        if kind == "song":
            card = item["card"]
            artwork = Gdk.Texture.new_from_filename(str(self.store.asset(card["photo"])))
            return SongCard(card["title"], card["artist"], card["album"], artwork=artwork,
                            on_play=lambda: self._notice("Opening in Tide"))
        if kind == "place":
            card = item["card"]
            return PlaceCard(card["name"], card["address"], eta=card["eta"],
                             on_directions=lambda: self._notice("Opening in Maps"))
        if kind == "voice":
            # TODO(kit-request messages-03-message-parts.md): the waveform and its
            # playback state belong to the shared voice-message part.
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.set_name("msg-voice")
            play = Gtk.Button()
            play.set_child(icons.image("play"))
            _accessible(play, "Play voice message")
            play.connect("clicked", lambda _b: self._notice("Voice message playback is not available in the fixture"))
            row.append(play)
            row.append(Gtk.Label(label=f"0:{item['duration']:02d}"))
            return row
        return None

    def _message_widgets(self, context: tuple, message: MessageRecord, media_of) -> list[Gtk.Widget]:
        _row, day_label, breaks, show_sender, delivery, footer, joins_below = context
        widgets: list[Gtk.Widget] = []
        if day_label:
            separator = Gtk.Label(label=day_label, halign=Gtk.Align.CENTER); separator.add_css_class("messages-day-separator")
            widgets.append(separator)
        wrapper = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.END if message.direction == "outgoing" else Gtk.Align.START); wrapper.add_css_class("messages-bubble-row")
        if breaks: wrapper.add_css_class("sequence-break")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        if show_sender:
            # In a group, each run of messages says who wrote it.
            sender_of = getattr(self.continuity, "sender", None) or getattr(self.store, "sender", None) or (lambda _uid: "")
            author = Gtk.Label(label=sender_of(message.uid), xalign=0); author.add_css_class("messages-bubble-sender")
            author.set_ellipsize(Pango.EllipsizeMode.END)
            content.append(author)
        if message.quote:
            content.append(self._quote_widget(message.quote))
        media = tuple(media_of(message.uid)) if media_of else ()
        previews = {part.attachment_uid: part.preview for part in media if part.attachment_uid and part.preview}
        for attachment in message.attachments:
            content.append(self._attachment_widget(attachment, previews.get(attachment.uid)))
        unfinished = [part for part in media if part.state != "done"]
        for part in unfinished:
            content.append(self._media_widget(message, part))
        fixture_content = self._fixture_message_widget(message)
        if fixture_content is not None:
            content.append(fixture_content)
        just_text = bool(message.body and not (show_sender or message.quote or message.attachments
                                                or unfinished or fixture_content or
                                                (self._fixture_mode and self.store.meta.get(message.uid, {}).get("link"))))
        if message.body and not just_text:
            body = Gtk.Label(label=message.body, wrap=True, xalign=0, selectable=True)
            body.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            body.set_max_width_chars(51)
            body.set_natural_wrap_mode(Gtk.NaturalWrapMode.NONE)
            body.add_css_class("messages-bubble-text")
            content.append(body)
        bubble = MessageBubble(message.body if just_text else None, mine=message.direction == "outgoing",
                               joined_above=not (bool(day_label) or breaks), joined_below=joins_below,
                               child=None if just_text else content)
        bubble.add_css_class("messages-bubble")  # legacy gesture and scroll-anchor tests
        bubble.set_name(f"msg-bubble-{message.uid}")
        if is_cloud(message):
            bubble.add_css_class("from-luma-cloud"); bubble.set_tooltip_text(CLOUD_LABEL)
        self._install_message_gesture(bubble, message)
        counts: dict[str, int] = {}
        for reaction in self.store.reactions(message.uid):
            if reaction.state in {"sent", "received"}:
                counts[reaction.emoji] = counts.get(reaction.emoji, 0) + 1
        face: Gtk.Widget = bubble
        if counts:
            # The pill rides on the bubble's top corner. Its room above the
            # bubble is the row's margin, not part of the row, so the scroll
            # anchor (which holds a row's own top) keeps the message's text
            # still when a reaction arrives on the message being read.
            face = Gtk.Overlay()
            face.set_child(bubble)
            face.add_overlay(self._reactions_pill(message, counts))
            wrapper.add_css_class("has-reactions")
            wrapper.set_margin_top(14)
        has_media = bool(message.attachments or unfinished or fixture_content)
        if message.direction == "outgoing" or has_media:
            if message.direction == "outgoing":
                wrapper.add_css_class("messages-outgoing-row")
            maximum = 290 if has_media else 429
            measure = Adw.Clamp(maximum_size=maximum, tightening_threshold=maximum)
            measure.set_child(face)
            wrapper.append(measure)
        else:
            wrapper.append(face)
        widgets.append(wrapper)
        # Networks that report it say whether a sent message was delivered or read.
        if delivery:
            bubble.set_tooltip_text(delivery)
            bubble.update_property([Gtk.AccessibleProperty.DESCRIPTION], [delivery])
        if footer:
            state = Gtk.Label(label=delivery, halign=Gtk.Align.END); state.add_css_class("messages-delivery")
            if message.state == "failed": state.add_css_class("failed")
            widgets.append(state)
        return widgets

    def _reactions_pill(self, message: MessageRecord, counts: dict[str, int]) -> Gtk.Widget:
        """A message's reactions, grouped in one pill on the bubble's top corner.

        On the trailing corner of a message sent from here and the leading
        corner of one received. It overlaps the bubble's top edge by less than
        the bubble's own top padding, so it never covers text, and a ring in the
        conversation's ground makes it read as cut out of the bubble. The emoji
        are shown once each, in the order they came, with the number of people
        beside them when there is more than one, as phones show them.
        """
        outgoing = message.direction == "outgoing"
        pill = Gtk.Box(spacing=2, halign=Gtk.Align.END if outgoing else Gtk.Align.START, valign=Gtk.Align.START,
                       can_target=False)
        pill.add_css_class("messages-reactions")
        pill.add_css_class("outgoing" if outgoing else "incoming")
        # Logical sides, so a right-to-left conversation mirrors it with the bubbles.
        if outgoing:
            pill.set_margin_end(REACTION_INSET)
        else:
            pill.set_margin_start(REACTION_INSET)
        for emoji in counts:
            if emoji == "♥":
                glyph: Gtk.Widget = Gtk.Image(icon_name=ICONS["heart"], pixel_size=14)
            else:
                glyph = Gtk.Label(label=emoji)
            glyph.add_css_class("messages-reaction-emoji")
            pill.append(glyph)
        total = sum(counts.values())
        if total > 1:
            number = Gtk.Label(label=str(total))
            number.add_css_class("messages-reaction-count")
            number.set_margin_start(2)  # logical, so it mirrors right to left
            pill.append(number)
        described = ", ".join(f"{count} {emoji}" for emoji, count in counts.items())
        _accessible(pill, f"{total} reaction{'s' if total != 1 else ''}: {described}")
        return pill

    def _intro_widget(self) -> Gtk.Widget:
        intro = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                        halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, vexpand=True)
        intro.add_css_class("messages-intro")
        avatar = Avatar(self.current_name, hero=True)
        avatar.add_css_class("messages-intro-avatar")
        intro.append(avatar)
        name = Gtk.Label(label=self.current_name, wrap=True, justify=Gtk.Justification.CENTER)
        name.add_css_class("messages-intro-name")
        intro.append(name)
        first = self.current_name.split()[0] if self.current_name.split() else self.current_address
        line = Gtk.Label(label=f"You haven’t messaged {first} yet. Whatever you write here starts the conversation.",
                         wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, justify=Gtk.Justification.CENTER)
        line.add_css_class("messages-intro-description")
        intro.append(line)
        clamp = Adw.Clamp(maximum_size=320, tightening_threshold=320, vexpand=True)
        clamp.set_child(intro)
        return clamp

    # Scroll anchoring. The reader is either following the newest message (at
    # the end) or reading: then the message at the top of the view is the
    # anchor: it is held exactly where it starts, and whatever happens to the
    # rows around it (pictures arriving,
    # receipts, reactions, upserts, older history, rows appearing or going at
    # the end, a resize) the view moves by exactly as much as that message
    # moved, in the same layout pass, so it never visibly moves. A person's own
    # scrolling picks the anchor again.

    def _block_top(self, key: str | None) -> float | None:
        block = self._blocks.get(key) if key is not None else None
        if block is None or block.anchor.get_parent() is not self.message_box:
            return None
        found, bounds = block.anchor.compute_bounds(self.message_box)
        return bounds.get_y() if found else None

    def _top_block(self, value: float) -> tuple[str | None, float]:
        """The message at the top of the view and how far above the view's edge it starts."""
        order = self._block_order
        low, high = 0, len(order)
        while low < high:
            middle = (low + high) // 2
            block = self._blocks[order[middle]]
            found, bounds = block.widgets[-1].compute_bounds(self.message_box)
            if found and bounds.get_y() + bounds.get_height() <= value:
                low = middle + 1
            else:
                high = middle
        for key in order[low:]:
            top = self._block_top(key)
            if top is not None:
                return key, top - value
        return None, 0.0

    def _capture_anchor(self, *_args) -> None:
        """A person moved the view: remember what they are looking at.

        The view sets the position itself only inside its own layout, and
        _set_scroll marks the rest, so every change seen here is the reader's.
        """
        if self._anchoring or self.closed:
            return
        adjustment = self.message_scroll.get_vadjustment()
        value = adjustment.get_value()
        self._stick_end = adjustment.get_upper() - adjustment.get_page_size() - value <= SCROLL_END_SLACK
        self._anchor_uid, self._anchor_offset = self._top_block(value)

    def _keep_anchor_through(self, contexts: dict) -> None:
        """Before a redraw: if the anchored message is about to change or go, hold a steady one near it.

        A message that is redrawn can change on either side of where its row
        starts: a reaction's room opens above it, a picture below. Held
        instead by the nearest message that is not changing, everything the
        redraw does to the anchored message is absorbed like any other change
        around the reader, and what they see stays put.
        """
        anchor = self._anchor_uid
        if anchor is None or anchor not in self._blocks:
            return
        steady = lambda key: key in contexts and self._blocks[key].context == contexts[key]
        if steady(anchor):
            return
        order = self._block_order
        index = order.index(anchor)
        value = self.message_scroll.get_vadjustment().get_value()
        nearby = order[index + 1:] + order[:index][::-1]
        # A message that stays exactly as it is, else (the anchored one is
        # going) any that stays at all.
        for usable in (steady, lambda key: anchor not in contexts and key in contexts):
            for key in nearby:
                top = self._block_top(key) if usable(key) else None
                if top is not None:
                    self._anchor_uid, self._anchor_offset = key, top - value
                    return
        if anchor not in contexts:
            self._anchor_uid = None

    def _set_scroll(self, value: float) -> None:
        """Move the view without taking it for a person's own scrolling."""
        self._anchoring = True
        try:
            self.message_scroll.get_vadjustment().set_value(value)
        finally:
            self._anchoring = False

    def _follow_end(self) -> None:
        """Show the newest message now and through whatever layout follows."""
        adjustment = self.message_scroll.get_vadjustment()
        self._stick_end = True
        self._set_scroll(max(adjustment.get_lower(), adjustment.get_upper() - adjustment.get_page_size()))
        self.message_scroll.get_child().queue_allocate()

    def _place_view(self, content: float, page: float, shown: float) -> float:
        """Where the view goes, asked by the conversation view once every row has its final place.

        At the end, the end, whatever grew. Otherwise wherever puts the
        message being read exactly where it was on screen: a picture that
        arrives, a line that appears or a rewrap on a resize above it moves
        the view by just as much, in the same layout, before any frame is
        drawn. Changes below the reader move nothing.
        """
        end = max(0.0, content - page)
        if self._stick_end:
            target = end
        else:
            top = self._block_top(self._anchor_uid)
            target = shown if top is None else top - self._anchor_offset
        clamped = min(max(target, 0.0), end)
        if _ANCHOR_DEBUG:
            print(f"anchor: content={content:.0f} page={page:.0f} shown={shown:.0f} stick={self._stick_end} "
                  f"uid={self._anchor_uid} target={target:.0f} -> {clamped:.0f}", file=sys.stderr, flush=True)
        if self._stick_end or self._anchor_uid not in self._blocks or abs(clamped - target) >= 0.5:
            # The anchor could not be honoured (it went, or the view ran out of
            # room): hold whatever is now at the top instead.
            self._anchor_uid, self._anchor_offset = self._top_block(round(clamped))
        return clamped

    def _focus_moved(self, *_args) -> None:
        """Keyboard focus on a message scrolls just enough to show it; a click never scrolls."""
        focus = self.get_focus()
        if focus is None or not self.get_focus_visible():
            return
        node = focus
        while node is not None and node is not self.message_box:
            node = node.get_parent()
        if node is None:
            return
        found, bounds = focus.compute_bounds(self.message_box)
        if not found:
            return
        adjustment = self.message_scroll.get_vadjustment()
        value, page = adjustment.get_value(), adjustment.get_page_size()
        top, bottom = bounds.get_y(), bounds.get_y() + bounds.get_height()
        if top < value or bottom - top > page:
            adjustment.set_value(max(adjustment.get_lower(), top - 12))
        elif bottom > value + page:
            adjustment.set_value(min(adjustment.get_upper() - page, bottom - page + 12))

    @staticmethod
    def _quote_widget(quote: QuoteRecord) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.add_css_class("messages-quote")
        author = Gtk.Label(label=quote.author, xalign=0)
        author.add_css_class("messages-quote-author")
        text = Gtk.Label(label=quote.body, xalign=0)
        text.set_max_width_chars(30)
        text.set_ellipsize(Pango.EllipsizeMode.END)
        text.add_css_class("messages-quote-text")
        box.append(author); box.append(text)
        _accessible(box, f"Replying to {quote.author}: {quote.body}")
        return box

    def _photo_view(self, path: Path, width: int, height: int, *, overlay: Gtk.Widget | None = None,
                    waiting: bool = False) -> Gtk.Widget:
        """A picture at its own shape, laid out at its final size before any pixels exist."""
        shown_width, shown_height = photos.display_size(width, height)
        frame = Gtk.Overlay(halign=Gtk.Align.START)
        frame.add_css_class("messages-photo")
        if waiting:
            frame.add_css_class("waiting")
        frame.set_overflow(Gtk.Overflow.HIDDEN)
        picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.COVER)
        picture.set_size_request(shown_width, shown_height)
        frame.set_child(picture)
        if overlay is not None:
            overlay.set_halign(Gtk.Align.CENTER)
            overlay.set_valign(Gtk.Align.CENTER)
            frame.add_overlay(overlay)
        try:
            info = path.stat()
        except OSError:
            return frame
        key = (str(path), info.st_mtime_ns, info.st_size, shown_width, shown_height)
        if texture := self.picture_cache.get(key):
            picture.set_paintable(texture)
        self._photo_slots.append((picture, key, path, width, height))
        return frame

    def _schedule_photo_sync(self, *_args) -> None:
        if not self._photo_sync_source and not self.closed:
            self._photo_sync_source = GLib.timeout_add(90, self._sync_photos)

    def _sync_photos(self) -> bool:
        """Give pictures near the view their pixels, and take them back from pictures far from it."""
        self._photo_sync_source = 0
        if self.closed:
            return GLib.SOURCE_REMOVE
        adjustment = self.message_scroll.get_vadjustment()
        page = max(adjustment.get_page_size(), 1)
        top = adjustment.get_value() - PHOTO_KEEP_PAGES * page
        bottom = adjustment.get_value() + page + PHOTO_KEEP_PAGES * page
        scale = max(1, self.get_scale_factor())
        pending = getattr(self, "_photo_pending", set())
        self._photo_pending = pending
        for picture, key, path, width, height in self._photo_slots:
            found, bounds = picture.compute_bounds(self.message_box)
            if not found:
                continue
            if bounds.get_y() + bounds.get_height() < top or bounds.get_y() > bottom:
                if picture.get_paintable() is not None:
                    picture.set_paintable(None)
                continue
            if picture.get_paintable() is not None:
                continue
            if texture := self.picture_cache.get(key):
                picture.set_paintable(texture)
                continue
            if key in pending:
                continue
            pending.add(key)
            # Decoded large enough to cover the frame at this display's scale, and no larger.
            cover = max(key[3] / width, key[4] / height) * scale
            future = self.picture_worker.submit(photos.decode, path, round(width * cover), round(height * cover))
            future.add_done_callback(lambda done, key=key: GLib.idle_add(self._photo_decoded, key, done))
        return GLib.SOURCE_REMOVE

    def _photo_decoded(self, key: tuple, future) -> bool:
        getattr(self, "_photo_pending", set()).discard(key)
        if self.closed or future.cancelled() or future.exception() is not None:
            return GLib.SOURCE_REMOVE
        pixels = future.result()
        if pixels is None:
            return GLib.SOURCE_REMOVE
        self.picture_cache.put(key, photos.texture(pixels))
        self._schedule_photo_sync()
        return GLib.SOURCE_REMOVE

    def _attachment_widget(self, attachment: AttachmentRecord, preview: Path | None = None) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        path = self.store.attachment_path(attachment)
        valid = path.is_file()
        size = _file_size(attachment.size)
        if valid and attachment.content_type.startswith("image/") and "svg" not in attachment.content_type:
            source, note = path, ""
            width, height = (photos.photo_dimensions(path, attachment.content_type)
                             if attachment.content_type in _previewable_image_types() else (0, 0))
            if not width:
                kind = attachment.content_type.split("/", 1)[1].upper()
                note = f"This computer can't show {kind} photos. Open it to view it in another app."
                if preview is not None and preview.is_file():
                    # The network's thumbnail stands in for a photo this system can't decode.
                    source = preview
                    width, height = photos.photo_dimensions(preview)
            if width:
                open_button = Gtk.Button(child=self._photo_view(source, width, height), halign=Gtk.Align.START)
                open_button.add_css_class("messages-photo-open")
                open_button.set_tooltip_text(f"{attachment.name} · {size}")
                # A picture opens in the window's lightbox; other apps only through its Open With….
                open_button.connect("clicked", lambda *_: self._open_lightbox(("attachment", attachment.uid)))
                self._photo_sources[("attachment", attachment.uid)] = open_button
                _accessible(open_button, f"Photo, {size}. View {attachment.name}")
                card.add_css_class("messages-photo-card")
                card.append(open_button)
                if note:
                    caption = Gtk.Label(label=note, xalign=0, wrap=True)
                    caption.add_css_class("messages-attachment-note")
                    card.append(caption)
                return card
        # Any other file, or a picture that is missing or can't be shown at all.
        row = Gtk.Box(spacing=10)
        icon = "image-x-generic-symbolic" if attachment.content_type.startswith("image/") else "text-x-generic-symbolic"
        symbol = Gtk.Image.new_from_icon_name(icon)
        symbol.set_pixel_size(20)
        symbol.add_css_class("messages-file-icon")
        row.append(symbol)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True, valign=Gtk.Align.CENTER)
        name = Gtk.Label(label=attachment.name if valid else f"{attachment.name} · Missing file", xalign=0)
        name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        name.add_css_class("messages-file-name")
        detail = Gtk.Label(label=size, xalign=0)
        detail.add_css_class("messages-attachment-size")
        text.append(name); text.append(detail); row.append(text)
        open_button = Gtk.Button(child=row)
        open_button.add_css_class("messages-attachment-open")
        open_button.set_sensitive(valid)
        open_button.connect("clicked", lambda *_: self._open_attachment(attachment))
        _accessible(open_button, f"Open {attachment.name}, {size}" if valid else f"{attachment.name} is missing")
        card.append(open_button)
        if valid and attachment.content_type.startswith("image/") and "svg" not in attachment.content_type:
            kind = attachment.content_type.split("/", 1)[1].upper()
            caption = Gtk.Label(label=f"This computer can't show {kind} photos. Open it to view it in another app.",
                                xalign=0, wrap=True)
            caption.add_css_class("messages-attachment-note")
            card.append(caption)
        return card

    def _media_widget(self, message: MessageRecord, part) -> Gtk.Widget:
        """An attachment on its way: its preview while it downloads, or what went wrong and Try Again."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("messages-media-pending")
        card.set_name(f"media-{part.state}")
        text, can_retry = _media_status(part)
        retry = can_retry and part.state != "downloading" and self.service.provider is not None
        busy = part.state in {"pending", "downloading"} and not (part.state == "pending" and part.error not in {"", "waiting_for_phone"})
        if busy:
            symbol: Gtk.Widget = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)
            symbol.set_size_request(22, 22)
            badge = Gtk.Box(); badge.add_css_class("messages-photo-badge"); badge.append(symbol)
            _accessible(badge, text)
        else:
            badge = Gtk.Box(); badge.add_css_class("messages-photo-badge")
            icon = Gtk.Image.new_from_icon_name("view-refresh-symbolic" if retry else "dialog-warning-symbolic")
            icon.set_pixel_size(18)
            badge.append(icon)
        width, height = (photos.photo_dimensions(part.preview) if part.preview is not None and part.preview.is_file()
                         else (0, 0))
        if width:
            face = self._photo_view(part.preview, width, height, overlay=badge, waiting=True)
        else:
            face = Gtk.Overlay(halign=Gtk.Align.START)
            face.add_css_class("messages-photo"); face.add_css_class("placeholder")
            tile = Gtk.Box()
            tile.set_size_request(*photos.display_size(4, 3))
            face.set_child(tile)
            badge.set_halign(Gtk.Align.CENTER); badge.set_valign(Gtk.Align.CENTER)
            face.add_overlay(badge)
        button = Gtk.Button(child=face, halign=Gtk.Align.START)
        self._photo_sources[("part", message.uid, part.part)] = button
        button.add_css_class("messages-photo-open")
        button.set_sensitive(retry)
        button.connect("clicked", lambda *_: self._retry_media(message, part))
        _accessible(button, f"{text} Try again" if retry else text)
        card.append(button)
        detail = text if not part.size else f"{text} · {_file_size(part.size)}"
        status = Gtk.Label(label=detail, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        status.add_css_class("messages-attachment-metadata")
        card.append(status)
        if retry and (part.state == "failed" or part.error):
            # A picture that has failed, or one that is waiting on something,
            # always offers Try Again: it must never be only a spinner. A part
            # that has just arrived and not hit anything yet doesn't, so the
            # button doesn't flash on a download that is simply running.
            again = Gtk.Button(label="Try Again", halign=Gtk.Align.START)
            again.add_css_class("messages-media-retry")
            again.connect("clicked", lambda *_: self._retry_media(message, part))
            card.append(again)
        return card

    def _lightbox_items(self) -> list[tuple[tuple, tuple, LightboxItem]]:
        """Every picture in the open conversation, in order, as the lightbox steps through them."""
        if not self.current_address:
            return []
        sender_of = getattr(self.continuity, "sender", None) or getattr(self.store, "sender", None) or (lambda _uid: "")
        media_of = getattr(self.continuity, "media_parts", None)
        found: list[tuple[tuple, tuple, LightboxItem]] = []
        for message in self._thread_messages(self.current_address):
            ordinal = 0
            who = "You" if message.direction == "outgoing" else (sender_of(message.uid) or self.current_name)
            moment = datetime.fromtimestamp(message.timestamp)
            when = f"{_day_label(message.timestamp)} {moment:%-I:%M %p}"
            for attachment in message.attachments:
                if not attachment.content_type.startswith("image/") or "svg" in attachment.content_type:
                    continue
                path = self.store.attachment_path(attachment)
                key = ("attachment", attachment.uid)
                ordinal += 1
                found.append((key, (message.uid, ordinal), LightboxItem(path if path.is_file() else None, attachment.content_type,
                                                name=attachment.name, title=who, subtitle=when,
                                                state="ready" if path.is_file() else "unavailable",
                                                message="" if path.is_file() else "This picture's file is missing.",
                                                source=self._photo_sources.get(key))))
            for part in (media_of(message.uid) if media_of else ()):
                if part.state == "done" or not (part.mime.startswith("image/") or part.part == "mms"):
                    continue
                text, can_retry = _media_status(part)
                key = ("part", message.uid, part.part)
                ordinal += 1
                busy = part.state == "downloading" or (part.state == "pending" and part.error in {"", "waiting_for_phone"})
                retry = (lambda m=message, p=part: self._retry_media(m, p)) if can_retry and not busy else None
                found.append((key, (message.uid, ordinal), LightboxItem(None, part.mime, name=part.name, title=who, subtitle=when,
                                                state="loading" if busy else "unavailable", message=text,
                                                source=self._photo_sources.get(key), retry=retry)))
        return found

    def _open_lightbox(self, key: tuple) -> None:
        entries = self._lightbox_items()
        index = next((i for i, (item_key, _identity, _item) in enumerate(entries) if item_key == key), None)
        if index is None:
            return
        self._lightbox_keys = [identity for _key, identity, _item in entries]
        Lightbox.for_window(self).open([item for _key, _identity, item in entries], index)

    def _sync_lightbox(self) -> None:
        """A picture shown in the lightbox that finishes downloading, or fails, is updated in place."""
        lightbox = getattr(self, "_luma_lightbox", None)
        if lightbox is None or not lightbox.is_open or not self._lightbox_keys:
            return
        current = {identity: item for _key, identity, item in self._lightbox_items()}
        for index, identity in enumerate(self._lightbox_keys):
            fresh, shown = current.get(identity), lightbox.items[index] if index < len(lightbox.items) else None
            if fresh is not None and shown is not None and (fresh.state, fresh.path, fresh.message) != (
                    shown.state, shown.path, shown.message):
                lightbox.update_item(index, fresh)

    def _retry_media(self, message: MessageRecord, part) -> None:
        provider = self.service.provider
        retry = getattr(provider, "retry_media", None)
        if callable(retry):
            retry(message.uid, part.part)

    def _open_attachment(self, attachment: AttachmentRecord) -> None:
        """Open a file in the application for its type: a photo opens in Viewer."""
        path = self.store.attachment_path(attachment)
        try:
            # Stored files are named by their digest, so choose by the recorded type, not the name.
            application = Gio.AppInfo.get_default_for_type(attachment.content_type, False)
            if application is not None:
                application.launch([Gio.File.new_for_path(str(path))], None)
            else:
                Gio.AppInfo.launch_default_for_uri(path.as_uri(), None)
        except (GLib.Error, OSError, ValueError) as error:
            self._notice(str(error))

    def _render_draft_content(self) -> None:
        self._clear(self.draft_content)
        if not self.current_address:
            self.draft_content.set_visible(False)
            return
        quote = self.store.draft_reply(self.current_address)
        if quote:
            row = Gtk.Box(spacing=6)
            preview = self._quote_widget(quote); preview.set_hexpand(True)
            row.append(preview)
            cancel = Gtk.Button.new_from_icon_name("window-close-symbolic")
            cancel.add_css_class("messages-square-button")
            _accessible(cancel, "Cancel reply")
            cancel.connect("clicked", lambda *_: self._cancel_reply())
            row.append(cancel); self.draft_content.append(row)
        attachments = self.store.draft_attachments(self.current_address)
        for attachment in attachments:
            row = Gtk.Box(spacing=6)
            label = Gtk.Label(label=attachment.name, xalign=0, hexpand=True)
            label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            row.append(label)
            remove = Gtk.Button.new_from_icon_name("window-close-symbolic")
            remove.add_css_class("messages-square-button")
            _accessible(remove, f"Remove {attachment.name}")
            remove.connect("clicked", lambda _button, uid=attachment.uid: self._remove_attachment(uid))
            row.append(remove); self.draft_content.append(row)
        self.draft_content.set_visible(bool(quote or attachments))
        self._sync_composer_chrome()

    def _reply_to(self, message: MessageRecord) -> None:
        if self.current_address:
            self.store.set_draft_reply(self.current_address, message.uid)
            self._render_draft_content()
            self.composer_view.grab_focus()

    def _cancel_reply(self) -> None:
        if self.current_address:
            self.store.set_draft_reply(self.current_address, None)
            self._render_draft_content()

    def _remove_attachment(self, uid: str) -> None:
        if self.current_address:
            self.store.remove_draft_attachment(self.current_address, uid)
            self._render_draft_content()

    def _scroll_to_bottom(self) -> bool:
        self._follow_end()
        return GLib.SOURCE_REMOVE

    def _install_message_gesture(self, bubble: Gtk.Widget, message: MessageRecord) -> None:
        # Captured before the bubble's text sees them: the text is selectable,
        # and a selectable label takes the right button and a long press for
        # its own Copy menu, so these actions never opened.
        # Handlers reach the bubble through the gesture or the action's widget,
        # never a closure over it: the bubble owns these controllers, and a
        # closure over their owner is a cycle Python cannot collect, so every
        # re-render kept every bubble, its pictures included.
        def open_actions(gesture, *_args):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._show_message_actions(gesture.get_widget(), message)
        gesture = Gtk.GestureLongPress(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        gesture.connect("pressed", open_actions); bubble.add_controller(gesture)
        # The same actions under the right button, because a pointer has one
        # and holding it down for half a second is not how a desktop behaves.
        secondary = Gtk.GestureClick(button=3, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        secondary.connect("pressed", open_actions)
        bubble.add_controller(secondary)
        menu = Gtk.ShortcutController()
        menu.set_scope(Gtk.ShortcutScope.LOCAL)
        menu.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("Menu"),
                Gtk.CallbackAction.new(
                    lambda widget, *_: (self._show_message_actions(widget, message), True)[1]
                ),
            )
        )
        bubble.add_controller(menu)

    def _show_message_actions(self, anchor: Gtk.Widget, message: MessageRecord) -> None:
        popover = Gtk.Popover(autohide=True, has_arrow=False); popover.add_css_class("messages-context-popover"); actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        cloud = is_cloud(message)
        if message.send_status == "uncertain":
            review = Gtk.Button(label="Review send")
            review.add_css_class("messages-context-action")
            review.connect("clicked", lambda *_: (popover.popdown(), self._review_send(message.uid)))
            actions.append(review)
        service = self.service
        offered = tuple(getattr(service.provider, "reaction_emoji", ()) or ()) if service.provider else ()
        if offered and not cloud and message.state not in {"queued", "sending", "failed"}:
            # The network's own reactions, as its app shows them: one per person,
            # chosen again to take it back.
            mine = next((r.emoji for r in self.store.reactions(message.uid) if r.sender == "self"), "")
            bar = Gtk.Box(spacing=2, halign=Gtk.Align.CENTER); bar.add_css_class("messages-reaction-bar")
            for emoji in offered:
                choice = Gtk.Button(label=emoji); choice.add_css_class("messages-reaction-choice")
                if emoji == mine:
                    choice.add_css_class("selected")
                _accessible(choice, f"Remove {emoji} reaction" if emoji == mine else f"React with {emoji}")
                choice.connect("clicked", lambda _b, e=emoji: (popover.popdown(), self._react(service, message, None if e == mine else e)))
                bar.append(choice)
            actions.append(bar)
        reply = Gtk.Button(label="Reply")
        reply.add_css_class("messages-context-action")
        reply.connect("clicked", lambda *_: (popover.popdown(), self._reply_to(message)))
        actions.append(reply)
        if service.native:
            # A modem has no reactions, so a heart goes as an ordinary text.
            react = Gtk.Button(label="Send heart reaction as text…")
            react.add_css_class("messages-context-action")
            react.set_sensitive(self.capability.available and bool(message.body))
            react.connect("clicked", lambda *_: (popover.popdown(), self._confirm_text_reaction(message)))
            actions.append(react)
        copy = Gtk.Button(label="Copy text"); copy.add_css_class("messages-context-action"); copy.connect("clicked", lambda *_: self._copy_message(message.body, popover)); actions.append(copy)
        details = Gtk.Button(label=f"Details · {_moment(message.timestamp)}"); details.add_css_class("messages-context-action"); details.set_sensitive(False); actions.append(details)
        if cloud:
            # Cloud rows belong to the device that sent them; this window only shows them.
            source = Gtk.Button(label=CLOUD_LABEL); source.add_css_class("messages-context-action"); source.set_sensitive(False); actions.append(source)
        delete = Gtk.Button(label="Delete"); delete.set_visible(not cloud); delete.add_css_class("messages-context-action"); delete.add_css_class("destructive"); delete.connect("clicked", lambda *_: self._confirm_delete(message.uid, popover)); actions.append(delete)
        popover.set_child(actions); popover.set_parent(anchor); _release_when_closed(popover); popover.popup()

    def _review_send(self, uid: str) -> None:
        service = self.service
        can_retry = service.provider is not None and callable(getattr(service.provider, "retry_message", None))
        dialog = Adw.AlertDialog(heading="Review send",
            body="Retry only if recipient did not receive this message." if can_retry else
                 "This send was not confirmed. Retry is not available on this device yet.")
        dialog.add_response("cancel", "Cancel")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        if can_retry:
            dialog.add_response("retry", "Retry")

        def answered(_dialog, response):
            if response != "retry" or not can_retry or self.closed:
                return
            def retry():
                error = ""
                state = "queued"
                try:
                    if getattr(service.provider, "requires_user_token", False):
                        # Confirming Retry is the person's new request for this message.
                        writer = service.writer()
                        try:
                            retry_token = writer.authorize_send(uid, retry=True)
                        finally:
                            writer.close()
                        result = service.provider.retry_message(uid, retry_token)
                    else:
                        result = service.provider.retry_message(uid)
                    state = result.get("state", "queued")
                except Exception:
                    error = "Could not retry this message. Review its status before trying again."
                GLib.idle_add(self._finish_transmit, service, uid, state, error)
            self.content_worker.submit(retry)
        dialog.connect("response", answered)
        dialog.present(self)

    def _react(self, service: MessageService, message: MessageRecord, emoji: str | None) -> None:
        def send():
            try:
                state = service.provider.react(message.uid, emoji).get("state")
            except Exception:
                state = "failed"
            GLib.idle_add(self._mms_imported, "" if state == "sent" else "The reaction didn’t go through. Try again.")
        self.content_worker.submit(send)

    def _copy_message(self, body: str, popover: Gtk.Popover) -> None: self.get_clipboard().set(body); popover.popdown(); self._notice("Message copied")

    def _confirm_text_reaction(self, message: MessageRecord) -> None:
        # A carrier without RCS receives this as an ordinary SMS. The person
        # explicitly reviews that fallback before it is sent.
        text = f'♥ “{message.body}”'
        dialog = Adw.AlertDialog(heading="Send a reaction as text?", body=text)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("send", "Send text")

        service = self.service

        def send_text_reaction(_dialog, response):
            if response != "send" or not service.capability.available:
                return
            record = service.store.add(message.address, text, direction="outgoing")
            service.store.set_reaction(message.uid, "self", "♥", state="queued")
            service.store.update_state(record.uid, "sending")
            user_token = (service.store.authorize_send(record.uid)
                          if getattr(service.provider, 'requires_user_token', False) else None)
            self._render_messages(); self._reload_threads()

            def deliver():
                self._transmit(service, record.uid, record.address, record.body, user_token)
                writer = service.writer()
                try:
                    state = writer.message(record.uid).state
                    writer.set_reaction(message.uid, "self", "♥", state="sent" if state == "sent" else "failed")
                except (KeyError, ValueError):
                    pass
                finally:
                    writer.close()
                GLib.idle_add(self._mms_imported, "")
            threading.Thread(target=deliver, daemon=True).start()
        dialog.connect("response", send_text_reaction)
        dialog.present(self)

    def update_peer_presence(self, address: str, *, active: bool, valid_for: int, source: str) -> None:
        """Accept an expiring presence event from an authenticated rich transport.

        SMS and MMS never call this: neither protocol proves online presence.
        Presence is deliberately not restored from disk after an app restart.
        """
        address = normalize_address(address)
        if not source or not 0 < valid_for <= 300:
            raise ValueError("Presence needs a source and a bounded lifetime.")
        if active:
            self.peer_presence[address] = (time.monotonic() + valid_for, source)
        else:
            self.peer_presence.pop(address, None)
        self._sync_peer_presence()

    def _sync_peer_presence(self) -> bool:
        if self.presence_expiry:
            GLib.source_remove(self.presence_expiry)
            self.presence_expiry = 0
        presence = self.peer_presence.get(self.current_address or "")
        remaining = presence[0] - time.monotonic() if presence else 0
        if self.current_address and not self._thread_messages(self.current_address):
            self.thread_subtitle.set_label("New conversation · Luma · End-to-end encrypted" if luma.is_luma(self.service) else
                                           f"New conversation · {self.service.label}" if len(self.services) > 1 else "New conversation")
        elif remaining > 0:
            self.thread_subtitle.set_label("Active now")
            self.presence_expiry = GLib.timeout_add(max(1, int(remaining * 1000) + 1), self._presence_expired)
        else:
            if luma.is_luma(self.service):
                subtitle = "Luma · End-to-end encrypted"
            elif not self.service.phone_numbers:
                # A network account's conversation: the service is what there is to say.
                subtitle = self.service.label
            else:
                subtitle = "Text message" if self.current_name == self.current_address else self.current_address or ""
                if len(self.services) > 1 and subtitle:
                    subtitle = f"{subtitle} · {self.service.label}"
            self.thread_subtitle.set_label(subtitle)
        return GLib.SOURCE_REMOVE

    def _presence_expired(self) -> bool:
        self.presence_expiry = 0
        return self._sync_peer_presence()

    def _confirm_delete(self, uid: str, popover: Gtk.Popover) -> None:
        popover.popdown(); dialog = Adw.AlertDialog(heading="Delete this message?", body="This removes the message from this Luma device. It does not unsend it.")
        dialog.add_response("cancel", "Cancel"); dialog.add_response("delete", "Delete"); dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda _dialog, response: self._delete_message(uid) if response == "delete" else None); dialog.present(self)

    def _delete_message(self, uid: str) -> None:
        message = self.store.message(uid)
        if self.store.delete_message(uid):
            self._delete_native_copies((message,), self.service)
            self._render_messages(); self._reload_threads()

    def _delete_native_copies(self, messages, service: MessageService) -> None:
        paths = [message.transport_id.removeprefix("mmsd:") for message in messages
                 if message.transport_id and message.transport_id.startswith("mmsd:")]
        if not paths:
            return

        def remove():
            failed = False
            for path in paths:
                try:
                    service.mms_transport.delete(path)
                except Exception:
                    failed = True
            if failed:
                GLib.idle_add(self._mms_imported, "The conversation is removed here, but its native MMS copy could not be deleted yet.")
        self.content_worker.submit(remove)

    def _composer_text(self) -> str:
        start, end = self.composer_buffer.get_bounds(); return self.composer_buffer.get_text(start, end, True)
    def _set_composer_text(self, text: str) -> None: self.composer_buffer.set_text(text)

    def _composer_changed(self, _buffer: Gtk.TextBuffer) -> None:
        text = self._composer_text()
        if text:
            self.composer_preedit = ""
        self._sync_composer_chrome()
        if self.current_address and not self._loading_draft: self.store.set_draft(self.current_address, text); self._reload_threads()

    def _composer_preedit_changed(self, _view: Gtk.TextView, preedit: str) -> None:
        # GTK owns the active input-method context, whether the handset uses
        # Stevia/input-method-v2, IBus, or a physical keyboard. Preedit is not
        # part of TextBuffer until it commits, so use this native signal for
        # placeholder and send-action state instead of coupling Messages to a
        # particular keyboard service.
        self.composer_preedit = preedit or ""
        self._sync_composer_chrome()

    def _sync_composer_chrome(self) -> None:
        text = self._composer_text()
        attachments = self.store.draft_attachments(self.current_address) if self.current_address else ()
        has_content = bool(text.strip()) or bool(self.composer_preedit) or bool(attachments)
        ready = self.capability.available
        reason = self.capability.reason
        if attachments:
            ready = self.mms_capability.available
            reason = self.mms_capability.reason
            caption = text.strip()
            if quote := self.store.draft_reply(self.current_address):
                caption = f"> {quote.author}: {quote.body}\n\n{caption}"
            if ready and (sum(item.size for item in attachments) + len(caption.encode("utf-8")) > self.mms_capability.max_bytes
                          or len(attachments) + bool(caption) > self.mms_capability.max_attachments):
                ready = False
                reason = "The draft exceeds the carrier's MMS attachment limits."
        self._set_transport_status(reason, ready)
        sendable = bool(
            ready and self.current_address and has_content
        )
        self.composer_placeholder.set_visible(not (text or self.composer_preedit))
        # A handset offers to record a voice message when there is nothing to
        # send. A desktop has no recorder, so its action is send at all times —
        # insensitive until there is something to send, never a different
        # button in the same place.
        self.composer_action.set_visible_child_name(
            "send" if has_content or not getattr(self, "_compact", False) else "voice"
        )
        self.send_button.set_sensitive(sendable)

    def _composer_focus_changed(self, *_args) -> None:
        if not self.composer_view.has_focus(): self._composer_return_down.clear()
        if not self.composer_view.has_focus() and self.composer_preedit:
            # Input methods normally commit or clear preedit on focus-out. A
            # deferred sync prevents stale send chrome if one disappears in
            # the same frame as the focus transition.
            self.composer_preedit = ""
            GLib.idle_add(self._sync_composer_after_focus)

    def _sync_composer_after_focus(self) -> bool:
        if not self.closed:
            self._sync_composer_chrome()
        return GLib.SOURCE_REMOVE

    def _set_transport_status(self, reason: str, ready: bool) -> None:
        # Hiding the label also removes its CSS padding and layout allocation.
        # Offline/locked/review states remain visible even if a queue can accept.
        actionable = not ready or (self.continuity is not None and self.continuity.status.get("state") != "ready")
        if not ready and self.service.native and not self._native_draft_waiting():
            # This device's own modem. A desktop has none, so every conversation
            # would carry the notice while there is nothing to send; it only
            # matters once the person has written something to send by text.
            actionable = False
        locked = self.continuity is not None and self.continuity.status.get("state") == "locked"
        if locked:
            reason = "Unlock to reconnect to your phone."
            if self._unlock_requested: reason = "Complete the system unlock prompt to reconnect."
            elif self._unlock_error: reason = "Could not open the unlock prompt. Try again."
        else:
            self._unlock_requested = False
            self._unlock_error = False
        waiting = self.continuity is not None and self.continuity.status.get("state") in {"offline", "unavailable"}
        self.unlock_phone_button.set_label("Unlock" if locked else "Retry")
        _accessible(self.unlock_phone_button, "Unlock phone connection" if locked else "Try to reach your phone again")
        self.unlock_phone_button.set_visible(locked or waiting)
        self.lookup_action("unlock-phone").set_enabled((locked and not self._unlock_requested) or waiting)
        self.transport_status.set_label(reason)
        self.transport_status.set_visible(bool(reason.strip()) and actionable)

    def _native_draft_waiting(self) -> bool:
        """True when the open conversation has something to send by this device's text service."""
        address = getattr(self, "current_address", None)
        if not address or getattr(self, "composer_buffer", None) is None:
            return False
        if self._composer_text().strip() or getattr(self, "composer_preedit", ""):
            return True
        return bool(self.store.draft_attachments(address))

    def _retry_phone(self) -> None:
        # The provider re-observes Connect and the phone; an already open keyring
        # or a phone that just woke is picked up without another prompt.
        retry = getattr(self.continuity, "retry", None) or getattr(self.continuity, "invalidate", None)
        if retry is not None and not self.closed: retry()

    def _unlock_phone(self, *_args) -> None:
        if self.closed or self.continuity is None: return
        if self.continuity.status.get("state") in {"offline", "unavailable"}:
            self._retry_phone(); return
        if self._unlock_requested or self.continuity.status.get("state") != "locked": return
        self._unlock_requested = True
        self._unlock_error = False
        self._set_transport_status(self.capability.reason, self.capability.available)
        def done(connection, result):
            try: connection.call_finish(result)
            except GLib.Error:
                if not self.closed:
                    self._unlock_error = True
            if not self.closed:
                self._unlock_requested = False
                self._set_transport_status(self.capability.reason, self.capability.available)
                self._retry_phone()
            # Broker acknowledgement is not proof of unlock. StateChanged owns
            # recovery; the broker coalesces requests while a prompt is active.
            # Re-enable on acknowledgement so cancellation can be retried.
        try:
            connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            connection.call("org.projectluma.Connect1", "/org/projectluma/Connect",
                "org.projectluma.Connect1", "UnlockAccount", None, None,
                Gio.DBusCallFlags.NONE, 1500, None, done)
        except GLib.Error:
            self._unlock_requested = False
            self._unlock_error = True
            self._set_transport_status(self.capability.reason, self.capability.available)

    def _composer_key_pressed(self, _controller, keyval, keycode, state) -> bool:
        if (Gdk.keyval_name(keyval) or "") not in {"Return", "KP_Enter"}: return False
        if state & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.META_MASK): return False
        key = keycode or keyval
        if key in self._composer_return_down: return True
        self._composer_return_down.add(key)
        # Capture sees preedit before TextView commits it. Let GTK confirm IME
        # composition, but consume any repeats until this Return is released.
        if self.composer_preedit: return False
        self._send_message()
        return True

    def _composer_key_released(self, _controller, keyval, keycode, _state) -> None:
        self._composer_return_down.discard(keycode or keyval)

    def _send_message(self, *_args) -> None:
        text = self._composer_text().strip()
        if not text and self.composer_preedit:
            # Moving focus commits the active input method's visible preedit
            # into TextBuffer. Defer sending until that commit reaches GTK.
            self.send_button.grab_focus()
            self.composer_preedit = ""
            GLib.idle_add(self._send_after_preedit)
            return
        if not self.current_address or not self.send_button.get_sensitive(): return
        service = self.service
        attachments = self.store.draft_attachments(self.current_address)
        quote = self.store.draft_reply(self.current_address)
        record = self.store.add(self.current_address, text, direction="outgoing",
                                attachment_uids=tuple(item.uid for item in attachments),
                                reply_to=quote.message_uid if quote else None)
        self.store.update_state(record.uid, "sending")
        # A network account delivers only what a person asked for, once: this press of Send is that request.
        user_token = (self.store.authorize_send(record.uid)
                      if getattr(service.provider, "requires_user_token", False) and hasattr(self.store, "authorize_send") else None)
        self.store.delete_draft(self.current_address)
        self.store.set_draft_reply(self.current_address, None)
        self._loading_draft = True; self._set_composer_text(""); self._loading_draft = False; self._render_messages(to_end=True); self._reload_threads(); self.send_button.set_sensitive(False)
        self._render_draft_content()
        if self._fixture_mode:
            self.store.update_state(record.uid, "sent")
            self._render_messages(to_end=True)
            self._reload_threads()
            return
        threading.Thread(target=self._transmit, args=(service, record.uid, record.address, record.wire_body or record.body, user_token),
                         daemon=True).start()

    def _send_after_preedit(self) -> bool:
        if not self.closed and self._composer_text().strip():
            self._send_message()
        return GLib.SOURCE_REMOVE

    def _transmit(self, service: MessageService, uid: str, address: str, body: str, user_token: str | None = None) -> None:
        # The service is fixed when the send starts; opening another conversation meanwhile changes nothing here.
        state = "sent"; error = ""; transport_id = None
        writer = service.writer()
        try:
            record = writer.message(uid)
            if service.provider:
                if getattr(service.provider, "requires_user_token", False):
                    result = service.provider.send_message(uid, user_token)
                else:
                    result = service.provider.send_message(uid)
                state = result["state"]
            elif record.attachments:
                with tempfile.TemporaryDirectory(prefix=".mms-send-", dir=writer.path.parent) as temporary:
                    parts = [(part.uid, part.content_type, str(writer.attachment_path(part))) for part in record.attachments]
                    if body:
                        caption = Path(temporary) / "message.txt"
                        caption.write_text(body, encoding="utf-8")
                        caption.chmod(0o600)
                        parts.insert(0, ("message.txt", "text/plain", str(caption)))
                    native_path = service.mms_transport.send((address,), parts)
                transport_id = "mmsd:" + native_path
                state = "queued"
            else:
                service.transport.send(address, body)
                transport_id = f"mm-outgoing:v1:{uid}"
        except Exception as failure:
            state = "failed"; error = str(failure)
        try:
            # Provider owns queue/receipt state; a fast receipt must not be overwritten.
            if service.provider is None or error:
                writer.update_state(uid, state, transport_id)
        except Exception as failure:
            state = "failed"
            error = error or str(failure)
        finally:
            writer.close()
        GLib.idle_add(self._finish_transmit, service, uid, state, error, address)

    def _finish_transmit(self, service: MessageService, _uid: str, _state: str, error: str,
                         original_address: str | None = None) -> bool:
        if self.closed: return GLib.SOURCE_REMOVE
        try:
            record = service.store.message(_uid)
            # Creating a network conversation replaces the temporary username
            # with its durable account id. Follow this send's stored identity
            # only while the person is still viewing its original conversation.
            if (original_address and service is self.service and
                    self.current_address == original_address and record.address != original_address):
                thread = service.store.resolve_sent_conversation(_uid, original_address)
                if thread is not None:
                    self._resolve_sent_conversation(thread, service=service)
            if record.transport_id and record.transport_id.startswith("mmsd:"):
                path = record.transport_id.removeprefix("mmsd:")
                if properties := service.mms_transport.cached_message(path):
                    self._mms_message(service, path, properties)
        except KeyError:
            pass
        self._render_messages(); self._reload_threads()
        if error: self._notice("Message was not delivered. Touch and hold it for details.")
        return GLib.SOURCE_REMOVE

    def _resolve_sent_conversation(self, thread: ThreadRecord, *, service: MessageService) -> None:
        """Resolve this selection's identity without replacing its pending composer."""
        self.current_address = thread.address
        self.current_name = thread.display_name
        self.thread_title.set_label(thread.display_name)
        self.remember("conversation", service.key(thread.address))
        self._sync_luma_thread()
        self._update_agent_viewing()
        if hasattr(service.provider, "conversation_opened"):
            service.provider.conversation_opened(thread.address)
        self._render_draft_content()

    def _file_dropped(self, _target, value, _x, _y) -> bool:
        if isinstance(value, Gio.File) and value.get_path():
            self._stage_attachment(Path(value.get_path()))
            return True
        return False

    def _attachment_requested(self, *_args) -> None:
        if not self.current_address:
            return
        picker = Gtk.FileDialog(title="Add attachment")

        def selected(dialog, result):
            try:
                file = dialog.open_finish(result)
                if file and file.get_path():
                    self._stage_attachment(Path(file.get_path()))
            except GLib.Error as error:
                if not error.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                    self._notice("The attachment could not be opened.")
        picker.open(self, None, selected)

    def _stage_attachment(self, path: Path) -> None:
        if not self.current_address:
            return
        address = self.current_address
        service = self.service

        def copied():
            writer = service.writer()
            error = ""
            try:
                writer.attach_file(address, path)
            except (OSError, ValueError) as failure:
                error = str(failure)
            finally:
                writer.close()
            GLib.idle_add(self._attachment_staged, service, address, error)
        self.content_worker.submit(copied)

    def _attachment_staged(self, service: MessageService, address: str, error: str) -> bool:
        if not self.closed:
            if error:
                self._notice(error)
            if service is self.service and address == self.current_address:
                self._render_draft_content()
        return GLib.SOURCE_REMOVE

    def _mms_capability_changed(self, service: MessageService, capability: MmsCapability) -> None:
        if not self.closed:
            service.mms_capability = capability
            if service is self.service:
                self._sync_composer_chrome()

    def _mms_message(self, service: MessageService, path: str, properties: dict) -> None:
        if self.closed:
            return
        self.content_worker.submit(self._import_mms, service, path, properties)

    def _import_mms(self, service: MessageService, path: str, properties: dict) -> None:
        writer = service.writer()
        try:
            from .messages_native_mailbox import import_mms
            import_mms(writer, path, properties)
            GLib.idle_add(self._mms_imported, '')
        except Exception as error:
            GLib.idle_add(self._mms_imported, str(error))
        finally:
            writer.close()

    def _mms_imported(self, error: str) -> bool:
        if not self.closed:
            if error:
                self._notice(error)
            self._received_changed()
        return GLib.SOURCE_REMOVE
    def _voice_requested(self, *_args) -> None: self._notice("Voice messages require native MMS support and are not available yet.")

    def _call_current(self, *_args) -> None:
        if not self.current_address or not self.call_button.get_sensitive(): return
        self.call_button.set_sensitive(False)
        self._call_number(self._callable_number(self.service, self.current_address))
        GLib.timeout_add(1_500, self._restore_call_button)

    def _restore_call_button(self) -> bool:
        if not self.closed and self.current_address:
            self.call_button.set_sensitive(bool(self._callable_number(self.service, self.current_address)))
        return GLib.SOURCE_REMOVE

    def _back_to_inbox(self, *_args) -> None:
        self._show_nothing_selected()
        self.split.set_show_content(False)

    def open_address(self, value: str) -> None:
        """Open a conversation from a link, a notification or the command line.

        A notification names its service. A bare number opens the most recent
        conversation with it on any service, or starts one on the default service.
        """
        service, raw = self._conversation_from_key(value)
        raw = raw.removeprefix("sms:").split("?", 1)[0]
        candidates = [service] if service is not None else self.services
        best: tuple[MessageService, ThreadRecord] | None = None
        for candidate in candidates:
            try: address = candidate.store.canonical_address(raw)
            except ValueError: continue
            thread = next((item for item in candidate.store.threads() if item.address == address), None)
            if thread is not None and (best is None or thread.updated > best[1].updated):
                best = (candidate, thread)
        if best is None:
            chosen = service or self._default_send_service()
            try: address = chosen.store.canonical_address(raw)
            except ValueError as error: self._notice(str(error)); return
            best = (chosen, ThreadRecord(address, address, "", 0, 0))
        self._open_thread(best[1], reveal=True, service=best[0]); self.composer_view.grab_focus()

    def _refresh(self, *_args) -> None:
        for service in self.services:
            service.capability = service.transport.inspect()
        self._set_transport_status(self.capability.reason, self.capability.available)
        self._poll_received(); self._reload_threads()

    def _poll_received(self) -> bool:
        if self.closed: return GLib.SOURCE_REMOVE
        for service in self.services:
            if service.provider is not None:
                service.provider.refresh()
        if self.polling: return GLib.SOURCE_CONTINUE
        self.polling = True; threading.Thread(target=self._collect_received, daemon=True).start(); return GLib.SOURCE_CONTINUE

    def _collect_received(self) -> None:
        # Only this device's own modem is polled; phones sync through their providers.
        try: records = self.native_service.transport.snapshot()
        except (ValueError, OSError, GLib.Error): GLib.idle_add(self._poll_complete, ()); return
        GLib.idle_add(self._poll_complete, records)

    def _poll_complete(self, records: tuple[TransportMessage, ...]) -> bool:
        self.polling = False
        if records and not self.closed:
            self.native_service.store.reconcile_outgoing(records)
            incoming = tuple(
                (
                    message.address, message.body,
                    incoming_transport_id(
                        message.address, message.body, message.timestamp
                    ),
                    message.timestamp,
                )
                for message in records
                if message.direction == "incoming"
                and message.state in {"received", "receiving"}
            )
            self._ingest_received(incoming)
        else: self._refresh_external_store()
        return GLib.SOURCE_REMOVE

    def _ingest_received(self, records) -> bool:
        arrived: list[tuple[str, str]] = []
        for address, body, transport_id, timestamp in records:
            if self.native_service.store.ingest(address, body, transport_id=transport_id, timestamp=timestamp) is not None:
                arrived.append((address, body))
        for address, body in arrived:
            self._notify_arrival(address, body)
        self._refresh_external_store(); return GLib.SOURCE_REMOVE

    def _notify_arrival(self, address: str, body: str) -> None:
        """Tell the desktop a message arrived, and open it when it is clicked.

        Nothing is announced for the conversation already on screen in a
        window the person is looking at: they can see it arrive.
        """
        native = self.native_service
        canonical = native.store.canonical_address(address)
        if self.is_active() and native is self.service and canonical == self.current_address:
            return
        application = self.get_application()
        if application is None:
            return
        thread = next(
            (item for item in native.store.threads() if item.address == canonical), None
        )
        notification = Gio.Notification.new(
            thread.display_name if thread is not None else canonical
        )
        notification.set_body(body)
        notification.set_priority(Gio.NotificationPriority.NORMAL)
        # Clicking it opens that conversation rather than merely raising the
        # window onto whatever happened to be showing.
        notification.set_default_action_and_target(
            "app.open-conversation", GLib.Variant.new_string(canonical)
        )
        application.send_notification(f"message:{canonical}", notification)

    def _notify_service_arrival(self, service: MessageService, address: str, name: str, body: str) -> None:
        """A message from a network account: announced like a text, unless it is already on screen."""
        if self.closed:
            return
        if self.is_active() and service is self.service and address == self.current_address:
            # The person is looking at it: the sender sees it was read.
            if hasattr(service.provider, "conversation_seen"):
                service.provider.conversation_seen(address)
            return
        application = self.get_application()
        if application is None:
            return
        key = service.key(address)
        notification = Gio.Notification.new(name)
        notification.set_body(body)
        notification.set_priority(Gio.NotificationPriority.NORMAL)
        notification.set_default_action_and_target("app.open-conversation", GLib.Variant.new_string(key))
        application.send_notification(f"message:{key}", notification)

    def show_conversation(self, address: str) -> None:
        """Bring one conversation forward, from a notification or a link."""
        self.open_address(address)
        self.present()

    def _host_store_changed(self):
        if self.closed: return
        self._store_changed()
        # MMS readiness may change without a database write (for example when
        # the carrier appears). Refresh that cached host capability off GTK.
        def refresh():
            try: capability = self.native_service.mms_transport.inspect()
            except Exception: capability = MmsCapability()
            GLib.idle_add(lambda: (None if self.closed else self._mms_capability_changed(self.native_service, capability), False)[1])
        if not getattr(self, '_host_capability_pending', False):
            self._host_capability_pending = True
            def completed():
                try: refresh()
                finally: self._host_capability_pending = False
            self.content_worker.submit(completed)

    def _store_changed(self, *_args) -> None:
        if self.closed or self.store_refresh_source: return
        self.store_refresh_source = GLib.timeout_add(150, self._refresh_external_store)

    def _refresh_external_store(self) -> bool:
        self.store_refresh_source = 0
        if self.closed: return GLib.SOURCE_REMOVE
        changed = False
        for service in self.services:
            revision = service.store.external_revision()
            if revision != service.incoming_revision:
                service.incoming_revision = revision; changed = True
        if changed: self._received_changed()
        return GLib.SOURCE_REMOVE

    def _received_changed(self) -> bool:
        self._reload_threads()
        if self.current_address:
            thread = next((item for item in self.store.threads() if item.address == self.current_address), None)
            if thread: self.current_name = thread.display_name; self.thread_title.set_label(thread.display_name)
            self._render_messages()
        return GLib.SOURCE_REMOVE

    def _notice(self, message: str) -> None: self.toast_overlay.add_toast(Adw.Toast(title=message, timeout=4))

    def notice(self, message: str) -> None:
        self._notice(message)

    # ── Luma Messages (ADR-051) ─────────────────────────────────────────────
    def _luma_service(self) -> MessageService | None:
        return next((service for service in self.services if luma.is_luma(service)), None)

    @staticmethod
    def _luma_flags(service: MessageService, record) -> dict:
        return luma.conversation_flags(service, record.address) if luma.is_luma(service) else {}

    def _build_luma_sidebar(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # Until there is a username, nobody can find this account: say so once, calmly.
        self.luma_banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.luma_banner.add_css_class("messages-luma-banner")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        title = Gtk.Label(label="Choose your Luma username", xalign=0); title.add_css_class("messages-luma-banner-title")
        detail = Gtk.Label(label="So people on Luma can message you.", xalign=0, wrap=True); detail.add_css_class("messages-luma-banner-detail")
        text.append(title); text.append(detail); self.luma_banner.append(text)
        choose = Gtk.Button(label="Choose", valign=Gtk.Align.CENTER); choose.add_css_class("pill"); choose.add_css_class("suggested-action")
        choose.connect("clicked", lambda *_: self._show_luma_profile())
        self.luma_banner.append(choose)
        self.luma_banner.set_visible(False)
        box.append(self.luma_banner)
        self.luma_requests_button = Gtk.Button()
        self.luma_requests_button.add_css_class("messages-luma-requests")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.luma_requests_label = Gtk.Label(label="Message Requests", xalign=0, hexpand=True)
        self.luma_requests_count = Gtk.Label(label=""); self.luma_requests_count.add_css_class("messages-luma-count")
        row.append(self.luma_requests_label); row.append(self.luma_requests_count)
        row.append(Gtk.Image(icon_name="go-next-symbolic"))
        self.luma_requests_button.set_child(row)
        self.luma_requests_button.connect("clicked", lambda *_: self._show_luma_requests(True))
        self.luma_requests_button.set_visible(False)
        box.append(self.luma_requests_button)
        self.luma_requests_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.luma_requests_header.add_css_class("messages-luma-requests-header")
        back = _icon_button("back", "Back to all conversations", "messages-square-button")
        back.connect("clicked", lambda *_: self._show_luma_requests(False))
        self.luma_requests_header.append(back)
        heading = Gtk.Label(label="Message Requests", xalign=0, hexpand=True); heading.add_css_class("messages-thread-title")
        self.luma_requests_header.append(heading)
        self.luma_requests_header.set_visible(False)
        box.append(self.luma_requests_header)
        self._luma_identity: dict | None = None
        self._luma_identity_asked = False
        return box

    def _sync_luma_sidebar(self, requests: int) -> None:
        if not hasattr(self, "luma_requests_button"):
            return
        showing = self.filter_mode == "requests"
        self.luma_requests_button.set_visible(requests > 0 and not showing)
        self.luma_requests_count.set_label(str(requests))
        self.luma_requests_header.set_visible(showing)
        if self.foot.filter_button is not None:
            self.foot.filter_button.set_sensitive(not showing)
        _accessible(self.luma_requests_button, f"Message requests, {requests}")
        service = self._luma_service()
        ready = service is not None and service.provider.status.get("state") == "ready"
        if ready and not self._luma_identity_asked:
            self._luma_identity_asked = True
            self.luma_call(service, "luma.identity", {}, self._luma_identity_loaded)
        self.luma_banner.set_visible(bool(ready and self._luma_identity is not None and not self._luma_identity.get("handle")))

    def _luma_identity_loaded(self, result, error) -> None:
        if error is not None:
            self._luma_identity_asked = False  # asked again when the account next reports in
            return
        self._luma_identity = result or {}
        self._reload_threads()

    def luma_profile_changed(self, service) -> None:
        self._luma_identity_asked = False
        self._luma_identity = None
        self._reload_threads()

    def luma_conversation_changed(self, service, conversation: str) -> None:
        if service is self.service and conversation == self.current_address:
            self._sync_luma_thread()
        self._reload_threads()

    def _show_luma_requests(self, show: bool) -> None:
        self.filter_mode = "requests" if show else self.foot.filter or "all"
        self._reload_threads()

    def luma_call(self, service, command: str, args: dict, done) -> None:
        """Ask the Luma helper off the main thread; ``done(result, error)`` runs on it."""
        provider = service.provider

        def work() -> None:
            try:
                result, error = provider.luma_call(command, args), None
            except Exception as problem:  # BridgeError, or the agent not answering
                result = None
                error = problem if hasattr(problem, "code") else type("Failure", (), {"code": "error", "message": "Luma didn't answer."})()
                if not getattr(error, "message", ""):
                    error.message = str(problem) or "Luma didn't answer."
            GLib.idle_add(lambda: (None if self.closed else done(result, error), GLib.SOURCE_REMOVE)[1])
        threading.Thread(target=work, daemon=True, name="messages-luma-call").start()

    def _show_luma_profile(self, *_args, start_with: str = "") -> None:
        service = self._luma_service()
        if service is None:
            # Connect may have enrolled after this window opened. Use the
            # existing account/provider path at explicit user intent, without
            # copying relay credentials or starting a second helper.
            try:
                from .messages_app_client import sandboxed
                if sandboxed():
                    self.agent.call('Reload')
                    account = next((a for a in self.accounts.list() if a.network == 'luma'), None)
                else:
                    account = luma.ensure_account(self.accounts, helper_dir=self.helper_directory)
                if account is not None:
                    self.account_added(account)
                    service = self._luma_service()
            except (OSError, ValueError, KeyError):
                self._notice("Luma Messages couldn't load this computer's account. Try again or reopen Messages.")
                return
        if service is None:
            from .messages_app_client import sandboxed
            problem = (json.loads(self.agent.call('ApplicationInfo')[0])['luma_problem'] if sandboxed()
                       else luma.setup_problem(helper_dir=self.helper_directory))
            self._notice(problem)
            return
        from .messages_luma_ui import LumaProfileDialog
        self.luma_dialog = LumaProfileDialog(self, service, start_with=start_with)
        self.luma_dialog.present(self)

    def open_luma_link(self, text: str) -> None:
        handle = luma.handle_from(text)
        if handle is None:
            self._notice("That link isn't a Luma profile.")
            return
        service = self._luma_service()
        if service is None:
            self._notice("Luma Messages needs Luma Connect on this device. Sign in there first.")
            return
        self.start_luma_conversation(service, handle, lambda error: error and self._notice(error.message))

    def start_luma_conversation(self, service, handle: str, done) -> None:
        """Validate the selected account before opening a writable conversation.

        The helper owns self conversations and their other enrolled devices.
        The UI checks actual account/device availability before opening it.
        """
        self._luma_conversation_generation = getattr(self, "_luma_conversation_generation", 0) + 1
        generation = self._luma_conversation_generation

        def active():
            return not self.closed and generation == self._luma_conversation_generation

        def identity_loaded(identity, error):
            if not active():
                return
            if error is not None:
                done(error); return
            own = str((identity or {}).get("account") or "")
            if not own:
                done(type("Refusal", (), {"code": "identity_unavailable", "message": "Luma Messages couldn't verify your account. Try again after it reconnects."})())
                return

            def found(person, error) -> None:
                if not active():
                    return
                if error is not None:
                    done(error); return
                account = str((person or {}).get("account") or "")
                name = luma.public_person_name(person, handle)
                if not account:
                    done(type("Refusal", (), {"code": "handle_unknown", "message": f"No one on Luma has the username @{handle}."})())
                    return
                if (person or {}).get("blocked"):
                    done(type("Refusal", (), {"code": "blocked_by_you", "message": f"You blocked {name}. Unblock them to send a message."})())
                    return

                def devices(listing, error) -> None:
                    if not active():
                        return
                    if error is not None:
                        done(error); return
                    if not (listing or {}).get("devices"):
                        done(type("Refusal", (), {"code": "not_on_luma", "message": f"{name} hasn't set up Luma Messages yet."})())
                        return
                    address = f"u:{account}"
                    record = next((item for item in service.store.threads() if item.address == address), None)
                    if record is None:
                        address = f"@{handle}"
                        service.store.set_display_name(address, name)
                        record = next((item for item in service.store.threads() if item.address == address), None) or \
                            ThreadRecord(address, name, "", 0, 0)
                    self.sidebar_stack.set_visible_child_name("inbox")
                    self._open_thread(record, reveal=True, service=service)
                    self.composer_view.grab_focus()
                    done(None)
                self.luma_call(service, "luma.devices", {"account": account}, devices)
            self.luma_call(service, "luma.people", {"handle": handle}, found)
        self.luma_call(service, "luma.identity", {}, identity_loaded)

    def _sync_luma_thread(self) -> None:
        on = luma.is_luma(self.service) and bool(self.current_address)
        flags = luma.conversation_flags(self.service, self.current_address) if on else {}
        group = on and self.service.store.conversation_kind(self.current_address) == "group"
        self.luma_button.set_visible(on and not flags.get("removed"))
        request = bool(flags.get("request"))
        if request:
            self.luma_request_bar.show_for(self.current_name, str(flags.get("handle") or ""), group)
        self.luma_request_bar.set_visible(request)
        self.composer_region.set_visible(not request and self.detail_stack.get_visible_child_name() == "conversation")
        self.luma_notice.set_visible(on and not group and self.luma_notice.show_for(self.current_name, flags))

    def _fill_luma_menu(self, button: Gtk.MenuButton) -> None:
        service, address, name = self.service, self.current_address, self.current_name
        flags = luma.conversation_flags(service, address) if address else {}
        group = service.store.conversation_kind(address) == "group" if address else False
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("messages-luma-menu")
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        head.add_css_class("messages-luma-menu-head")
        from .messages_luma_ui import encrypted_badge
        head.append(encrypted_badge("Verified · End-to-end encrypted" if flags.get("verified") else "End-to-end encrypted"))
        explain = Gtk.Label(label="Messages and photos here are encrypted on your devices. Luma can't read them.",
                            wrap=True, xalign=0, max_width_chars=34)
        explain.add_css_class("messages-luma-menu-detail")
        head.append(explain)
        box.append(head)
        popover = Gtk.Popover(child=box)

        def item(label: str, action, destructive: bool = False) -> None:
            entry = Gtk.Button(label=label)
            entry.add_css_class("flat"); entry.add_css_class("messages-luma-menu-item")
            if destructive: entry.add_css_class("destructive")
            entry.get_child().set_xalign(0)
            entry.connect("clicked", lambda *_: (popover.popdown(), action()))
            box.append(entry)
        own = str((self._luma_identity or {}).get('account') or '')
        if not group and flags.get("account") and flags.get('account') != own and not flags.get("request"):
            item("Safety Number", self._show_luma_safety)
        if flags.get("handle"):
            item(f"Copy {flags['handle']}", lambda: Gdk.Display.get_default().get_clipboard().set(str(flags["handle"])))
        person = str(flags.get("account") or flags.get("request_from") or "")
        if person and person != own:
            item("Report…", self._report_luma)
            item(f"Block {name}" if not group else "Block the person who added you", self._block_luma, destructive=True)
        button.set_popover(popover)

    def _answer_luma_request(self, answer: str) -> None:
        service, address = self.service, self.current_address
        if answer == "block":
            self._block_luma(); return

        def done(_result, error) -> None:
            if error is not None:
                self._notice(error.message); return
            if answer == "declined":
                self._show_nothing_selected()
            else:
                self._show_luma_requests(False)
                self._sync_luma_thread()
            self._reload_threads()
        self.luma_call(service, "luma.request.answer", {"conversation": address, "state": answer}, done)

    def _show_luma_safety(self) -> None:
        from .messages_luma_ui import SafetyNumberDialog
        self.luma_safety_dialog = SafetyNumberDialog(self, self.service, self.current_address, self.current_name)
        self.luma_safety_dialog.present(self)

    def _block_luma(self) -> None:
        service, address, name = self.service, self.current_address, self.current_name
        flags = luma.conversation_flags(service, address)
        account = str(flags.get("request_from") or flags.get("account") or "")
        dialog = Adw.AlertDialog(heading=f"Block {name}?",
                                 body="They won't be able to message you or add you to groups. They aren't told.")
        dialog.add_response("cancel", "Cancel"); dialog.add_response("block", "Block")
        dialog.set_response_appearance("block", Adw.ResponseAppearance.DESTRUCTIVE)

        def answered(_dialog, response) -> None:
            if response != "block":
                return

            def done(_result, error) -> None:
                if error is not None:
                    self._notice(error.message); return
                self._notice(f"{name} is blocked.")
                self._show_nothing_selected()
            self.luma_call(service, "luma.block", {"account": account, "conversation": address, "on": True}, done)
        dialog.connect("response", answered)
        dialog.present(self)

    def _report_luma(self) -> None:
        service, address, name = self.service, self.current_address, self.current_name
        flags = luma.conversation_flags(service, address)
        account = str(flags.get("request_from") or flags.get("account") or "")
        recent = [(message.transport_id.split(":", 1)[1], message.body) for message in self._thread_messages(address)
                  if message.direction == "incoming" and message.body and message.transport_id
                  and message.transport_id.startswith("luma:")][-10:]
        from .messages_luma_ui import ReportDialog
        self.luma_report_dialog = ReportDialog(self, service, address, account, name, recent)
        self.luma_report_dialog.present(self)

    def _key_pressed(self, _controller, keyval, _keycode, state) -> bool:
        name = Gdk.keyval_name(keyval) or ""
        control = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if control and name in {"f", "F"}:
            # The search field is always there on a desktop; this puts the
            # caret in it without reaching for the mouse.
            self.search.grab_focus()
            return True
        if control and name in {"n", "N"}:
            self._show_new_message()
            return True
        if name in {"Up", "Down"} and not self.composer_view.has_focus():
            return self._step_conversation(-1 if name == "Up" else 1)
        if name != "Escape": return False
        if self.sidebar_stack.get_visible_child_name() == "new": self._show_inbox(); return True
        if self.split.get_show_content(): self._back_to_inbox(); return True
        return False

    def _step_conversation(self, delta: int) -> bool:
        """Move between conversations from the keyboard."""
        rows = []
        child = self.thread_list.get_first_child()
        while child is not None:
            if isinstance(child, ConversationRow):
                rows.append(child)
            child = child.get_next_sibling()
        if not rows:
            return False
        current = next(
            (index for index, row in enumerate(rows)
             if row.service is self.service and row.record.address == self.current_address),
            -1,
        )
        index = max(0, min(len(rows) - 1, current + delta)) if current >= 0 else 0
        row = rows[index]
        self.thread_list.select_row(row)
        self._open_thread(row.record, reveal=False, service=row.service)
        return True

    def _watch_wake(self) -> None:
        """Reconnect network accounts after sleep or when the network comes back.

        A connection held open across sleep is often dead without saying so;
        waiting for it left messages stuck at Sending until Try Again.
        """
        def wake(*_args) -> None:
            if self.closed:
                return
            for service in self.services:
                if hasattr(service.provider, "wake"):
                    service.provider.wake()
        self._network_available = Gio.NetworkMonitor.get_default().get_network_available()

        def network_changed(_monitor, available) -> None:
            if available and not self._network_available:
                wake()
            self._network_available = available
        Gio.NetworkMonitor.get_default().connect("network-changed", network_changed)
        try:
            system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            system.signal_subscribe("org.freedesktop.login1", "org.freedesktop.login1.Manager", "PrepareForSleep",
                                    "/org/freedesktop/login1", None, Gio.DBusSignalFlags.NONE,
                                    lambda _c, _s, _p, _i, _n, parameters: (
                                        None if parameters.unpack()[0] else GLib.timeout_add_seconds(3, lambda: (wake(), False)[1])))
        except GLib.Error:
            pass  # no system bus in tests or containers

    def quit(self) -> None:
        """Quit for real: accounts disconnect and nothing is announced until Messages opens again."""
        if self.agent is not None:
            from .background_agent import SERVICE_BUS, SERVICE_PATH, SERVICE_INTERFACE
            try:
                self.agent.connection.call_sync(SERVICE_BUS, SERVICE_PATH, SERVICE_INTERFACE, 'StopNow',
                    GLib.Variant('(s)', (APPLICATION_ID,)), None, Gio.DBusCallFlags.NONE, 5000, None)
            except GLib.Error:
                self._notice('Messages could not stop its background service. Try Quit again or review Background activity in Settings.')
                return
        self.quitting = True
        self.close()

    def _close_requested(self, *_args) -> bool:
        # Closing the window keeps Messages connected in the background, so a
        # message that arrives later is announced by the system like a text on
        # a phone. Quit Messages ends it. Before, closing stopped every account
        # and nothing was ever announced unless the window stayed open.
        # With the agent connected, the window really closes: the agent keeps
        # every account connected and announces what arrives.
        if (self.agent is None and not getattr(self, "quitting", False) and not _agent_turned_off()
                and any(service.provider is not None for service in self.services)):
            self.set_visible(False)
            return True
        self.closed = True
        if self.agent is not None:
            self.agent.close()
        for service in self.services:
            if service.provider is not None: service.provider.close()
        if self.presence_expiry:
            GLib.source_remove(self.presence_expiry)
            self.presence_expiry = 0
        for service in self.services:
            service.mms_transport.stop()
        self.content_worker.shutdown(wait=False, cancel_futures=True)
        self.picture_worker.shutdown(wait=False, cancel_futures=True)
        self.picture_cache.clear()
        if self.poll_source: GLib.source_remove(self.poll_source); self.poll_source = 0
        if self.store_refresh_source: GLib.source_remove(self.store_refresh_source); self.store_refresh_source = 0
        self.shell_surface.clear()
        for service in self.services:
            if service.monitor is not None: service.monitor.cancel()
            service.store.close()
        return False


class MessagesApplication(Adw.Application):
    def __init__(self, *, message_provider=None) -> None:
        super().__init__(application_id=APPLICATION_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE); self.pending_address = ""
        self.message_provider = message_provider
    def do_startup(self) -> None:
        from luma_appkit import install_lumaui
        Adw.Application.do_startup(self); install_appkit(); install_lumaui(); install_theme(); install_messages_theme()
        # A notification names the conversation it came from, so clicking it
        # opens that one rather than merely raising the window.
        open_conversation = Gio.SimpleAction.new(
            "open-conversation", GLib.VariantType.new("s")
        )
        open_conversation.connect(
            "activate",
            lambda _action, target: self._show_conversation(target.get_string()),
        )
        self.add_action(open_conversation)

    def _window(self):
        # A closed window is only hidden while Messages runs in the background.
        return self.props.active_window or next(iter(self.get_windows()), None)

    def _show_conversation(self, address: str) -> None:
        window = self._window()
        if window is None:
            self.pending_address = address
            self.activate()
            return
        window.present()
        if getattr(window, '_service_unavailable', False):
            self.pending_address = address
            return
        window.show_conversation(address)
        self.withdraw_notification(f"message:{address}")
    def do_activate(self) -> None:
        from .messages_app_port import LumaUIMessagesWindow
        from .messages_app_client import HostServiceUnavailable
        window = self._window()
        if window is None:
            try:
                window = LumaUIMessagesWindow(self)
            except HostServiceUnavailable as error:
                window = AppWindow(application=self, app_id=APPLICATION_ID, title='Messages',
                    icon_name=APPLICATION_ID, commands=CommandRegistry(()), default_width=560, default_height=380,
                    minimum_width=360, minimum_height=320)
                window._service_unavailable = True
                def retry(*_args):
                    window.destroy()
                    self.activate()
                window.set_body(EmptyState('Messages service unavailable', str(error),
                    'dialog-warning-symbolic', primary=('Try again', retry)))
        window.present()
        if self.pending_address and not getattr(window, '_service_unavailable', False):
            window.open_address(self.pending_address); self.pending_address = ""
    def do_command_line(self, command_line) -> int:
        arguments = command_line.get_arguments()
        if "--background" in arguments[1:]:
            # Older autostart entries: the agent (ADR-033) now does this without a window.
            from .background_agent import ensure_agent
            from .messages_agent import AGENT_NAME, INFO
            ensure_agent(APPLICATION_ID, AGENT_NAME, reason=INFO.purpose)
            return 0
        conversation = next((item.split("=", 1)[1] for item in arguments[1:] if item.startswith("--conversation=")), "")
        if conversation:
            # A notification from the agent names the conversation it announced.
            self._show_conversation(conversation)
            return 0
        link = next((item for item in arguments[1:] if item.startswith(f"{luma.APP_SCHEME}://") or
                     item.startswith("https://simplyluma.com/@")), "")
        if link:
            # A Luma profile link or QR code (ADR-051): open a conversation with them.
            self.activate()
            window = self._window()
            if window is not None and not getattr(window, '_service_unavailable', False):
                window.open_luma_link(link)
            return 0
        self.pending_address = next((item for item in arguments[1:] if item.startswith("sms:")), ""); self.activate(); return 0


def _release_when_closed(popover: Gtk.Popover) -> None:
    """A context popover's buttons close over the popover; once closed, let it go.

    Unparenting and dropping its child after it closes ends that cycle, so a
    popover (and the row or bubble it hangs off) doesn't outlive its use.
    """
    popover.connect("closed", _release_popover)


def _release_popover(popover: Gtk.Popover) -> None:
    # Once GTK has finished closing it and the action that closed it has run.
    # Holding the parent until then means a row that action removed can't be
    # finalized with the popover still attached.
    parent = popover.get_parent()

    def release() -> bool:
        popover.set_child(None)
        if popover.get_parent() is parent and parent is not None:
            popover.unparent()
        return GLib.SOURCE_REMOVE
    GLib.idle_add(release)


def _agent_turned_off() -> bool:
    """Whether the person turned Messages' background activity off (luma-background's decision)."""
    try:
        from .background_agent import agent_allowed
        return agent_allowed(Gio.bus_get_sync(Gio.BusType.SESSION, None), APPLICATION_ID) is False
    except (GLib.Error, ImportError):
        return False


def main() -> int: return MessagesApplication().run(sys.argv)
if __name__ == "__main__": raise SystemExit(main())
