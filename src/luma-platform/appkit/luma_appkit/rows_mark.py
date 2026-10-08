# SPDX-License-Identifier: Apache-2.0
"""LumaUI rows: Mark, MarkButton and MarkPicker. How anything gets its mark.

    Mark()                                          # the default: a blue dot
    Mark(kind="icon", icon="book", hue="green")     # a coloured Lucide glyph
    Mark(kind="emoji", emoji="📚")
    MarkButton(Mark(hue="orange"), on_change=save)  # a 22 px button that opens the picker
    MarkPicker.present(anchor, mark, on_pick=save)  # beside the button; a drawer on a phone

v70 `lMark`/`lPick` (the icon picker, one component for every app): a mark is
a coloured dot (the default), an icon or an emoji. One colour row serves dots
and icons: `MARK_HUES` (blue, teal, green, yellow, orange, red, pink, purple,
violet, grey). The picker shows the mark large, a Dot · Icon · Emoji switch,
the colour row, and for icons and emoji a search field over a grid. Picking a
colour or a kind keeps it open; picking an icon or an emoji closes it. Esc
closes it, arrows move through a grid, focus returns to the button.

Notes, Tasks and Calendar use it for folders, lists and calendars; a tree or
sidebar row takes one with `SidebarRow(mark=)`.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GObject, Gtk  # noqa: E402

from . import icons, lumaui  # noqa: E402

__all__ = ["MARK_HUES", "MARK_ICONS", "MARK_KINDS", "Mark", "MarkButton", "MarkPicker", "MarkValue"]

#: The colours a mark comes in, in v70's order; grey is the muted ink.
MARK_HUES = ("blue", "teal", "green", "yellow", "orange", "red", "pink", "purple", "violet", "grey")
MARK_KINDS = ("dot", "icon", "emoji")
#: v70 LICONS: the glyphs the picker offers, in its order.
MARK_ICONS = (
    "folder", "file-text", "notebook-pen", "sticky-note", "book", "book-open", "bookmark", "library", "list-checks",
    "square-check", "clipboard", "inbox", "archive", "tag", "flag", "pin", "star", "heart", "briefcase", "building",
    "house", "calendar", "clock", "timer", "hourglass", "bell", "mail", "message-circle", "phone", "send", "users",
    "user", "smile", "sparkles", "zap", "badge-check", "shield", "key", "lock", "globe", "map-pin", "compass",
    "navigation", "car", "bike", "train-front", "footprints", "tree-pine", "leaf", "sun", "moon", "cloud", "droplets",
    "waves", "wind", "coffee", "utensils", "cake", "camera", "image", "film", "music", "headphones", "mic", "guitar",
    "piano", "palette", "pen-tool", "pencil", "highlighter", "scissors", "code", "terminal", "cpu", "monitor",
    "smartphone", "database", "chart-column", "chart-pie", "receipt", "wallet", "credit-card", "dollar-sign",
    "package", "wrench", "gauge", "activity", "layers", "layout-grid", "git-branch", "hash", "at-sign", "eye", "watch")
#: v70 LEMOJI: (emoji, the words that find it).
MARK_EMOJI = (
    ("📝", "note memo write"), ("📓", "notebook"), ("📚", "books library read"), ("🔖", "bookmark"), ("📌", "pin"),
    ("📎", "clip"), ("✅", "done check tick"), ("⭐", "star favourite"), ("❤️", "heart love"), ("🔥", "fire hot"),
    ("💡", "idea light"), ("🎯", "goal target"), ("🚀", "launch rocket ship"), ("🎉", "party celebrate"),
    ("🏆", "trophy win"), ("📅", "calendar date"), ("⏰", "alarm clock time"), ("📣", "announce press"),
    ("📰", "news press"), ("💼", "work briefcase"), ("🏠", "home house"), ("🏡", "garden home"), ("🧾", "receipt tax"),
    ("💰", "money"), ("💳", "card pay"), ("🛒", "shopping groceries cart"), ("🥕", "groceries food carrot"),
    ("🍳", "cook recipe breakfast"), ("🍝", "pasta recipe dinner"), ("🥗", "salad food"), ("☕", "coffee"),
    ("🍷", "wine"), ("🎂", "birthday cake"), ("🎁", "gift present"), ("✈️", "travel plane flight"),
    ("🧳", "trip luggage"), ("🗺️", "map trip"), ("🏔️", "mountain climb"), ("🧗", "climbing gear"), ("⛺", "camp tent"),
    ("🚲", "bike"), ("🏃", "run exercise"), ("🧘", "yoga calm"), ("🌱", "plant grow"), ("🌸", "flower spring"),
    ("🌊", "sea wave"), ("☀️", "sun summer"), ("🌙", "moon night"), ("❄️", "snow winter"), ("🎵", "music song"),
    ("🎸", "guitar"), ("🎧", "headphones listen"), ("🎬", "film movie"), ("📷", "camera photo"),
    ("🎨", "art paint design"), ("✏️", "pencil draft"), ("🖥️", "computer desktop"), ("📱", "phone mobile"),
    ("⌨️", "keyboard"), ("🧪", "test lab"), ("🐞", "bug"), ("🔧", "fix tool"), ("🔒", "lock private"),
    ("🔑", "key password wifi"), ("📶", "wifi signal"), ("🐶", "dog pet"), ("🐱", "cat pet"), ("👶", "baby"),
    ("👪", "family"), ("🤝", "meeting deal"), ("💬", "chat talk"), ("📧", "email mail"), ("📞", "call phone"),
    ("🧠", "brain think"), ("📈", "growth chart"), ("🗂️", "files folder"), ("📦", "package ship"),
    ("🧹", "clean chores"), ("🩺", "health doctor"), ("💊", "medicine"))


@dataclass(frozen=True)
class MarkValue:
    """What a mark is, for saving: kind, hue, and the icon or emoji."""

    kind: str = "dot"
    hue: str | float = "blue"
    icon: str | None = None
    emoji: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in MARK_KINDS:
            raise ValueError(f"mark kind must be one of {', '.join(MARK_KINDS)}, not {self.kind!r}")
        numeric = isinstance(self.hue, (int, float)) and not isinstance(self.hue, bool)
        if not numeric and self.hue not in MARK_HUES:
            raise ValueError(f"mark hue must be one of {', '.join(MARK_HUES)} or a hue in degrees, not {self.hue!r}")
        if self.kind == "icon" and not self.icon:
            raise ValueError("an icon mark names its Lucide glyph")
        if self.kind == "emoji" and not self.emoji:
            raise ValueError("an emoji mark names its emoji")

    @property
    def spoken(self) -> str:
        if self.kind == "emoji":
            return f"{self.emoji} mark"
        colour = self.hue.title() if isinstance(self.hue, str) else "Coloured"
        if self.kind == "icon":
            return f"{colour} {self.icon.replace('-', ' ')} mark"
        return f"{colour} dot"


class Mark(Gtk.Box):
    """A mark: a dot, an icon or an emoji in one of the mark colours. `large=True` for a header."""

    __gtype_name__ = "LumaUIMark"

    def __init__(self, kind: str = "dot", *, hue: str = "blue", icon: str | None = None,
                 emoji: str | None = None, large: bool = False, value: MarkValue | None = None,
                 density: str = "normal") -> None:
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.IMG)
        if density not in ("normal", "card", "filter", "path"):
            raise ValueError("Mark density must be normal, card, filter or path")
        if density != "normal" and large:
            raise ValueError("compact Marks cannot also be large")
        self.add_css_class("lumaui-mark")
        self.density = density
        self.add_css_class(f"density-{density}")
        lumaui.set_css_class(self, "large", large)
        self.value = MarkValue()
        self.set_value(value or MarkValue(kind, hue, icon, emoji))

    def set_value(self, value: MarkValue) -> None:
        if self.density in ("card", "filter") and value.kind != "dot":
            raise ValueError("compact Marks support dot values only")
        for name in [c for c in self.get_css_classes() if c.startswith(("hue-", "kind-"))]:
            self.remove_css_class(name)
        child = self.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.remove(child)
            child = following
        self.value = value
        self.add_css_class(_hue_css_class(self, value.hue))
        self.add_css_class(f"kind-{value.kind}")
        if value.kind == "dot":
            dot = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, hexpand=True)
            dot.add_css_class("lumaui-mark-dot")
            self.append(dot)
        elif value.kind == "icon":
            image = icons.image(value.icon)
            image.add_css_class("lumaui-mark-icon")
            image.set_hexpand(True)
            self.append(image)
        else:
            label = Gtk.Label(label=value.emoji, hexpand=True)
            label.add_css_class("lumaui-mark-emoji")
            self.append(label)
        self.update_property([Gtk.AccessibleProperty.LABEL], [value.spoken])
        # The glyph expands to centre itself; the mark must not pass that on to the row.
        self.set_hexpand(False)


class MarkButton(Gtk.Button):
    """The 22 px button a mark sits in: it opens the MarkPicker and reports the pick."""

    __gtype_name__ = "LumaUIMarkButton"

    def __init__(self, mark: Mark | MarkValue | None = None, *, on_change: Callable[[MarkValue], None] | None = None,
                 label: str = "Change mark", kind: str = "normal") -> None:
        super().__init__(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        if kind not in ("normal", "page"):
            raise ValueError("MarkButton kind must be normal or page")
        self.kind = kind
        self.empty = kind == "page" and mark is None
        self.add_css_class("lumaui-mark-button")
        lumaui.set_css_class(self, "page", kind == "page")
        self.mark = mark if isinstance(mark, Mark) else Mark(value=mark if isinstance(mark, MarkValue) else None)
        self.set_child(self.mark)
        self.set_hexpand(False)
        self.on_change = on_change
        self._label = label
        self.set_tooltip_text(label)
        self.set_empty(self.empty)
        self.connect("clicked", lambda _b: self.open())

    def set_empty(self, empty: bool) -> None:
        self.empty = bool(empty) and self.kind == "page"
        lumaui.set_css_class(self, "empty", self.empty)
        if self.empty:
            line = Gtk.Box(spacing=6)
            line.append(icons.image("smile"))
            line.append(Gtk.Label(label="Add icon"))
            self.set_child(line)
        else:
            self.set_child(self.mark)
        self._speak()

    @property
    def value(self) -> MarkValue:
        return self.mark.value

    def _speak(self) -> None:
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Page icon" if self.kind == "page" else f"{self._label}: {self.mark.value.spoken}"])

    def open(self) -> "MarkPicker":
        self.add_css_class("open")

        def picked(value: MarkValue) -> None:
            self.mark.set_value(value)
            self.set_empty(False)
            if self.on_change is not None:
                self.on_change(value)

        return MarkPicker.present(self, self.mark.value, on_pick=picked,
                                  on_closed=lambda: self.remove_css_class("open"))


class MarkPicker(Gtk.Box):
    """Choose a mark: the mark large, Dot · Icon · Emoji, the colours, then icons or emoji to search."""

    __gtype_name__ = "LumaUIMarkPicker"

    def __init__(self, value: MarkValue | None = None, *, on_pick: Callable[[MarkValue], None] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.DIALOG)
        self.add_css_class("lumaui-mark-picker")
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Choose a mark"])
        self.value = value or MarkValue()
        self.on_pick = on_pick
        self.on_closed: Callable[[], None] | None = None
        self.tab = self.value.kind
        self.query = ""
        self._close: Callable[[], None] | None = None
        head = Gtk.Box(spacing=12)
        head.add_css_class("lumaui-mark-picker-head")
        self.preview = Mark(value=self.value, large=True)
        self.preview.add_css_class("lumaui-mark-preview")
        head.append(self.preview)
        self.tabs: dict[str, Gtk.ToggleButton] = {}
        switch = Gtk.Box(hexpand=True, homogeneous=True, accessible_role=Gtk.AccessibleRole.RADIO_GROUP)
        switch.add_css_class("lumaui-mark-kinds")
        switch.update_property([Gtk.AccessibleProperty.LABEL], ["Kind of mark"])
        first = None
        for kind, text in (("dot", "Dot"), ("icon", "Icon"), ("emoji", "Emoji")):
            button = Gtk.ToggleButton(label=text, accessible_role=Gtk.AccessibleRole.RADIO)
            if first is None:
                first = button
            else:
                button.set_group(first)
            button.connect("toggled", self._tab_toggled, kind)
            self.tabs[kind] = button
            switch.append(button)
        head.append(switch)
        self.append(head)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.body.add_css_class("lumaui-mark-picker-body")
        self.append(self.body)
        self.tabs[self.tab].set_active(True)
        self._draw()
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

    # ── what the picker shows ──────────────────────────────────────────────

    def _tab_toggled(self, button: Gtk.ToggleButton, kind: str) -> None:
        if button.get_active() and kind != self.tab:
            self.tab, self.query = kind, ""
            self._draw()

    def _draw(self) -> None:
        child = self.body.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.body.remove(child)
            child = following
        self.hue_buttons: list[Gtk.Button] = []
        self.grid_buttons: list[Gtk.Button] = []
        if self.tab in ("dot", "icon"):
            self.body.append(self._hues())
        if self.tab in ("icon", "emoji"):
            search = Gtk.SearchEntry(placeholder_text="Search icons" if self.tab == "icon" else "Search emoji",
                                     text=self.query)
            search.add_css_class("lumaui-mark-search")
            search.connect("search-changed", self._searched)
            self.search = search
            self.body.append(search)
            self.grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                    min_children_per_line=8, max_children_per_line=8)
            self.grid.add_css_class("lumaui-mark-grid")
            scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                        max_content_height=220, child=self.grid)
            self.body.append(scroll)
            self.none = Gtk.Label(label="No icons match" if self.tab == "icon" else "No emoji match")
            self.none.add_css_class("lumaui-mark-none")
            self.body.append(self.none)
            self._fill_grid()

    def _hues(self) -> Gtk.Widget:
        row = Gtk.Box(homogeneous=True, accessible_role=Gtk.AccessibleRole.RADIO_GROUP)
        row.add_css_class("lumaui-mark-hues")
        row.update_property([Gtk.AccessibleProperty.LABEL], ["Colour"])
        current = self.value.hue if self.value.kind in ("dot", "icon") else None
        for hue in MARK_HUES:
            button = Gtk.Button(accessible_role=Gtk.AccessibleRole.RADIO, tooltip_text=hue.title())
            button.add_css_class("lumaui-mark-hue")
            button.add_css_class(f"hue-{hue}")
            swatch = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            swatch.add_css_class("lumaui-mark-swatch")
            button.set_child(swatch)
            on = hue == current and (self.tab == "dot") == (self.value.kind == "dot")
            lumaui.set_css_class(button, "on", on)
            button.update_property([Gtk.AccessibleProperty.LABEL], [hue.title()])
            button.update_state([Gtk.AccessibleState.CHECKED], [GObject.Value(GObject.TYPE_INT, int(on))])
            button.connect("clicked", lambda _b, h=hue: self._pick_hue(h))
            self.hue_buttons.append(button)
            row.append(button)
        return row

    def _fill_grid(self) -> None:
        self.grid.remove_all()
        self.grid_buttons = []
        query = self.query.strip().lower()
        hue = self.value.hue
        if self.tab == "icon":
            for name in (n for n in MARK_ICONS if not query or query in n.replace("-", " ")):
                button = Gtk.Button(tooltip_text=name.replace("-", " "))
                button.add_css_class("lumaui-mark-cell")
                button.add_css_class(_hue_css_class(button, hue))
                button.set_child(icons.image(name))
                button.update_property([Gtk.AccessibleProperty.LABEL], [name.replace("-", " ")])
                lumaui.set_css_class(button, "on", self.value.kind == "icon" and self.value.icon == name)
                button.connect("clicked", lambda _b, n=name: self._pick(replace(self.value, kind="icon", icon=n,
                                                                                    emoji=None), close=True))
                self.grid.append(button)
                self.grid_buttons.append(button)
        else:
            for emoji, words in (e for e in MARK_EMOJI if not query or query in e[1]):
                button = Gtk.Button(label=emoji, tooltip_text=words.split()[0])
                button.add_css_class("lumaui-mark-cell")
                button.add_css_class("emoji")
                button.update_property([Gtk.AccessibleProperty.LABEL], [words.split()[0]])
                lumaui.set_css_class(button, "on", self.value.kind == "emoji" and self.value.emoji == emoji)
                button.connect("clicked", lambda _b, e=emoji: self._pick(MarkValue("emoji", self.value.hue, None, e),
                                                                         close=True))
                self.grid.append(button)
                self.grid_buttons.append(button)
        self.none.set_visible(not self.grid_buttons)

    def _searched(self, entry: Gtk.SearchEntry) -> None:
        self.query = entry.get_text()
        self._fill_grid()

    # ── picking ────────────────────────────────────────────────────────────

    def _pick_hue(self, hue: str) -> None:
        if self.tab == "icon":
            icon = self.value.icon if self.value.kind == "icon" else "folder"
            self._pick(MarkValue("icon", hue, icon, None), close=False)
        else:
            self._pick(MarkValue("dot", hue, None, None), close=False)

    def _pick(self, value: MarkValue, *, close: bool) -> None:
        self.value = value
        self.preview.set_value(value)
        if self.on_pick is not None:
            self.on_pick(value)
        if close:
            self.close()
        else:
            focus_hue = value.hue
            self._draw()
            for button in self.hue_buttons:
                if button.has_css_class(f"hue-{focus_hue}"):
                    button.grab_focus()

    def _key(self, _controller, keyval: int, _code: int, _state: object) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        for group in (self.hue_buttons, self.grid_buttons):
            index = next((i for i, b in enumerate(group) if focus is b or (focus and focus.is_ancestor(b))), -1)
            if index < 0:
                continue
            step = {Gdk.KEY_Left: -1, Gdk.KEY_Right: 1}.get(keyval)
            if step is None and group is self.grid_buttons:
                step = {Gdk.KEY_Up: -8, Gdk.KEY_Down: 8}.get(keyval)
            if step is None:
                return False
            target = index + step
            if 0 <= target < len(group):
                group[target].grab_focus()
            return True
        return False

    # ── presenting ─────────────────────────────────────────────────────────

    @classmethod
    def present(cls, anchor: Gtk.Widget, value: MarkValue | None = None, *,
                on_pick: Callable[[MarkValue], None] | None = None,
                on_closed: Callable[[], None] | None = None) -> "MarkPicker":
        """Open beside `anchor` (below it, flipping above near the foot); a drawer at phone width."""
        picker = cls(value, on_pick=on_pick)
        picker.on_closed = on_closed
        picker._close = _anchored(anchor, picker, on_closed=picker._closed)
        return picker

    def close(self) -> None:
        if self._close is not None:
            close, self._close = self._close, None
            close()

    def _closed(self) -> None:
        self._close = None
        if self.on_closed is not None:
            self.on_closed()


_hue_providers: dict[int, tuple[Gtk.CssProvider, set[int]]] = {}


def _hue_css_class(widget: Gtk.Widget, hue: str | float) -> str:
    """The class that colours a mark: `hue-blue` for a named hue; for a hue in degrees (an app's
    own folder colour) `hue-deg-45`, whose rule the kit renders from the mark tokens."""
    if isinstance(hue, str):
        return f"hue-{hue}"
    degrees = int(round(float(hue))) % 360
    name = f"hue-deg-{degrees}"
    display = widget.get_display() or Gdk.Display.get_default()
    if display is None:
        return name
    key = hash(display)
    if key not in _hue_providers:
        provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        _hue_providers[key] = (provider, set())
    provider, hues = _hue_providers[key]
    if degrees not in hues:
        hues.add(degrees)
        from . import rows_tokens
        m = rows_tokens.group("mark")
        rules = []
        for d in sorted(hues):
            colour = lumaui.oklch_rgba(m["hue_lightness_ratio"], m["hue_chroma_ratio"], d)
            halo = lumaui.oklch_rgba(m["hue_lightness_ratio"], m["hue_chroma_ratio"], d, m["halo_alpha"])
            rules.append(f".hue-deg-{d} box.lumaui-mark-dot {{ background: {colour}; }}\n"
                         f".hue-deg-{d}:not(.density-card):not(.density-filter):not(.density-path) box.lumaui-mark-dot {{ "
                         f"box-shadow: 0 0 0 var(--lumaui-mark-halo) {halo}; }}\n"
                         f".hue-deg-{d} image.lumaui-mark-icon {{ color: {colour}; }}")
        provider.load_from_string("\n".join(rules))
    return name


def _anchored(anchor: Gtk.Widget, card: Gtk.Widget, *, on_closed: Callable[[], None]) -> Callable[[], None]:
    """Float `card` beside `anchor` in the window's LayerHost, or as a drawer at phone width.

    Returns the function that closes it. A click outside and Esc close it;
    focus returns to `anchor`.
    """
    from .action_bubble import float_at, rect_in
    from .structure_layers import LayerHost

    host = LayerHost.window_host(anchor)
    lumaui.install(host.get_display())
    if lumaui.is_phone_width(host):
        card.add_css_class("drawer")
        handle = host.present_modal(card, on_cancel=on_closed, drawer=True)

        def close_drawer() -> None:
            handle.close()
            on_closed()
        return close_drawer
    catcher = Gtk.Box(hexpand=True, vexpand=True)
    state = {"open": True}

    def close() -> None:
        if not state["open"]:
            return
        state["open"] = False
        for widget in (catcher, card):
            if widget.get_parent() is host:
                host.remove_overlay(widget)
        if anchor.get_mapped():
            anchor.grab_focus()
        on_closed()

    click = Gtk.GestureClick()
    click.connect("released", lambda *_a: close())
    catcher.add_controller(click)
    host.add_overlay(catcher)
    host.add_overlay(card)
    float_at(host, card, rect_in(host, anchor), prefer="below", align="start", offset=8, edge=8)
    lumaui.on_next_frame(card, lambda: card.add_css_class("shown"))
    first = card.get_first_child()
    card.grab_focus() if first is None else card.child_focus(Gtk.DirectionType.TAB_FORWARD)
    return close
