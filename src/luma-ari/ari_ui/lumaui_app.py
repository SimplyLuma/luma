# SPDX-License-Identifier: Apache-2.0
"""Ari v71: kit window/navigation/actions, app-owned assistant content.

v71 (luma-next-71 `arChat`, `arBar`, `arBarPhone`, `arTtl`): the thread reads
like Messages (kit MessageBubble: your words in the accent bubble, Ari's in the
chip bubble, no mark); the composer is one bar row (house or cloud, the field
with Attach inside, Send). Under 560 the title island (☰ | the conversation,
its day) grows into the sidebar, and each bar grows its panel (the model
picker, Activity's filter, Models' Good at) instead of a popover.

Fixture construction precedes every live-service import or connection. Capture
mode has a separate application ID and can only change its own memory.
"""
from __future__ import annotations

import copy
import json
import inspect
import os
import sys
import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk, Pango

from luma_appkit import (
    ActionCenter, AppWindow, BarAction, BarEntry, BarFrame, BarSearch, Card, Command, CommandGroup, CommandRegistry,
    LayerHost, CountBadge, AddRow, MessageBubble, ModeSwitch, Island, PanelChoices, PanelRow,
    SidebarRow, SidebarSection, RowLead, NavigationSidebar, PersonAvatar, ScrollView,
    SidebarFoot, SidebarToggle, TextField, TitleIsland, Toast, ToastHost, ParagraphField, ProgressLine, RowAction,
    add_style_builder, apply_type, icons, install_appkit, install_lumaui, lumaui_tokens, panel_list,
)
from luma_appkit.action_center import make_control, BarWidget, SPACER
from luma_appkit.action_bubble import FloatingMenu, MenuItem
from luma_appkit.rows_menu import RichMenuItem
from luma_appkit.lumaui import hue_class
from .fixture import (
    activity_items, audit_timestamp, chat_groups, model_catalog, model_fit, pending_count,
    recipient_first_name, source_from_environment, trace_state, scripted_reply,
)
from . import lumaui_style
from .live_data import live_capacity, stored_messages, stored_receipt, action_node, clock_time
from .rich_text import blocks as reply_blocks, plain_text, safe_url
from .model_families import FAMILIES, family_for, families

APP_ID = "org.projectluma.Ari"
FIXTURE_ID = APP_ID + ".LumaUIPreview"
SUGGESTIONS = (
    ("folder", "Put my tax documents in one folder", "tax"),
    ("moon", "Quiet until 6, except Priya", "quiet"),
    ("cpu", "What model should I use for code?", "code"),
    ("memory-stick", "Explain local models in two sentences", "local"),
)
VERBS = {"stack": "Found", "file": "Made", "person": "Shared with", "email": "Email to",
         "folder": "Moved into", "app": "Made", "apps": "Added to", "setting": "Turned on", "event": "Added"}
GLYPHS = {"stack": "file-text", "file": "table", "person": "user", "email": "mail", "folder": "folder",
          "app": "app-window", "apps": "layout-grid", "setting": "moon", "event": "calendar"}


def box(*children, vertical=False, spacing=0, **properties):
    widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                     spacing=spacing, **properties)
    for child in children:
        widget.append(child)
    return widget


def named(widget, name):
    widget.set_name("ari-" + name)
    return widget


def text(value, role="body", *, wrap=False, expand=False, muted=False):
    label = apply_type(Gtk.Label(label=str(value), xalign=0, wrap=wrap, hexpand=expand,
                                 wrap_mode=Pango.WrapMode.WORD_CHAR), role, muted=muted)
    if wrap:
        label.set_max_width_chars(60)
    if not wrap:
        label.set_ellipsize(Pango.EllipsizeMode.END)
    return label


def ari_text(value, role, fallback="body", **options):
    # TODO(kit-request ari-06): K generates these approved app-only roles.
    generated = "ari_" + role.replace("-", "_")
    return text(value, "ari-" + role if generated in lumaui_tokens.TYPE_SCALE else fallback, **options)


def ari_surface(css_class, fallback):
    # Build app-owned surfaces only once the sanctioned metrics are available.
    if not hasattr(lumaui_tokens, "ARI"):
        return fallback()
    widget = box(vertical=True)
    widget.add_css_class(css_class)
    return widget


def connection(value, role="caption"):
    label = text(value, role)
    label.add_css_class("ari-connection")
    return label


def _labels(widget):
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            yield child
        yield from _labels(child)
        child = child.get_next_sibling()


def clear(widget):
    while child := widget.get_first_child():
        widget.remove(child)


def gb(number):
    # JS Math.round rounds half upward; fixtures use positive sizes.
    from decimal import Decimal, ROUND_HALF_UP
    precision = "1" if number >= 10 else "0.1"
    value = Decimal(str(number)).quantize(Decimal(precision), rounding=ROUND_HALF_UP)
    return f"{value.normalize():f} GB"


def effort_label(level):
    return {"none": ("Fastest", "Answers right away"), "minimal": ("Fastest", "Answers right away"),
            "low": ("Light", "Quick, for simple things"), "medium": ("Standard", "A balance of speed and care"),
            "high": ("Thorough", "Thinks longer. Slower"),
            "xhigh": ("Hardest problems", "Thinks the longest. Slow, and uses more of your plan")}.get(
                level, (level.replace("_", " ").capitalize(), "Model-supported effort"))


class FixtureFace(GObject.Object, Gdk.Paintable):
    """Read-only crop coordinates from v70's shared sample portrait helper."""
    def __init__(self, path, focus, size=64):
        super().__init__()
        self.size = size
        self.texture = Gdk.Texture.new_from_filename(str(path))
        self.fx, self.fy, self.zoom = focus

    def do_get_intrinsic_width(self):
        return self.size

    def do_get_intrinsic_height(self):
        return self.size

    def do_get_intrinsic_aspect_ratio(self):
        return 1.0

    def do_snapshot(self, snapshot, width, height):
        wide = width * self.zoom
        tall = wide * 9 / 16
        rect = Graphene.Rect()
        rect.init(width / 2 - self.fx * wide, height / 2 - self.fy * tall, wide, tall)
        clip = Graphene.Rect()
        clip.init(0, 0, width, height)
        snapshot.push_clip(clip)
        snapshot.append_texture(self.texture, rect)
        snapshot.pop()


class LiveDaemon:
    """Only the existing Ari1 interface. Connection and reads are asynchronous."""

    def __init__(self, ready, event):
        self.proxy = None
        self.ready, self.event = ready, event
        self.closed = False
        self._event_handler = None
        self._owner_handler = None
        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES,
            None, "org.projectluma.Ari1", "/org/projectluma/Ari1", "org.projectluma.Ari1",
            None, self._connected)

    def _connected(self, _source, result):
        try:
            proxy = Gio.DBusProxy.new_for_bus_finish(result)
            if self.closed:
                return
            self.proxy = proxy
            self._event_handler = proxy.connect("g-signal", self._signal)
            self._owner_handler = proxy.connect("notify::g-name-owner", self._owner_changed)
            self._owner_changed(proxy)
        except GLib.Error as error:
            if not self.closed:
                self.ready(error)

    def _owner_changed(self, proxy, *_):
        if not self.closed:
            self.ready(None if proxy.get_name_owner() else RuntimeError("Ari is not running"))

    def _signal(self, _proxy, _sender, name, params):
        if not self.closed and name == "Event":
            self.event(*params.unpack())

    def close(self):
        self.closed = True
        if self.proxy is not None and self._event_handler is not None:
            self.proxy.disconnect(self._event_handler)
            self._event_handler = None
        if self.proxy is not None and self._owner_handler is not None:
            self.proxy.disconnect(self._owner_handler)
            self._owner_handler = None
        # Release bound window callbacks even if the async connection finishes
        # after closure. Keep the proxy for late Ask's required Stop cleanup.
        self.ready = None
        self.event = None

    def call(self, method, signature=None, arguments=(), callback=None):
        if self.proxy is None:
            if callback:
                callback(RuntimeError("Ari is not running"))
            return
        def finish(proxy, result):
            try:
                value = proxy.call_finish(result).unpack()
            except GLib.Error as error:
                value = error
            if callback:
                callback(value)
        self.proxy.call(method, GLib.Variant(signature, arguments) if signature else None,
                        Gio.DBusCallFlags.NONE, 120000 if "ChatGPT" in method else 30000, None, finish)


class ModelChoice(RichMenuItem):
    """Ari's model mark and destination around the kit's two-line menu row."""

    def __init__(self, identity, label, subtitle, mark, local, selected, callback, adjust=None):
        super().__init__(label, subtitle=subtitle, selected=selected, checked=selected, on_activate=callback,
                         trail=[RowAction("sliders-horizontal", "Adjust effort", adjust)] if adjust else ())
        self.identity, self.mark, self.local = identity, mark, local

    def menu_widget(self, close):
        row = super().menu_widget(close)
        line = row.get_child()
        line.prepend(self.mark())
        line.insert_child_after(icons.image("house" if self.local else "cloud", pixel_size=14),
                                line.get_first_child().get_next_sibling())
        # v71 third pass: a model's description wraps; it is never cut off.
        for label in _labels(row):
            if label.get_label() == self.subtitle:
                label.set_ellipsize(Pango.EllipsizeMode.NONE)
                label.set_wrap(True)
                label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        return named(row, "pick-" + self.identity)


class ModelAdd:
    """Construct a fresh kit AddRow for either the floating menu or phone drawer."""

    def __init__(self, label, icon, name, callback, subtitle=None):
        self.label, self.icon, self.name, self.callback = label, icon, name, callback
        self.subtitle = subtitle

    def menu_widget(self, close):
        def activate():
            close()
            GLib.idle_add(lambda: (self.callback(), False)[1])
        row = named(AddRow(self.label, icon=self.icon, on_activate=activate), self.name)
        if self.subtitle:
            line = row.get_child()
            label = line.get_last_child()
            line.remove(label)
            line.append(box(label, text(self.subtitle, "caption", wrap=True), vertical=True, hexpand=True))
        row.menu_buttons = [row]
        return row


class AriWindow(AppWindow):
    provider_columns = GObject.Property(type=int, default=3, minimum=1, maximum=3)

    def __init__(self, application, source=None):
        self.source = source
        self.fixture = source is not None
        self.data = source.data if self.fixture else {
            "computer": {}, "local_models": [], "providers": [], "provider_choices": [],
            "activity": {}, "conversations": [], "state": {"view": "chat", "cur": "new", "model": "auto",
                "filter": "All", "store": "All", "q": "", "sq": "", "busy": False, "n": 0}}
        self.state = self.data["state"]
        self.daemon = None
        self.models_loading = not self.fixture
        self.models_error = ""
        self.live_cloud_model = None
        self.chatgpt_data = {"accounts": [], "active": "", "models": [], "model": "", "error": ""}
        self.chatgpt_dialog = None
        self.generation = 0
        self.request = ""
        self.downloads = {}
        self.pending_model_installs = set()
        self.pending_download_events = []
        self.pending_request_events = []
        self.pending_approvals = set()
        self.permissions_dialog = None
        self.pending_permissions = False
        self.read_generation = 0
        self.chatgpt_metadata_requested = False
        self.route_generation = 0
        self.closed = False
        self._capture_source = None
        self._capture_clock = None
        self.draft = ""
        self.provider_generation = 0
        self.provider_state = {"step": "pick", "prov": None, "key": "", "url": "http://192.168.1.20:11434",
                               "test": None, "sel": {}}
        self.popover = None
        self.modal = None
        self._rendering = False
        self.providers_view = None
        self.hero_view = None
        self.models_tab = "yours"
        self.models_switch = None
        self.show_large_models = False
        self.model_family = None
        self.family_selection = {}
        self.removing_model = ""
        self.paused_models = set()
        self.task_models = {"code": "qwen30"} if self.fixture else {}
        registry = CommandRegistry([CommandGroup("", (
            Command("ari.new", "New chat", self.new_chat, "square-pen", shortcut=("Ctrl", "N")),
            Command("ari.find", "Search chats", lambda: self.foot.entry.grab_focus(), "search", shortcut=("Ctrl", "F")),
            Command("ari.stop", "Stop", self.stop, "square", shortcut=("Escape",)),
            Command("ari.permissions", "Permissions and autonomy", self.show_permissions, "settings"),
            Command("ari.sidebar", "Show or hide sidebar", lambda: self.toggle.toggle(), "panel-left",
                    shortcut=("F9",)),
        ))])
        super().__init__(application=application, app_id=application.get_application_id(),
                         title="Ari", icon_name=APP_ID, commands=registry,
                         default_width=1180, default_height=740, minimum_width=360, minimum_height=420)
        # A separate preview launcher may be called Ari2; the app still says Ari.
        self.identity.label.set_label("Ari")
        Gtk.IconTheme.get_for_display(self.get_display()).add_search_path(str(Path(__file__).with_name("assets") / "icons"))
        identity_icon = self.identity.get_child().get_first_child().get_first_child()
        if isinstance(identity_icon, Gtk.Image):
            identity_icon.set_from_icon_name("ari-app")
        self.sidebar = named(NavigationSidebar(variant="destinations"), "sidebar")
        self.sidebar.set_size_request(224, -1)
        # Focusing a row when Places opens must not navigate or close it.
        self.sidebar.list.connect("row-activated", self._selected)
        self.foot = named(SidebarFoot(search="Search chats", on_search=self.search_chats,
                                     add=("New chat", "square-pen", self.new_chat)), "foot")
        named(self.foot.entry, "search-chats")
        named(self.foot.add_button, "new")
        self.sidebar.append_footer(self.foot)
        self.page = named(box(vertical=True, spacing=18), "page")
        self.scroll = named(ScrollView(), "scroll")
        self.clamp = Adw.Clamp(maximum_size=740, tightening_threshold=600, child=self.page)
        self.models_header = box(vertical=True)
        self.models_header_clamp = Adw.Clamp(maximum_size=920, tightening_threshold=920, child=self.models_header)
        self.scroll_content = box(self.models_header_clamp, self.clamp, vertical=True)
        self.scroll.set_child(self.scroll_content)
        self.island = named(Island(), "island")
        self.island.append(self.scroll)
        self.host = named(ToastHost(self.island), "host")
        self.host.set_hexpand(True)
        self.columns = box(self.sidebar, self.host)
        self.set_body(self.columns)
        # v71 lNavTtl: on a phone, ☰ and the conversation's name are one island; tapping it
        # grows it into the sidebar. Desktop navigation stays in the app menu and F9.
        self.title_island = named(TitleIsland(lead="menu"), "title-island")
        self.toggle = named(SidebarToggle(self.sidebar, island=self.title_island), "sidebar-toggle")
        self.toggle.set_control_visible(False)
        self.set_leading(self.toggle)
        self.title_island.float_over(self.columns)
        self.bar = named(ActionCenter().attach(self.host), "bar")
        self.page.set_margin_top(30)
        self.page.set_margin_bottom(150)
        self.page.set_margin_start(32)
        self.page.set_margin_end(32)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 900px"))
        narrow.add_setter(self.clamp, "maximum-size", 740)
        narrow.add_setter(self, "provider-columns", 1)
        self.add_breakpoint(narrow)
        # v71 tiers: the phone shape is under 560; crossing it redraws the page and its bar.
        self.phone = False
        self.tier_watch.connect("tier-changed", self._tier_changed)
        # The watch's first measurement can run before the opening size is laid out, and a
        # window opened at its final size sends no later change: measure once it has painted.
        self.connect("map", self._measure_after_paint)
        self.connect("notify::provider-columns", self._provider_columns_changed)
        self.connect("close-request", self._closing)
        self._scroll_clock = None
        self._scroll_handler = None
        self._follow_end = True
        self._chat_rows = {}
        self._chat_key = None
        self._bar_key = None
        # Only a user's navigation stops following the conversation. Allocation,
        # focus and content-size changes must not be mistaken for scrolling up.
        navigation = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        navigation.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        navigation.connect('scroll', self._user_scroll)
        self.scroll.add_controller(navigation)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed', self._scroll_key)
        self.scroll.add_controller(keys)
        drag = Gtk.GestureDrag(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        drag.connect('drag-update', self._scroll_drag)
        self.scroll.add_controller(drag)
        scrollbar = self.scroll.get_vscrollbar()
        scrollbar_click = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        scrollbar_click.connect('pressed', lambda *_: setattr(self, '_follow_end', False))
        scrollbar_click.connect('released', lambda *_: GLib.idle_add(self._resume_at_end))
        scrollbar.add_controller(scrollbar_click)
        self.connect("map", lambda _window: self._scroll_after_layout())
        # The kit's phone safe area (bar height + 20) lands after the first layout; a thread
        # showing its end keeps showing it as the room below grows.
        adjustment = self.scroll.get_vadjustment()
        self._scroll_upper = adjustment.get_upper()
        adjustment.connect("notify::upper", self._scroll_upper_changed)
        adjustment.connect('notify::page-size', lambda *_: self._scroll_after_layout())
        if self.fixture:
            self._capture_state(os.environ.get("LUMA_ARI_STATE", "budget"))
            self.render()
            # Fixture layers need the window's allocation, which map precedes.
            # Run after that frame has painted, without a separate timer delay.
            self.connect("map", self._capture_mapped, os.environ.get("LUMA_ARI_STATE", "budget"))
        else:
            self.render()
            self.page.append(text("Loading…", "caption"))
            self.daemon = LiveDaemon(self._live_ready, self.on_event)

    def _capture_state(self, name):
        if name in ("models-yours", "models-store", "model-page-download", "model-page-installed", "model-remove"):
            self.state["view"] = "models"
            self.models_tab = "store" if name in ("models-store", "model-page-download") else "yours"
            if name.startswith("model-page") or name == "model-remove":
                self.model_family = "Qwen3"
                self.family_selection["Qwen3"] = "qwen14" if name == "model-page-download" else "qwen8"
                if name == "model-remove":
                    self.removing_model = "qwen8"
        elif name == "effort":
            self.state.update(view="chat", cur="new", model="chatgpt:fixture-reasoning")
            self.chatgpt_data = {"models": [{"id": "fixture-reasoning", "name": "Reasoning model",
                "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}, {"effort": "high"}],
                "default_reasoning_level": "medium"}]}
        elif name in {chat["id"] for chat in self.data["conversations"]}:
            self.state.update(view="chat", cur=name)
        elif name == "new":
            self.state.update(view="chat", cur="new")
        elif name == "busy":
            identity = "new-1"
            self.data["conversations"].insert(0, {"id": identity, "t": "Local models", "day": "Today",
                "msgs": [{"u": SUGGESTIONS[-1][1]}, {"think": "Thinking"}]})
            self.state.update(view="chat", cur=identity, busy=True, n=1)
        elif name == "model-downloading":
            self.state.update(view="models", store="All")
            next(model for model in self.data["local_models"] if model["id"] == "qwen14")["dl"] = 35
        elif name == "activity-filter":
            self.state.update(view="activity", filter="All")
        elif name.startswith("activity-"):
            self.state.update(view="activity", filter=name.split("-", 1)[1].title())
        elif name == "models-filter":
            self.state.update(view="models", store="All")
        elif name.startswith("models-") and name.split("-", 1)[1].title() in ("All", "Chat", "Code", "Images", "Reasoning"):
            self.state.update(view="models", store=name.split("-", 1)[1].title())
        elif name == "approval-working":
            self.data["activity"]["budget"]["nodes"][-1]["st"] = "run"
        elif name == "declined":
            self.source.answer("budget", False)
        elif name in ("approved", "undo"):
            self.source.answer("budget", True)
            if name == "undo":
                self.source.undo("budget")
        elif name in ("provider-key", "provider-error", "provider-checking", "provider-connected", "provider-selection"):
            self.state["view"] = "models"
        elif name in ("chats-search", "chats-empty"):
            self.state["sq"] = "budget" if name == "chats-search" else "no such chat"
            self.state["q"] = self.state["sq"]
        elif name in ("models-search", "models-empty"):
            self.state.update(view="models", q="Qwen" if name == "models-search" else "no such model")
            self.state["sq"] = self.state["q"]
        elif name in ("model-local", "model-cloud"):
            self.state["model"] = "qwen30" if name == "model-local" else "sonnet"

    def _capture_mapped(self, _widget, name):
        self._capture_clock = self.get_frame_clock()
        self._capture_source = self._capture_clock.connect_after("after-paint", self._capture_retry, name)
        self._capture_clock.request_phase(Gdk.FrameClockPhase.AFTER_PAINT)

    def _capture_retry(self, clock, name):
        if self._capture_overlay(name):
            clock.request_phase(Gdk.FrameClockPhase.AFTER_PAINT)
        else:
            clock.disconnect(self._capture_source)
            self._capture_source = None
            self._capture_clock = None

    def _capture_overlay(self, name):
        if name not in ("effort", "model-picker", "providers", "provider-key", "provider-error", "provider-checking", "provider-own", "provider-connected", "provider-selection", "approved", "undo", "model-local", "model-cloud", "chats-search", "chats-empty", "places", "activity-filter", "models-filter") or self.closed:
            return GLib.SOURCE_REMOVE
        if not self.get_mapped():
            return GLib.SOURCE_CONTINUE
        size = os.environ.get("LUMAUI_CONFORM_SIZE")
        if size:
            width, height = map(int, size.split("x"))
            if (self.get_width(), self.get_height()) != (width, height):
                return GLib.SOURCE_CONTINUE
        # The layer depends on the shape (a phone grows the bar, a computer pops over):
        # measure the tier now, and if that redraws the page, open it on the next frame.
        phone = self.phone
        self.tier_watch.check()
        if self.phone != phone:
            return GLib.SOURCE_CONTINUE
        if name in ("approved", "undo"):
            self.notify(f'Emailed {recipient_first_name(self.data["activity"]["budget"])}' if name == "approved"
                        else "Undone. The email stays sent")
        elif name in ("model-local", "model-cloud"):
            self.use_model(self.state["model"])
        elif name in ("chats-search", "chats-empty"):
            self.search_chats(self.state["sq"])
        elif name == "effort":
            self.show_effort()
        elif name == "model-picker":
            # A click, not activate(): GtkButton clicks 250 ms after activate.
            self.model_picker() if not self.phone else self.model_button.emit("clicked")
        elif name == "places":
            # v71 phone: the title island grown into the sidebar (a computer shows the sidebar anyway).
            if self.phone:
                self.title_island.grow_into()
        elif name in ("activity-filter", "models-filter"):
            # v71 phone: the filter grown into its chips; a computer shows them in the bar.
            if self.phone:
                self.bar.bar_row.get_last_child().emit("clicked")
        elif name == "providers":
            self.provider_sheet()
        elif name == "provider-own":
            self.provider_sheet("own")
        elif name in ("provider-key", "provider-error", "provider-checking", "provider-connected", "provider-selection"):
            self.provider_sheet("openai")
            if name != "provider-key":
                self.provider_state.update(key="fixture-test-key", test="run" if name == "provider-checking" else "bad" if name == "provider-error" else "ok")
                if name == "provider-selection":
                    self.provider_state["sel"]["gpt5"] = False
                self._provider_render()
        return GLib.SOURCE_REMOVE

    def action_control(self, item, *, size="small", appearance="fill"):
        # ari-07: semantic small/quiet action variants are still a kit dependency.
        if "appearance" in inspect.signature(make_control).parameters:
            return make_control(item, size=size, appearance=appearance)
        return make_control(item)

    def button(self, label, callback, icon=None, *, primary=False, name=None, sensitive=True,
               size="small", appearance="fill", tooltip=None):
        button = self.action_control(BarAction(icon or "", label, callback, tooltip=tooltip,
                                              primary=primary, sensitive=sensitive),
                                     size=size, appearance=appearance)
        if name:
            named(button, name)
        return button

    def icon_button(self, icon, tooltip, callback, name=None, primary=False, *, size="tiny"):
        widget = self.action_control(BarAction(icon, tooltip=tooltip, on_activate=callback, primary=primary),
                                     size=size, appearance="quiet")
        return named(widget, name) if name else widget

    def render(self):
        focus = self.get_focus()
        typing = (focus is not None and hasattr(self, "entry") and self.entry.widget is not None
                  and focus.is_ancestor(self.entry.widget))
        # Release focus before removing the subtree that owns it. GTK may
        # otherwise retain the old focused label during its next allocation.
        # The composer regains focus below; an open provider sheet keeps it.
        if focus is not None and any(focus is region or focus.is_ancestor(region)
                                     for region in (self.sidebar, self.page)):
            self.set_focus(None)
        self._rendering = True
        self.render_sidebar()
        chat_key = (self.state['cur'], self.phone) if self.state['view'] == 'chat' else None
        if chat_key is None or chat_key != self._chat_key:
            clear(self.page)
            self._chat_rows.clear()
            if chat_key is None or self._chat_key is None or chat_key[0] != self._chat_key[0]:
                self._follow_end = True
        self._chat_key = chat_key
        clear(self.models_header)
        if self.models_switch is not None:
            self.host.remove_overlay(self.models_switch)
            self.models_switch = None
        view = self.state["view"]
        self.models_header.set_visible(view == "models" and not self.model_family)
        (self.page.add_css_class if view == "models" else self.page.remove_css_class)("ari-models-page")
        chat = next((chat for chat in self.data["conversations"] if chat["id"] == self.state["cur"]), None)
        welcome = view == "chat" and chat is None
        maximum = 580 if welcome else 740 if view == "chat" else 920 if view == "models" else 860
        self.clamp.set_maximum_size(maximum)
        self.clamp.set_tightening_threshold(maximum)
        # v71 .arithread 30 32 150, .aripad 34 36 120; under 560 (4835) the thread starts under
        # the title island (104) and the pages at 52, on the 16 gutter, and the kit's one safe
        # area gives the room above the bar.
        side = 0 if welcome else 16 if self.phone else 32 if view == "chat" else 40 if view == "models" else 36
        self.page.set_margin_start(side)
        self.page.set_margin_end(side)
        self.page.set_margin_top(0 if welcome else 36 if view == "models" and not self.model_family
                                 else (104 if view == "chat" else 52) if self.phone
                                 else 30 if view == "chat" else 34)
        self.page.set_margin_bottom(0 if self.phone else 150)
        self.page.set_spacing(10 if self.phone and view == "chat" else 18 if view == "chat" else 12)
        # The island names the conversation beside ☰; elsewhere it is ☰ alone.
        self.title_island.set_title(chat["t"] if view == "chat" and chat else "",
                                    chat["day"] if view == "chat" and chat else "")
        if view == "activity":
            self.render_activity()
        elif view == "models":
            self.render_models()
        else:
            self.render_chat()
        self.render_bar()
        self._rendering = False
        if view == "chat":
            if typing:
                self.entry.focus()
            # v70 arMain sets this before the replacement content is laid out.
            self._scroll_after_layout()

    def _tier_changed(self, _watch, tier):
        phone = tier == "phone"
        if phone == self.phone:
            return
        self.phone = phone
        self.close_picker()
        self.render()

    def _scroll_after_layout(self):
        if self.closed or not self._follow_end or not self.get_mapped() or self.state["view"] != "chat":
            return
        if self._scroll_handler is not None:
            return
        self._scroll_clock = self.get_frame_clock()
        self._scroll_handler = self._scroll_clock.connect_after("after-paint", self._scroll_layout_ready)
        self._scroll_clock.request_phase(Gdk.FrameClockPhase.AFTER_PAINT)

    def _measure_after_paint(self, _window):
        clock = self.get_frame_clock()
        handler = None

        def painted(clock):
            clock.disconnect(handler)
            if not self.closed:
                self.tier_watch.check()

        handler = clock.connect_after("after-paint", painted)
        clock.request_phase(Gdk.FrameClockPhase.AFTER_PAINT)

    def _scroll_upper_changed(self, adjustment, _pspec):
        self._scroll_upper = adjustment.get_upper()
        if self._follow_end and not self.closed and self.state["view"] == "chat":
            self._scroll_after_layout()

    def _user_scroll(self, _controller, _dx, dy):
        if dy:
            self._follow_end = False
        if dy > 0:
            GLib.idle_add(self._resume_at_end)
        return False

    def _scroll_drag(self, _gesture, _dx, dy):
        if dy:
            self._follow_end = False
        if dy < 0:
            GLib.idle_add(self._resume_at_end)

    def _scroll_key(self, _controller, key, _code, _modifiers):
        if key in (Gdk.KEY_Page_Up, Gdk.KEY_Home, Gdk.KEY_Up):
            self._follow_end = False
        elif key == Gdk.KEY_End:
            self._follow_end = True
            self._scroll_after_layout()
        elif key in (Gdk.KEY_Page_Down, Gdk.KEY_Down):
            GLib.idle_add(self._resume_at_end)
        return False

    def _resume_at_end(self):
        if not self.closed and self.state['view'] == 'chat':
            adjustment = self.scroll.get_vadjustment()
            end = max(adjustment.get_lower(), adjustment.get_upper()-adjustment.get_page_size())
            # Follow the real end, including the space below the last message.
            # Never align that message with the composer during a wheel gesture.
            self._follow_end = end-adjustment.get_value() <= 24
        return GLib.SOURCE_REMOVE

    def _scroll_layout_ready(self, clock):
        if self.closed or not self._follow_end or self.state["view"] != "chat":
            self._finish_scroll_layout(clock)
            return
        # A single adjustment after layout. Later content-size changes schedule
        # their own adjustment; user scrolling never starts a paint feedback loop.
        self._scroll_to_end()
        self._finish_scroll_layout(clock)

    def _finish_scroll_layout(self, clock):
        clock.disconnect(self._scroll_handler)
        self._scroll_handler = None
        self._scroll_clock = None

    def _scroll_to_end(self):
        if not self.closed and self.state["view"] == "chat":
            adjustment = self.scroll.get_vadjustment()
            adjustment.set_value(max(adjustment.get_lower(), adjustment.get_upper() - adjustment.get_page_size()))
        return GLib.SOURCE_REMOVE

    def render_sidebar(self):
        query = self.state.get("sq", "")
        if self.foot.entry.get_text() != query:
            self.foot.entry.set_text(query)
        self.sidebar.clear()
        for view, label, glyph in (("activity", "Activity", "history"), ("models", "Models", "cpu")):
            downloading = next((model for model in self.data["local_models"] if model.get("dl") is not None), None)
            row = SidebarRow(label, lead=RowLead.icon(glyph),
                             trail=CountBadge(pending_count(self.data["activity"]), attention=True) if view == "activity" else
                             f'{round(downloading["dl"])}%' if downloading else None)
            named(row, "nav-" + view)
            row.view = view
            self.sidebar.append_row(row)
            if self.state["view"] == view:
                self.sidebar.list.select_row(row)
        for day, chats in chat_groups(self.data["conversations"], self.state.get("sq", "")):
            self.sidebar.append_row(SidebarSection(day))
            for chat in chats:
                row = named(SidebarRow(chat["t"], lead=RowLead.icon("message-circle")), "chat-" + chat["id"])
                row.chat = chat["id"]
                self.sidebar.append_row(row)
                if self.state["view"] == "chat" and chat["id"] == self.state["cur"]:
                    self.sidebar.list.select_row(row)
        if self.state.get("sq") and not chat_groups(self.data["conversations"], self.state["sq"]):
            label = text(f'No chats match “{self.state["sq"]}”', wrap=True)
            label.set_margin_start(10)
            label.set_margin_end(10)
            self.sidebar.append_row(Gtk.ListBoxRow(child=label, selectable=False, activatable=False))

    def _selected(self, _listing, row):
        if self._rendering or row is None:
            return
        if hasattr(row, "view"):
            self.state["view"] = row.view
            self.render()
        elif hasattr(row, "chat"):
            self.open_chat(row.chat)

    def search_chats(self, query):
        if self._rendering:
            return
        self.state["sq"] = query
        self.state["q"] = query
        self._rendering = True
        self.render_sidebar()
        self._rendering = False
        # Both v70 search fields share data-arisq and refresh the current page.
        clear(self.page)
        if self.state["view"] == "models":
            self.render_models()
        elif self.state["view"] == "activity":
            self.render_activity()
        else:
            self.render_chat()
            self._scroll_to_end()

    def open_chat(self, identity):
        self.stop()
        self.title_island.fold()
        self.generation += 1
        self.state.update(view="chat", cur=identity)
        self.render()
        self.entry.focus()
        if not self.fixture:
            generation = self.generation
            def loaded(value):
                if generation != self.generation or self.closed:
                    return
                if isinstance(value, Exception):
                    self.notify("The conversation could not be loaded", error=True)
                    return
                raw = json.loads(value[0])
                chat = next((chat for chat in self.data["conversations"] if chat["id"] == identity), None)
                if chat is None:
                    return
                chat["msgs"] = stored_messages(raw)
                for step in raw["steps"]:
                    self.data["activity"][step["id"]] = stored_receipt(step, identity, chat["day"])
                self.render()
            self.daemon.call("GetConversation", "(s)", (identity,), loaded)

    def new_chat(self):
        self.stop()
        self.title_island.fold()
        self.generation += 1
        self.state.update(view="chat", cur="new")
        self.draft = ""
        self.render()
        self.entry.focus()

    def mark(self, size=26):
        image = Gtk.Image.new_from_icon_name("ari-app")
        image.set_pixel_size(size)
        image.set_valign(Gtk.Align.START)
        if hasattr(lumaui_tokens, "ARI"):
            mark = box(image, valign=Gtk.Align.START, overflow=Gtk.Overflow.HIDDEN)
            if size == lumaui_tokens.ARI["mark"]["welcome_size"]:
                mark.add_css_class("ari-welcome-mark")
            return mark
        return image

    def render_chat(self):
        chat = next((chat for chat in self.data["conversations"] if chat["id"] == self.state["cur"]), None)
        if chat is None:
            clear(self.page)
            self._chat_rows.clear()
            welcome = named(box(vertical=True), "welcome")
            # .arinew uses 11vh: of Studio's 1000px reference viewport, or the phone's 874.
            welcome.set_margin_top(96 if self.phone else 110)
            welcome.set_margin_start(32)
            welcome.set_margin_end(32)
            mark = self.mark(64)
            mark.set_halign(Gtk.Align.START)
            welcome.append(mark)
            heading = ari_text("What should Ari do?", "page-title", "title-1")
            heading.set_xalign(.5)
            heading.set_margin_top(18)
            heading.set_margin_bottom(6)
            welcome.append(heading)
            explanation = text("Ari works with your files, apps and settings. It asks before it sends anything, or uses the cloud.",
                               "ari-welcome" if "ari_welcome" in lumaui_tokens.TYPE_SCALE else "body", wrap=True)
            explanation.set_justify(Gtk.Justification.CENTER)
            explanation.set_xalign(.5)
            explanation.add_css_class("ari-welcome-copy")
            explanation.set_margin_bottom(26)
            welcome.append(Adw.Clamp(maximum_size=420, tightening_threshold=420, child=explanation))
            if not self.fixture and not any(p.get("on") for p in self.data["providers"]) and not any(m.get("have") for m in self.data["local_models"]):
                welcome.append(self.button("Use my ChatGPT plan", self.show_chatgpt, primary=True, name="welcome-chatgpt"))
                welcome.append(self.button("Get a local model", self.show_models, name="welcome-local"))
            grid = Gtk.Grid(column_spacing=8, row_spacing=8, column_homogeneous=True)
            for index, (glyph, label, key) in enumerate(SUGGESTIONS):
                if hasattr(lumaui_tokens, "ARI"):
                    metrics = lumaui_tokens.ARI["suggestion"]
                    button = named(Gtk.Button(), "suggest-" + key)
                    button.add_css_class("ari-suggestion")
                    button.connect("clicked", lambda _button, value=label: self.send(value))
                    glyph_view = icons.image(glyph, pixel_size=metrics["icon"])
                    glyph_view.add_css_class("ari-suggestion-icon")
                    words = ari_text(label, "suggestion", wrap=True)
                    words.add_css_class("ari-suggestion-copy")
                    button.set_child(box(glyph_view, words, spacing=metrics["gap"]))
                else:
                    button = self.button(label, lambda value=label: self.send(value), glyph, name="suggest-" + key, size="bar", appearance="outline")
                    words = button.get_child().get_last_child()
                words.set_wrap(True)
                words.set_max_width_chars(25)
                words.set_xalign(0)
                button.set_hexpand(True)
                grid.attach(button, index % 2, index // 2, 1, 1)
            welcome.append(grid)
            self.page.append(Adw.Clamp(maximum_size=580, tightening_threshold=580, child=welcome))
            return
        previous, used = None, set()

        def row(key, value, create):
            nonlocal previous
            cached = self._chat_rows.get(key)
            if cached is None or cached[0] != value or cached[1].get_parent() is not self.page:
                if cached is not None and cached[1].get_parent() is self.page:
                    self.page.remove(cached[1])
                widget = create()
                self.page.insert_child_after(widget, previous)
                self._chat_rows[key] = (copy.deepcopy(value), widget)
            else:
                widget = cached[1]
                self.page.reorder_child_after(widget, previous)
            used.add(key)
            previous = widget

        if not self.phone:
            # On a phone the title island names the conversation (v71 hides .arihd under 560).
            title = ari_text(chat["t"], "heading", "assistant-title")
            day = ari_text(chat["day"], "day", "caption")
            day.set_valign(Gtk.Align.CENTER)
            heading = named(box(title, day, spacing=10), "chat-heading")
            heading.set_margin_bottom(6)
            row('heading', (chat['t'], chat['day']), lambda: heading)
        for index, message in enumerate(chat["msgs"]):
            if "u" in message:
                # v71: your words in the accent bubble on the right, as in Messages.
                bubble = MessageBubble(message["u"], mine=True,
                    max_width_ratio=lumaui_tokens.ARI["user"]["phone_width_ratio" if self.phone else "width_ratio"])
                bubble.add_css_class("ari-user-bubble-phone" if self.phone else "ari-user-bubble")
                bubble.label.remove_css_class("lumaui-message-text")
                apply_type(bubble.label, "ari-user-phone" if self.phone else "ari-user")
                widget = named(bubble, 'user-' + str(index))
                if message.get('at'):
                    timestamp = text(message['at'], 'caption')
                    timestamp.set_halign(Gtk.Align.END)
                    widget = box(widget, timestamp, vertical=True, spacing=4)
                row(index, message, lambda: widget)
            elif "a" in message:
                row(index, message, lambda: named(self.reply_bubble(message), 'reply-' + str(index)))
            elif "ch" in message and message["ch"] in self.data["activity"]:
                # What Ari did spans the column (v71 .arisay.chain, no indent).
                row(index, self.data['activity'][message['ch']], lambda: self.receipt(message['ch']))
            elif "think" in message:
                row(index, message, lambda: named(text(message['think'], 'lead', muted=True), 'thinking'))
        for key in set(self._chat_rows) - used:
            widget = self._chat_rows.pop(key)[1]
            if widget.get_parent() is self.page:
                self.page.remove(widget)

    def reply_bubble(self, message):
        """Ari's reply: the chip bubble on the left with no mark (a one-to-one thread), its
        paragraphs, then "On this computer · 3.1 s" and Copy under a hairline inside it."""
        content = box(vertical=True, spacing=6 if self.phone else 10)
        for paragraph, code in reply_blocks(message["a"].replace("|", "\n\n") if self.fixture else message["a"]):
            label = Gtk.Label(xalign=0, wrap=True, selectable=True,
                              wrap_mode=Pango.WrapMode.WORD_CHAR)
            label.set_markup(paragraph)
            label.connect("activate-link", lambda _label, url: self.open_reply_link(url))
            if code:
                label.add_css_class("monospace")
            # The kit's message type, so a reply reads as a message bubble's text.
            apply_type(label, "ari-reading")
            content.append(label)
        if message.get("mem"):
            content.append(self.memory(markers=True))
        if message.get("m"):
            model = message.get("model_info") or self.model(message["m"])
            local = model.get("local", True)
            where = box(apply_type(icons.image("house" if local else "cloud", pixel_size=13), "caption", muted=True),
                        ari_text("On this computer" if local else "Cloud", "metadata", "caption"), spacing=5)
            foot = box(where, ari_text("·", "metadata", "caption"), ari_text(f'{message["s"]} s', "metadata", "caption"),
                       Gtk.Box(hexpand=True),
                       make_control(BarAction("copy", tooltip="Copy",
                                              on_activate=lambda value=message["a"]: self.copy(plain_text(value))), size="inline"),
                       spacing=8)
            if model.get("effort") and model.get("effort") != model.get("default_effort"):
                foot.insert_child_after(text("· " + effort_label(model["effort"])[0], "caption"), where)
            foot.set_tooltip_text(model["n"] + (" · on this computer" if local else " · sent to " + model.get("via", "the cloud")))
            rule = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            rule.set_margin_top(2)
            content.append(rule)
            content.append(named(foot, "reply-foot"))
        sources = message.get("sources", [])
        if sources:
            links = Adw.WrapBox(child_spacing=12, line_spacing=6)
            links.append(text("Sources", "caption"))
            seen = set()
            for source in sources[:100]:
                url = source.get("url", "")
                if not safe_url(url) or url in seen:
                    continue
                seen.add(url)
                link = Gtk.LinkButton(uri=url, label=source.get("title") or url)
                link.connect("activate-link", lambda _link, target=url: self.open_reply_link(target))
                links.append(link)
            content.append(named(links, "reply-sources"))
        bubble = MessageBubble(child=content, padded=True, max_width=lumaui_tokens.ARI["reply"]["max_width"],
                             max_width_ratio=lumaui_tokens.ARI["reply"]["width_ratio"])
        bubble.add_css_class("ari-reply-bubble")
        return bubble

    def open_reply_link(self, url):
        if safe_url(url):
            Gtk.UriLauncher.new(url).launch(self, None, None)
        return True

    def receipt_lead(self, node):
        if self.fixture:
            photos = Path(os.environ.get("XDG_DATA_HOME", "")) / "photos"
            if not photos.is_dir():
                photos = Path(__file__).resolve().parents[3] / "tests/fixtures/ari-v70-data/photos"
            if node["k"] == "person":
                portraits = {"TH": ("take-back", (.575, .12, 5.4)),
                             "PR": ("life-ari-street", (.705, .2, 5.2)),
                             "MO": ("life-hero-home", (.8, .33, 6))}
                name, focus = portraits[node["p"]]
                return PersonAvatar(node["n"], 22, picture=FixtureFace(photos / (name + ".webp"), focus))
            if node["k"] == "stack" and node.get("kind") in ("shots", "photos"):
                name = "life-hero-studio" if node["kind"] == "photos" else "life-work-suite"
                picture = Gtk.Picture.new_for_paintable(FixtureFace(
                    photos / (name + ".webp"), (.5, .5, 16 / 9), size=22))
                picture.set_content_fit(Gtk.ContentFit.COVER)
                picture.set_size_request(22, 22)
                # Keep the square independent of the two-line row's height request.
                thumbnail = Gtk.Fixed(width_request=22, height_request=22)
                thumbnail.put(picture, 0, 0)
                return named(thumbnail, "receipt-thumbnail")
        glyph = icons.image(GLYPHS.get(node["k"], "file-text"), pixel_size=17)
        glyph.add_css_class("lumaui-hue-tint")
        lead = box(glyph, width_request=22, height_request=22, halign=Gtk.Align.CENTER)
        hue_class(lead, node.get("h", {"stack": 60, "file": 150, "email": 240, "folder": 250,
                                      "app": 35, "apps": 265, "setting": 280, "event": 25}.get(node["k"], 250)))
        return lead

    def receipt(self, identity, compact=False):
        receipt = self.data["activity"][identity]
        state, state_label = trace_state(receipt)
        if len(receipt["nodes"]) == 1 and state == "done" and receipt["undo"] == "none" and not receipt.get("ask"):
            line = named(box(self.receipt_lead(receipt["nodes"][0]),
                             text(receipt["t"], "body", wrap=True),
                             text(receipt["at"], "caption"), spacing=10), "receipt-" + identity)
            if receipt.get("target"):
                line.append(self.button("Open", lambda: self.open_receipt(identity), size="tiny", appearance="quiet"))
            return line
        card = named(ari_surface("ari-receipt-surface", lambda: Card(padded=False)), "receipt-" + identity)
        card.add_css_class("ari-receipt")
        if compact:
            card.add_css_class("compact")
        status = ari_text(state_label, "state", "caption", wrap=True)
        status.set_wrap_mode(Pango.WrapMode.WORD)
        status.set_natural_wrap_mode(Gtk.NaturalWrapMode.NONE)
        status.add_css_class("ari-receipt-state-" + state)
        state_view = box(status, spacing=6)
        if state == "run":
            state_view.prepend(Gtk.Spinner(spinning=True, width_request=12, height_request=12))
        title = ari_text(receipt["t"], "receipt-title", "title-2", wrap=True)
        title.set_wrap_mode(Pango.WrapMode.WORD)
        title.set_natural_wrap_mode(Gtk.NaturalWrapMode.NONE)
        # v71 third pass: the title wraps on its own line; the state, Undo and the time follow.
        meta = box(state_view, Gtk.Box(hexpand=True), spacing=10)
        heading = box(title, meta, vertical=True, spacing=2)
        heading.add_css_class("ari-receipt-heading")
        if state != "run" and (receipt["undo"] == "can" or receipt["undo"] == "part" and state != "ask"):
            meta.append(self.button("Undo", lambda: self.undo(identity), name="undo-" + identity,
                                       size="tiny", appearance="quiet",
                                       tooltip="Removes the file and the share. The email can’t be unsent."
                                       if receipt["undo"] == "part" else None))
        if receipt["undo"] in ("done", "undone"):
            note = ari_text("Turned off at 16:40" if receipt["undo"] == "done" else "Put back as it was", "metadata", "caption", wrap=True)
            note.set_wrap_mode(Pango.WrapMode.WORD)
            note.set_natural_wrap_mode(Gtk.NaturalWrapMode.NONE)
            meta.append(note)
        if not compact:
            meta.append(ari_text(receipt["at"], "time", "caption"))
        if receipt.get("target") or (self.fixture and compact and any(node['k'] in ('file','folder','app') and node['st']=='done' for node in receipt['nodes'])):
            meta.append(self.button("Open", lambda: self.open_receipt(identity), size="tiny", appearance="quiet"))
        if receipt.get("confirm_within") and not receipt.get("kept"):
            meta.append(self.button("Keep", lambda: self.keep(identity), name="keep-" + identity, size="tiny", appearance="quiet"))
        card.append(heading)
        steps = named(box(vertical=True), "steps-" + identity)
        steps.add_css_class("ari-receipt-steps")
        for index, node in enumerate(receipt["nodes"]):
            if node["st"] == "hide":
                continue
            verb = "Email to" if node["k"] == "email" and node["st"] in ("ask", "undone") else node.get("v", VERBS.get(node["k"], ""))
            if node["st"] == "done":
                trail = apply_type(icons.image("check", pixel_size=14), "caption", muted=True)
            elif node["st"] == "run":
                trail = Gtk.Spinner(spinning=True, width_request=11, height_request=11)
            else:
                trail = ari_text({"ask": "Waiting", "undone": "Undone"}.get(node["st"], ""), "waiting", "caption")
                if node["st"] == "ask":
                    trail.add_css_class("ari-step-waiting")
            # v71 third pass: the step's detail wraps under its name; Waiting never clips.
            name = ari_text(node["n"], "step-title", "body", wrap=True)
            name.add_css_class("ari-step-name")
            if verb:
                parts = Adw.WrapBox(child_spacing=4, line_spacing=0)
                parts.append(ari_text(verb, "step-verb", "body"))
                parts.append(name)
                name = parts
            detail = ari_text(node["m"], "subtitle", "caption", wrap=True, muted=True)
            words = box(name, detail, vertical=True, hexpand=True, valign=Gtk.Align.CENTER)
            words.set_margin_top(7)
            words.set_margin_bottom(7)
            trail.set_valign(Gtk.Align.CENTER)
            # A wrapped detail makes the row taller; the 22px lead stays square, centred.
            lead = self.receipt_lead(node)
            lead.set_valign(Gtk.Align.CENTER)
            line = box(lead, words, trail, spacing=10)
            line.add_css_class("ari-receipt-step")
            if node["st"] == "undone":
                line.add_css_class("undone")
            steps.append(named(line, f"step-{identity}-{index}"))
        card.append(steps)
        if state == "ask" and not compact and receipt.get("ask"):
            draft = receipt["ask"]
            approval = named(box(vertical=True, spacing=2), "approval-" + identity)
            approval.add_css_class("ari-draft")
            approval.append(ari_text(draft["subj"], "draft-title", "title-2"))
            body = " ".join(draft["body"][1:-1])
            if hasattr(lumaui_tokens, "ARI"):
                preview = ari_text(body, "draft", wrap=True)
                # v71 .ariwin .ardraft span removes the earlier two-line clamp.
                preview.add_css_class("ari-draft-copy")
                approval.append(preview)
                metrics = lumaui_tokens.ARI["approval"]
                approval.set_margin_top(metrics["margin_top"])
                def fit_approval(width=None):
                    # Initial rendering precedes allocation; the kit measures
                    # content width after layout, excluding compositor shadows.
                    width = width or self.get_width() or self.get_default_size()[0]
                    wide = width > 560
                    approval.set_margin_start(metrics["margin_start"] if wide else metrics["phone_margin_x"])
                    approval.set_margin_end(metrics["margin_end"] if wide else metrics["phone_margin_x"])
                    approval.set_margin_bottom(metrics["margin_bottom"] if wide else metrics["phone_margin_bottom"])
                watching = []
                def watch_size(*_args):
                    if not watching:
                        watching.append(self.tier_watch.connect("width-changed", lambda _watch, width: fit_approval(width)))
                    fit_approval()
                def unwatch_size(*_args):
                    for handler in watching:
                        self.tier_watch.disconnect(handler)
                    watching.clear()
                card.connect("map", watch_size)
                card.connect("unmap", unwatch_size)
                fit_approval()
            else:
                draft_copy = ParagraphField(body, label="Email draft" if self.fixture else "Change preview")
                # arChain renders a preview; Edit is its separate action.
                draft_copy.set_editable(False)
                draft_copy.set_cursor_visible(False)
                # TextView otherwise expands vertically inside this receipt,
                # pushing the separate approval actions out of view.
                draft_copy.set_vexpand(False)
                draft_copy.set_valign(Gtk.Align.START)

                def reserve_draft_lines(field):
                    # v70 previews two lines. A GtkTextView can otherwise request
                    # zero height before its first allocation inside the receipt.
                    line_height = field.create_pango_layout("Ag").get_pixel_size()[1]
                    field.set_size_request(-1, 2 * line_height)

                reserve_draft_lines(draft_copy)
                draft_copy.connect("map", reserve_draft_lines)
                approval.append(draft_copy)
            if not self.fixture and draft.get('permission'):
                remember = Gtk.CheckButton(active=bool(draft.get('remember')))
                remember.set_child(Gtk.Label(label='Remember this choice: '+draft.get('permission_label',''),wrap=True,xalign=0))
                remember.set_name('remember-'+identity)
                remember.connect('toggled',lambda widget, value=draft: value.__setitem__('remember',widget.get_active()))
                approval.append(remember)
            actions = box(spacing=6)
            actions.set_margin_top(8)
            actions.append(self.button("Don’t send" if self.fixture else "Decline", lambda: self.answer(identity, False),
                                       name="decline-" + identity, appearance="quiet"))
            actions.append(Gtk.Box(hexpand=True))
            choices = Adw.WrapBox(child_spacing=6, line_spacing=6)
            choices.set_halign(Gtk.Align.END)
            for button in (
                self.button("Edit", self.edit_approval, name="edit-" + identity),
                self.button(f"Send to {recipient_first_name(receipt)}" if self.fixture else "Approve", lambda: self.answer(identity, True), primary=True, name="approve-" + identity)):
                choices.append(button)
            actions.append(choices)
            approval.append(actions)
            card.append(approval)
        return card

    def open_receipt(self, identity):
        receipt = self.data["activity"].get(identity, {})
        target = receipt.get("target")
        if self.fixture:
            self.notify("Opened in this preview")
        elif isinstance(target, str) and Path(target).is_absolute():
            Gtk.FileLauncher.new(Gio.File.new_for_path(target)).launch(self, None, None)

    def render_activity(self):
        files = len(activity_items(self.data["activity"], "Files"))
        people = len(activity_items(self.data["activity"], "People"))
        self.page.append(box(ari_text("Activity", "page-title", "title-1"),
                             ari_text(f"Everything Ari has done on this computer, newest first. {files} changes to files, {people} involving people.",
                                      "description", "body", wrap=True, muted=True), vertical=True, spacing=5))
        items = activity_items(self.data["activity"], self.state["filter"])
        for day in dict.fromkeys(receipt["day"] for _, receipt in items):
            heading = text(day, "label")
            heading.set_margin_top(20)
            self.page.append(heading)
            for identity, receipt in items:
                if receipt["day"] != day:
                    continue
                content = box(self.receipt(identity, compact=True), vertical=True, spacing=8, hexpand=True)
                chat_id = receipt.get("conv")
                chat = next((chat for chat in self.data["conversations"] if chat["id"] == chat_id), None)
                if chat:
                    source = self.button(f'From “{chat["t"]}”', lambda value=chat_id: self.open_chat(value), "message-circle", appearance="quiet")
                    source.add_css_class("ari-activity-source")
                    source.set_halign(Gtk.Align.START)
                    source.set_margin_start(16)
                    image = source.get_child().get_first_child()
                    if isinstance(image, Gtk.Image):
                        image.set_pixel_size(13)
                    content.append(source)
                else:
                    content.append(text("From a voice request", "caption"))
                if self.phone:
                    # v71 phone: the time sits above its card, so the card uses the full width.
                    when = text(receipt["at"], "caption", muted=True)
                    when.set_margin_start(4)
                    row = box(when, content, vertical=True, spacing=6)
                else:
                    when = text(receipt["at"], "body")
                    when.set_size_request(60, -1)
                    when.set_valign(Gtk.Align.START)
                    when.set_margin_top(14)
                    row = box(when, content, spacing=12)
                row.set_margin_bottom(8)  # page gap12 + row margin8 = v70 margin20
                self.page.append(row)
        if not items:
            self.page.append(text("Nothing here yet.", "caption"))

    def model(self, identity=None):
        identity = identity or self.state["model"]
        if identity == "auto":
            return {"id": "auto", "n": "Auto", "local": True, "mono": "", "h": 250}
        for model in self.data["local_models"]:
            if model["id"] == identity:
                return {**model, "local": True}
        for provider in self.data["providers"]:
            for key, name in provider["models"]:
                if key == identity:
                    return {"id": key, "n": name, "local": False, "via": provider["n"], "pid": provider["id"]}
        if self.live_cloud_model and identity == self.live_cloud_model["id"]:
            provider = self.live_cloud_model.get("provider", "openrouter")
            return {"id": identity, "n": self.live_cloud_model["name"], "local": False,
                    "via": "ChatGPT" if provider == "chatgpt" else "OpenRouter", "pid": provider}
        if identity.startswith("chatgpt:"):
            return {"id": identity, "n": "ChatGPT", "local": False, "via": "ChatGPT", "pid": "chatgpt"}
        return {"id": identity, "n": identity, "local": True}

    def model_mark(self, model, size=34):
        identity = "auto" if model["id"] == "auto" else model.get("pid") or model["id"]
        if not model.get("pid") and identity != "auto":
            maker = FAMILIES.get(family_for(model), {}).get("by")
            identity = {"Qwen": "qwen", "Google": "gemma", "DeepSeek": "deepseek", "Mistral": "mistral",
                        "Meta": "meta", "Microsoft": "microsoft"}.get(maker, identity)
        if identity == "anthropic" and model.get("pid"):
            identity = "claude"
        if identity == "chatgpt":
            identity = "openai"
        asset = Path(__file__).with_name("assets") / (identity + ".svg")
        if identity == "auto" or asset.is_file():
            image = (Gtk.Image.new_from_icon_name("ari-app") if identity == "auto"
                     else Gtk.Image.new_from_file(str(asset)))
            image.set_pixel_size(size)
            if hasattr(lumaui_tokens, "ARI"):
                tile = box(image, overflow=Gtk.Overflow.HIDDEN, valign=Gtk.Align.CENTER, halign=Gtk.Align.START)
                tile.add_css_class("ari-logo")
                self._model_corner(tile, size)
                return tile
            return image
        if identity == "auto":
            return self.mark(size)
        if hasattr(lumaui_tokens, "ARI"):
            roles = {34: "monogram-small", 36: "monogram-regular", 56: "monogram-hero", 28: "monogram-picker"}
            letter = ari_text(model.get("mono") or model["n"][:1], roles.get(size, "monogram-hero"), "title-2")
            letter.set_xalign(.5)
            letter.set_halign(Gtk.Align.CENTER)
            letter.set_valign(Gtk.Align.CENTER)
            tile = box(letter, width_request=size, height_request=size, halign=Gtk.Align.CENTER,
                       valign=Gtk.Align.CENTER, overflow=Gtk.Overflow.HIDDEN)
            tile.add_css_class("ari-monogram")
            if model.get("h") is not None:
                tile.add_css_class(lumaui_style.monogram_hue(model["h"]))
            self._model_corner(tile, size)
            return tile
        return PersonAvatar(model.get("mono") or model["n"][:1], size=size)

    @staticmethod
    def _model_corner(tile, size):
        css_class = "ari-model-size-" + str(size)
        tile.add_css_class(css_class)
        if css_class not in lumaui_style._registered_monograms:
            add_style_builder(lambda _appearance: f".{css_class} {{ border-radius: {size * lumaui_tokens.ARI['monogram']['corner_ratio']}px; }}")
            lumaui_style._registered_monograms.add(css_class)

    def memory(self, markers=False):
        computer = self.data["computer"]
        if not computer.get("mem"):
            return text("Memory information is unavailable", "caption")
        if not self.fixture and "sys" not in computer:
            return text(f'{gb(computer["mem"])} memory', "caption")
        identity = "qwen8" if self.state["model"] == "auto" else self.state["model"]
        loaded = next((model for model in self.data["local_models"] if model["id"] == identity), None) if self.fixture else None
        total, system = computer["mem"], computer.get("sys", 0)
        used = loaded.get("mem", 0) if loaded else 0
        # TODO(kit-request ari-03): replace with DT2 SegmentBar.
        progress = ProgressLine(min(1, (system + used) / total), label="Memory in use")
        progress.add_css_class("ari-memory")
        metrics = getattr(lumaui_tokens, "ARI", {}).get("memory", {})
        legends = Adw.WrapBox(child_spacing=metrics.get("legend_gap", 16), line_spacing=metrics.get("legend_line_gap", 6))
        legends.append(ari_text(f"Luma and apps {gb(system)}", "metadata", "caption"))
        if loaded:
            legends.append(ari_text(f'{loaded["n"]} {gb(used)}', "metadata", "caption"))
        legends.append(ari_text(f"Free {gb(total - system - used)}", "metadata", "caption"))
        total_label = ari_text(f"{gb(total)} memory", "field-label", "label", expand=True)
        total_label.add_css_class("ari-memory-total")
        total_label.set_xalign(1)
        legends.append(total_label)
        view = box(progress, legends, vertical=True, spacing=metrics.get("legend_top", 9))
        if markers:
            marks = box(spacing=12)
            for key, label in (("qwen8", "8B"), ("qwen14", "14B"), ("qwen30", "30B"), ("llama70", "70B")):
                marks.append(text(label, "caption", expand=True))
            view.prepend(marks)
        return named(view, "memory")

    def _provider_columns_changed(self, *_):
        if self.providers_view is not None:
            self.providers_view.set_max_children_per_line(self.provider_columns)
        self._reflow_hero()
        if self.model_family and self.state["view"] == "models" and not self._rendering:
            self.render()

    def _reflow_hero(self):
        if self.hero_view is None:
            return
        grid, mark, information, fitting, action = self.hero_view
        narrow = self.provider_columns == 1
        for child in (mark, information, fitting, action):
            if child.get_parent() is grid:
                grid.remove(child)
        fit_width = -1 if narrow else 240
        if not narrow and hasattr(lumaui_tokens, "ARI"):
            fit_width = lumaui_tokens.ARI["hero"]["fit_width"]
        fitting.set_size_request(fit_width, -1)
        grid.attach(mark, 0, 0, 1, 1)
        grid.attach(information, 1, 0, 1, 1)
        grid.attach(fitting, 0 if narrow else 2, 1 if narrow else 0, 2 if narrow else 1, 1)
        grid.attach(action, 0 if narrow else 3, 2 if narrow else 0, 2 if narrow else 1, 1)

    def switch_models_tab(self, tab):
        self.models_tab, self.model_family, self.removing_model = tab, None, ""
        self.render()

    def show_model_family(self, family, selected=None):
        self.model_family, self.removing_model = family, ""
        if selected:
            self.family_selection[family] = selected
        self.state["view"] = "models"
        self.render()

    def selected_family_model(self):
        values = families(self.data["local_models"]).get(self.model_family, [])
        return next((model for model in values if model["id"] == self.family_selection.get(self.model_family)),
                    values[0] if values else None)

    def fitting(self, model):
        if self.fixture:
            return model_fit(model, self.data["computer"])
        total = self.data["computer"].get("mem", 0)
        available = self.data["computer"].get("free", total)
        need = model.get("mem", 0)
        if not total or not need:
            return "unknown", "Memory requirements unavailable"
        if need > total - 2:
            return "no", "Too big for this computer"
        if need <= available - 2:
            return "good", "Runs well here"
        return "ok", "May run slowly with other apps open"

    def render_models(self):
        self.hero_view, self.providers_view = None, None
        if self.model_family:
            self.render_family_page()
            return
        model = self.model()
        if model["id"] == "auto" and self.fixture:
            model = self.model("qwen8")
        description = ("Pick what Ari runs on." if model["id"] == "auto" else
                       f'Ari runs on {model["n"]}' + (", on this computer." if model.get("local") else ", in the cloud."))
        heading = ari_text("Get models" if self.models_tab == "store" else "Models", "page-title", "title-1")
        heading.add_css_class("ari-models-title")
        if self.models_tab == "store":
            machine = self.data["computer"].get("n", "this computer")
            machine = machine[0].lower()+machine[1:] if machine.startswith("This ") else machine
            description = "Free models that run on " + machine + ". Nothing you say to them leaves it."
        heading.set_hexpand(True)
        switch = ModeSwitch([("yours", "Yours"), ("store", "Get models")], current=self.models_tab,
                            on_change=self.switch_models_tab, label="Models")
        description_label = text(description, "body", wrap=True)
        description_label.add_css_class("ari-models-description")
        header = box(heading, description_label, vertical=True, spacing=6)
        switch.set_halign(Gtk.Align.END)
        switch.set_valign(Gtk.Align.START)
        switch.set_margin_end(16)
        switch.set_margin_top(48 if self.phone else 16)
        if self.models_switch is not None:
            self.host.remove_overlay(self.models_switch)
        self.models_switch = switch
        self.host.add_overlay(switch)
        header.add_css_class("ari-models-page")
        for edge in ("start", "end"):
            getattr(header, "set_margin_" + edge)(16 if self.phone else 40)
        header.set_margin_top(61 if self.phone else 30)
        header.set_margin_bottom(0)
        self.models_header.append(header)
        self.models_header.set_visible(True)
        if self.models_loading or self.models_error:
            self.page.append(text("Loading available models…" if self.models_loading else self.models_error, "body", wrap=True))
            if self.models_error:
                self.page.append(self.button("Try again", self.retry_models, name="retry-models"))
            if not self.data["local_models"]:
                return
        if self.models_tab == "store":
            self.render_model_store()
            return
        self.section("Ari uses")
        self.models_change_button = self.button("Change", self.model_picker)
        use_rows = box(vertical=True, spacing=0)
        use_rows.append(box(self.model_mark(model, 44),
                        box(text(model["n"], "title-2"), self.model_row_caption("For everything · On this computer" if model.get("local") else "For everything · Cloud"), vertical=True, hexpand=True, valign=Gtk.Align.CENTER),
                        self.models_change_button, spacing=14))
        task_names = {"code": "Code", "hard": "Hard problems", "long": "Long reading", "writing": "Writing"}
        for task, identity in self.task_models.items():
            assigned = self.model(identity)
            lead = icons.image("code" if task == "code" else "sliders-horizontal", pixel_size=18)
            lead.set_size_request(44, 40)
            row = box(lead,
                      box(text(task_names.get(task, task), "title-2"), self.model_row_caption(assigned["n"] + (" · On this computer" if assigned.get("local") else " · Cloud")), vertical=True, hexpand=True),
                      self.icon_button("x", "Use the same as everything", lambda task=task: self.set_task_model(task, ""), size="tiny"),
                      self.button("Change", lambda task=task: self.assign_task(task)), spacing=14)
            use_rows.append(Gtk.Separator())
            use_rows.append(row)
        self.models_task_button = self.button("Use a different model for a task", self.choose_task, "sliders-horizontal")
        self.models_task_button.get_child().set_halign(Gtk.Align.START)
        self.models_task_button.add_css_class("ari-task-add")
        use_rows.append(Gtk.Separator())
        use_rows.append(self.models_task_button)
        use_rows.add_css_class("ari-model-assignments")
        assignments = Card(use_rows)
        assignments.add_css_class("ari-assignments-card")
        self.page.append(assignments)
        self.section("Cloud", "Ari asks before it sends anything")
        provider_rows = box(vertical=True, spacing=0)
        providers = sorted(self.data["providers"], key=lambda item: 0 if item["id"] in ("chatgpt", "openai") else 1 if item["id"] == "openrouter" else 2)
        for provider in providers:
            connected = provider.get("on")
            is_chatgpt = provider["id"] in ("chatgpt", "openai")
            descriptions = {"openrouter":"Hundreds of models with one key", "anthropic":"Claude, with an API key",
                            "google":"Gemini, with an API key", "own":"Ollama, LM Studio or anything like them"}
            line = provider.get("note") if connected else ("Sign in and Ari can use the models in your ChatGPT plan. No API key." if is_chatgpt else descriptions.get(provider["id"], "Connect your account to use its models"))
            provider_title = "Use your ChatGPT account" if is_chatgpt and not connected else provider["n"]
            promo = is_chatgpt and not connected
            details = box(text(provider_title, "title-2"),
                          text(line or "Connected", "caption", wrap=True) if promo else self.model_row_caption(line or "Connected"),
                          vertical=True, hexpand=True, spacing=4 if promo else 2)
            if connected:
                details.append(connection("Connected"))
            row = box(self.model_mark(provider, 56 if promo else 40), details, spacing=18 if promo else 14)
            if is_chatgpt:
                action = (self.icon_button("ellipsis", "ChatGPT account and usage", self.show_chatgpt) if connected else
                          self.button("Continue with ChatGPT", self.show_chatgpt, primary=True, name="provider-chatgpt"))
                if promo:
                    action.add_css_class("ari-chatgpt-signin")
                    if self.phone:
                        action.set_hexpand(True)
                if self.phone and promo:
                    row = box(row, action, vertical=True, spacing=16)
                else:
                    row.append(action)
            else:
                row.append(self.button("Account" if connected else "Connect", lambda value=provider["id"]: self.provider_sheet(value), name="provider-" + provider["id"]))
            if promo:
                card = Card(row)
                card.add_css_class("ari-chatgpt-card")
                self.page.append(card)
            else:
                if provider_rows.get_first_child():
                    provider_rows.append(Gtk.Separator())
                row.add_css_class("ari-provider-row")
                provider_rows.append(row)
        if provider_rows.get_first_child():
            card = Card(provider_rows)
            card.add_css_class("ari-providers-card")
            self.page.append(card)
        installed = [model for model in self.data["local_models"] if model.get("have") or model.get("dl") is not None]
        if installed:
            self.section("On this computer", f'{len(installed)} ' + ("model" if len(installed) == 1 else "models") + " · " + gb(sum(item["sz"] for item in installed)))
            installed_rows = box(vertical=True, spacing=0)
            for model in installed:
                usage = (["everything"] if model["id"] == self.state["model"] or
                         (self.state["model"] == "auto" and model == installed[0]) else []) + [
                             task_names.get(task, task) for task, value in self.task_models.items() if value == model["id"]]
                body = box(self.model_mark(model, 40), box(text(model["n"], "title-2"),
                           self.model_row_caption("Used for " + ", ".join(usage) if usage else "Not in use"), vertical=True, hexpand=True), spacing=14)
                button = Gtk.Button(child=body, hexpand=True)
                button.add_css_class("ari-open-model")
                button.connect("clicked", lambda _button, value=model: self.show_model_family(family_for(value), value["id"]))
                row = box(button, spacing=10)
                if not self.phone:
                    row.append(text(gb(model["sz"]), "caption"))
                options = self.icon_button("ellipsis", model["n"] + " options", lambda: None)
                options.connect("clicked", lambda _button, value=model, anchor=options: self.model_options(value, anchor))
                row.append(options)
                row.add_css_class("ari-provider-row")
                if installed_rows.get_first_child():
                    installed_rows.append(Gtk.Separator())
                installed_rows.append(row)
            card = Card(installed_rows)
            card.add_css_class("ari-providers-card")
            self.page.append(card)
            self.page.append(self.button("Get more models", lambda: self.switch_models_tab("store"), "download"))
        computer = self.data["computer"]
        if computer.get("disk") is not None:
            self.page.append(text(gb(computer["disk"]) + " free on disk", "caption"))

    def model_row_caption(self, value):
        label = text(value, "caption")
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(1)
        label.set_hexpand(True)
        return label

    def model_options(self, model, anchor):
        self.close_picker()
        def remove():
            self.show_model_family(family_for(model), model["id"])
            self.begin_remove(model["id"])
        menu = FloatingMenu([
            RichMenuItem("View model", icon="cpu", on_activate=lambda: self.show_model_family(family_for(model), model["id"])),
            RichMenuItem("Remove", icon="trash-2", on_activate=remove)], label=model["n"])
        self.popover = menu.popup(anchor)

    def render_model_store(self):
        filters = box(spacing=6)
        for value, glyph in (("All", "layout-grid"), ("Code", "code"), ("Pictures", "image"), ("Hard problems", "lightbulb")):
            chip = self.button(value, lambda value=value: self.filter_models(value), glyph)
            chip.add_css_class("ari-filter-chip")
            if value == self.state["store"]:
                chip.add_css_class("selected")
            filters.append(chip)
        if self.phone:
            filter_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                vscrollbar_policy=Gtk.PolicyType.NEVER, child=filters, min_content_height=34,
                propagate_natural_height=True)
            self.page.append(filter_scroll)
            filter_scroll.set_margin_bottom(8)
        else:
            self.page.append(filters)
            filters.set_margin_bottom(8)
        groups = families(self.data["local_models"])
        capabilities = {"Code": "code", "Pictures": "pictures", "Hard problems": "think", "Images": "pictures", "Reasoning": "think"}
        shown = []
        for name, sizes in groups.items():
            facts = FAMILIES.get(name, {})
            if self.state["q"].casefold() not in (name + " " + facts.get("by", "")).casefold():
                continue
            if self.state["store"] in capabilities and capabilities[self.state["store"]] not in facts.get("can", []):
                continue
            shown.append((name, sizes, facts))
        too_big = [group for group in shown if all(self.fitting(model)[0] == "no" for model in group[1])]
        if not self.show_large_models:
            shown = [group for group in shown if group not in too_big]
        available = [model for _name, sizes, _facts in shown for model in sizes if not model.get("have")]
        recommended = next((model for model in available if model.get("recommended")), None)
        if recommended is None:
            recommended = max((model for model in available if self.fitting(model)[0] == "good"),
                              key=lambda model:model.get("mem",0), default=None)
        if recommended:
            machine = self.data["computer"].get("n", "this computer")
            machine = machine[0].lower()+machine[1:] if machine.startswith("This ") else machine
            recommendation = (f'The largest Qwen3 that runs well with {self.data["computer"].get("mem", 0):g} GB of memory. A step up from the 8B you have.'
                              if self.fixture else FAMILIES.get(family_for(recommended), {}).get("line", "Runs on this computer"))
            info = box(text("Best for " + machine, "caption"),
                       text(recommended["n"], "title-1"), text(recommendation, "body", wrap=True), vertical=True, hexpand=True, spacing=4)
            action = self.button("Download · " + gb(recommended["sz"]), lambda value=recommended["id"]: self.download(value), "download", primary=True, name="get-" + recommended["id"])
            if self.phone:
                action.set_hexpand(True)
            hero = named(Card(box(self.model_mark(recommended, 64), info, action, vertical=self.phone, spacing=22)), "model-" + recommended["id"])
            hero.add_css_class("ari-recommended-card")
            self.page.append(hero)
        grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                           min_children_per_line=1 if self.phone or self.provider_columns == 1 else 3,
                           max_children_per_line=1 if self.phone or self.provider_columns == 1 else 3, column_spacing=12, row_spacing=12)
        for name, sizes, facts in shown:
            best = next((model for model in sizes if self.fitting(model)[0] == "good"), sizes[0])
            installed = next((model for model in sizes if model.get("have")), None)
            fit = box(icons.image("circle-check" if installed else "circle", pixel_size=12),
                      text("On this computer" if installed else self.fitting(best)[1], "caption"), spacing=5)
            content = box(box(self.model_mark(best, 44), box(text(name, "title-2"), text(facts.get("by", ""), "caption"), vertical=True, hexpand=True, spacing=1), spacing=12),
                          text(facts.get("line", "Available for this computer"), "body", wrap=True),
                          box(fit, Gtk.Box(hexpand=True), text(f'{len(sizes)} sizes' if len(sizes) > 1 else gb(sizes[0]["sz"]), "caption"), spacing=4),
                          vertical=True, spacing=12)
            content.add_css_class("ari-family-content")
            button = Gtk.Button(child=content)
            button.add_css_class("ari-family-card")
            button.connect("clicked", lambda _button, value=name: self.show_model_family(value))
            grid.append(named(button, "family-" + name.lower().replace(' ', '-')))
        self.page.append(grid)
        if too_big and not self.show_large_models:
            amount = "one model" if len(too_big) == 1 else f"{len(too_big)} models"
            reveal = self.button("Show " + amount + " too big for this computer", self.reveal_large_models)
            reveal.set_halign(Gtk.Align.CENTER)
            self.page.append(reveal)
        if not shown:
            self.page.append(text("No models match your search.", "caption"))

    def reveal_large_models(self):
        self.show_large_models = True
        self.render()

    def render_family_page(self):
        model = self.selected_family_model()
        if model is None:
            self.model_family = None
            self.render_models()
            return
        family = self.model_family
        facts = FAMILIES.get(family, {})
        back = self.button("Get models" if self.models_tab == "store" else "Models",
                           lambda: self.switch_models_tab(self.models_tab), "chevron-left")
        back.set_halign(Gtk.Align.START)
        self.page.append(back)
        family_title = ari_text(family, "page-title", "title-1")
        family_title.add_css_class("ari-models-title")
        self.page.append(box(self.model_mark(model, 80), box(text(facts.get("by", ""), "caption"),
            family_title, text(facts.get("line", "A model on this computer"), "body", wrap=True),
            vertical=True, spacing=6), vertical=self.phone, spacing=20))
        self.section("Sizes", "Bigger sizes are smarter, and need more memory")
        sizes = Gtk.Grid(column_homogeneous=True, column_spacing=12, row_spacing=12)
        columns = 2 if self.phone or self.provider_columns == 1 else 4
        for index, option in enumerate(sorted(families(self.data["local_models"])[family], key=lambda item: item["sz"])):
            fit = self.fitting(option)[0]
            subtitle = "On this computer" if option.get("have") else {"good":"Runs well", "ok":"Slower", "no":"Too big"}.get(fit, "Requirements unknown")
            status = box(icons.image("circle", pixel_size=7), text(subtitle, "caption", wrap=True), spacing=6)
            status.set_margin_top(8)
            rung = box(text(option.get("parameters") or option["n"].removeprefix(family).strip(), "title-1"),
                       text(gb(option["sz"]), "body"), status, vertical=True, spacing=2)
            button = Gtk.Button(child=rung, width_request=110, hexpand=True)
            button.add_css_class("ari-size-rung")
            if option["id"] == model["id"]:
                button.add_css_class("selected")
            button.connect("clicked", lambda _button, value=option["id"]: self.show_model_family(family, value))
            sizes.attach(button, index % columns, index // columns, 1, 1)
        self.page.append(sizes)
        computer = self.data["computer"]
        estimate = f'{model["n"]} needs about {gb(model["mem"])} of memory. ' if model.get("mem") else "Memory requirements are not available. "
        estimate += (f'{computer.get("n", "This computer").capitalize()} has {gb(computer["mem"])}, so it ' +
                     ("runs well." if self.fitting(model)[0] == "good" else "runs, but answers come slowly." if self.fitting(model)[0] == "ok" else "won’t run here.")
                     if computer.get("mem") else self.fitting(model)[1] + ".")
        if model.get("have"):
            jobs = (["everything"] if self.state["model"] in (model["id"], "auto") else []) + [
                task for task, value in self.task_models.items() if value == model["id"]]
            estimate = "On this computer. " + ("Ari uses it for " + ", ".join(jobs) + "." if jobs else "Ari doesn’t use it yet.")
        say = box(icons.image("circle-check" if model.get("have") else "memory-stick", pixel_size=18), text(estimate, "body", wrap=True, expand=True), spacing=12)
        say.add_css_class("ari-model-fit")
        self.page.append(say)
        self.section("What it can do")
        labels = {"chat": ("Chat and writing", "Questions, emails, summaries"), "code": ("Code", "Reads and writes code"),
                  "pictures": ("Pictures", "Reads pictures"), "think": ("Thinking mode", "Works a problem through before answering")}
        capabilities = Gtk.Grid(column_homogeneous=True, column_spacing=12, row_spacing=12)
        for index, capability in enumerate(facts.get("can", [])):
            label, line = labels[capability]
            glyph = {"chat":"message-circle", "code":"code", "pictures":"image", "think":"lightbulb"}[capability]
            capabilities.attach(Card(box(icons.image(glyph, pixel_size=18), box(text(label, "title-2"), text(line, "caption", wrap=True), vertical=True, spacing=1), spacing=12)),
                                0 if self.phone else index, index if self.phone else 0, 1, 1)
        self.page.append(capabilities)
        self.section("Details")
        context = model.get("context") or facts.get("context", 0)
        details = [("Reads at once", f'About {round(context / 650):,} pages' if context else "Not specified"),
                   ("Languages", facts.get("languages", "See the maker's model card")),
                   ("Licence", model.get("licence") or facts.get("licence", "See the model card")),
                   ("Released", facts.get("released", "See the model card")),
                   ("Made by", facts.get("by", "See the model card")),
                   ("Privacy", "Runs on this computer. Nothing you say to it leaves")]
        detail_grid = Gtk.Grid(column_homogeneous=True, column_spacing=24, row_spacing=18)
        for index, (label, value) in enumerate(details):
            glyph = ("book-open", "languages", "scale", "calendar", "building-2", "house")[index]
            detail_grid.attach(box(icons.image(glyph, pixel_size=18), box(text(label, "caption"), text(value, "body", wrap=True), vertical=True, spacing=2), spacing=14),
                               0 if self.phone else index % 2, index if self.phone else index // 2, 1, 1)
        self.page.append(Card(detail_grid))

    def family_actions(self):
        model = self.selected_family_model()
        if model is None:
            return
        identity = model["id"]
        if self.removing_model == identity:
            remaining = [item for item in self.data["local_models"] if item.get("have") and item["id"] != identity]
            selected = self.model()
            if selected["id"] == "auto":
                active = next((item for item in self.data["local_models"] if item.get("have")), None)
                if active:
                    selected = self.model(active["id"])
            replacement = (remaining[0]["n"] if remaining else "no local model until you choose or download one") if selected["id"] == identity else selected["n"]
            jobs = [name for name, value in self.task_models.items() if value == identity]
            confirm = box(box(self.model_mark(model, 36), box(text(f'Remove {model["n"]}?', "title-2", wrap=True),
                          text(f'Frees {gb(model["sz"])}. Your chats stay as they are.', "caption", wrap=True),
                          vertical=True, hexpand=True, spacing=1),
                          self.icon_button("x", "Close", self.cancel_remove), spacing=12), vertical=True, spacing=10)
            labels = {"code":"Code", "hard":"Hard problems", "long":"Long reading", "writing":"Writing"}
            if selected["id"] == identity:
                confirm.append(self.removal_fallback_row("Everything", f'Ari will use {replacement} instead', "sparkles"))
            for task in jobs:
                confirm.append(self.removal_fallback_row(labels.get(task,task), "Goes back to the same as everything", "code" if task == "code" else "sliders-horizontal"))
            confirm.append(box(icons.image("download", pixel_size=14), text("You can download it again any time.", "caption", wrap=True), spacing=7))
            cancel = self.button("Cancel", self.cancel_remove, size="bar")
            cancel.bar_span = (600, 12, 12)
            self.bar.show_bar([SPACER, BarWidget(cancel),
                              BarAction("trash-2", "Remove", on_activate=lambda: self.remove_model(identity), primary=True, danger=True, keep_label=True)], fill=self.phone)
            self.bar.grow("remove-model", confirm)
        elif model.get("have"):
            self.bar.show_bar([BarAction("trash-2", "Remove", on_activate=lambda: self.begin_remove(identity), primary=True, danger=True, fill=self.phone, keep_label=True)], fill=self.phone)
        elif model.get("dl") is not None:
            self.bar.show_bar([BarAction("", label=f'{round(model["dl"])}%', sensitive=False),
                              BarAction("pause", "Pause", on_activate=lambda: self.pause_download(identity)),
                              BarAction("x", "Cancel", on_activate=lambda: self.pause_download(identity, cancel=True))],
                              head=ProgressLine(model["dl"] / 100, label="Downloading"), fill=self.phone)
        elif identity in self.paused_models:
            self.bar.show_bar([BarAction("play", "Resume", on_activate=lambda: self.resume_download(identity)),
                              BarAction("x", "Cancel", on_activate=lambda: self.cancel_download(identity))], fill=self.phone)
        elif self.fitting(model)[0] == "no":
            self.bar.show_bar([BarAction("", label="Too big for this computer", sensitive=False)])
        else:
            self.bar.show_bar([BarAction("download", "Download · " + gb(model["sz"]), on_activate=lambda: self.download(identity), primary=True, fill=self.phone, keep_label=True)], fill=self.phone)

    def removal_fallback_row(self, title, subtitle, glyph):
        row = box(icons.image(glyph, pixel_size=18), box(text(title, "title-2"), text(subtitle, "caption", wrap=True),
                  vertical=True, hexpand=True), spacing=12)
        row.add_css_class("ari-removal-fallback")
        return row

    def begin_remove(self, identity):
        self.removing_model = identity
        self.render_bar()

    def cancel_remove(self):
        self.removing_model = ""
        self.render_bar()

    def remove_model(self, identity):
        model = next(item for item in self.data["local_models"] if item["id"] == identity)
        def removed(result):
            if self.closed:
                return
            if result == (True,):
                model["have"] = False
                self.task_models = {task: value for task, value in self.task_models.items() if value != identity}
                self.removing_model = ""
                self.render()
                if self.state["model"] == identity:
                    self.state["model"] = "auto"
                    if not self.fixture:
                        self.retry_models()
                self.notify("Removed " + model["n"])
            else:
                self.notify("The model could not be removed", error=True)
        if self.fixture:
            removed((True,))
        else:
            self.daemon.call("RemoveModel", "(s)", (identity,), removed)

    def pause_download(self, identity, cancel=False):
        request = next((request for request, model in self.downloads.items() if model == identity), None)
        if not cancel:
            self.paused_models.add(identity)
        if request:
            self.daemon.call("CancelDownload" if cancel else "Stop", "(s)", (request,))
        elif self.fixture:
            model = next(item for item in self.data["local_models"] if item["id"] == identity)
            model["paused_progress"] = model.pop("dl", 0)
            if cancel:
                self.cancel_download(identity)
            else:
                self.render()

    def resume_download(self, identity):
        self.paused_models.discard(identity)
        self.download(identity)

    def cancel_download(self, identity):
        self.paused_models.discard(identity)
        if not self.fixture:
            self.daemon.call("RemoveModel", "(s)", (identity,), lambda result: self.retry_models())
        else:
            model = next(item for item in self.data["local_models"] if item["id"] == identity)
            model.pop("dl", None)
            self.render()


    def section(self, title, subtitle=""):
        # v71 phone: the section's label stacks above its explanation.
        heading = ari_text(title, "section", "title-2")
        heading.set_hexpand(True)
        line = box(heading, vertical=self.phone and self.state["view"] != "models", spacing=2 if self.phone else 10)
        if self.state["view"] == "models":
            line.get_first_child().add_css_class("ari-models-section")
        if subtitle:
            line.append(text(subtitle, "caption", wrap=self.state["view"] != "models"))
        line.set_margin_top(20)
        self.page.append(line)

    def model_card(self, model, recommended=False):
        card = named(ari_surface("ari-hero-surface" if recommended else "ari-model-surface", Card), "model-" + model["id"])
        if hasattr(lumaui_tokens, "ARI"):
            card.set_spacing(lumaui_tokens.ARI["catalog"]["content_gap"])
        information = box(vertical=True, spacing=6, hexpand=True)
        if recommended:
            if hasattr(lumaui_tokens, "ARI"):
                information.set_spacing(lumaui_tokens.ARI["hero"]["information_gap"])
            information.append(ari_text("Recommended for this computer", "recommended", "label"))
        information.append(ari_text(model["n"], "hero-title" if recommended else "model-title", "assistant-title" if recommended else "title-2"))
        if not recommended:
            information.append(text(model["by"], "caption"))
        if recommended:
            description = ari_text("The largest model that runs well here. " + ", ".join(model["good"]) + ".", "description", "body", wrap=True)
            if hasattr(lumaui_tokens, "ARI"):
                description.set_margin_top(lumaui_tokens.ARI["hero"]["description_top"])
            information.append(description)
        else:
            capabilities = Adw.WrapBox(child_spacing=4, line_spacing=4)
            for capability in model["good"]:
                chip = ari_text(capability, "capability", "caption")
                if hasattr(lumaui_tokens, "ARI"):
                    chip.add_css_class("ari-capability")
                capabilities.append(chip)
            capability_labels = capabilities
        fit, label = model_fit(model, self.data["computer"]) if self.fixture else ("good", "")
        fitting = box(vertical=True, spacing=5)
        if self.fixture:
            # TODO(kit-request ari-03): DT2 SegmentBar for system/model segments.
            fitting.append(ProgressLine(min(1, model["mem"] / self.data["computer"]["mem"]),
                           tone="good" if fit == "good" else "danger" if fit == "no" else "accent", label="Memory fit"))
            fitting.append(ari_text(label, "fit", "label"))
        if model.get("mem"):
            description = f'Needs {model["mem"]} GB'
            if model.get("wps"):
                description += f' · about {model["wps"]} words a second'
            fitting.append(ari_text(description, "subtitle", "caption", wrap=True))
        if model.get("have"):
            action = box(icons.image("check", pixel_size=14), connection("On this computer"), spacing=6)
            action.add_css_class("ari-connection")
        elif model.get("dl") is not None:
            action = box(ProgressLine(model["dl"] / 100, label="Downloading"),
                         text(f'{gb(model["sz"] * model["dl"] / 100)} of {gb(model["sz"])}', "caption"),
                         vertical=True, spacing=5)
        else:
            action = self.button(f'Needs {model["mem"]} GB' if fit == "no" else "Get · " + gb(model["sz"]),
                                 lambda value=model["id"]: self.download(value), "download" if fit != "no" else None,
                                 primary=recommended, sensitive=fit != "no", name="get-" + model["id"],
                                 size="bar" if recommended and fit != "no" else "small")
        if recommended:
            gap = lumaui_tokens.ARI["hero"]["gap"] if hasattr(lumaui_tokens, "ARI") else 20
            grid = Gtk.Grid(column_spacing=gap, row_spacing=gap)
            mark = self.model_mark(model, 56)
            for child in (mark, information, fitting, action):
                child.set_valign(Gtk.Align.CENTER)
            self.hero_view = (grid, mark, information, fitting, action)
            self._reflow_hero()
            card.append(grid)
            return card
        card.append(box(self.model_mark(model, 36), information, spacing=12))
        card.append(capability_labels)
        card.append(fitting)
        card.append(action)
        return card

    def render_bar(self):
        view = self.state["view"]
        phone = self.phone
        key = (view, phone, self.state['model'], repr(self.model()), repr(self.effort_options(self.model()['id'])))
        if view == 'chat' and key == self._bar_key and self.entry.widget.get_parent() is not None:
            self.entry.set_busy(self.state['busy'])
            self.entry.set_text(self.draft)
            return
        self._bar_key = key if view == 'chat' else None
        focus = self.get_focus()
        if focus is not None and focus.is_ancestor(self.bar):
            self.set_focus(None)
        (self.bar.add_css_class if view == "models" else self.bar.remove_css_class)("ari-model-actions")
        if view == "chat":
            model = self.model()
            local = model.get("local", True)
            # v71: one row. Where it runs (house or cloud), the field carrying Attach, then Send.
            self.entry = BarEntry(kind="compose", placeholder="Ask Ari" if phone else "Ask, or tell Ari what to do",
                tools=[BarAction("paperclip", tooltip="Attach", on_activate=self.attach)], text=self.draft,
                grows=False, busy=self.state["busy"], on_change=lambda value: setattr(self, "draft", value),
                on_submit=self.send, on_stop=self.stop, span="regular")
            where = ("Auto" if model["id"] == "auto" else model["n"]) + (
                (", on this phone" if phone else " · stays on this computer") if local
                else (", sent to " if phone else " · sent to ") + model.get("via", "the cloud"))
            # On a phone the house grows the bar into the model picker; on a computer it opens it by the button.
            home = BarAction("house" if local else "cloud", tooltip=where,
                             on_activate=None if phone else self.model_picker,
                             panel=self.model_panel if phone else None, key="model")
            controls = [home]
            options = self.effort_options(model["id"])
            if options:
                controls.append(BarAction("sliders-horizontal", tooltip="Adjust effort", key="effort",
                    active=options["selected"] != options["default"],
                    on_activate=None if phone else self.show_effort,
                    panel=(lambda: self.effort_panel(model["id"])) if phone else None))
            self.bar.show_bar([*controls, self.entry], fill=phone)
            self.model_button = named(self.bar.bar_row.get_first_child(), "model-picker-button")
            named(self.entry.widget, "composer")
            # The kit's Stop glyph is smaller than Send; its slot in Ari's
            # single-line composer stays the same size and alignment.
            key_size = int(lumaui_tokens.BAR['entry']['send'])
            self.entry.widget.key.set_size_request(key_size, key_size)
            self.entry.widget.key.set_valign(Gtk.Align.CENTER)
        elif view == "activity":
            filters = [(value, value) for value in ("All", "Files", "People", "Apps", "Settings")]
            if phone:
                # v71 .arimod2: the filter is one control that grows the bar into its chips.
                self.bar.show_bar([BarAction("list-filter", self.state["filter"], dropdown=True, key="filter",
                                             panel=lambda: PanelChoices(filters, selected=self.state["filter"],
                                                                        on_choose=self.filter_activity))], fill=True)
            else:
                self.bar.show_bar([], modes=ModeSwitch(filters, current=self.state["filter"],
                                                       on_change=self.filter_activity, label="Show"))
        else:
            if self.model_family:
                self.family_actions()
                return
            if self.models_tab == "yours":
                self.bar.hide_bar()
                return
            search = BarSearch("Search models", text=self.state["q"], on_change=self.search_models, keep=phone)
            self.bar.show_bar([search], fill=phone)
            named(search.widget, "search-models")

    def model_panel(self):
        """v71 aripick2: the model picker as the bar's grown panel on a phone."""
        rows = []

        def choice(identity, name, subtitle):
            if identity == "more-chatgpt":
                rows.append(PanelRow(name, icon="cloud", on_activate=self.show_chatgpt))
                return
            row = ModelChoice(identity, name, subtitle, lambda: self.model_mark(self.model(identity), 28),
                              self.model(identity).get("local", True), identity == self.state["model"],
                              lambda: self.use_model(identity),
                              adjust=(lambda: self.show_effort(identity)) if self.effort_options(identity) else None)
            rows.append(row.menu_widget(self.bar.fold))
        for identity, name, subtitle in self.picker_choices():
            if identity is None:
                rows.append(name)
            else:
                choice(identity, name, subtitle)
        rows.append(named(PanelRow("Add a provider", icon="plus", subtitle=self.add_provider_note(),
                                   on_activate=self.provider_sheet), "add-provider"))
        rows.append(None)
        rows.append(named(PanelRow("Get more models…", icon="download", on_activate=self.get_more_models), "more-models"))
        if not self.fixture:
            rows.append(named(PanelRow("ChatGPT account and usage", icon="cloud", on_activate=self.show_chatgpt), "chatgpt-settings"))
            rows.append(named(PanelRow("Permissions and autonomy", icon="settings", on_activate=self.show_permissions), "permissions-settings"))
        return named(panel_list(rows, label="Models"), "model-picker")

    def entry_text(self):
        return self.entry.text

    def _compose_key(self, _controller, key, _code, modifiers):
        if key == Gdk.KEY_Return and not modifiers & Gdk.ModifierType.SHIFT_MASK:
            self.send(self.entry_text())
            return True
        return False

    def filter_activity(self, category):
        self.state["filter"] = category
        self.render()

    def filter_models(self, category):
        self.state["store"] = category
        self.render()

    def search_models(self, entry):
        self.state["q"] = entry if isinstance(entry, str) else entry.get_text()
        self.state["sq"] = self.state["q"]
        self._rendering = True
        self.render_sidebar()
        self._rendering = False
        clear(self.page)
        clear(self.models_header)
        self.render_models()

    def close_picker(self):
        if self.popover:
            self.popover.close()
            self.popover = None

    def picker_choices(self):
        """The model picker's rows (v71 arPickHTML): (identity, name, subtitle), or (None, heading, None)."""
        installed = [model for model in self.data["local_models"] if model.get("have")]
        default = next((model for model in installed if model['id'] == self.state['model']), installed[0] if installed else None)
        yield ("auto", "Auto", f'Uses {default["n"]} here. Asks before using the cloud.' if default else
               "No local model installed. Choose a connected cloud model or get a local model.")
        if installed:
            yield (None, "On this computer", None)
        for model in self.data["local_models"]:
            if model.get("have"):
                # v70's sample speeds are fixture data, not host measurements.
                subtitle = ("Quick" if model.get("mem", 0) <= 12 else "Slower, more capable") if self.fixture else "On this computer"
                if model.get("mem"):
                    subtitle += " · " + gb(model['mem']) + " memory"
                yield (model["id"], model["n"], subtitle)
        for provider in self.data["providers"]:
            if provider.get("on") and provider["models"]:
                yield (None, "ChatGPT · your plan" if provider["id"] == "chatgpt" else provider["n"], None)
                for key, label in provider["models"][:6]:
                    row = next((item for item in self.chatgpt_data.get("models", []) if "chatgpt:" + item["id"] == key), {})
                    yield (key, label, row.get("description", ""))
                if len(provider["models"]) > 6 and provider["id"] == "chatgpt":
                    yield ("more-chatgpt", "More ChatGPT models…", "")

    def add_provider_note(self):
        return "OpenAI, OpenRouter, Anthropic or your own" if self.fixture else "ChatGPT or OpenRouter"

    def model_picker(self, task=None):
        if self.popover is not None:
            self.close_picker()
            return
        rows = []
        for identity, name, subtitle in self.picker_choices():
            if identity is None:
                rows.append(name)
                continue
            if identity == "more-chatgpt":
                rows.append(ModelAdd(name, "cloud", identity, self.show_chatgpt))
                continue
            rows.append(ModelChoice(identity, name, subtitle,
                                    lambda identity=identity: self.model_mark(self.model(identity), 28),
                                    self.model(identity).get("local", True), identity == self.state["model"],
                                    lambda identity=identity: self.set_task_model(task, "" if identity == "auto" else identity) if task else self.use_model(identity),
                                    adjust=(lambda identity=identity: self.show_effort(identity)) if self.effort_options(identity) else None))
        rows.extend([ModelAdd("Add a provider", "plus", "add-provider", self.provider_sheet, self.add_provider_note()),
                     None, ModelAdd("Get more models…", "download", "more-models", self.get_more_models)])
        menu = named(FloatingMenu(rows, label="Models"), "model-picker")
        menu.set_size_request(340, -1)
        self.popover = menu.popup(self.models_change_button if self.state["view"] == "models" else self.model_button, align="start")

    def choose_task(self):
        self.close_picker()
        menu = FloatingMenu([RichMenuItem(label, on_activate=lambda task=task: self.assign_task(task))
            for task, label in (("code", "Code"), ("hard", "Hard problems"), ("long", "Long reading"), ("writing", "Writing"))], label="Choose a task")
        self.popover = menu.popup(self.models_task_button)

    def assign_task(self, task):
        self.close_picker()
        self.model_picker(task=task)

    def set_task_model(self, task, identity):
        self.close_picker()
        def saved(result):
            if self.closed:
                return
            if result == (True,):
                if identity:
                    self.task_models[task] = identity
                else:
                    self.task_models.pop(task, None)
                self.render()
            else:
                self.notify("Choose an installed model or connect its cloud provider first", error=True)
        if self.fixture:
            saved((True,))
        else:
            self.daemon.call("SetTaskModel", "(ss)", (task, identity), saved)

    def get_more_models(self):
        self.models_tab, self.model_family = "store", None
        self.show_models()

    def effort_options(self, identity):
        if identity == "auto":
            return None
        if identity.startswith("chatgpt:"):
            row = next((item for item in self.chatgpt_data.get("models", []) if "chatgpt:" + item["id"] == identity), {})
            levels = [entry["effort"] for entry in row.get("supported_reasoning_levels", [])]
            if not levels:
                return None
            default = row.get("default_reasoning_level")
            selected = self.chatgpt_data.get("efforts", {}).get(row["id"])
            if selected not in levels:
                selected = default
            return {"levels": levels, "default": default, "selected": selected, "name": row["name"]}
        model = next((item for item in self.data["local_models"] if item["id"] == identity), {})
        if model.get("thinking"):
            return {"levels": ["off", "on"], "default": "off", "selected": "on" if model.get("thinking_enabled") else "off", "name": model["n"], "local": True}
        return None

    def effort_items(self, identity):
        options = self.effort_options(identity)
        if not options:
            return []
        if options.get("local"):
            return [RichMenuItem("Think before answering", subtitle="Better on hard questions. Slower",
                toggle=options["selected"] == "on", on_toggle=lambda value: self.set_effort(identity, "on" if value else "off"))]
        return [RichMenuItem(effort_label(level)[0], subtitle=effort_label(level)[1] + (" · Default" if level == options["default"] else ""),
                            checked=level == options["selected"], on_activate=lambda level=level: self.set_effort(identity, level))
                for level in options["levels"]]

    def effort_panel(self, identity):
        options = self.effort_options(identity)
        content = box(text(options["name"], "title-2"), vertical=True, spacing=8)
        for item in self.effort_items(identity):
            content.append(item.menu_widget(lambda: None))
        return named(content, "effort-panel")

    def show_effort(self, identity=None):
        identity = identity or self.state["model"]
        if not self.effort_options(identity):
            return
        self.close_picker()
        if self.phone:
            self.bar.grow("effort", self.effort_panel(identity))
        else:
            menu = named(FloatingMenu(self.effort_items(identity), label=self.effort_options(identity)["name"]), "effort-popover")
            if self.state["view"] == "models":
                anchor = self.models_change_button
            else:
                anchor = self.model_button.get_next_sibling() if identity == self.state["model"] else self.model_button
            self.popover = menu.popup(anchor or self.model_button, align="start")

    def set_effort(self, identity, level):
        self.close_picker()
        options = self.effort_options(identity)
        if not options or level not in options["levels"]:
            return
        account = self.chatgpt_data.get("active")
        def saved(result):
            if self.closed:
                return
            if isinstance(result, Exception) or not result or not result[0]:
                self.notify("The effort setting could not be saved", error=True)
                return
            if options.get("local"):
                model = next(item for item in self.data["local_models"] if item["id"] == identity)
                model["thinking_enabled"] = level == "on"
            elif account == self.chatgpt_data.get("active"):
                self.chatgpt_data.setdefault("efforts", {})[identity.removeprefix("chatgpt:")] = level
            self.render()
        if self.fixture:
            saved((True,))
        elif options.get("local"):
            self.daemon.call("SetLocalThinking", "(sb)", (identity, level == "on"), saved)
        else:
            self.daemon.call("SetChatGPTEffort", "(ss)", (identity.removeprefix("chatgpt:"), level), saved)

    def show_models(self):
        if self.popover:
            self.close_picker()
        self.state["view"] = "models"
        self.render()

    def use_model(self, identity):
        if self.popover:
            self.close_picker()
        self.route_generation += 1
        token = self.route_generation
        def current():
            return not self.closed and token == self.route_generation
        if self.fixture:
            self.state["model"] = identity
            self.render()
            model = self.model(identity)
            self.notify(f'{model["n"]}: stays on this computer' if model.get("local") else f'{model["n"]}: conversations go to {model["via"]}')
        elif identity.startswith("chatgpt:"):
            def selected(result):
                if not current():
                    return
                if isinstance(result, Exception) or not result[0]:
                    self.notify("ChatGPT could not select that model. Open ChatGPT account settings.", error=True)
                    return
                def enabled(result):
                    if not current():
                        return
                    if not isinstance(result, Exception) and result[0]:
                        self.state["model"] = identity
                        self.render()
                    else:
                        self.notify("ChatGPT plan usage could not be enabled", error=True)
                self.daemon.call("SetBrain", "(s)", ("chatgpt",), enabled)
            self.daemon.call("SetChatGPTModel", "(s)", (identity[len("chatgpt:"):],), selected)
        elif identity == "auto" or any(model["id"] == identity for model in self.data["local_models"]):
            if identity == "auto":
                def local(result):
                    if not current():
                        return
                    if not isinstance(result, Exception) and result[0]:
                        self.state["model"] = "auto"
                        self.render()
                    else:
                        self.notify("The local model could not be enabled", error=True)
                self.daemon.call("SetBrain", "(s)", ("local",), local)
                return
            def selected(result):
                if not current():
                    return
                if result == (True,):
                    def local(brain):
                        if not current():
                            return
                        if not isinstance(brain, Exception) and brain[0]:
                            self.state["model"] = identity
                            self.render()
                        else:
                            self.notify("The local model could not be enabled", error=True)
                    self.daemon.call("SetBrain", "(s)", ("local",), local)
                else:
                    self.notify("The model could not be selected", error=True)
            self.daemon.call("SetActiveModel", "(s)", (identity,), selected)
        else:
            def selected(result):
                if not current():
                    return
                if isinstance(result, Exception) or not result[0]:
                    self.notify(result[1] if not isinstance(result, Exception) else "The cloud model could not be selected", error=True)
                    return
                def enabled(brain):
                    if not current():
                        return
                    if not isinstance(brain, Exception) and brain[0]:
                        self.state["model"] = identity
                        self.render()
                    else:
                        self.notify(brain[1] if not isinstance(brain, Exception) else "The cloud model could not be enabled", error=True)
                self.daemon.call("SetBrain", "(s)", ("openrouter",), enabled)
            self.daemon.call("SetCloudModel", "(sb)", (identity, False), selected)

    def provider_sheet(self, identity=None):
        if self.popover:
            self.close_picker()
        if not self.fixture and identity == "chatgpt":
            self.show_chatgpt()
            return
        if not self.fixture and identity not in (None, "openrouter"):
            self.notify("This provider is not available", error=True)
            return
        if not self.fixture and not self.data["provider_choices"]:
            self.notify("Providers are still loading")
            return
        if not self.fixture and identity == "openrouter" and not any(p["id"] == "openrouter" for p in self.data["provider_choices"]):
            self.notify("OpenRouter models are still loading")
            return
        self.provider_generation += 1
        self.provider_state.update(step="key" if identity else "pick", prov=identity, key="", test=None, sel={})
        if not self.fixture and identity:
            provider = next(item for item in self.data["provider_choices"] if item["id"] == "openrouter")
            self.provider_state["sel"] = {key: index == 0 for index, (key, *_rest) in enumerate(provider["models"])}
        self._provider_render()

    def _provider_render(self):
        self.provider_field = None
        if self.modal:
            self._release_sheet_focus()
            self._close_modal(True)
            self.modal = None
        state = self.provider_state
        metrics = getattr(lumaui_tokens, "ARI", {}).get("sheet")
        sheet = named(box(vertical=True, spacing=0 if metrics else 12), "provider-sheet")
        sheet.add_css_class("ari-provider-sheet")
        if state["step"] == "pick":
            sheet.append(box(ari_text("Add a provider", "sheet-title", "title-1", expand=True), self.icon_button("x", "Close", self.close_sheet, size="small"), spacing=metrics["header_gap"] if metrics else 8))
            description = ari_text("Use a model that runs somewhere else. Ari sends the current conversation and the results of its tools to the provider.", "description", wrap=True)
            if metrics:
                description.set_margin_top(metrics["description_top"])
                description.set_margin_bottom(metrics["description_bottom"])
            sheet.append(description)
            choices = box(vertical=True, spacing=metrics["choices_gap"] if metrics else 0)
            for provider in self.data["provider_choices"]:
                connected = any(item["id"] == provider["id"] and item.get("on") for item in self.data["providers"])
                if metrics:
                    button = named(Gtk.Button(sensitive=not connected), "choose-provider-" + provider["id"])
                    button.add_css_class("ari-provider-choice")
                    button.connect("clicked", lambda _button, key=provider["id"]: self.provider_sheet(key))
                else:
                    button = self.button(provider["n"], lambda key=provider["id"]: self.provider_sheet(key), sensitive=not connected,
                                         name="choose-provider-" + provider["id"])
                button.set_child(box(self.model_mark(provider, 36),
                    box(ari_text(provider["n"], "provider-title", "title-2"), ari_text(provider["what"], "subtitle", "caption", wrap=True), vertical=True, hexpand=True),
                    connection("Connected", "ari-connected" if "ari_connected" in lumaui_tokens.TYPE_SCALE else "label") if connected else icons.image("chevron-right", pixel_size=metrics["choice_chevron"] if metrics else 16), spacing=metrics["choice_gap"] if metrics else 12))
                choices.append(button)
            sheet.append(choices)
        else:
            provider = next(item for item in self.data["provider_choices"] if item["id"] == state["prov"])
            sheet.append(box(self.icon_button("chevron-left", "Back", lambda: self.provider_sheet(), size="small"),
                             self.model_mark(provider, 28), ari_text(provider["n"], "sheet-title", "title-1", expand=True),
                             self.icon_button("x", "Close", self.close_sheet, size="small"), spacing=metrics["header_gap"] if metrics else 8))
            own = provider["id"] == "own"
            field = TextField("Server address" if own else "API key", value=state["url"] if own else state["key"],
                              placeholder=provider["key"], purpose="url" if own else "password",
                              on_changed=lambda value: self._provider_key(value, own))
            self.provider_field = field
            if metrics:
                field.set_margin_top(metrics["field_top"])
                if "ari_key" in lumaui_tokens.TYPE_SCALE:
                    apply_type(field.entry, "ari-key")
                if "ari_field_label" in lumaui_tokens.TYPE_SCALE:
                    apply_type(field.label, "ari-field-label")
            named(field.entry, "provider-key")
            sheet.append(field)
            if not own:
                hint = ari_text(f'Get one at {provider["where"]}. It stays in Luma’s keyring on this computer.', "subtitle", "caption", wrap=True)
                if metrics:
                    hint.set_margin_top(metrics["hint_top"])
                sheet.append(hint)
            if state["test"] == "ok":
                status_label = ari_text(f'Connected. {len(provider["models"])} models available', "status")
                status_label.add_css_class("ari-connection")
                status = box(icons.image("check", pixel_size=metrics["status_check"] if metrics else 14), status_label, spacing=metrics["status_gap"] if metrics else 7)
                status.add_css_class("ari-connection")
                if metrics:
                    status.set_margin_top(metrics["status_top"])
                    status.set_size_request(-1, metrics["status_height"])
                sheet.append(status)
                heading = ari_text("Show in the picker", "field-label", "label")
                if metrics:
                    heading.set_margin_top(metrics["models_heading_top"])
                    heading.set_margin_bottom(metrics["models_heading_bottom"])
                sheet.append(heading)
                models = box(vertical=True, spacing=metrics["models_gap"] if metrics else 0)
                for key, label, subtitle in provider["models"]:
                    checked = state["sel"].get(key, True)
                    if metrics:
                        button = named(Gtk.ToggleButton(active=checked), "provider-model-" + key)
                        button.add_css_class("ari-provider-model")
                        button.connect("clicked", lambda _button, identity=key: self._provider_toggle(identity))
                        check = box(width_request=metrics["checkbox_size"], height_request=metrics["checkbox_size"],
                                    halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
                        check.add_css_class("ari-provider-check")
                        if checked:
                            check.add_css_class("ari-provider-check-on")
                            check.append(icons.image("check", pixel_size=metrics["checkbox_icon"]))
                    else:
                        button = self.button(label, lambda identity=key: self._provider_toggle(identity), "check")
                        check = icons.image("check" if checked else "square")
                    button.set_child(box(check, box(ari_text(label, "result-title"), ari_text(subtitle, "result-subtitle", "caption"), vertical=True),
                                         spacing=metrics["model_gap"] if metrics else 10))
                    models.append(button)
                sheet.append(models)
            elif state["test"] == "run":
                status = box(Gtk.Spinner(spinning=True, width_request=metrics["status_spinner"] if metrics else 12,
                                         height_request=metrics["status_spinner"] if metrics else 12),
                             ari_text("Checking…", "status"), spacing=metrics["status_gap"] if metrics else 7)
                if metrics:
                    status.set_margin_top(metrics["status_top"])
                    status.set_size_request(-1, metrics["status_height"])
                sheet.append(status)
            elif state["test"] == "bad":
                status = box(icons.image("x", pixel_size=metrics["status_check"] if metrics else 14),
                             ari_text("That key didn’t work. Check it and try again", "status", wrap=True),
                             spacing=metrics["status_gap"] if metrics else 7)
                status.add_css_class("ari-provider-bad")
                if metrics:
                    status.set_margin_top(metrics["status_top"])
                    status.set_size_request(-1, metrics["status_height"])
                sheet.append(status)
            elif metrics:
                # v70 keeps the empty aptest slot before checking starts.
                sheet.append(box(height_request=metrics["status_height"], margin_top=metrics["status_top"]))
            actions = box(Gtk.Box(hexpand=True), self.button("Cancel", self.close_sheet, size="bar"),
                          spacing=metrics["actions_gap"] if metrics else 8)
            if metrics:
                actions.set_margin_top(metrics["actions_top"])
            self.connect_button = self.button("Add " + provider["n"] if state["test"] == "ok" else "Connect",
                                             self._provider_add if state["test"] == "ok" else self._provider_test,
                                             primary=True, sensitive=state["test"] == "ok" or own or len(state["key"]) > 6,
                                             name="provider-connect", size="bar")
            actions.append(self.connect_button)
            sheet.append(actions)
        if self.phone:
            # v71: on a phone a sheet rises from the bar's place, in the bar's frame.
            self.modal = self.bar.sheet(named(sheet, "provider-sheet-card"), on_cancel=self.close_sheet)
        else:
            card = Card(sheet, padded=False)
            card.set_size_request(min(460, max(1, self.get_width() - 32)), -1)
            named(card, "provider-sheet-card")
            layer_host = LayerHost.window_host(self)
            # ari-05: the top-anchored sheet API is not available yet.
            self.modal = layer_host.present_modal(card, on_cancel=self.close_sheet, drawer=False)
        if state["step"] == "key" and state["test"] is None:
            def focus_field():
                if not self.closed and self.provider_field is field and field.entry.get_root() is not None:
                    field.entry.grab_focus()
                return GLib.SOURCE_REMOVE
            GLib.idle_add(focus_field)

    def _provider_key(self, value, own):
        self.provider_state["url" if own else "key"] = value
        if hasattr(self, "connect_button"):
            self.connect_button.set_sensitive(own or len(value) > 6)

    def _provider_toggle(self, identity):
        state = self.provider_state
        if self.fixture:
            state["sel"][identity] = not state["sel"].get(identity, True)
        else:
            provider = next(item for item in self.data["provider_choices"] if item["id"] == "openrouter")
            state["sel"] = {key: key == identity for key, *_rest in provider["models"]}
        self._provider_render()

    def _provider_test(self):
        token = self.provider_generation
        self.provider_state["test"] = "run"
        self._provider_render()
        def finish():
            if self.modal and token == self.provider_generation and not self.closed:
                self.provider_state["test"] = "ok"
                self._provider_render()
            return GLib.SOURCE_REMOVE
        if self.fixture:
            GLib.timeout_add(900, finish)
        else:
            def connected(result):
                if self.closed or token != self.provider_generation:
                    return
                if isinstance(result, Exception) or not result[0]:
                    self.provider_state["test"] = "bad"
                    self._provider_render()
                    self.notify(result[1] if not isinstance(result, Exception) else "The key could not be checked", error=True)
                    return
                self.provider_state["key"] = ""
                for provider in self.data["providers"]:
                    if provider["id"] == "openrouter":
                        provider["on"] = True
                finish()
                self.notify(result[1])
            self.daemon.call("SetCloudKey", "(s)", (self.provider_state["key"],), connected)


    def _provider_add(self):
        if not self.fixture:
            provider = next(item for item in self.data["provider_choices"] if item["id"] == "openrouter")
            selected = next((key for key, *_rest in provider["models"] if self.provider_state["sel"].get(key, False)), None)
            self.close_sheet()
            if selected:
                self.use_model(selected)
            return
        state = self.provider_state
        provider = next(item for item in self.data["provider_choices"] if item["id"] == state["prov"])
        item = next((item for item in self.data["providers"] if item["id"] == provider["id"]), None)
        if item is None:
            item = {"id": provider["id"], "n": provider["n"], "mono": provider["mono"], "api": "your server" if provider["id"] == "own" else provider["n"] + " API",
                    "h": provider.get("h")}
            self.data["providers"].append(item)
        item.update(on=True, key="…" + (state["key"][-4:] or "9x2k"),
                    models=[[key, label] for key, label, _ in provider["models"] if state["sel"].get(key, True)])
        state["key"] = ""
        self.close_sheet()
        self.render()
        count = len(item["models"])
        self.notify(f'{provider["n"]} added. {count} model{"" if count == 1 else "s"} in the picker')

    def _release_sheet_focus(self):
        focus = self.get_focus()
        surface = getattr(self.modal, "card", self.modal)
        if self.modal and focus is not None and (focus is surface or focus.is_ancestor(surface)):
            self.set_focus(None)

    def _close_modal(self, quiet):
        if isinstance(self.modal, BarFrame):
            self.modal.close()
        else:
            self.modal.close(quiet=quiet)

    def close_sheet(self, *, quiet=False):
        self.provider_generation += 1
        self.provider_field = None
        self.provider_state["key"] = ""
        if self.modal:
            self._release_sheet_focus()
            self._close_modal(quiet)
            self.modal = None

    def edit_approval(self):
        if self.fixture:
            self.notify("The draft opens in Charlie")
        else:
            # Ari1 exposes Approve(bool), with no pending-change edit method.
            self.notify("Editing this change is not available", error=True)

    def answer(self, identity, approved):
        if self.fixture:
            self.source.answer(identity, approved)
            self.render()
            if approved:
                self.notify(f'Emailed {recipient_first_name(self.data["activity"][identity])}')
        else:
            if identity in self.pending_approvals:
                return
            receipt = self.data["activity"].get(identity)
            nodes = [node for node in receipt.get("nodes", []) if node["st"] == "ask"] if receipt else []
            if not nodes:
                return
            self.pending_approvals.add(identity)
            if approved:
                for node in nodes:
                    node["st"] = "run"
                self.render()
            def settled(result):
                self.pending_approvals.discard(identity)
                if self.closed or self.data["activity"].get(identity) is not receipt:
                    return
                success = not isinstance(result, Exception) and bool(result) and result[0]
                for node in nodes:
                    node["st"] = ("done" if approved else "undone") if success else "ask"
                self.render()
                if not success:
                    self.notify("The approval could not be sent", error=True)
            remember = bool(receipt.get('ask',{}).get('remember'))
            self.daemon.call("ApproveRemembered" if remember else "Approve", "(sb)", (identity, approved), settled)

    def undo(self, identity):
        if self.fixture:
            self.source.undo(identity)
            self.render()
            self.notify("Undone. The email stays sent" if any(node["k"] == "email" and node["st"] == "done"
                        for node in self.data["activity"][identity]["nodes"]) else "Undone")
        else:
            def finished(result):
                if self.closed:
                    return
                if result == (True,):
                    receipt = self.data["activity"].get(identity)
                    if receipt:
                        receipt["undo"] = "undone"
                        for node in receipt["nodes"]:
                            node["st"] = "undone"
                    self.render()
                    self.notify("Undone")
                else:
                    self.notify("Couldn’t undo", error=True)
            self.daemon.call("Undo", "(s)", (identity,), finished)

    def download(self, identity):
        if not self.fixture:
            model = next((item for item in self.data["local_models"] if item["id"] == identity), None)
            if model is None or model.get("have") or model.get("dl") is not None or identity in self.pending_model_installs:
                return
            self.pending_model_installs.add(identity)
            model["dl"] = 0
            self.render()
            def started(result):
                self.pending_model_installs.discard(identity)
                if self.closed:
                    return
                if isinstance(result, Exception) or not result or not result[0]:
                    model.pop("dl", None)
                    if not self.pending_model_installs:
                        self.pending_download_events.clear()
                    self.render()
                    self.notify("Download could not start", error=True)
                    return
                request = result[0]
                self.downloads[request] = identity
                early_events = self.pending_download_events
                self.pending_download_events = [(key, raw) for key, raw in early_events
                                                if key != request] if self.pending_model_installs else []
                for key, raw in early_events:
                    if key == request:
                        self.on_event(key, raw)
            self.daemon.call("InstallModel", "(s)", (identity,), started)
            return
        model = next(item for item in self.data["local_models"] if item["id"] == identity)
        if model.get("have") or model.get("dl") is not None:
            return
        model["dl"] = model.pop("paused_progress", 0)
        self.render()
        def tick():
            if self.closed or identity in self.paused_models or model.get("dl") is None:
                return GLib.SOURCE_REMOVE
            ready = self.source.advance_download(identity)
            self.render()
            if ready:
                self.notify(f'{model["n"]} is ready')
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE
        GLib.timeout_add(260, tick)

    def attach(self):
        # No new portal/store/backend write is introduced by this port.
        self.notify("The file picker opens" if self.fixture else "Attachments are not available", error=not self.fixture)

    def send(self, value):
        value = value.strip()
        if not value or self.state["busy"]:
            return
        chat = next((chat for chat in self.data["conversations"] if chat["id"] == self.state["cur"]), None)
        created = chat is None
        if chat is None:
            self.state["n"] += 1
            title = " ".join(value.split())
            if len(title) > 48:
                title = title[:48].rsplit(" ", 1)[0].rstrip('.,;:') + "…"
            if self.fixture:
                lower = value.lower()
                title = ("Tax documents" if "tax" in lower else "Quiet until 6" if any(word in lower for word in ("quiet", "focus", "disturb"))
                         else "A model for code" if "code" in lower else "Local models" if "local" in lower or "model" in lower else title)
            chat = {"id": "new-" + str(self.state["n"]), "t": title, "day": "Today", "msgs": []}
            self.data["conversations"].insert(0, chat)
            self.state["cur"] = chat["id"]
        chat["msgs"].append({"u": value, 'at': clock_time()})
        self._follow_end = True
        self.draft = ""
        self.state["busy"] = True
        self.render()
        if self.fixture:
            chat["msgs"].append({"think": "Thinking"})
            self.render()
            token = self.generation
            def finish():
                if self.closed or token != self.generation or not self.state["busy"]:
                    return GLib.SOURCE_REMOVE
                chat["msgs"].pop()
                model = "qwen8" if self.state["model"] == "auto" else self.state["model"]
                chat["msgs"].extend(scripted_reply(self.source, chat, value, model))
                self.state["busy"] = False
                self.render()
                return GLib.SOURCE_REMOVE
            GLib.timeout_add(900, finish)
            return
        generation = self.generation
        self.pending_request_events.clear()
        previous = "" if chat["id"].startswith("new-") else chat["id"]
        def started(result):
            if self.closed or generation != self.generation:
                if not isinstance(result, Exception):
                    self.daemon.call("Stop", "(s)", (result[1],))
                return
            if isinstance(result, Exception):
                self.state["busy"] = False
                self.pending_request_events.clear()
                # Ask has not supplied an ID, so the user's text is still a
                # draft. Restore it for retry and avoid a phantom sent turn.
                chat["msgs"].pop()
                self.draft = value
                if created:
                    self.data["conversations"].remove(chat)
                    self.state["cur"] = "new"
                reason = str(result)
                if "Disabled" in reason or "turned off" in reason:
                    message = "Ari is turned off in Settings"
                elif "timed out" in reason.lower():
                    message = "Ari took too long to respond. Try again"
                elif "not running" in reason.lower() or "ServiceUnknown" in reason:
                    message = "Ari is not running. Try again"
                else:
                    message = "Ari could not start this request. Try again"
                self.notify(message, error=True)
                self.render()
                self.entry.focus()
                return
            identity, request = result
            chat["id"] = identity
            self.state["cur"], self.request = identity, request
            early_events, self.pending_request_events = self.pending_request_events, []
            for event_request, raw in early_events:
                if event_request == request:
                    self.on_event(event_request, raw)
        self.daemon.call("Ask", "(ss)", (previous, value), started)

    def stop(self):
        self.generation += 1
        self.pending_request_events.clear()
        if self.request and not self.fixture:
            self.daemon.call("Stop", "(s)", (self.request,))
        self.request = ""
        self.state["busy"] = False
        for chat in self.data["conversations"]:
            chat["msgs"] = [message for message in chat.get("msgs", []) if "think" not in message]
        self.render()

    def keep(self, identity):
        def finished(result):
            if not self.closed and result == (True,):
                self.data["activity"][identity]["kept"] = True
                self.render()
        self.daemon.call("KeepStep", "(s)", (identity,), finished)

    def on_event(self, request, raw):
        if self.closed:
            return
        try:
            event = json.loads(raw)
        except (ValueError, TypeError):
            self.notify("Ari returned an unreadable event", error=True)
            return
        if not isinstance(event, dict):
            self.notify("Ari returned an unreadable event", error=True)
            return
        kind = event.get("type")
        if kind in ("approval_settled", "reverted"):
            identity = event.get("approval") or event.get("step")
            receipt = self.data["activity"].get(identity)
            # A timed revert belongs to its receipt even after done/navigation.
            if receipt and receipt.get("request") == request:
                for node in receipt["nodes"]:
                    node["st"] = "done" if event.get("approved") else "undone"
                receipt["undo"] = "none"
                receipt["kept"] = True
                self.render()
            elif not self.fixture and self.state["busy"] and not self.request:
                if len(self.pending_request_events) < 200:
                    self.pending_request_events.append((request, raw))
            return
        if request in self.downloads:
            identity = self.downloads[request]
            model = next((item for item in self.data["local_models"] if item["id"] == identity), None)
            if model and kind == "download":
                model["dl"] = event["done"] / max(1, event["total"]) * 100
            elif kind in ("installed", "error", "download_paused", "download_cancelled"):
                self.downloads.pop(request)
                if model:
                    if kind == "download_paused":
                        model["paused_progress"] = model.get("dl", 0)
                        self.paused_models.add(identity)
                    else:
                        self.paused_models.discard(identity)
                    model.pop("dl", None)
                    model["have"] = kind == "installed"
                if kind not in ("download_paused", "download_cancelled"):
                    self.notify("The model is ready" if kind == "installed" else event.get("message", "Download failed"), kind == "error")
            self.render()
            return
        if self.pending_model_installs and request != self.request and kind in ("download", "installed", "error"):
            # The installer worker can signal before InstallModel returns its ID.
            if len(self.pending_download_events) < 200:
                self.pending_download_events.append((request, raw))
            return
        if request != self.request:
            # Ari starts its worker before returning Ask's request ID. Hold
            # early bus events until that ID arrives, then replay only its own.
            if not self.fixture and self.state["busy"] and not self.request:
                if len(self.pending_request_events) < 200:
                    self.pending_request_events.append((request, raw))
            return
        chat = next((chat for chat in self.data["conversations"] if chat["id"] == self.state["cur"]), None)
        if chat is None:
            return
        messages = chat["msgs"]
        if kind in ("working", "tool"):
            messages[:] = [item for item in messages if "think" not in item]
            messages.append({"think": event.get("label", "Thinking")})
        elif kind == "text":
            messages[:] = [item for item in messages if "think" not in item]
            if not messages or not messages[-1].get("streaming"):
                messages.append({"a": "", "streaming": True})
            messages[-1]["a"] += event["text"]
        elif kind == "discard_text":
            messages[:] = [item for item in messages if not item.get("streaming")]
        elif kind == "done":
            messages[:] = [item for item in messages if "think" not in item and not item.get("streaming")]
            reply = {"a": event["text"]}
            metadata = event.get("meta") or {}
            if metadata.get("sources"):
                reply["sources"] = metadata["sources"]
            model = metadata.get("model_info")
            if isinstance(model, dict) and model.get("id") and isinstance(metadata.get("elapsed"), (int, float)):
                reply.update(m=model["id"], model_info=model, s=metadata["elapsed"])
            if event["text"]:
                messages.append(reply)
            self.state["busy"] = False
            self.request = ""
        elif kind in ("step", "approval"):
            identity = event["step"] if kind == "step" else event["approval"]
            previous = event.get("approval") if kind == "step" else ""
            if previous:
                self.data["activity"].pop(previous, None)
                messages[:] = [item for item in messages if item.get("ch") != previous]
            node, category = action_node(event["tool"], event["summary"], "ask" if kind == "approval" else "done")
            self.data["activity"][identity] = {"t": event["summary"], "at": clock_time(), "day": "Today", "cats": [category],
                "target": event.get("target"),
                "undo": "can" if event.get("undo") else "none", "conv": chat["id"], "request": request,
                "confirm_within": event.get("confirm_within"),
                "nodes": [node]}
            if kind == "approval":
                self.data["activity"][identity]["ask"] = {"subj": event["summary"], "body": ["", event.get("detail", ""), ""],
                    'permission':event.get('permission',''),'permission_label':event.get('permission_label','')}
            messages[:] = [item for item in messages if "think" not in item]
            messages.append({"ch": identity})
        elif kind == "needs_model":
            self.state["busy"] = False
            self.notify("Choose a model in Models", error=True)
        elif kind == "error":
            messages[:] = [item for item in messages if "think" not in item]
            self.notify(event.get("message", "The request failed"), error=True)
            self.state["busy"] = False
            self.request = ""
        self.render()

    def _capacity_ready(self, generation, value):
        if not self.closed and not self.fixture and generation == self.read_generation:
            self.data["computer"].update(value)
            self.render()
        return GLib.SOURCE_REMOVE

    def show_permissions(self):
        if self.fixture:
            self.notify('Permissions are available in the live application')
            return
        if self.daemon is None or getattr(self.daemon,'proxy',True) is None:
            self.pending_permissions = True
            return
        self.pending_permissions = False
        if self.permissions_dialog is not None:
            self.permissions_dialog.present(self)
            return
        from .settings import SettingsView
        dialog = Adw.Dialog(title='Permissions and autonomy', content_width=680, content_height=650)
        dialog.set_name('permissions-dialog')
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        settings = SettingsView(self)
        view.set_content(settings)
        dialog.set_child(view)
        dialog.connect('closed',lambda *_:setattr(self,'permissions_dialog',None))
        self.permissions_dialog = dialog
        settings.show('permissions')
        dialog.present(self)

    def retry_models(self):
        if self.closed or self.fixture:
            return
        self.models_loading, self.models_error = True, ""
        self.render()
        if self.daemon is None or self.daemon.proxy is None:
            if self.daemon:
                self.daemon.close()
            self.daemon = LiveDaemon(self._live_ready, self.on_event)
        else:
            # A method call activates an idle service again through D-Bus.
            self._live_ready(None)

    def _live_ready(self, error):
        if self.closed:
            return
        self.read_generation += 1
        read_generation = self.read_generation
        if error and self.data["local_models"] and not self.models_loading:
            # The host deliberately exits while idle. Keep a loaded catalogue
            # usable; the next action activates it and owner arrival refreshes.
            return
        self.models_loading, self.models_error = not bool(error), str(error) if error else ""
        if not self.fixture:
            def capacity_worker():
                GLib.idle_add(self._capacity_ready, read_generation, live_capacity())
            threading.Thread(target=capacity_worker, daemon=True, name="ari-capacity-read").start()
        if error:
            self.render()
            return
        if self.pending_permissions:
            self.show_permissions()
        navigation_generation = self.generation
        route_generation = self.route_generation
        def models_loaded(value):
            if self.closed or read_generation != self.read_generation:
                return
            try:
                if isinstance(value, Exception):
                    raise value
                data = json.loads(value[0])
                local_models = [dict(id=model["id"], n=model["name"], by=model.get("source", ""),
                    family=model.get("family", ""), parameters=model.get("parameters", ""), context=model.get("context", 0),
                    licence=model.get("licence", ""), thinking=model.get("thinking", False), thinking_enabled=model.get("thinking_enabled", False),
                    recommended=model["id"] == data.get("recommended"),
                    mono=model["name"][:1], sz=model["size"] / 1e9, mem=model.get("ram", 0), wps=0,
                    good=[], have=model.get("installed", False)) for model in data["models"]]
                if not local_models:
                    raise ValueError("The model catalogue is empty")
            except Exception as failure:
                self.models_loading, self.models_error = False, str(failure)
                self.render()
                return
            self.models_loading, self.models_error = False, ""
            self.task_models = data.get("task_models", {})
            hardware = data.get("hardware", {})
            self.data["computer"].setdefault("n", "This computer")
            if hardware.get("ram_gb"):
                self.data["computer"]["mem"] = hardware["ram_gb"]
            if hardware.get("gpu_name"):
                self.data["computer"]["gpu"] = hardware["gpu_name"]
            if route_generation == self.route_generation:
                self.live_cloud_model = data.get("cloud_model")
            self.data["local_models"] = local_models
            if route_generation == self.route_generation and (data.get("brain") != "chatgpt" or self.live_cloud_model):
                self.state["model"] = self.live_cloud_model["id"] if self.live_cloud_model else data.get("active") or "auto"
            self.render()
        def chats_loaded(value):
            if self.closed or read_generation != self.read_generation or isinstance(value, Exception):
                return
            import time
            rows = json.loads(value[0])
            # Model/account refreshes and daemon reconnection also reach here.
            # Keep loaded messages (including a turn currently streaming).
            existing = {chat["id"]: chat for chat in self.data["conversations"]}
            merged = []
            for row in rows:
                chat = existing.pop(row["id"], {"id": row["id"], "msgs": []})
                chat.update(t=row["title"], day=time.strftime("%b %-d", time.localtime(row["updated"])))
                merged.append(chat)
            for chat in existing.values():
                if chat["id"] == self.state["cur"] or chat["id"].startswith("new-"):
                    merged.insert(0, chat)
            self.data["conversations"] = merged
            self.render()
            if (rows and self.generation == navigation_generation and self.state["view"] == "chat"
                    and self.state["cur"] == "new" and not self.draft):
                self.open_chat(rows[0]["id"])
        def cloud_loaded(value):
            if self.closed or read_generation != self.read_generation or isinstance(value, Exception):
                return
            data = json.loads(value[0])
            choices = data.get("recommended", [])
            selected = next((item for item in data.get("models", []) if item["id"] == data.get("model")), None)
            shown = ([selected] if selected else []) + [item for item in choices if not selected or item["id"] != selected["id"]]
            self.data["provider_choices"] = [p for p in self.data["provider_choices"] if p["id"] != "openrouter"] + [{"id": "openrouter", "n": "OpenRouter", "mono": "O",
                "what": ", ".join(item["name"] for item in shown), "where": data.get("keys_page", "OpenRouter"), "key": "sk-or-…",
                "models": [[item["id"], item["name"], item.get("cost_label", "")] for item in shown]}]
            self.data["providers"] = [p for p in self.data["providers"] if p["id"] != "openrouter"] + [{"id": "openrouter", "n": "OpenRouter", "api": "OpenRouter", "on": bool(data.get("key_set")),
                                       "key": "", "models": [[item["id"], item["name"]] for item in shown]}]
            if route_generation == self.route_generation and data.get("brain") == "openrouter" and selected:
                self.live_cloud_model = selected
                self.state["model"] = selected["id"]
            self.render()
        def activity_loaded(value):
            if self.closed or read_generation != self.read_generation or isinstance(value, Exception):
                return
            import time
            for entry in json.loads(value[0]):
                identity = entry.get("step")
                if not identity or identity in self.data["activity"]:
                    continue
                timestamp = audit_timestamp(entry.get("at", entry.get("time")))
                day = time.strftime("%b %-d", time.localtime(timestamp)) if timestamp is not None else ""
                state = entry.get("step_state", "done")
                node, category = action_node(entry.get("tool", ""), entry.get("summary") or "Updated a preference",
                                             "undone" if state in ("undone", "reverted") else "done")
                self.data["activity"][identity] = {"t": entry.get("summary") or entry.get("tool", ""), "day": day,
                    "at": clock_time(timestamp) if timestamp is not None else "", "cats": [category],
                    "conv": entry.get("conversation", ""), "undo": "can" if entry.get("undo") and state == "done" else "none",
                    "nodes": [node]}
            self.render()
        self.daemon.call("Models", callback=models_loaded)
        def chatgpt_loaded(value):
            if not self.closed and read_generation == self.read_generation and not isinstance(value, Exception):
                self.chatgpt_loaded(json.loads(value[0]), route_generation)
            elif isinstance(value, Exception):
                self.chatgpt_metadata_requested = False
        refresh_capabilities = not self.chatgpt_metadata_requested
        self.chatgpt_metadata_requested = True
        self.daemon.call("ChatGPT", "(b)", (refresh_capabilities,), chatgpt_loaded)
        self.daemon.call("Cloud", "(b)", (False,), cloud_loaded)
        self.daemon.call("Activity", "(u)", (200,), activity_loaded)
        self.daemon.call("ListConversations", callback=chats_loaded)

    def show_chatgpt(self):
        if self.fixture:
            return
        self.close_picker()
        self.close_sheet(quiet=True)
        if self.chatgpt_dialog is None:
            from .chatgpt_settings import ChatGPTDialog
            self.chatgpt_dialog = ChatGPTDialog(self)
        self.chatgpt_dialog.present(self)

    def chatgpt_loaded(self, data, route_generation=None):
        if self.closed:
            return
        self.chatgpt_data = data
        active = next((item for item in data.get("accounts", []) if item["id"] == data.get("active")), {})
        models = [["chatgpt:" + item["id"], item["name"]] for item in data.get("models", [])]
        provider = {"id": "chatgpt", "n": "ChatGPT", "api": "ChatGPT", "on": bool(active.get("signed_in") and active.get("plan_enabled")),
                    "key": "", "note": active.get("label") or "Use your existing ChatGPT plan", "models": models}
        self.data["providers"] = [p for p in self.data["providers"] if p["id"] != "chatgpt"] + [provider]
        choice = {"id": "chatgpt", "n": "ChatGPT", "mono": "C", "what": "Use your existing ChatGPT plan", "models": []}
        self.data["provider_choices"] = [p for p in self.data["provider_choices"] if p["id"] != "chatgpt"] + [choice]
        if data.get("brain") == "chatgpt" and route_generation in (None, self.route_generation):
            self.state["model"] = "chatgpt:" + data.get("model", "")
        self.render()

    def copy(self, value):
        self.get_clipboard().set(value.replace("|", "\n") if self.fixture else value)
        self.notify("Copied")

    def notify(self, message, error=False):
        Toast.show(self.host, message, kind="error" if error else "done")

    def _closing(self, *_):
        if self.chatgpt_dialog:
            self.chatgpt_dialog._closed()
            self.chatgpt_dialog = None
        self.closed = True
        if self._scroll_handler is not None:
            self._scroll_clock.disconnect(self._scroll_handler)
            self._scroll_handler = None
            self._scroll_clock = None
        if self._capture_source is not None:
            self._capture_clock.disconnect(self._capture_source)
            self._capture_source = None
            self._capture_clock = None
        self.generation += 1
        self.set_focus(None)
        self.close_picker()
        self.close_sheet(quiet=True)
        # Closing the picker restores its anchor's focus. The window itself
        # is closing, so clear that restored focus before GTK disposes it.
        self.set_focus(None)
        if self.daemon:
            if self.request:
                self.daemon.call("Stop", "(s)", (self.request,))
            self.daemon.call("Release")
            self.daemon.close()
        return False


class AriApplication(Adw.Application):
    def __init__(self, source=None):
        self.source = source
        preview = source is not None or os.environ.get("LUMA_ARI_PREVIEW") == "1"
        super().__init__(application_id=FIXTURE_ID if preview else APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.add_main_option("close-preview", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Close only Ari's separate development preview", None)
        self.add_main_option("popover", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Open Ari at a compact size", None)
        self.add_main_option("settings", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "Open models, activity or permissions", "PAGE")

    def do_startup(self):
        Adw.Application.do_startup(self)
        Gtk.Window.set_default_icon_name(FIXTURE_ID if self.source is not None else APP_ID)
        install_appkit()
        install_lumaui()
        add_style_builder(lumaui_style.build)

    def do_activate(self):
        window = self.props.active_window
        created = window is None
        window = window or AriWindow(self, self.source)
        window.present()
        if created and window.state["view"] == "chat":
            window.entry.focus()

    def do_command_line(self, command_line):
        options = command_line.get_options_dict().end().unpack()
        if options.get("close-preview"):
            if self.get_application_id() != FIXTURE_ID:
                return 2
            for window in self.get_windows():
                window.close()
            return 0
        window = self.props.active_window
        created = window is None
        if options.get("popover") and window is not None and window.is_active():
            window.close()
            return 0
        window = window or AriWindow(self, self.source)
        if options.get("popover"):
            window.set_default_size(480, 620)
        elif options.get("settings") == 'permissions':
            window.show_permissions()
        elif options.get("settings") in ("models", "activity"):
            window.state["view"] = options["settings"]
            window.render()
        window.present()
        if created and window.state["view"] == "chat":
            window.entry.focus()
        return 0


def main(argv=None):
    GLib.set_application_name("Ari")
    source = source_from_environment()
    return AriApplication(source).run(sys.argv if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
