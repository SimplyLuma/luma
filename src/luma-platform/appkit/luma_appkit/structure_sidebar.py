# SPDX-License-Identifier: Apache-2.0
"""LumaUI structure: the sidebar foot and the sidebar toggle.

SidebarFoot — search at the foot of the sidebar, above a hairline, then a
filter picker, the raised New (or Add) square, or both (v70 `lFoot`,
`lFiltHead`; Memos, Contacts). The filter's menu opens upward (a drawer at phone width); when a
filter other than the first is on, the picker lights and the list names the
filter at its top with Show all (`foot.heading`, which the kit keeps in step).
It goes at the foot of a NavigationSidebar:

    foot = SidebarFoot(search="Search what was said", on_search=find,
                       filters=[("all", "All", "layers"), ("fav", "Favourites", "star"),
                                ("deleted", "Recently deleted", "trash-2")],
                       on_filter=show)
    sidebar.append_header(foot.heading)          # "Favourites · Show all" while filtered
    sidebar.append_footer(foot)
    SidebarFoot(search="Search mail", add=("New email", "square-pen", compose))
    SidebarFoot(search="Search people", filters=[("all", "All", "users", 214), ("fav", "Favourites", "star", 12)],
                on_filter=show, add=("Add a contact", "user-plus", add))   # Contacts: both

A filter may carry a count (key, label, icon, count), shown as a count badge
in the filter menu; `set_filter_counts({key: n})` keeps them current.

SidebarToggle — the one control that hides and shows a window's sidebar:
panel-left just after the app's name, F9 anywhere in the window, pressed
while the sidebar shows (v70 `lSideBtn`, `lSide`, `lSideOn`). What "closed"
means is the app's: by default the sidebar gives its room to the content
(sliding closed with the window's ease); `closed=fn` hands it to the app
(Viola keeps a strip of tab icons). At phone width the sidebar opens as a
drawer from the left over the dimmed window, and a tap beside it, Esc or
choosing a row closes it. v71: that drawer comes out of the bottom-left
corner, level with the page title, square to the left and bottom edges with
its top-right corner rounded, 44 px rows and a 42 px search (PhoneDrawer).

    toggle = SidebarToggle(sidebar)                    # a NavigationSidebar in a Box
    toggle = SidebarToggle(split_view)                 # an Adw.OverlaySplitView
    toggle = SidebarToggle(sidebar, island=island)     # v71: on a phone, the title island's ☰

v71: navigation is top left, never in the bar. With `island` (a TitleIsland),
the toggle steps aside on a phone and the island's ☰ grows into the sidebar
(F9 still opens it); on a computer the toggle is the toggle again.

Rules every part follows: docs/developer/kit/lumaui-principles.md and
behaviour.md. CSS lives in luma-appkit-base.css under `/* LumaUI: Sidebar
foot */` and `/* LumaUI: Sidebar toggle */`.
"""
from __future__ import annotations

from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gtk, Pango  # noqa: E402

from . import icons, lumaui  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402
from .commands import Command, CommandGroup, CommandRegistry  # noqa: E402
from .structure_adapt import WidthWatch, icon_button, is_phone, window_width  # noqa: E402
from .structure_layers import LayerHost, ModalHandle  # noqa: E402

__all__ = ["SidebarFoot", "FilterHeading", "SidebarToggle"]

#: (key, label, Lucide icon) or (key, label, Lucide icon, count).
Filter = tuple


class FilterHeading(Gtk.Box):
    """"Favourites · Show all" at the top of a filtered list (v70 `lFiltHead`)."""

    __gtype_name__ = "LumaUIFilterHeading"

    def __init__(self, label: str = "", *, on_show_all: Callable[[], None] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, accessible_role=Gtk.AccessibleRole.STATUS)
        self.add_css_class("lumaui-filter-heading")
        self.label = Gtk.Label(label=label, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.label.add_css_class("lumaui-filter-heading-label")
        self.append(self.label)
        self.show_all = Gtk.Button(label="Show all", valign=Gtk.Align.CENTER)
        self.show_all.add_css_class("lumaui-filter-heading-button")
        self.append(self.show_all)
        if on_show_all is not None:
            self.show_all.connect("clicked", lambda _b: on_show_all())

    def set_label(self, label: str) -> None:
        self.label.set_label(label)


class SidebarFoot(Gtk.Box):
    """Search and its actions; placement="header" omits the footer separator/inset."""

    __gtype_name__ = "LumaUISidebarFoot"

    def __init__(self, *, search: "str | Gtk.Widget", on_search: Callable[[str], None] | None = None,
                 filters: Sequence[Filter] = (), filter: str | None = None,
                 on_filter: Callable[[str], None] | None = None,
                 add: tuple[str, str, Callable[[], None]] | None = None,
                 placement: str = "footer") -> None:
        if placement not in ("header", "footer"):
            raise ValueError("SidebarFoot placement is header or footer")
        if filters and len(filters) < 2:
            raise ValueError("a filter picker offers at least two views (the first is 'everything')")
        for spec in filters:
            if len(spec) not in (3, 4):
                raise ValueError("a filter is (key, label, icon) or (key, label, icon, count)")
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.add_css_class("lumaui-sidebar-foot")
        self.placement = placement
        if placement == "header":
            self.add_css_class("header")
        self._filters = [tuple(f[:3]) for f in filters]
        self._counts: dict[str, int | None] = {f[0]: (f[3] if len(f) == 4 else None) for f in filters}
        self._filter = filter if filter is not None else (filters[0][0] if filters else "")
        if self._filters and self._filter not in {k for k, _l, _i in self._filters}:
            raise ValueError(f"unknown filter {self._filter!r}")
        self._on_filter = on_filter
        self._on_search = on_search

        self.clear_button: Gtk.Button | None = None

        # A place field (KB2): PlaceSearch brings its own well and suggestions.
        if isinstance(search, Gtk.Widget):
            self.field = search
            self.entry = getattr(search, "entry", None)
            search.set_hexpand(True)
            self.append(search)
        else:
            # The field: a recessed well with the search glyph and the text.
            self.field = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
            self.field.add_css_class("lumaui-foot-search")
            glyph = icons.image("search")
            glyph.add_css_class("lumaui-foot-search-icon")
            self.field.append(glyph)
            self.entry = Gtk.Text(hexpand=True, placeholder_text=search, valign=Gtk.Align.CENTER,
                                  accessible_role=Gtk.AccessibleRole.SEARCH_BOX)  # v70 input[type=search]
            # Gtk.Text otherwise asks for a 200px natural width. With the add
            # button and edge padding that widens the named 236px sidebar to 256px.
            # This only caps its preferred width; text still scrolls while editing.
            self.entry.set_width_chars(12)
            self.entry.set_max_width_chars(12)
            self.entry.add_css_class("lumaui-foot-search-text")
            self.entry.update_property([Gtk.AccessibleProperty.LABEL], [search])
            self.entry.connect("changed", self._changed)
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", self._key)
            self.entry.add_controller(keys)
            click = Gtk.GestureClick()
            click.connect("released", lambda *_a: self.entry.grab_focus())
            self.field.add_controller(click)
            focus = Gtk.EventControllerFocus()
            focus.connect("enter", lambda *_a: self.field.add_css_class("focused"))
            focus.connect("leave", lambda *_a: self.field.remove_css_class("focused"))
            self.entry.add_controller(focus)
            self.field.append(self.entry)
            self.clear_button = icon_button("x", "Clear search", "lumaui-foot-search-clear")
            self.clear_button.set_valign(Gtk.Align.CENTER)
            self.clear_button.set_visible(False)
            self.clear_button.connect("clicked", lambda _b: self._clear_search())
            self.field.append(self.clear_button)
            self.append(self.field)

        self.heading = FilterHeading(on_show_all=lambda: self.set_filter(self._filters[0][0], notify=True))
        self.heading.set_visible(False)

        self.filter_button: Gtk.Button | None = None
        self.add_button: Gtk.Button | None = None
        if self._filters:
            self.filter_button = icon_button("list-filter", "Show", "lumaui-foot-square")
            self.filter_button.update_property([Gtk.AccessibleProperty.HAS_POPUP], [True])
            self.filter_button.connect("clicked", lambda _b: self.open_filters())
            self.append(self.filter_button)
        if add is not None:
            label, icon, callback = add
            self.add_button = icon_button(icon, label, "lumaui-foot-square")
            self.add_button.connect("clicked", lambda _b: callback())
            self.append(self.add_button)
        self._menu = None
        self._sync_filter()

    # ── search ────────────────────────────────────────────────────────────

    @property
    def text(self) -> str:
        return self.entry.get_text()

    def _clear_search(self) -> None:
        self.entry.set_text("")
        self.entry.grab_focus()

    def _changed(self, _entry: Gtk.Text) -> None:
        if self.clear_button is not None:
            self.clear_button.set_visible(bool(self.entry.get_text()))
        if self._on_search is not None:
            self._on_search(self.entry.get_text())

    def _key(self, _c, keyval: int, _code: int, _state) -> bool:
        if keyval == Gdk.KEY_Escape and self.entry.get_text():
            self.entry.set_text("")  # Esc clears first; a second Esc reaches the window
            return True
        return False

    # ── filter ────────────────────────────────────────────────────────────

    @property
    def filter(self) -> str:
        return self._filter

    def set_filter(self, key: str, *, notify: bool = False) -> None:
        if key not in {k for k, _l, _i in self._filters}:
            raise ValueError(f"unknown filter {key!r}")
        changed = key != self._filter
        self._filter = key
        self._sync_filter()
        if notify and changed and self._on_filter is not None:
            self._on_filter(key)

    def set_filter_counts(self, counts: dict[str, int | None]) -> None:
        """How many each view shows, for the filter menu's count badges."""
        for key, count in counts.items():
            if key not in self._counts:
                raise ValueError(f"unknown filter {key!r}")
            self._counts[key] = count

    def filter_registry(self) -> CommandRegistry:
        """The picker's menu as commands: one checked row per view, with its count."""
        commands = tuple(Command(id=f"lumaui-filter.{key}", label=label, icon=icons.icon_name(icon),
                                 execute=lambda k=key: self.set_filter(k, notify=True),
                                 checked=lambda k=key: self._filter == k, count=self._counts.get(key))
                         for key, label, icon in self._filters)
        return CommandRegistry([CommandGroup("Show", commands)])

    def open_filters(self) -> None:
        """Open the filter menu upward from the picker (a drawer at phone width)."""
        from .menus import command_popover

        if self.filter_button is None:
            return
        menu = command_popover(self.filter_registry())
        menu.set_parent(self.filter_button)
        menu.set_position(Gtk.PositionType.TOP)
        self._menu = menu
        menu.popup()

    def _sync_filter(self) -> None:
        if not self._filters:
            return
        current = next(f for f in self._filters if f[0] == self._filter)
        on = current[0] != self._filters[0][0]
        lumaui.set_css_class(self.filter_button, "on", on)
        self.filter_button.set_tooltip_text(f"Show: {current[1]}")
        self.filter_button.update_property([Gtk.AccessibleProperty.LABEL], [f"Show: {current[1]}"])
        self.heading.set_label(current[1])
        self.heading.set_visible(on)


class SidebarToggle(Gtk.ToggleButton):
    """Hide and show a sidebar: panel-left, F9, and a drawer below `drawer_below`.

    The default 560 is phone width; 901 lets compact layouts use the same drawer.
    Phone-only title islands retain the standard phone breakpoint.
    """

    __gtype_name__ = "LumaUISidebarToggle"

    def __init__(self, sidebar: Gtk.Widget, *, shown: bool = True,
                 closed: Callable[[bool], None] | None = None, island: Gtk.Widget | None = None,
                 drawer_below: int = tokens.PHONE_MAX_WIDTH + 1, phone_enabled: bool = True) -> None:
        if isinstance(drawer_below, bool) or not isinstance(drawer_below, int) or drawer_below <= 0:
            raise ValueError("drawer_below is a positive integer")
        super().__init__()
        self.drawer_below = drawer_below
        self.phone_enabled = bool(phone_enabled)
        self._control_visible = True
        self.island = None
        self.add_css_class("lumaui-sidebar-toggle")
        self.set_child(icons.image("panel-left"))
        self.remove_css_class("image-button")  # a LumaUI part, not the legacy icon-button look
        self.set_valign(Gtk.Align.CENTER)
        self.update_property([Gtk.AccessibleProperty.KEY_SHORTCUTS], ["F9"])
        self.sidebar = sidebar
        self._closed_by_app = closed
        self._drawer: ModalHandle | None = None
        self._home: tuple[Gtk.Widget, Gtk.Widget | None] | None = None
        self._row_handlers: list[tuple[Gtk.Widget, int]] = []
        self._split = _is_overlay_split(sidebar)
        self._revealer: Gtk.Revealer | None = None
        if not self._split and closed is None:
            self._revealer = _wrap_in_revealer(sidebar)
            if self._revealer is not None:
                sidebar.connect("notify::visible", self._sync_revealer_visibility)
                for prop in ("child", "reveal-child", "child-revealed"):
                    self._revealer.connect(f"notify::{prop}", self._sync_revealer_visibility)
                self._sync_revealer_visibility()
        self.set_active(shown)
        self._apply_desktop(shown, animate=False)
        self._label()
        self.connect("toggled", self._toggled)
        # F9 anywhere in the window, as GNOME Files.
        shortcuts = Gtk.ShortcutController(scope=Gtk.ShortcutScope.GLOBAL)
        shortcuts.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("F9"),
                                            action=Gtk.CallbackAction.new(lambda *_a: (self.toggle(), True)[1])))
        self.add_controller(shortcuts)
        self._watch = WidthWatch(self, lambda _w: self._reflow(), threshold=drawer_below - 1,
                                 on_tier=lambda _tier: self._reflow())
        if island is not None:
            self.set_island(island)

    def set_island(self, island: Gtk.Widget | None) -> None:
        """On a phone, `island` (a TitleIsland) is the sidebar's ☰: it grows into the sidebar."""
        if self.island is not None:
            self.island.set_grow(None)
        self.island = island
        if island is not None:
            island.set_grow(self.sidebar, grows="menu")
        self._reflow()

    def _where(self) -> Gtk.Widget:
        """What to measure: the toggle in its title bar, or the sidebar before it is placed."""
        return self if self.get_root() is not None else self.sidebar

    def _uses_drawer(self) -> bool:
        """Layout breakpoint, independent of the phone presentation and its title island."""
        return 0 < window_width(self._where()) < self.drawer_below

    @property
    def shown(self) -> bool:
        """Whether the sidebar shows (beside the content, or as the phone drawer)."""
        if self._drawer is not None:
            return not self._drawer.closed
        return False if self._uses_drawer() and not self._split else self.get_active()

    def set_control_visible(self, visible: bool) -> None:
        """Omit the standalone button while keeping menu and sidebar behavior."""
        self._control_visible = bool(visible)
        self.set_visible(self._control_visible and
                         (self.island is None or not is_phone(self._where())))

    def set_phone_enabled(self, enabled: bool) -> None:
        """Disable phone sidebar navigation when the app has another places control."""
        self.phone_enabled = bool(enabled)
        self._reflow()

    def toggle(self, *, initial_focus: Gtk.Widget | None = None) -> None:
        """What the button and F9 do; an opening drawer can focus its search field."""
        if not self.phone_enabled and is_phone(self._where()):
            return
        if self.island is not None and is_phone(self._where()):
            self.island.toggle()
            return
        if self._uses_drawer() and not self._split:
            if self.shown and self._drawer is not None:
                self._drawer.cancel()
            else:
                self._open_drawer(initial_focus=initial_focus)
            return
        self.set_active(not self.get_active())
        if self.get_active() and initial_focus is not None:
            initial_focus.grab_focus()

    # ── computer ──────────────────────────────────────────────────────────

    def _toggled(self, _button: Gtk.ToggleButton) -> None:
        if not self.phone_enabled and is_phone(self._where()):
            self._apply_desktop(False, animate=False)
            return
        if self._uses_drawer() and not self._split:
            # A click on the button at phone width: the drawer, not the column.
            want = self.get_active()
            self.handler_block_by_func(self._toggled)
            self.set_active(not want)
            self.handler_unblock_by_func(self._toggled)
            self.toggle()
            return
        self._apply_desktop(self.get_active(), animate=True)
        self._label()

    def _sync_revealer_visibility(self, *_args) -> None:
        """An empty column must not reserve its parent box's inter-child gap."""
        revealer = self._revealer
        if revealer is not None:
            # Keep the wrapper allocated through its closing animation. Moving
            # the sidebar into a drawer leaves no column to allocate at all.
            revealer.set_visible(
                revealer.get_child() is self.sidebar and self.sidebar.get_visible()
                and (revealer.get_reveal_child() or revealer.get_child_revealed()))

    def _apply_desktop(self, shown: bool, *, animate: bool) -> None:
        if self._split:
            self.sidebar.set_show_sidebar(shown)
        elif self._closed_by_app is not None:
            self._closed_by_app(shown)
        elif self._revealer is not None:
            self._revealer.set_transition_duration(lumaui.duration("morph") if animate else 0)
            self._revealer.set_reveal_child(shown)

    def _label(self) -> None:
        text = "Hide sidebar" if self.shown else "Show sidebar"
        self.set_tooltip_text(f"{text} (F9)")
        self.update_property([Gtk.AccessibleProperty.LABEL], [text])

    # ── phone ─────────────────────────────────────────────────────────────

    def _reflow(self) -> None:
        phone = is_phone(self._where())
        drawer = self._uses_drawer()
        if phone and not self.phone_enabled:
            if self._drawer is not None:
                self._drawer.close(quiet=True)
                self._drawer_closed(now=True)
            if self.island is not None and self.island.grown:
                self.island.fold()
            self._apply_desktop(False, animate=False)
            self._label()
            return
        if self.has_css_class("phone") != phone or self.has_css_class("drawer-mode") != drawer:
            lumaui.set_css_class(self, "phone", phone)
            lumaui.set_css_class(self, "drawer-mode", drawer)
            self.set_child(icons.image("menu" if drawer else "panel-left"))
            self.remove_css_class("image-button")
        if self.island is not None:
            # v71: a phone's ☰ is the title island's; the title bar's own toggle steps aside.
            self.set_visible(self._control_visible and not phone)
            if not phone and self.island.grown:
                self.island.fold()
                self.island._folded()
        if self._split:
            return
        if drawer:
            # The column gives the content the room; the drawer shows on demand.
            if self._revealer is not None:
                self._revealer.set_transition_duration(0)
                self._revealer.set_reveal_child(False)
            elif self._closed_by_app is not None:
                self._closed_by_app(False)
        else:
            if self._drawer is not None:
                self._drawer.close(quiet=True)
                self._drawer_closed(now=True)
            self._apply_desktop(self.get_active(), animate=False)
        self._label()

    def _open_drawer(self, *, initial_focus: Gtk.Widget | None = None) -> None:
        if self._drawer is not None and not self._drawer.closed:
            return
        host = LayerHost.window_host(self._where())
        parent = self.sidebar.get_parent()
        if parent is None:
            return
        self._home = (parent, self.sidebar.get_prev_sibling())
        if isinstance(parent, Gtk.Revealer):
            parent.set_child(None)
        elif isinstance(parent, Gtk.Box):
            parent.remove(self.sidebar)
        else:
            raise TypeError("SidebarToggle moves a sidebar that lives in a Gtk.Box or a Revealer")
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.DIALOG)
        card.add_css_class("lumaui-sidebar-drawer")
        card.update_property([Gtk.AccessibleProperty.LABEL], ["Sidebar"])
        self.sidebar.set_visible(True)
        # The drawer sets the width (264, or the window's): a sidebar's own width request
        # (a tree asks for 284) steps aside while it is inside, and comes back after.
        self._width_request = self.sidebar.get_size_request()
        self.sidebar.set_size_request(-1, self._width_request[1])
        # The drawer is the floating surface; the sidebar inside it is not a second island.
        self._was_island = self.sidebar.has_css_class("luma-island")
        self.sidebar.remove_css_class("luma-island")
        card.append(self.sidebar)
        handle = host.present_modal(card, on_cancel=self._drawer_closed, drawer=False, initial_focus=initial_focus)
        # A side drawer: the left edge, the full height, and a tap beside it closes it.
        handle.drawer = True
        handle.scrim.add_css_class("drawer")
        card.add_css_class("side-drawer")
        # v70: a list of people (Messages) opens as the whole window less its gutter, on the
        # window's own surface (.mwin.navopen .msidebar); places open as a 264 drawer (.nside, .pside).
        full = getattr(self.sidebar, "variant", None) == "people"
        lumaui.set_css_class(card, "full", full)
        card.set_halign(Gtk.Align.FILL if full else Gtk.Align.START)
        card.set_valign(Gtk.Align.FILL)
        if not full:
            _phone_drawer(card, handle.scrim, host)
        self._drawer = handle
        self._card = card
        for listbox in _descendants(self.sidebar, Gtk.ListBox):
            self._row_handlers.append((listbox, listbox.connect("row-activated", lambda *_a: handle.cancel())))
        self._label()

    def _drawer_closed(self, now: bool = False) -> None:
        for widget, handler in self._row_handlers:
            widget.disconnect(handler)
        self._row_handlers = []
        home, self._home = self._home, None
        card = getattr(self, "_card", None)

        def restore() -> bool:
            if home is None or card is None or self.sidebar.get_parent() is not card:
                return False
            card.remove(self.sidebar)
            if getattr(self, "_width_request", None) is not None:
                self.sidebar.set_size_request(*self._width_request)
                self._width_request = None
            if getattr(self, "_was_island", False):
                self.sidebar.add_css_class("luma-island")
            parent, previous = home
            if isinstance(parent, Gtk.Revealer):
                parent.set_child(self.sidebar)
            else:
                parent.insert_child_after(self.sidebar, previous)
            return False

        if now:
            restore()
        else:
            GLib.timeout_add(max(1, lumaui.duration("dialog_fade")) + 20, restore)
        self._drawer = None
        self._label()


def _phone_drawer(card: Gtk.Widget, scrim: Gtk.Widget, host: Gtk.Widget) -> None:
    """v71's phone drawer (5806-5813): out of the bottom-left corner, starting level with the
    page title (64 from the top of the screen), min(328, w − 52) wide, square to the left and
    bottom edges with one rounded corner at top right, 44 px rows and a 42 px search."""
    drawer = tokens.PHONE_DRAWER
    card.add_css_class("phone-drawer")
    scrim.add_css_class("phone-drawer")
    width = host.get_width()
    if width > 0:
        card.set_size_request(min(drawer["max_width"], width - drawer["side_room"]), -1)
    # 64 from the window's top; a host under a title row (AppWindow) starts that much lower already.
    root = host.get_root()
    above = 0
    if root is not None and root is not host:
        ok, point = host.compute_point(root, Graphene.Point())
        above = round(point.y) if ok else 0
    card.set_margin_top(max(0, drawer["top"] - above))


def _is_overlay_split(widget: Gtk.Widget) -> bool:
    try:
        gi.require_version("Adw", "1")
        from gi.repository import Adw
    except (ImportError, ValueError):
        return False
    return isinstance(widget, Adw.OverlaySplitView)


def _wrap_in_revealer(sidebar: Gtk.Widget) -> Gtk.Revealer | None:
    """Put a Box-held sidebar in a revealer in place, so closing slides with the window's ease."""
    parent = sidebar.get_parent()
    if isinstance(parent, Gtk.Revealer):
        parent.add_css_class("lumaui-sidebar-revealer")
        return parent
    revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_RIGHT, reveal_child=True,
                            transition_duration=lumaui.duration("morph"))
    revealer.add_css_class("lumaui-sidebar-revealer")
    if isinstance(parent, Gtk.Box):
        previous = sidebar.get_prev_sibling()
        parent.remove(sidebar)
        revealer.set_child(sidebar)
        parent.insert_child_after(revealer, previous)
        return revealer
    if parent is None:
        revealer.set_child(sidebar)  # the app appends toggle.sidebar_widget
        return revealer
    raise TypeError("SidebarToggle needs the sidebar in a Gtk.Box (or not yet placed)")


def _descendants(widget: Gtk.Widget, kind: type):
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, kind):
            yield child
        yield from _descendants(child, kind)
        child = child.get_next_sibling()
