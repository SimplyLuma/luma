# SPDX-License-Identifier: Apache-2.0
"""LumaUI action: the selection bubble, and the kit's floating placement.

SelectionBubble — Write's bubble for every rich-text surface: the common
marks float 10 px above the selection, centred on it and kept 8 px inside the
region, and flip below when they would come within 12 px of its top. It
waits for a drag to finish, hides on typing, scrolling and when the
selection ends. Left and Right move inside it, Esc hides it and returns to
the text, Alt+F10 reaches it from the text (v70 `.selbar`, `lBubble`,
`lBubble.hide`). The actions are the application's; the kit owns the look,
the placement and the keys.

    bubble = SelectionBubble(text_view, [
        BarAction("bold", tooltip="Bold", on_activate=bold),
        BarAction("italic", tooltip="Italic", on_activate=italic),
        SEPARATOR,
        BarAction("link", tooltip="Link", on_activate=link),
    ])
    bubble.set_active("bold", True)          # the mark the selection already has

A Gtk.TextView is tracked by itself. Any other widget with a selection calls
`bubble.show_for(rect)` (a Gdk.Rectangle in the widget's coordinates) and
`bubble.hide()`.

The bubble floats in the nearest LayerHost. `float_at()` is the placement
the kit's other floating parts share (the action center's menus, Open in,
place suggestions): above or below an anchor rectangle, flipped at the edge,
kept inside the host.
"""
from __future__ import annotations

from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gtk  # noqa: E402

from . import lumaui  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402
from .action_center import SEPARATOR, BarAction, make_control  # noqa: E402
from .structure_layers import LayerHost  # noqa: E402

__all__ = ["SelectionBubble", "float_at", "FloatingMenu", "SearchMenu", "MenuItem"]


# ── placement ──────────────────────────────────────────────────────────────

def _natural(widget: Gtk.Widget, for_width: int = -1) -> tuple[int, int]:
    _min_w, width, _b1, _b2 = widget.measure(Gtk.Orientation.HORIZONTAL, -1)
    if for_width > 0:
        width = for_width
    _min_h, height, _b3, _b4 = widget.measure(Gtk.Orientation.VERTICAL, width)
    return width, height


def float_at(host: Gtk.Widget, widget: Gtk.Widget, rect: Gdk.Rectangle, *, prefer: str = "above",
             align: str = "center", offset: int | None = None, edge: int | None = None,
             inset: int | None = None, width: int | None = None, flip: bool = True) -> str:
    """Place `widget` (already an overlay of `host`) beside `rect`, in host coordinates.

    It goes `offset` above the rectangle (or below), flips when it would come
    within `edge` of the host's edge, and stays `inset` inside the host's
    sides. `align` lines it up with the rectangle's centre, start or end.
    Returns the side it went to ("above" or "below").
    """
    metrics = tokens.SELECTION_BUBBLE
    offset = metrics["offset"] if offset is None else offset
    edge = metrics["edge"] if edge is None else edge
    inset = metrics["inset"] if inset is None else inset
    widget.set_halign(Gtk.Align.START)
    widget.set_valign(Gtk.Align.START)
    for setter in (widget.set_margin_start, widget.set_margin_top, widget.set_margin_end, widget.set_margin_bottom):
        setter(0)
    if width:
        widget.set_size_request(width, -1)
    bw, bh = _natural(widget, width or -1)
    hw, hh = host.get_width(), host.get_height()
    above_y = rect.y - bh - offset
    below_y = rect.y + rect.height + offset
    if not flip:
        side = prefer
    elif prefer == "above":
        side = "above" if above_y >= edge else "below"
    else:
        side = "below" if below_y + bh <= hh - edge or above_y < edge else "above"
    y = above_y if side == "above" else below_y
    # A menu may be taller than the room on either side of its anchor.
    # Keep its visible edge inside the host even in that case.
    if hh > 0:
        y = max(edge, min(y, max(edge, hh - bh - edge)))
    if align == "start":
        x = rect.x
    elif align == "end":
        x = rect.x + rect.width - bw
    else:
        x = rect.x + rect.width / 2 - bw / 2
    x = max(inset, min(hw - bw - inset, x)) if hw > 0 else x
    if host.get_direction() == Gtk.TextDirection.RTL and hw > 0:
        x = hw - x - bw  # START is the right edge: measure the margin from there
    widget.set_margin_start(max(0, int(round(x))))
    widget.set_margin_top(max(0, int(round(y))))
    lumaui.set_css_class(widget, "below", side == "below")
    return side


def rect_in(host: Gtk.Widget, widget: Gtk.Widget, rect: Gdk.Rectangle | None = None) -> Gdk.Rectangle:
    """`rect` (default: the whole widget, border box) moved into `host`'s coordinates."""
    if rect is None:
        ok, bounds = widget.compute_bounds(host)
        out = Gdk.Rectangle()
        if ok:
            out.x, out.y = int(bounds.origin.x), int(bounds.origin.y)
            out.width, out.height = int(bounds.size.width), int(bounds.size.height)
        else:
            out.x = out.y = 0
            out.width, out.height = widget.get_width(), widget.get_height()
        return out
    ok, point = widget.compute_point(host, Graphene.Point().init(rect.x, rect.y))
    out = Gdk.Rectangle()
    out.x, out.y = (int(point.x), int(point.y)) if ok else (rect.x, rect.y)
    out.width, out.height = rect.width, rect.height
    return out


# ── the selection bubble ───────────────────────────────────────────────────

class SelectionBubble(Gtk.Box):
    """Formatting marks that float over a text selection. See the module docstring."""

    __gtype_name__ = "LumaUISelectionBubble"

    def __init__(self, view: Gtk.Widget, items: Sequence[BarAction | object], *, label: str = "Formatting") -> None:
        if not items:
            raise ValueError("a selection bubble needs at least one action")
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, accessible_role=Gtk.AccessibleRole.TOOLBAR,
                         focusable=False)  # its buttons still take focus (Alt+F10); can_focus=False would stop them
        self.add_css_class("lumaui-bubble")
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self.view = view
        self._buttons: dict[str, Gtk.Widget] = {}
        self._host: LayerHost | None = None
        self._down = False
        self._settle = 0
        self.side = "above"
        for item in items:
            widget = make_control(item, size="bubble")
            if isinstance(item, BarAction):
                self._buttons[item.icon] = widget
            self.append(widget)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._bubble_key)
        self.add_controller(keys)
        self._track(view)

    # ── public API ────────────────────────────────────────────────────────

    @property
    def shown(self) -> bool:
        return self._host is not None and self.get_parent() is self._host and self.has_css_class("shown")

    def show_for(self, rect: Gdk.Rectangle) -> None:
        """Float over `rect`, in the view's coordinates (waits if a drag is still going)."""
        if self._down:
            return
        host = LayerHost.for_widget(self.view)
        lumaui.install(host.get_display())
        if self.get_parent() is not host:
            if self.get_parent() is not None:
                self.get_parent().remove_overlay(self)
            host.add_overlay(self)
            self._host = host
        self.set_visible(True)
        self.side = float_at(host, self, rect_in(host, self.view, rect))
        if not self.has_css_class("shown"):
            lumaui.on_next_frame(self, lambda: self.add_css_class("shown"))

    def hide(self) -> None:
        """Take the bubble away (the selection ended, the text scrolled, someone typed)."""
        self._cancel_settle()
        self.remove_css_class("shown")
        self.set_visible(False)

    def set_active(self, icon: str, active: bool) -> None:
        """Mark a toggle (Bold, Italic…) as on for the current selection."""
        button = self._buttons.get(icon)
        if button is None:
            raise KeyError(icon)
        lumaui.set_css_class(button, "on", active)
        button.update_state([Gtk.AccessibleState.PRESSED], [int(Gtk.AccessibleTristate.TRUE if active
                                                                else Gtk.AccessibleTristate.FALSE)])

    def focus_first(self) -> bool:
        """Alt+F10: move keyboard focus into the bubble when it shows."""
        if not self.shown:
            return False
        first = self._focusable()
        if first:
            first[0].grab_focus()
            return True
        return False

    # ── tracking a text view ──────────────────────────────────────────────

    def _track(self, view: Gtk.Widget) -> None:
        legacy = Gtk.EventControllerLegacy(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        legacy.connect("event", self._view_event)
        view.add_controller(legacy)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._view_key)
        view.add_controller(keys)
        if isinstance(view, Gtk.TextView):
            buffer = view.get_buffer()
            buffer.connect("mark-set", self._mark_set)
            buffer.connect("changed", lambda *_a: self.hide())
            view.connect("notify::vadjustment", lambda *_a: self._watch_scroll())
            self._watch_scroll()

    def _watch_scroll(self) -> None:
        adjustment = getattr(self.view, "get_vadjustment", lambda: None)()
        if adjustment is not None and not getattr(adjustment, "_lumaui_bubble", False):
            adjustment._lumaui_bubble = True
            adjustment.connect("value-changed", lambda *_a: self.hide())

    def _view_event(self, _controller: Gtk.EventControllerLegacy, event: Gdk.Event) -> bool:
        if event is None:
            return False
        kind = event.get_event_type()
        if kind == Gdk.EventType.BUTTON_PRESS:
            self._down = True
            self.hide()
        elif kind == Gdk.EventType.BUTTON_RELEASE:
            self._down = False
            self._schedule()
        return False

    def _view_key(self, _controller: Gtk.EventControllerKey, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_F10 and state & Gdk.ModifierType.ALT_MASK:
            return self.focus_first()
        return False

    def _mark_set(self, buffer, _location, mark) -> None:
        if mark is buffer.get_insert() or mark is buffer.get_selection_bound():
            if not buffer.get_has_selection():
                self.hide()
            elif not self._down:
                self._schedule()

    def _schedule(self) -> None:
        self._cancel_settle()
        self._settle = GLib.timeout_add(int(tokens.MOTION["bubble_settle_ms"]), self._settled)

    def _cancel_settle(self) -> None:
        if self._settle:
            GLib.source_remove(self._settle)
            self._settle = 0

    def _settled(self) -> bool:
        self._settle = 0
        rect = self.selection_rect()
        if rect is None:
            self.hide()
        else:
            self.show_for(rect)
        return False

    def selection_rect(self) -> Gdk.Rectangle | None:
        """The selection's bounding box in the view's coordinates, or None when nothing is selected."""
        view = self.view
        if not isinstance(view, Gtk.TextView):
            return None
        bounds = view.get_buffer().get_selection_bounds()
        if not bounds:
            return None
        start, end = bounds
        first, last = view.get_iter_location(start), view.get_iter_location(end)
        top, bottom = first.y, last.y + last.height
        if first.y == last.y:
            left, right = first.x, last.x
        else:  # several lines: the width of the text column, as a DOM range's box
            visible = view.get_visible_rect()
            left, right = visible.x + view.get_left_margin(), visible.x + visible.width - view.get_right_margin()
        x1, y1 = view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, left, top)
        x2, y2 = view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, right, bottom)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = x1, y1, max(1, x2 - x1), max(1, y2 - y1)
        return rect

    # ── keys inside the bubble ────────────────────────────────────────────

    def _focusable(self) -> list[Gtk.Widget]:
        found = []
        child = self.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Button) and child.get_visible() and child.is_sensitive():
                found.append(child)
            child = child.get_next_sibling()
        return found

    def _bubble_key(self, _controller: Gtk.EventControllerKey, keyval: int, _code: int, _state: object) -> bool:
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            buttons = self._focusable()
            root = self.get_root()
            focus = root.get_focus() if root is not None else None
            index = next((i for i, b in enumerate(buttons) if b is focus), -1)
            step = 1 if keyval == Gdk.KEY_Right else -1
            if buttons:
                buttons[(index + step) % len(buttons)].grab_focus()
            return True
        if keyval == Gdk.KEY_Escape:
            self.hide()
            self.view.grab_focus()
            return True
        return False


# ── a small anchored menu (a placeholder for F2's MenuDrawer) ──────────────

class MenuItem:
    """One row: an icon (a Lucide name or a Gio.Icon), a label, an optional note on the right."""

    def __init__(self, label: str, *, icon: str | None = None, gicon: object | None = None,
                 note: str | None = None, on_activate: Callable[[], None] | None = None,
                 selected: bool = False, danger: bool = False) -> None:
        self.label, self.icon, self.gicon, self.note = label, icon, gicon, note
        self.on_activate, self.selected, self.danger = on_activate, selected, danger


class FloatingMenu(Gtk.Box):
    """A short menu anchored to a control: a popover card on a computer, a drawer on a phone.

    At phone width it presents through F2's `MenuDrawer.present_items`, the one
    menu drawer every LumaUI menu uses; on a computer it is this floating card.
    `rows` holds MenuItem, a heading string, or None for a separator.
    """

    __gtype_name__ = "LumaUIFloatingMenu"

    def __init__(self, rows: Sequence[MenuItem | str | None], *, label: str = "Menu", title: str | None = None,
                 width: str | None = None) -> None:
        """`title` heads the phone drawer (the menu's name: "Sessions"); `width="wide"` is v70's
        300 wide menu (Terminal's sessions, .tsesspop); `width="narrow"` is v71's 208 (Charlie's crPop)."""
        if width not in (None, "wide", "narrow"):
            raise ValueError('a floating menu is its natural width, "wide" or "narrow"')
        super().__init__(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.MENU)
        self.add_css_class("lumaui-menu")
        lumaui.set_css_class(self, "wide", width == "wide")
        lumaui.set_css_class(self, "narrow", width == "narrow")
        self._title, self._label = title, label
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self.buttons: list[Gtk.Button] = []
        self._rows = list(rows)
        self._drawer = None
        self._catcher: Gtk.Widget | None = None
        self._host: LayerHost | None = None
        self._handle = None
        self._keys: Gtk.EventControllerKey | None = None
        self._return: Gtk.Widget | None = None
        self._scroll: Gtk.ScrolledWindow | None = None
        self.handle_bar = Gtk.Box(halign=Gtk.Align.CENTER, visible=False)
        self.handle_bar.add_css_class("lumaui-drawer-handle")
        self.append(self.handle_bar)
        from . import icons
        for row in rows:
            if row is None:
                sep = Gtk.Box()
                sep.add_css_class("lumaui-menu-separator")
                self.append(sep)
            elif isinstance(row, str):
                heading = Gtk.Label(label=row, xalign=0)
                heading.add_css_class("lumaui-menu-heading")
                self.append(heading)
            elif hasattr(row, "menu_widget"):  # rows family (MN1): RichMenuItem, MenuSection
                widget = row.menu_widget(self.close)
                self.append(widget)
                self.buttons.extend(getattr(widget, "menu_buttons", []))
            else:
                button = Gtk.Button(accessible_role=Gtk.AccessibleRole.MENU_ITEM)
                button.add_css_class("lumaui-menu-item")
                if row.selected:
                    button.add_css_class("on")
                line = Gtk.Box(spacing=10)
                if row.gicon is not None:
                    image = Gtk.Image.new_from_gicon(row.gicon)
                    image.add_css_class("lumaui-app-icon")
                    line.append(image)
                elif row.icon:
                    line.append(icons.image(row.icon))
                text = Gtk.Label(label=row.label, xalign=0, hexpand=True)
                line.append(text)
                if row.note:
                    note = Gtk.Label(label=row.note)
                    note.add_css_class("lumaui-menu-note")
                    line.append(note)
                button.set_child(line)
                name = f"{row.label}, {row.note}" if row.note else row.label
                button.update_property([Gtk.AccessibleProperty.LABEL], [name])
                button.connect("clicked", self._activated, row)
                self.buttons.append(button)
                self.append(button)

    def popup(self, anchor: Gtk.Widget, *, align: str = "end", frame_bottom: int | None = None,
              frame_max_fraction: float | None = None, frame_opaque: bool | None = None,
              side: str | None = None) -> "FloatingMenu":
        """Open under or over `anchor` (over it in the lower half of the window, as v70's lMenu).

        `align` lines the menu up with the anchor's "start", "center" or "end" edge; `side` ("above" or
        "below") fixes that side and clamps to the window edge (Memos' speed menu rises).
        """
        if side not in (None, "above", "below"):
            raise ValueError('a menu opens "above" or "below" its anchor')
        if align not in ("start", "center", "end"):
            raise ValueError('a menu aligns with its anchor\'s "start", "center" or "end"')
        host = LayerHost.window_host(anchor)
        lumaui.install(host.get_display())
        root = anchor.get_root()
        self._return = anchor
        # A menu from inside a media context keeps its palette, though it floats at window level.
        lumaui.set_css_class(self, "lumaui-media", lumaui.in_media_context(anchor))
        if lumaui.is_phone_width(host) or lumaui.mobile_form_factor():
            # v71: on a phone every menu rises from the bar's place (the bar's frame), not a drawer.
            from .bar_frame import BarFrame
            from .bar_panel import panel_list
            self._drawer = BarFrame.present(anchor, panel_list(self._rows, label=self._label), kind="menu",
                                            title=self._title, bottom=frame_bottom,
                                            max_fraction=frame_max_fraction, opaque=frame_opaque)
            self._drawer.on_closed = self._drawer_cancelled
            return self
        self._host = host
        available_height = max(96, host.get_height() - 16)
        if _natural(self)[1] > available_height:
            # Keep every choice reachable when a short window cannot fit the
            # menu above or below the transport key.
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            while child := self.get_first_child():
                self.remove(child)
                content.append(child)
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_propagate_natural_height(True)
            scroll.set_max_content_height(available_height - 12)
            scroll.set_child(content)
            self._scroll = scroll
            self.append(scroll)
        catcher = Gtk.Box(hexpand=True, vexpand=True)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_a: self.close())
        catcher.add_controller(click)
        self._catcher = catcher
        host.add_overlay(catcher)
        host.add_overlay(self)
        rect = rect_in(host, anchor)
        prefer = side or ("above" if rect.y > host.get_height() * 0.5 else "below")
        float_at(host, self, rect, prefer=prefer, align=align, offset=8, edge=8, flip=side is None)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        if root is not None:
            root.add_controller(keys)
            self._keys = keys
        lumaui.on_next_frame(self, lambda: self.add_css_class("shown"))
        if self.buttons:
            self.buttons[0].grab_focus()
        return self

    def close(self) -> None:
        if self._drawer is not None:
            drawer, self._drawer = self._drawer, None
            drawer.close()
            return
        if self._handle is not None:
            handle, self._handle = self._handle, None
            handle.close()
            return
        host, self._host = self._host, None
        if host is None:
            return
        root = host.get_root()
        if self._keys is not None and root is not None:
            root.remove_controller(self._keys)
            self._keys = None
        for widget in (self._catcher, self):
            if widget is not None and widget.get_parent() is host:
                host.remove_overlay(widget)
        self._catcher = None
        if self._return is not None and self._return.get_mapped():
            self._return.grab_focus()

    def _drawer_cancelled(self) -> None:
        self._handle = None
        self._drawer = None

    @property
    def is_open(self) -> bool:
        return self._host is not None or self._handle is not None or self._drawer is not None

    def _activated(self, _button: Gtk.Button, row: MenuItem) -> None:
        self.close()
        if row.on_activate is not None:
            row.on_activate()

    def _key(self, _controller, keyval: int, _code: int, _state: object) -> bool:
        root = self.get_root()
        if isinstance(root.get_focus() if root is not None else None, Gtk.Text):
            return False  # an inline rename (MN1) owns its keys
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down) and self.buttons:
            root = self.get_root()
            focus = root.get_focus() if root is not None else None
            index = next((i for i, b in enumerate(self.buttons) if focus is b or (focus and focus.is_ancestor(b))), -1)
            step = 1 if keyval == Gdk.KEY_Down else -1
            self.buttons[(index + step) % len(self.buttons)].grab_focus()
            return True
        return False


class SearchMenu(FloatingMenu):
    """A filtered command menu using the same field, rows and mobile panel.

    Typing stays in the search field; arrows select a visible command, Enter
    runs it, Escape returns to the caller. Application callbacks own edits.
    """
    __gtype_name__ = "LumaUISearchMenu"

    def __init__(self, rows: Sequence[MenuItem], *, label: str = "Commands",
                 on_cancel: Callable[[], None] | None = None) -> None:
        from .content_field import TextField
        from .rows_menu import MenuSection, RichMenuItem
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.field = TextField(label, placeholder="Search commands")
        content.append(self.field)
        self.command_rows = list(rows)
        self.command_buttons = []
        self._selected = 0
        self._on_cancel = on_cancel
        for row in self.command_rows:
            item = RichMenuItem(row.label, icon=row.icon,
                                on_activate=lambda r=row: self._run(r))
            button = item.menu_widget(lambda: None)
            self.command_buttons.append(button)
            content.append(button)
        self.empty = Gtk.Label(label="No matching commands", visible=False)
        from .content_type import apply_type
        apply_type(self.empty, "caption", muted=True)
        content.append(self.empty)
        super().__init__([MenuSection(content)], label=label)
        self.field.entry.connect("changed", lambda *_a: self._filter())
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.field.add_controller(keys)  # also owns keys in the shared mobile panel
        self._filter()

    def popup(self, anchor: Gtk.Widget, **kwargs):
        super().popup(anchor, **kwargs)
        self.field.grab_focus()
        return self

    def _filter(self):
        query = self.field.text.strip().casefold()
        for row, button in zip(self.command_rows, self.command_buttons):
            button.set_visible(query in row.label.casefold())
        self._selected = 0
        self._selection()

    def _selection(self):
        visible = [b for b in self.command_buttons if b.get_visible()]
        self.empty.set_visible(not visible)
        if visible:
            self._selected %= len(visible)
        for button in self.command_buttons:
            lumaui.set_css_class(button, "on", bool(visible) and button is visible[self._selected])

    def close(self):
        callback, self._on_cancel = self._on_cancel, None
        super().close()
        if callback:
            callback()

    def _drawer_cancelled(self):
        super()._drawer_cancelled()
        callback, self._on_cancel = self._on_cancel, None
        if callback:
            callback()

    def _run(self, row):
        self._on_cancel = None
        self.close()
        if row.on_activate:
            row.on_activate()

    def _key(self, controller, keyval, code, state):
        visible = [b for b in self.command_buttons if b.get_visible()]
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
            self._selected += 1 if keyval == Gdk.KEY_Down else -1
            self._selection()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if visible:
                visible[self._selected].emit("clicked")
            return True
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False
