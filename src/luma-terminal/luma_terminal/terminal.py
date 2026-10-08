# SPDX-License-Identifier: Apache-2.0
"""Luma Terminal: VTE sessions composed with LumaUI.

The session tree and command history live only in memory. In fixture mode no
shell is spawned and no user store is opened.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from luma_appkit import (  # noqa: E402
    ActionCenter, ActionEditor, AddRow, AppWindow, BarAction, BarChip, BarContext,
    BarEntry, BarMenu, Command, CommandGroup, CommandRegistry, Island, MenuSection,
    RichMenuItem, RowAction,
    SPACER, ScrollView, Toast, ToastHost, add_style_sheet, apply_type, install_appkit,
    install_lumaui, lumaui_tokens, panel_list, type_font,
)
from luma_appkit.action_bubble import FloatingMenu  # noqa: E402
from luma_appkit.action_center import make_control, register_item  # noqa: E402
from luma_appkit import icons  # noqa: E402
from luma_appkit.media_style import colour  # noqa: E402

from .model import (
    PHONE_KEYS, Node, Pane, PhoneKey, Split, TerminalModel, fixture_branch, git_branch,
    history_suggestion, session_subtitle, short_path,
)
from .profiles import argv_for_profile, read_default_profile


APP_ID = "org.projectluma.Terminal"
FIXTURE_ENV = "LUMA_TERM_FIXTURE"
WIDGET_NAMES = ("tm-stage", "tm-pane", "tm-blocks", "tm-bar", "tm-sessions",
                "tm-new", "tm-split-right", "tm-split-down", "tm-keys", "tm-command", "tm-run")
#: Commands the ghost suggestion also knows (v71 `TCMDS`), after what was typed before.
COMMON_COMMANDS = ("cat", "cd", "clear", "date", "echo", "exit", "git status", "git log", "help",
                   "history", "ls", "ll", "lumafetch", "luma update", "pwd", "uname -a", "uptime", "whoami")


def fixture_from_environment() -> dict | None:
    path = os.environ.get(FIXTURE_ENV)
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), list):
        raise ValueError("Terminal fixture needs a sessions list")
    return data


def cwd_from_uri(uri: str | None) -> str | None:
    if not uri:
        return None
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        return None
    path = unquote(parsed.path)
    return path if path.startswith("/") else None


class TermKeys:
    """The phone's keys row, an item for `ActionCenter.show_bar` (v71 `.tmkeys`).

    It is Terminal's own surface: esc, tab, Ctrl C, the arrows and the symbols a phone keyboard
    hides, in a strip that scrolls sideways. `on_key(key)` hears each press.
    """

    def __init__(self, on_key, keys: tuple[PhoneKey, ...] = PHONE_KEYS) -> None:
        self.on_key, self.keys = on_key, keys


def _keys_strip(item: TermKeys, _size: str) -> Gtk.Widget:
    row = Gtk.Box()
    row.add_css_class("tm-keys-row")
    for key in item.keys:
        button = Gtk.Button(valign=Gtk.Align.CENTER, focus_on_click=False)
        button.add_css_class("tm-key")
        if key.icon:
            button.set_child(icons.image(key.icon))
        else:
            button.set_child(apply_type(Gtk.Label(label=key.label), "mono", weight=600))
        button.set_tooltip_text(key.name)
        button.update_property([Gtk.AccessibleProperty.LABEL], [key.name])
        button.connect("clicked", lambda _b, k=key: item.on_key(k))
        row.append(button)
    strip = Gtk.ScrolledWindow(hexpand=True, child=row, vscrollbar_policy=Gtk.PolicyType.NEVER,
                               hscrollbar_policy=Gtk.PolicyType.EXTERNAL, propagate_natural_height=True)
    strip.add_css_class("tm-keys")
    strip.set_name("tm-keys")
    strip.update_property([Gtk.AccessibleProperty.LABEL], ["Keys"])
    strip.bar_phone_wide = True  # the full span of the phone's bar, not the width of its keys
    return strip


register_item(TermKeys, _keys_strip)


def session_rows(model: TerminalModel, home: str, *, phone: bool, on_activate, on_close, on_rename,
                 on_new) -> list[object]:
    """The sessions list: one row per open session, then New session.

    A computer's menu opens with the "Sessions" heading; a phone's panel is the bar grown, so it
    has none. Each row names the folder and what is in it, renames in place, and closes (unless it
    is the only one).
    """
    rows: list[object] = [] if phone else ["Sessions"]
    for session in model.sessions:
        rows.append(RichMenuItem(
            session.display_name,
            icon="house" if session.display_name == "Home" else "folder",
            subtitle=session_subtitle(session, home),
            selected=session.id == model.current_id,
            trail=([RowAction("x", "Close session", lambda sid=session.id: on_close(sid))]
                   if len(model.sessions) > 1 else []),
            rename=True,
            on_rename=lambda name, sid=session.id: on_rename(sid, name),
            on_activate=lambda sid=session.id: on_activate(sid),
        ))
    rows.append(None)
    if phone:
        new = make_control(BarAction("plus", "New session", primary=True, keep_label=True,
                                     on_activate=on_new))
        new.set_hexpand(True)
    else:
        new = AddRow("New session", icon="plus", shortcut="Ctrl Shift T", on_activate=on_new)
    new.set_name("tm-new")
    rows.append(MenuSection(new))
    return rows


class TerminalWindow(AppWindow):
    def __init__(self, application: Adw.Application, fixture: dict | None = None) -> None:
        self.fixture = fixture
        self.profile = read_default_profile() if fixture is None else None
        first = fixture["sessions"][0] if fixture and fixture["sessions"] else {}
        self.display_home = fixture.get("home", first.get("cwd", "")) if fixture else str(Path.home())
        self.model = TerminalModel(first.get("cwd") or str(Path.home()), home=self.display_home)
        if first.get("name"):
            self.model.rename_session(self.model.current_id, first["name"])
        self.history: list[str] = list(fixture.get("history", [])) if fixture else []
        self.pane_views: dict[int, Gtk.Widget] = {}
        self.pane_scrolls: dict[int, Gtk.ScrolledWindow] = {}
        self.terminals: dict[int, Gtk.Widget] = {}
        self._spawn_generation: dict[int, int] = {}
        self._search_entry: Gtk.Entry | None = None
        self._selected_block: int | None = None
        self._live_selection_pane_id: int | None = None
        self._pane_close_buttons: dict[int, Gtk.Button] = {}
        self._hovered_panes: set[int] = set()
        self._action_widgets: dict[str, Gtk.Widget] = {}
        self._fetch_logo: Gtk.Widget | None = None
        self._fetch_layouts: dict[int, list[Gtk.Stack]] = {}
        self._session_menu: FloatingMenu | None = None
        self._command: BarEntry | None = None
        self._bar_kind = ""
        self._bar_key: tuple | None = None
        self._selected_block_data: dict | None = None
        self.block_widgets: dict[int, Gtk.Button] = {}
        super().__init__(application=application, app_id=APP_ID, title="Terminal", icon_name=APP_ID,
                         commands=self._commands(), default_width=960, default_height=620,
                         minimum_width=360, minimum_height=420)
        self.connect("notify::css-classes", self._refresh_vte_palettes)

        self.stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.stage.set_name("tm-stage")
        self.host = ToastHost(self.stage)
        self.set_body(self.host)
        self.center = ActionCenter().attach(self.host, over="frame", inset="edge")
        self.center.set_name("tm-bar")
        self._render_stage()
        self._show_prompt()
        self._install_shortcuts()
        if self.fixture is None:
            self.connect("map", lambda *_args: self._focus_active_terminal())
        # v71 tiers: under 560 the bar is the phone's keys, command line and sessions panel.
        self.tier_watch.connect("tier-changed", self._tier_changed)

    @property
    def _phone(self) -> bool:
        return self.tier == "phone"

    def _tier_changed(self, _watch, tier: str) -> None:
        self._sync_tier()
        # A phone's bar floats 34 above the foot (the home bar); a computer's sits at the pane's edge.
        self.center.attach(self.host, over="frame", inset="island" if tier == "phone" else "edge")
        self._bar_key = None  # the bar is drawn in the other shape
        data = self._selected_block_data
        if data is not None:
            self._show_selected_bar(data)
        else:
            self._show_prompt()

    def _sync_tier(self) -> None:
        phone = self._phone
        if self._fetch_logo is not None:
            self._fetch_logo.set_visible(not phone)
        self._sync_fetch_layouts(phone)

    def _sync_fetch_layouts(self, phone: bool) -> None:
        narrow = (phone and isinstance(self.model.current.root, Split)
                  and self.model.current.root.direction == "row")
        for pane in self.model.current.panes:
            for stack in self._fetch_layouts.get(pane.id, []):
                stack.set_visible_child_name("narrow" if narrow else "wide")

    def _commands(self) -> CommandRegistry:
        return CommandRegistry((
            CommandGroup("", (
                Command("term.new", "New session", self.new_session, "plus", shortcut=("Ctrl", "Shift", "T")),
                Command("term.split-right", "Split right", lambda: self.split("row"), "columns-2",
                        shortcut=("Ctrl", "Shift", "D")),
                Command("term.split-down", "Split down", lambda: self.split("col"), "rows-2",
                        shortcut=("Ctrl", "Shift", "E")),
                Command("term.find", "Find", self.find, "search", shortcut=("Ctrl", "Shift", "F")),
                Command("term.copy", "Copy", self.copy, "copy", shortcut=("Ctrl", "Shift", "C")),
                Command("term.paste", "Paste", self.paste, "clipboard", shortcut=("Ctrl", "Shift", "V")),
            )),
            CommandGroup("", (
                Command("term.zoom-in", "Zoom in", lambda: self.zoom(1), "zoom-in", shortcut=("Ctrl", "plus")),
                Command("term.zoom-out", "Zoom out", lambda: self.zoom(-1), "zoom-out", shortcut=("Ctrl", "minus")),
                Command("term.about", "About Terminal", self.about, "info"),
                Command("term.quit", "Quit Terminal", self.close, shortcut=("Ctrl", "Q")),
            )),
        ))

    def _install_shortcuts(self) -> None:
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

    def _key(self, _controller, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        key = (Gdk.keyval_name(keyval) or "").lower()
        if ctrl and shift:
            actions = {"t": self.new_session, "d": lambda: self.split("row"),
                       "e": lambda: self.split("col"), "w": self.close_active_pane,
                       "f": self.find, "c": self.copy, "v": self.paste}
            if key in actions:
                actions[key]()
                return True
        if ctrl and len(key) == 1 and key in "123456789":
            index = int(key) - 1
            if index < len(self.model.sessions):
                self.activate_session(self.model.sessions[index].id)
                return True
        if ctrl and key in ("plus", "equal", "minus"):
            self.zoom(-1 if key == "minus" else 1)
            return True
        return False

    def _render_stage(self) -> None:
        current = self.stage.get_first_child()
        if current is not None:
            self.stage.remove(current)
        root = self._node_widget(self.model.current.root)
        self.stage.append(root)
        for index, pane in enumerate(self.model.current.panes, start=1):
            self.pane_views[pane.id].set_name(f"tm-pane-{index}")
        self._mark_active_pane()
        self._sync_tier()
        if self.fixture is not None and len(self.model.current.panes) > 1:
            GLib.timeout_add(30, self._scroll_fixture_panes_to_bottom)

    def _scroll_fixture_panes_to_bottom(self) -> bool:
        for pane in self.model.current.panes:
            scroll = self.pane_scrolls.get(pane.id)
            if scroll is not None:
                adjustment = scroll.get_vadjustment()
                adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        return GLib.SOURCE_REMOVE

    def _mark_active_pane(self) -> None:
        split = len(self.model.current.panes) > 1
        for pane in self.model.current.panes:
            view = self.pane_views[pane.id]
            (view.add_css_class if split else view.remove_css_class)("compact")
            (view.add_css_class if split and pane.id == self.model.active_pane_id
             else view.remove_css_class)("tm-active")
            self._pane_close_buttons[pane.id].set_visible(split and pane.id in self._hovered_panes)

    @staticmethod
    def _detach(widget: Gtk.Widget) -> None:
        parent = widget.get_parent()
        if isinstance(parent, Gtk.Paned):
            if parent.get_start_child() is widget:
                parent.set_start_child(None)
            else:
                parent.set_end_child(None)
        elif isinstance(parent, Gtk.Box):
            parent.remove(widget)

    def _node_widget(self, node: Node) -> Gtk.Widget:
        if isinstance(node, Pane):
            if node.id not in self.pane_views:
                self.pane_views[node.id] = self._build_pane(node)
            view = self.pane_views[node.id]
            self._detach(view)
            return view
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL if node.direction == "row"
                          else Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        paned.add_css_class("tm-split")
        paned.set_wide_handle(False)
        paned.set_start_child(self._node_widget(node.first))
        paned.set_end_child(self._node_widget(node.second))
        paned.set_resize_start_child(True)
        paned.set_resize_end_child(True)
        paned.set_shrink_start_child(True)
        paned.set_shrink_end_child(True)

        def equalize() -> bool:
            size = paned.get_width() if node.direction == "row" else paned.get_height()
            if size <= 0:
                return GLib.SOURCE_CONTINUE
            paned.set_position((size - lumaui_tokens.WINDOW["gutter"]) // 2)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(10, equalize)
        return paned

    def _build_pane(self, pane: Pane) -> Gtk.Widget:
        island = Island()
        island.set_accessible_role(Gtk.AccessibleRole.REGION)
        island.set_name("tm-pane")
        island.set_hexpand(True)
        island.set_vexpand(True)
        if self.fixture is not None:
            contents = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            contents.set_valign(Gtk.Align.START)
            contents.add_css_class("tm-blocks")
            contents.set_name("tm-blocks")
            blocks = self.fixture.get("blocks", []) if pane.id == self.model.sessions[0].panes[0].id else []
            for index, block in enumerate(blocks, start=1):
                block_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                block_content.add_css_class("tm-block-content")
                if block.get("welcome"):
                    self._fill_welcome(block_content, block, pane.id)
                elif block.get("command"):
                    line = Gtk.Box()
                    line.add_css_class("tm-block-header")
                    line.append(apply_type(Gtk.Label(label=short_path(block.get("cwd", pane.cwd), self.display_home), xalign=0),
                                           "caption", muted=True))
                    command = apply_type(Gtk.Label(label=block["command"], xalign=0, hexpand=True,
                                                   ellipsize=Pango.EllipsizeMode.END), "mono")
                    command.add_css_class("tm-block-command")
                    line.append(command)
                    status = apply_type(Gtk.Label(label=block.get("duration") or block.get("status", "")), "mono")
                    status.add_css_class("tm-block-status")
                    line.append(status)
                    block_content.append(line)
                if not block.get("welcome"):
                    output = apply_type(Gtk.Label(label="\n".join(block.get("output", [])), xalign=0,
                                                  selectable=True, wrap=True,
                                                  wrap_mode=Pango.WrapMode.WORD_CHAR), "mono")
                    output.add_css_class("tm-block-output")
                    block_content.append(output)
                button = Gtk.Button(child=block_content)
                button.add_css_class("tm-block")
                button.set_name(f"tm-block-{index}")
                button.update_property([Gtk.AccessibleProperty.LABEL],
                                       [block.get("command") or "Welcome block"])
                button.connect("clicked", lambda _button, number=index: self._select_block(number))
                self.block_widgets[index] = button
                contents.append(button)
            if not blocks:
                empty = Gtk.Stack()
                empty.set_hhomogeneous(False)
                empty.set_vhomogeneous(False)
                empty.add_css_class("tm-empty")
                wide_empty = Gtk.Box()
                first = Gtk.Label(label="Type a command below. ")
                first.add_css_class("tm-empty-fragment")
                wide_empty.append(first)
                help_word = apply_type(Gtk.Label(), "mono")
                help_word.set_markup("<b>help</b>")
                help_word.add_css_class("tm-empty-help")
                wide_empty.append(help_word)
                last = Gtk.Label(label=" shows a few to try.")
                last.add_css_class("tm-empty-fragment")
                wide_empty.append(last)
                narrow_empty = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
                narrow_empty.set_markup("Type a command below. <b>help</b> shows a few to try.")
                narrow_empty.add_css_class("tm-empty-fragment")
                empty.add_named(wide_empty, "wide")
                empty.add_named(narrow_empty, "narrow")
                self._fetch_layouts.setdefault(pane.id, []).append(empty)
                contents.append(empty)
            scroll = ScrollView(contents)
            scroll.add_css_class("tm-pane-scroll")
            self.pane_scrolls[pane.id] = scroll
            self._attach_pane_content(island, scroll, pane.id)
            return island

        terminal = self._vte().Terminal()
        terminal.add_css_class("tm-vte")
        terminal.set_font(type_font("mono"))
        terminal.set_allow_hyperlink(True)
        if self.profile and self.profile.limit_scrollback:
            terminal.set_scrollback_lines(self.profile.scrollback_lines)
        terminal.set_hexpand(True)
        terminal.set_vexpand(True)
        terminal.connect("notify::current-directory-uri", lambda vte, _p, pid=pane.id:
                         self._cwd_changed(pid, vte.get_current_directory_uri()))
        terminal.connect("child-exited", lambda _vte, _status, pid=pane.id: self._child_exited(pid))
        terminal.connect("selection-changed", lambda vte, pid=pane.id:
                         self._selection_changed(pid, vte))
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda _f, pid=pane.id: self._focus_pane(pid))
        terminal.add_controller(focus)
        click = Gtk.GestureClick(button=1)
        click.connect("pressed", lambda gesture, _n, x, y, vte=terminal: self._open_link(gesture, vte, x, y))
        terminal.add_controller(click)
        terminal.connect("realize", lambda vte: self._set_vte_palette(vte))
        canvas = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        canvas.add_css_class("tm-vte-wrap")
        canvas.set_hexpand(True)
        canvas.set_vexpand(True)
        # Let the shared ActionCenter reserve its measured phone safe area for
        # VTE's viewport, so the cursor and final shell lines stay above the bar.
        scroll = Gtk.ScrolledWindow(child=terminal, hexpand=True, vexpand=True,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    vscrollbar_policy=Gtk.PolicyType.EXTERNAL)
        canvas.append(scroll)
        self.center.attach_scroller(scroll)
        self._attach_pane_content(island, canvas, pane.id)
        self.terminals[pane.id] = terminal
        self._spawn(pane, terminal)
        return island

    def _set_vte_palette(self, terminal: Gtk.Widget) -> None:
        # VTE paints its own cells, so GTK CSS alone cannot change their black
        # default. Resolve the same LumaUI role as the surrounding canvas.
        terminal.set_color_background(colour(terminal, "luma_terminal_pane"))
        terminal.set_color_foreground(colour(terminal, "luma_ink"))

    def _attach_pane_content(self, island: Island, content: Gtk.Widget, pane_id: int) -> None:
        pane = Gtk.Overlay(hexpand=True, vexpand=True)
        pane.set_child(content)
        # TODO(kit-request term-18-pane-corner-close.md): use Island's shared
        # curved corner control when it lands; this kit button keeps close usable.
        close = Gtk.Button(tooltip_text="Close pane · Ctrl Shift W",
                           halign=Gtk.Align.END, valign=Gtk.Align.START)
        close.add_css_class("lumaui-corner-button")
        close.add_css_class("tm-pane-close")
        close.set_child(icons.image("x"))
        close.update_property([Gtk.AccessibleProperty.LABEL], ["Close pane"])
        close.connect("clicked", lambda _button: self._close_pane(pane_id))
        close.set_visible(False)
        pane.add_overlay(close)
        island.append(pane)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_args: self._set_pane_hover(pane_id, True))
        motion.connect("leave", lambda *_args: self._set_pane_hover(pane_id, False))
        island.add_controller(motion)
        self._pane_close_buttons[pane_id] = close

    def _set_pane_hover(self, pane_id: int, hovered: bool) -> None:
        if hovered:
            self._hovered_panes.add(pane_id)
        else:
            self._hovered_panes.discard(pane_id)
        button = self._pane_close_buttons.get(pane_id)
        if button is not None:
            button.set_visible(hovered and len(self.model.current.panes) > 1)

    def _refresh_vte_palettes(self, *_args) -> None:
        for terminal in self.terminals.values():
            if terminal.get_realized():
                self._set_vte_palette(terminal)

    def _fill_welcome(self, content: Gtk.Box, block: dict, pane_id: int) -> None:
        # TODO(kit-request term-04-terminal-type-token.md): TY1 owns mono type.
        clock_value = ((os.environ.get("LUMAUI_CONFORM_NOW") or self.fixture.get("clock"))
                       if self.fixture else None)
        clock = datetime.fromisoformat(clock_value) if clock_value else datetime.now()
        greeting_text = "Luma 1.0 · bash 5.2 · " + clock.strftime("%A %-I:%M %p")
        greeting = Gtk.Stack()
        greeting.set_hhomogeneous(False)
        greeting.set_vhomogeneous(False)
        wide_greeting = apply_type(Gtk.Label(label=greeting_text, xalign=0, wrap=True,
                                             wrap_mode=Pango.WrapMode.WORD_CHAR), "mono")
        narrow_greeting = apply_type(Gtk.Label(label=greeting_text.replace(" · " + clock.strftime("%A"),
                                                                           " ·\n" + clock.strftime("%A"), 1),
                                               xalign=0, halign=Gtk.Align.START,
                                               valign=Gtk.Align.START), "mono")
        wide_greeting.add_css_class("tm-welcome-greeting")
        narrow_greeting.add_css_class("tm-welcome-greeting")
        greeting.add_named(wide_greeting, "wide")
        narrow_clip = Gtk.Overlay()
        narrow_clip.add_css_class("tm-greeting-narrow")
        narrow_clip.set_child(Gtk.Box())
        narrow_clip.add_overlay(narrow_greeting)
        narrow_clip.set_measure_overlay(narrow_greeting, False)
        narrow_clip.set_clip_overlay(narrow_greeting, True)
        greeting.add_named(narrow_clip, "narrow")
        self._fetch_layouts.setdefault(pane_id, []).append(greeting)
        content.append(greeting)
        fetch = Gtk.Box(spacing=26)
        fetch.add_css_class("tm-fetch")
        logo = apply_type(Gtk.Label(label=block.get("logo", ""), xalign=0, yalign=0), "mono")
        self._fetch_logo = logo
        logo.add_css_class("tm-fetch-logo")
        logo.set_size_request(110, -1)
        fetch.append(logo)
        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        details.add_css_class("tm-fetch-details")
        user = Gtk.Box()
        user.set_size_request(-1, 24)
        for part, css in ((block.get("user", "nick"), "tm-fetch-user"),
                          ("@", "tm-fetch-at"), (block.get("host", "thinkpad"), "tm-fetch-user")):
            label = apply_type(Gtk.Label(label=part), "mono")
            label.add_css_class(css)
            user.append(label)
        details.append(user)
        divider = apply_type(Gtk.Label(label="──────────────", xalign=0), "mono")
        divider.add_css_class("tm-fetch-divider")
        divider.set_size_request(-1, 19)
        details.append(divider)
        for label_text, value in block.get("facts", []):
            row = Gtk.Stack()
            row.set_hhomogeneous(False)
            row.set_vhomogeneous(False)
            wide = Gtk.Box()
            wide.set_size_request(-1, 22)  # TODO(kit-request term-04-terminal-type-token.md): output line metric.
            wide.add_css_class("tm-fetch-row")
            wide_title = apply_type(Gtk.Label(label=label_text), "mono")
            wide_title.add_css_class("tm-fetch-key")
            wide.append(wide_title)
            wide_answer = apply_type(Gtk.Label(label=" " * max(1, 9 - len(label_text)) + value,
                                               xalign=0, wrap=True,
                                               wrap_mode=Pango.WrapMode.WORD_CHAR), "mono")
            wide_answer.add_css_class("tm-fetch-value")
            wide.append(wide_answer)
            narrow = Gtk.Overlay()
            narrow.add_css_class("tm-fetch-row")
            narrow_title = apply_type(Gtk.Label(label=label_text), "mono")
            narrow_title.add_css_class("tm-fetch-key")
            narrow_title.set_halign(Gtk.Align.START)
            narrow_title.set_valign(Gtk.Align.START)
            # The preformatted value wraps at the pane's left edge.
            narrow_answer = apply_type(Gtk.Label(label=" " * 9 + value, xalign=0,
                                                 wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR), "mono")
            narrow_answer.add_css_class("tm-fetch-value")
            narrow.set_child(narrow_answer)
            narrow.add_overlay(narrow_title)
            narrow.set_measure_overlay(narrow_title, False)
            row.add_named(wide, "wide")
            row.add_named(narrow, "narrow")
            self._fetch_layouts[pane_id].append(row)
            details.append(row)
        strip = Gtk.Overlay()
        strip.add_css_class("tm-fetch-swatches")
        strip.set_size_request(-1, 12)
        swatches = Gtk.Box(halign=Gtk.Align.START)
        for index in range(8):
            swatch = Gtk.Box()
            swatch.add_css_class(f"tm-swatch-{index}")
            swatch.set_size_request(22, 12)
            swatches.append(swatch)
        strip.add_overlay(swatches)
        strip.set_measure_overlay(swatches, False)
        strip.set_clip_overlay(swatches, True)
        details.append(strip)
        fetch.append(details)
        content.append(fetch)

    @staticmethod
    def _vte():
        # Fixture captures never load VTE and never spawn a child process. The
        # real host has Vte 3.91; the conform server only needs fixture mode.
        gi.require_version("Vte", "3.91")
        from gi.repository import Vte
        return Vte

    def _spawn(self, pane: Pane, terminal: Gtk.Widget) -> None:
        shell = os.environ.get("SHELL") or "/bin/bash"
        if not Path(shell).is_file():
            shell = "/bin/bash"
        generation = self._spawn_generation[pane.id] = self._spawn_generation.get(pane.id, 0) + 1

        def spawned(_vte, pid, error, *_unused):
            if self._spawn_generation.get(pane.id) != generation:
                return
            if error is not None:
                Toast.show(self.host, f"Session could not start: {error.message}", kind="error")
            elif pid <= 0:
                Toast.show(self.host, "Session could not start", kind="error")

        if self.profile and self.profile.container not in ("", "session"):
            Toast.show(self.host, "This Ptyxis container profile needs its own launcher", kind="warning")
            return
        argv = argv_for_profile(self.profile, shell)
        terminal.spawn_async(
            pty_flags=self._vte().PtyFlags.DEFAULT,
            working_directory=pane.cwd,
            argv=argv,
            envv=None,
            spawn_flags=GLib.SpawnFlags.DEFAULT,
            child_setup=None,
            timeout=-1,
            cancellable=None,
            callback=spawned,
        )

    def _cwd_changed(self, pane_id: int, uri: str | None) -> None:
        cwd = cwd_from_uri(uri)
        if cwd and pane_id in self.pane_views:
            self.model.update_cwd(pane_id, cwd)
            if pane_id == self.model.active_pane_id:
                self._show_prompt()

    def _child_exited(self, pane_id: int) -> None:
        if pane_id in self.pane_views:
            Toast.show(self.host, "Session ended", kind="done")

    def _focus_pane(self, pane_id: int) -> None:
        if pane_id in {pane.id for pane in self.model.current.panes}:
            if pane_id != self.model.active_pane_id:
                self._reset_selection()
            self.model.activate_pane(pane_id)
            self._mark_active_pane()
            self._show_prompt()

    def _open_link(self, gesture: Gtk.GestureClick, terminal: Gtk.Widget, x: float, y: float) -> None:
        state = gesture.get_current_event_state()
        if not state & Gdk.ModifierType.CONTROL_MASK:
            return
        uri = terminal.check_hyperlink_at(x, y)
        if not uri:
            return
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        except GLib.Error as error:
            Toast.show(self.host, f"Link could not open: {error.message}", kind="error")

    def _show_prompt(self) -> None:
        session = self.model.current
        pane = self.model.active
        key = (self._phone, session.display_name, len(self.model.sessions), pane.id, pane.cwd)
        if self._bar_kind == "prompt" and key == self._bar_key:
            return  # the bar already says this; redrawing would drop what is being typed
        self._bar_kind, self._bar_key = "prompt", key
        self._selected_block_data = None
        if self._phone:
            self._show_phone_prompt()
        else:
            self._show_desktop_prompt()

    def _show_desktop_prompt(self) -> None:
        session = self.model.current
        self._command = None
        self.center.show_bar([
            BarMenu(session.display_name, self._open_sessions,
                    icon=self._session_icon(), name="Sessions"),
            BarAction("plus", on_activate=self.new_session, tooltip="New session"),
            BarAction("columns-2", on_activate=lambda: self.split("row"), tooltip="Split right"),
            BarAction("rows-2", on_activate=lambda: self.split("col"), tooltip="Split down"),
        ])
        children = []
        child = self.center.bar_row.get_first_child()
        while child is not None:
            children.append(child)
            child = child.get_next_sibling()
        self._action_widgets.clear()
        for widget, name in zip(children, ("tm-sessions", "tm-new",
                                           "tm-split-right", "tm-split-down")):
            widget.set_name(name)
            self._action_widgets[name] = widget
        children[0].set_hexpand(False)
        children[0].set_halign(Gtk.Align.START)
        children[0].set_tooltip_text("Sessions")
        children[0].update_property([Gtk.AccessibleProperty.LABEL], ["Sessions"])

    def _session_icon(self) -> str:
        return "house" if self.model.current.display_name == "Home" else "folder"

    def _branch(self, cwd: str) -> str | None:
        return fixture_branch(cwd) if self.fixture is not None else git_branch(cwd)

    def _show_phone_prompt(self) -> None:
        """v71 `tmBarPhone`: the keys row, a hairline, then sessions, the command line and Run.

        Shell input itself stays in VTE; this line hands what is typed to it with `feed_child`.
        """
        pane = self.model.active
        count = len(self.model.sessions)
        old, self._command = self._command, None
        had_focus = (old is not None and old.widget is not None
                     and old.widget.text_widget.has_focus())
        entry = BarEntry("command", prefix=(short_path(pane.cwd, self.display_home), self._branch(pane.cwd)),
                         text=old.text if old is not None else "",
                         on_change=self._command_changed, on_submit=self._run_command)
        self._command = entry
        entry.set_suggestion(self._suggestion(entry.text))
        sessions = BarAction(self._session_icon(), tooltip=f"Sessions: {self.model.current.display_name}",
                             panel=self._sessions_panel,
                             key="sessions", badge=count if count > 1 else None, badge_tone="accent")
        run = BarAction("corner-down-left", tooltip="Run", primary=True,
                        on_activate=lambda: self._run_command(entry.text))
        foot = Gtk.Box(valign=Gtk.Align.CENTER)
        foot.add_css_class("tm-command-row")
        controls = [make_control(sessions), make_control(entry), make_control(run)]
        for widget in controls:
            foot.append(widget)
        # A count wraps the button in an overlay; the button is what activates.
        sessions_button = getattr(controls[0], "bar_button", controls[0])
        sessions_button.set_name("tm-sessions")
        controls[1].set_name("tm-command")
        controls[2].set_name("tm-run")
        self.center.show_bar([TermKeys(self._press_key)], entry=foot)
        self._action_widgets.clear()
        self._action_widgets.update({"tm-sessions": sessions_button, "tm-command": controls[1],
                                     "tm-run": controls[2]})
        if had_focus:
            GLib.idle_add(lambda: (entry.focus(), GLib.SOURCE_REMOVE)[1])

    def _suggestion(self, text: str) -> str:
        return history_suggestion(text, list(COMMON_COMMANDS) + self.history)

    def _command_changed(self, text: str) -> None:
        if self._command is not None:
            self._command.set_suggestion(self._suggestion(text))

    def _run_command(self, text: str) -> None:
        if self._command is not None:
            self._command.clear()
            self._command.set_suggestion(None)
        if text.strip():
            self._send_command(text)
        elif self.fixture is None:
            self._feed("\n")

    def _press_key(self, key: PhoneKey) -> None:
        """A key of the keys row: a symbol goes into the command line, the rest to the shell."""
        if key.field and self._command is not None:
            self._command.set_text(self._command.text + key.send)
            self._command_changed(self._command.text)
            self._command.focus()
        else:
            self._feed(key.send)

    def _feed(self, text: str) -> None:
        terminal = self._active_terminal()
        if self.fixture is None and terminal is not None:
            terminal.feed_child(text.encode())

    def _open_sessions(self, anchor: Gtk.Widget) -> None:
        """The computer's sessions menu; a phone's is the bar grown (`_sessions_panel`)."""
        rows = session_rows(self.model, self.display_home, phone=False, on_activate=self.activate_session,
                            on_close=self.close_session, on_rename=self._rename_session,
                            on_new=lambda: (menu.close(), self.new_session()))
        menu = FloatingMenu(rows, label="Sessions", width="wide")
        menu.popup(anchor, align="start")
        self._session_menu = menu

    def _sessions_panel(self) -> Gtk.Widget:
        rows = session_rows(self.model, self.display_home, phone=True, on_activate=self.activate_session,
                            on_close=self.close_session, on_rename=self._rename_session,
                            on_new=self.new_session)
        return panel_list(rows, label="Sessions")

    def _rename_session(self, session_id: int, name: str) -> None:
        self.model.rename_session(session_id, name)
        if self._session_menu is not None:
            self._session_menu.close()
        if session_id == self.model.current_id:
            self._show_prompt()

    def _send_command(self, command: str) -> None:
        if not command.strip():
            return
        self.history.append(command)
        if self.fixture is None:
            terminal = self.terminals.get(self.model.active_pane_id)
            if terminal is not None:
                terminal.feed_child((command + "\n").encode())
                if not self._phone:  # a phone keeps its keyboard for the command line
                    terminal.grab_focus()

    def _focus_active_terminal(self) -> None:
        terminal = self._active_terminal()
        if terminal is not None:
            terminal.grab_focus()

    def _select_block(self, number: int) -> None:
        if self.fixture is None:
            return
        self._selected_block = number
        self.block_widgets[number].add_css_class("sel")
        block = self.fixture["blocks"][number - 1]
        self._show_selected_bar(block)

    def _selection_changed(self, pane_id: int, terminal: Gtk.Widget) -> None:
        if self.fixture is not None or pane_id != self.model.active_pane_id:
            return
        if not terminal.get_has_selection():
            if self._live_selection_pane_id == pane_id:
                self._live_selection_pane_id = None
                self._show_prompt()
            return
        output = terminal.get_text_selected(self._vte().Format.TEXT)
        if not output:
            return
        self._live_selection_pane_id = pane_id
        self._show_selected_bar({"command": "", "output": [output]})

    def _show_selected_bar(self, block: dict) -> None:
        self._bar_kind, self._bar_key = "selected", None
        self._selected_block_data = block
        command = block.get("command") or ""
        name = command or ("Welcome" if self.fixture is not None else "Selected output")
        has_command = bool(command) or self.fixture is not None
        if self._phone:
            # v71 `tmBarPhone`: the command on the line over the bar, then icon keys sharing the row.
            actions = [BarAction("copy", tooltip="Copy output",
                                 on_activate=lambda: self._copy_block(block, "output"))]
            if has_command:
                actions.extend((
                    BarAction("clipboard", tooltip="Copy command",
                              on_activate=lambda: self._copy_block(block, "command")),
                    BarAction("rotate-cw", tooltip="Run again", on_activate=lambda: self._rerun_block(block)),
                ))
            actions.extend((
                BarAction("file-text", tooltip="Send to Notes",
                          on_activate=lambda: self._handoff_block(block, "org.projectluma.Notes.desktop", "Notes")),
                BarAction("sparkle", tooltip="Ask Ari",
                          on_activate=lambda: self._handoff_block(block, "org.projectluma.Ari.desktop", "Ari")),
                BarAction("x", tooltip="Done", on_activate=self._clear_block),
            ))
            self.center.show_bar(actions, context=BarContext("terminal", name), fill=True)
            return
        actions = [
            BarChip(name, icon="terminal"),
            BarAction("copy", "Copy output", on_activate=lambda: self._copy_block(block, "output")),
        ]
        if has_command:
            actions.extend((
                BarAction("copy", "Copy command", on_activate=lambda: self._copy_block(block, "command")),
                BarAction("rotate-cw", tooltip="Run again", on_activate=lambda: self._rerun_block(block)),
            ))
        actions.extend((
            BarAction("file-text", tooltip="Send to Notes",
                      on_activate=lambda: self._handoff_block(block, "org.projectluma.Notes.desktop", "Notes")),
            BarAction("sparkle", "Ask Ari",
                      on_activate=lambda: self._handoff_block(block, "org.projectluma.Ari.desktop", "Ari")),
            SPACER,
            BarAction("", "Done", on_activate=self._clear_block, primary=True),
        ))
        self.center.show_bar(actions)

    def _copy_block(self, block: dict, field: str) -> None:
        text = "\n".join(block.get("output", [])) if field == "output" else block.get("command") or ""
        self.get_clipboard().set(text)
        Toast.show(self.host, "Output copied" if field == "output" else "Command copied", kind="copied")

    def _handoff_block(self, block: dict, desktop_id: str, app_name: str) -> None:
        command = block.get("command") or ""
        output = "\n".join(block.get("output", []))
        content = f"{command}\n\n{output}" if command else output
        prompt = (f"Explain this terminal output:\n\n{content}"
                  if app_name == "Ari" else content)
        self.get_clipboard().set(prompt)
        if self.fixture is None:
            app = Gio.DesktopAppInfo.new(desktop_id)
            if app is None:
                Toast.show(self.host, f"{app_name} is not installed; output copied", kind="warning")
                return
            try:
                app.launch([], None)
            except GLib.Error as error:
                Toast.show(self.host, f"Could not open {app_name}: {error.message}", kind="error")
                return
        Toast.show(self.host, f"Copied for {app_name}; paste it there", kind="copied")

    def _rerun_block(self, block: dict) -> None:
        command = block.get("command")
        self._clear_block()
        if command:
            self._send_command(command)

    def _clear_block(self) -> None:
        self._reset_selection()
        self._show_prompt()

    def _reset_selection(self) -> None:
        if self._selected_block in self.block_widgets:
            self.block_widgets[self._selected_block].remove_css_class("sel")
        self._selected_block = None
        pane_id, self._live_selection_pane_id = self._live_selection_pane_id, None
        if pane_id is not None:
            terminal = self.terminals.get(pane_id)
            if terminal is not None:
                terminal.unselect_all()

    def new_session(self) -> None:
        self._reset_selection()
        self.model.new_session(self.model.active.cwd)
        self._render_stage()
        self._show_prompt()
        self._focus_active_terminal()

    def activate_session(self, session_id: int) -> None:
        self._reset_selection()
        self.model.activate_session(session_id)
        self._render_stage()
        self._show_prompt()
        self._focus_active_terminal()

    def split(self, direction: str) -> None:
        self._reset_selection()
        self.model.split_active(direction)
        self._render_stage()
        self._show_prompt()
        self._focus_active_terminal()

    def close_active_pane(self) -> None:
        self._close_pane(self.model.active_pane_id)

    def _close_pane(self, pane_id: int) -> None:
        self._reset_selection()
        self._discard_pane(pane_id)
        self.model.close_pane(pane_id)
        self._render_stage()
        self._show_prompt()
        self._focus_active_terminal()

    def close_current_session(self) -> None:
        self.close_session(self.model.current_id)

    def close_session(self, session_id: int) -> None:
        self._reset_selection()
        session = next(item for item in self.model.sessions if item.id == session_id)
        for pane in session.panes:
            self._discard_pane(pane.id)
        self.model.close_session(session_id)
        self._render_stage()
        self._show_prompt()
        self._focus_active_terminal()

    def _discard_pane(self, pane_id: int) -> None:
        view = self.pane_views.pop(pane_id, None)
        self.terminals.pop(pane_id, None)
        self._fetch_layouts.pop(pane_id, None)
        if self._live_selection_pane_id == pane_id:
            self._live_selection_pane_id = None
        self.pane_scrolls.pop(pane_id, None)
        self._pane_close_buttons.pop(pane_id, None)
        self._hovered_panes.discard(pane_id)
        self._spawn_generation.pop(pane_id, None)
        if view is not None:
            self._detach(view)

    def rename_current(self) -> None:
        session_id = self.model.current_id
        entry = Gtk.Entry(text=self.model.current.display_name, hexpand=True)
        entry.update_property([Gtk.AccessibleProperty.LABEL], ["Session name"])

        def save() -> None:
            self.model.rename_session(session_id, entry.get_text())
            self.center.fold()
            self._show_prompt()

        entry.connect("activate", lambda _e: save())
        self.center.set_editor(ActionEditor("Rename session", "pencil", body=entry,
                                           primary=BarAction("check", "Done", on_activate=save)))
        self.center.grow()

    def _active_terminal(self) -> Gtk.Widget | None:
        return self.terminals.get(self.model.active_pane_id)

    def copy(self) -> None:
        terminal = self._active_terminal()
        if terminal is not None:
            terminal.copy_clipboard_format(self._vte().Format.TEXT)

    def paste(self) -> None:
        terminal = self._active_terminal()
        if terminal is not None:
            terminal.paste_clipboard()

    def zoom(self, amount: int) -> None:
        terminal = self._active_terminal()
        if terminal is not None:
            terminal.set_font_scale(max(0.6, min(2.0, terminal.get_font_scale() + amount * 0.1)))

    def find(self) -> None:
        entry = Gtk.Entry(hexpand=True, placeholder_text="Search output")
        entry.update_property([Gtk.AccessibleProperty.LABEL], ["Search output"])
        self._search_entry = entry
        entry.connect("activate", lambda _e: self._find_next())
        self.center.set_editor(ActionEditor("Find", "search", body=entry,
                                           primary=BarAction("arrow-down", "Find next", on_activate=self._find_next)))
        self.center.grow()

    def _find_next(self) -> None:
        terminal = self._active_terminal()
        query = self._search_entry.get_text() if self._search_entry else ""
        if not terminal or not query:
            return
        import re
        try:
            regex = self._vte().Regex.new_for_search(re.escape(query), -1, 0)
            terminal.search_set_regex(regex, 0)
            terminal.search_set_wrap_around(True)
            if not terminal.search_find_next():
                Toast.show(self.host, "No matches", kind="warning")
        except GLib.Error as error:
            Toast.show(self.host, f"Search failed: {error.message}", kind="error")

    def about(self) -> None:
        Adw.AboutDialog(application_name="Terminal", application_icon=APP_ID,
                        developer_name="Project Luma").present(self)


class TerminalApplication(Adw.Application):
    def __init__(self) -> None:
        # A fresh preview can coexist with the prior preview's live shells.
        flags = (Gio.ApplicationFlags.NON_UNIQUE if os.environ.get("LUMA_TERM_PREVIEW_FRESH") == "1"
                 else Gio.ApplicationFlags.FLAGS_NONE)
        super().__init__(application_id=APP_ID, flags=flags)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        install_appkit()
        install_lumaui()
        add_style_sheet(str(Path(__file__).resolve().parent.parent / "style" / "terminal.css"))

    def do_activate(self) -> None:
        (self.props.active_window or TerminalWindow(self, fixture_from_environment())).present()


def main() -> int:
    return TerminalApplication().run([])


if __name__ == "__main__":
    raise SystemExit(main())
