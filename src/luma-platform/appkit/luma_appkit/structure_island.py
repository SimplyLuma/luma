# SPDX-License-Identifier: Apache-2.0
"""LumaUI structure: the title island (v71 `lNavTtl`, `.fisl`, `.nisl`, `.misl`, `.crisl`).

On a phone an app names where you are in one island at the top left, in the
bar's material: the lead (☰ for the app's places at a top level, ‹ Back when
you are inside something) as a 48 px square, a full-height hairline, then the
title with a subtitle under it, and optional faces or trailing buttons. It is
48 tall and sits 12 px from the left, at top 52 (6 px under the title row).

Tap it and it grows down into its menu (the places, ☰ becoming ✕) or into its
information (details), as one piece of glass: the island's row stays as the
header, with a hairline under it, and the panel continues below. ✕, Back, a
second tap, Esc, a tap beside it or choosing a place folds it.

    island = TitleIsland(title="Launch", subtitle="Priya is here · Edited just now",
                         lead="back", on_lead=self.go_back, grow=self.note_details)
    island.float_over(page)                # top left of the window, over `page`
    island.set_title("Launch", "Edited Sep 20")

    head = TitleIsland(lead=None, on_title=self.month_picker)          # Calendar's head
    head.set_title_content(month_label)
    head.add_trailing(dots_button)
    head.follow(month_scroller, until=90)

    places = TitleIsland(title="Home", subtitle="6 items", grow=sidebar)   # ☰ grows into the sidebar
    SidebarToggle(sidebar, island=places)  # F9 and the desktop toggle stay the toggle's

By default the island shows only at phone tier (under 560); `phone_only=False`
keeps it at every width (it grows to min(360, window − 24) in a window).
CSS lives in luma-appkit-base.css under `/* LumaUI: Title island */`.
"""
from __future__ import annotations

from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, GObject, Gtk, Pango  # noqa: E402

from . import icons, lumaui  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402
from .structure_adapt import window_tier, window_width  # noqa: E402

__all__ = ["TitleIsland", "LEADS"]

#: The lead glyphs: ☰ (the app's places) and ‹ (up one level). None: no lead (Calendar's head).
LEADS = {"menu": ("menu", "Places"), "back": ("chevron-left", "Back"), None: (None, "")}

#: v71 metrics (luma-next-71 5323-5433, .fisl/.nisl/.misl), from design-tokens.d/structure-nav.json.
_M = tokens.TITLE_ISLAND
HEIGHT = _M["height"]
LEFT = _M["left"]
TOP = _M["top"]                      # under the 46 px title row: 52 from the window's top
MENU_WIDTH = _M["menu_width"]        # ☰ grown: min(320, window − 24)
DETAILS_WIDTH = _M["details_width"]  # details grown in a window: min(360, window − 24); a phone: window − 24
MENU_FOOT = _M["menu_foot"]          # the grown menu stops 128 above the window's foot
DETAILS_FOOT = _M["details_foot"]


class _NoNaturalWidth(Gtk.Widget):
    """Its child at whatever width it is given, never asking for more than nothing (it ellipsizes)."""

    def __init__(self, child: Gtk.Widget) -> None:
        super().__init__(hexpand=False)
        self.child = child
        child.set_parent(self)

    def do_measure(self, orientation, for_size):
        if not self.child.get_visible() or orientation == Gtk.Orientation.HORIZONTAL:
            return 0, 0, -1, -1
        minimum, natural, _b, _n = self.child.measure(orientation, for_size)
        return minimum, natural, -1, -1

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        if self.child.get_visible():
            self.child.allocate(width, height, baseline, None)

    def do_dispose(self) -> None:
        if self.child is not None:
            self.child.unparent()
            self.child = None
        super().do_dispose()


class TitleIsland(Gtk.Box):
    """☰ or ‹ | title and subtitle, in the bar's material; grows into its menu or its details."""

    __gtype_name__ = "LumaUITitleIsland"
    __gsignals__ = {
        # The lead was pressed while folded (Back, or ☰ with nothing to grow into).
        "lead": (GObject.SignalFlags.RUN_LAST, None, ()),
        # The island grew (True) or folded (False).
        "grown": (GObject.SignalFlags.RUN_LAST, None, (bool,)),
    }

    def __init__(self, title: str = "", subtitle: str = "", *, lead: str | None = "menu",
                 lead_label: str | None = None, on_lead: Callable[[], None] | None = None,
                 grow: "Gtk.Widget | Callable[[], Gtk.Widget] | None" = None, grows: str | None = None,
                 faces: Gtk.Widget | None = None, trailing: Sequence[Gtk.Widget] = (),
                 on_title: Callable[[], None] | None = None, phone_only: bool = True,
                 disclosure: bool = False, lead_icon: str | None = None, variant: str = "standard",
                 clip_subtitle: bool = False) -> None:
        if lead not in LEADS:
            raise ValueError(f"lead is 'menu', 'back' or None, not {lead!r}")
        if grows not in (None, "menu", "details"):
            raise ValueError("grows is 'menu' or 'details'")
        super().__init__(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.START, valign=Gtk.Align.START)
        self.add_css_class("lumaui-title-island")
        self.set_name("lumaui-title-island")
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self._variant = "standard"
        self._on_lead = on_lead
        self._on_title = on_title
        self._grow = grow
        self._grows = grows
        self._grown = False
        self._panel_child: Gtk.Widget | None = None
        self._home: tuple[Gtk.Widget, Gtk.Widget | None] | None = None
        self._row_handlers: list[tuple[Gtk.Widget, int]] = []
        self._scrim: Gtk.Widget | None = None
        self._host: Gtk.Overlay | None = None
        self._phone_only = phone_only
        self._tier_handler: tuple[GObject.Object, int] | None = None
        self._width_handler: int | None = None
        self._size_clock = None
        self._lead = lead

        # The row: lead | title (and faces) | trailing buttons. It stays the header when grown.
        self.row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.row.add_css_class("lumaui-title-island-row")
        self.append(self.row)
        self.lead_button = self._make_lead(lead == "menu")
        self.row.append(self.lead_button)

        self.title_button = Gtk.Button(hexpand=False)
        self.title_button.add_css_class("lumaui-title-island-title")
        self.title_button.connect("clicked", lambda _b: self._title_pressed())
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        inner.add_css_class("lumaui-title-island-inner")
        self.faces_slot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, valign=Gtk.Align.CENTER)
        self.faces_slot.add_css_class("lumaui-title-island-faces")
        self.faces_slot.set_visible(False)
        inner.append(self.faces_slot)
        self._inner = inner
        self.status_dot = Gtk.Box(valign=Gtk.Align.CENTER, visible=False)
        self.status_dot.add_css_class("lumaui-title-island-status")
        inner.append(self.status_dot)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        self._text = text
        text.add_css_class("lumaui-title-island-text")
        self.title_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, single_line_mode=True)
        self.title_label.add_css_class("lumaui-title-island-label")
        self.subtitle_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, single_line_mode=True)
        self.subtitle_label.add_css_class("lumaui-title-island-subtitle")
        text.append(self.title_label)
        # The subtitle line: a presence dot ("here") before the words (v71 .nisli small .ond).
        self.subtitle_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.subtitle_row.add_css_class("lumaui-title-island-subtitle-row")
        self.presence_dot = Gtk.Box(valign=Gtk.Align.CENTER, visible=False)
        self.presence_dot.add_css_class("lumaui-title-island-presence")
        self.subtitle_row.append(self.presence_dot)
        self.subtitle_row.append(self.subtitle_label)
        if clip_subtitle:
            # v71 .crsubj: the words are one grid track as wide as the title; a longer subtitle ellipsizes.
            text.append(_NoNaturalWidth(self.subtitle_row))
        else:
            text.append(self.subtitle_row)
        inner.append(text)
        # v71 Charlie's subject pill (`.crsubj`): a chevron after the words says the title opens something.
        self.disclosure = icons.image("chevron-down")
        self.disclosure.add_css_class("lumaui-title-island-disclosure")
        self.disclosure.set_valign(Gtk.Align.CENTER)
        self.disclosure.set_visible(disclosure)
        inner.append(self.disclosure)
        self.title_button.set_child(inner)
        self.row.append(self.title_button)

        self.trailing = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.trailing.add_css_class("lumaui-title-island-trailing")
        self.trailing.set_visible(False)
        self.row.append(self.trailing)
        for widget in trailing:
            self.add_trailing(widget)
        if faces is not None:
            self.set_faces(faces)

        # The panel the island grows into, inside the same glass.
        self.revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                                     transition_duration=lumaui.duration("grow"), reveal_child=False)
        self.revealer.add_css_class("lumaui-title-island-revealer")
        self.panel = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
        self.panel.add_css_class("lumaui-title-island-panel")
        self.panel.get_vadjustment().connect("value-changed", self._scrolled)
        self.revealer.set_child(self.panel)
        self.append(self.revealer)
        self.revealer.connect("notify::child-revealed", self._revealed)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

        self._lead_icon = None
        self.set_lead(lead, lead_label)
        self.set_lead_icon(lead_icon)
        self.set_title(title, subtitle)
        self.set_variant(variant)
        self.connect("notify::root", self._rooted)

    # ── what it says ──────────────────────────────────────────────────────

    @property
    def title(self) -> str:
        return self.title_label.get_label()

    @property
    def subtitle(self) -> str:
        return self.subtitle_label.get_label()

    def set_title(self, title: str, subtitle: str | None = None) -> None:
        """Name the place; `subtitle=None` keeps the current one, "" removes it."""
        self.title_label.set_label(title)
        if subtitle is not None:
            self.subtitle_label.set_label(subtitle)
            self.subtitle_label.set_visible(bool(subtitle))
        self.subtitle_row.set_visible(self.subtitle_label.get_visible() or self.presence_dot.get_visible())
        self._describe()
        self._sync_alone()

    def set_variant(self, variant: str) -> None:
        """The standard divided 48px island or creative single Back/title control (.rlback2)."""
        if variant not in ("standard", "creative"):
            raise ValueError("variant is standard or creative")
        self._variant = variant
        lumaui.set_css_class(self, "creative", variant == "creative")
        self._sync_alone()

    def set_subtitle(self, subtitle: str) -> None:
        self.set_title(self.title, subtitle)

    @property
    def lead(self) -> str | None:
        return self._lead

    def set_lead(self, lead: str | None, label: str | None = None) -> None:
        """☰ ("menu"), ‹ ("back"; `label` names where it goes, "Back to Projects") or None."""
        if lead not in LEADS:
            raise ValueError(f"lead is 'menu', 'back' or None, not {lead!r}")
        self._lead = lead
        self._remerge((lead == "menu" or (self._variant == "creative" and lead == "back")) and self.title_button.get_visible())
        glyph, default = LEADS[lead]
        self._lead_glyph = (self._lead_icon or glyph) if lead == "menu" else glyph
        self._lead_label = label or default
        self.lead_button.set_visible(lead is not None)
        lumaui.set_css_class(self, "no-lead", lead is None)
        self._sync_lead()
        self._sync_alone()

    def set_lead_icon(self, icon: str | None) -> None:
        """Use a semantic icon for the menu lead (Disks' drive kind), or None for ☰.

        This keeps the menu's shared title control and close-while-grown behavior.
        Back and lead-less islands retain their standard presentation.
        """
        if icon is not None and (not isinstance(icon, str) or not icon.strip()):
            raise ValueError("lead_icon is a nonempty icon name or None")
        self._lead_icon = icon
        glyph = LEADS[self._lead][0]
        self._lead_glyph = (icon or glyph) if self._lead == "menu" else glyph
        self._sync_lead()

    def _remerge(self, merged: bool) -> None:
        if merged != getattr(self, "_lead_merged", merged) and self.lead_button.get_parent() is self.row:
            # ☰ and the title are one control (v71 lNavTtl's one button); ‹ is its own.
            button = self._make_lead(merged)
            self.row.insert_child_after(button, self.lead_button)
            self.row.remove(self.lead_button)
            self.lead_button = button
            button.set_visible(self._lead is not None)
        self._lead_merged = merged

    def _make_lead(self, merged: bool) -> Gtk.Button:
        """The lead square. Beside a title that does the same (☰), it is part of that one
        control for assistive technology and the keyboard; Back is a control of its own."""
        button = Gtk.Button(accessible_role=Gtk.AccessibleRole.PRESENTATION if merged else Gtk.AccessibleRole.BUTTON,
                            focusable=not merged)
        button.add_css_class("lumaui-title-island-lead")
        button.remove_css_class("image-button")
        button.connect("clicked", lambda _b: self._lead_pressed())
        self._lead_merged = merged
        return button

    def set_faces(self, faces: Gtk.Widget | None) -> None:
        """Faces before the title (Messages' avatar, Charlie's stack), or None."""
        while child := self.faces_slot.get_first_child():
            self.faces_slot.remove(child)
        if faces is not None:
            self.faces_slot.append(faces)
        self.faces_slot.set_visible(faces is not None)
        lumaui.set_css_class(self, "has-faces", faces is not None)
        self._sync_alone()

    def set_status(self, status: str | None) -> None:
        """A dot before the title: "record" (the record red, pulsing), "paused" (dimmed, still) or None.
        "here" is a different dot: the green presence dot before the subtitle (v71 Notes: ● Priya is here ·
        Edited just now).

        v71 Memos while recording: ‹ | ● New memo / Recording · Studio."""
        if status not in (None, "record", "paused", "here"):
            raise ValueError("status is 'record', 'paused', 'here' or None")
        for name in ("record", "paused"):
            lumaui.set_css_class(self.status_dot, name, status == name)
        self.status_dot.set_visible(status in ("record", "paused"))
        self.presence_dot.set_visible(status == "here")
        self.subtitle_row.set_visible(self.subtitle_label.get_visible() or status == "here")
        self._sync_alone()

    def set_title_content(self, content: Gtk.Widget | None) -> None:
        """Draw the title half yourself (Calendar: "September 2026 ⌄"); None restores title and subtitle.

        It stays one button: tapping it grows the island, or calls `on_title`.
        """
        current = self._text.get_next_sibling()
        if current is not None:
            self._inner.remove(current)
        self._text.set_visible(content is None)
        if content is not None:
            content.set_valign(Gtk.Align.CENTER)
            self._inner.append(content)
        self._sync_alone()

    def follow(self, scroller: Gtk.ScrolledWindow, *, until: int = 90, fade: int = 60) -> None:
        """Fade the island away as `scroller` scrolls (v71 Calendar's Month): gone after `fade` px.

        GTK cannot translate a widget, so the island fades and stops taking taps
        instead of sliding up with the page; it returns when the page comes back.
        """
        adjustment = scroller.get_vadjustment()

        def moved(adj: Gtk.Adjustment) -> None:
            y = min(max(adj.get_value(), 0), until)
            opacity = max(0.0, 1 - y / fade)
            self.set_opacity(opacity)
            self.set_can_target(opacity > 0.05)

        adjustment.connect("value-changed", moved)
        moved(adjustment)

    def add_trailing(self, widget: Gtk.Widget) -> Gtk.Widget:
        """A trailing button past a hairline (Messages' Call and Video): 44 × 48."""
        widget.add_css_class("lumaui-title-island-button")
        widget.remove_css_class("image-button")
        self.trailing.append(widget)
        self.trailing.set_visible(True)
        self._sync_alone()
        return widget

    @staticmethod
    def button(icon: str, label: str, callback: Callable[[], None]) -> Gtk.Button:
        """A trailing button for `add_trailing`: the glyph, its name as tooltip and accessible label."""
        button = Gtk.Button(tooltip_text=label)
        button.set_child(icons.image(icon))
        button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        button.connect("clicked", lambda _b: callback())
        return button

    def _sync_alone(self) -> None:
        """No title, subtitle, faces, status or trailing button: the island is only its lead square
        (v71 lNavTtl(w, null): Ari's welcome with ☰ alone, a person's card with ‹ alone), no hairline.
        The title part returns with the first thing to say."""
        if not hasattr(self, "trailing"):
            return
        alone = (self._lead is not None and not self.title and not self.subtitle
                 and not self.faces_slot.get_visible() and not self.status_dot.get_visible()
                 and not self.presence_dot.get_visible()
                 and not self.trailing.get_visible() and self._text.get_visible())
        self.title_button.set_visible(not alone)
        lumaui.set_css_class(self, "lead-only", alone)
        # ☰ alone is a control of its own (focusable, named); beside a title it is part of that one.
        if hasattr(self, "_lead_glyph"):
            self._remerge((self._lead == "menu" or (self._variant == "creative" and self._lead == "back")) and not alone)
            self._sync_lead()

    def _describe(self) -> None:
        title = self.title
        if self._grow is not None:
            spoken = f"{title}. {'Show the places' if self._kind() == 'menu' else 'Show the details'}"
        else:
            spoken = title
        self.title_button.update_property([Gtk.AccessibleProperty.LABEL], [spoken])
        self.title_button.set_tooltip_text(self.subtitle or None)

    def _sync_lead(self) -> None:
        # ☰ becomes ✕ while grown into the places; ‹ stays ‹ (it folds first).
        closing = self._grown and self._lead == "menu"
        glyph = "x" if closing else self._lead_glyph
        if glyph is not None:
            self.lead_button.set_child(icons.image(glyph))
        label = "Close" if closing else self._lead_label
        # ☰ beside its title is part of the title's control; it names itself only as ✕.
        self.lead_button.set_tooltip_text(label if closing or not self._lead_merged else None)
        self.lead_button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        if self._lead == "menu" or closing:
            self.lead_button.update_state([Gtk.AccessibleState.EXPANDED], [GObject.Value(GObject.TYPE_INT, int(self._grown))])

    # ── growing ───────────────────────────────────────────────────────────

    @property
    def grown(self) -> bool:
        return self._grown

    def set_grow(self, grow: "Gtk.Widget | Callable[[], Gtk.Widget] | None", grows: str | None = None) -> None:
        """What a tap grows into: a widget (the sidebar), a callable building one, or None."""
        if self._grown:
            self.fold()
        self._grow = grow
        if grows is not None:
            self._grows = grows
        self._describe()

    def _kind(self) -> str:
        if self._grows is not None:
            return self._grows
        return "menu" if self._lead == "menu" else "details"

    def _lead_pressed(self) -> None:
        if self._grown:
            self.fold()
            return
        if self._lead == "menu" and self._grow is not None:
            self.grow_into()
            return
        if self._on_lead is not None:
            self._on_lead()
        self.emit("lead")

    def _title_pressed(self) -> None:
        if self._grown:
            self.fold()
        elif self._grow is not None:
            self.grow_into()
        elif self._on_title is not None:
            self._on_title()
        elif self._lead == "menu" or (self._variant == "creative" and self._lead == "back"):
            self._lead_pressed()

    def toggle(self) -> None:
        self.fold() if self._grown else self.grow_into()

    def grow_into(self, widget: Gtk.Widget | None = None) -> bool:
        """Grow down into `widget` (or what `grow` gives). Returns whether it grew."""
        target = widget if widget is not None else self._grow
        if callable(target) and not isinstance(target, Gtk.Widget):
            target = target()
        if target is None:
            return False
        if self._grown:
            self._release_child()
        self._take(target)
        self._grown = True
        self.add_css_class("grown")
        lumaui.set_css_class(self, "menu", self._kind() == "menu")
        self._size()
        self._queue_size()
        self._show_scrim(True)
        self.revealer.set_transition_duration(lumaui.duration("grow"))
        self.revealer.set_reveal_child(True)
        self._sync_lead()
        self.emit("grown", True)
        return True

    def fold(self) -> None:
        """Fold back to the island's row."""
        if not self._grown:
            return
        self._grown = False
        self.revealer.set_transition_duration(lumaui.duration("grow") // 2)
        self.revealer.set_reveal_child(False)
        self._show_scrim(False)
        self.remove_css_class("scrolled")
        self._sync_lead()
        if not self.revealer.get_transition_duration() or not self.get_mapped():
            self._folded()
        self.emit("grown", False)

    def _revealed(self, *_args) -> None:
        if not self._grown and not self.revealer.get_child_revealed():
            self._folded()

    def _folded(self) -> None:
        if self._grown:
            return
        self.remove_css_class("grown")
        self.remove_css_class("menu")
        self.set_size_request(-1, -1)
        self._release_child()

    def _take(self, child: Gtk.Widget) -> None:
        parent = child.get_parent()
        if parent is not None and parent is not self.panel and parent.get_parent() is not self.panel:
            # A sidebar that lives in the page: borrow it, put it back on fold.
            self._home = (parent, child.get_prev_sibling())
            if isinstance(parent, Gtk.Revealer):
                self._home_reveal = parent.get_reveal_child()
                parent.set_child(None)
            elif isinstance(parent, Gtk.Box):
                parent.remove(child)
            else:
                raise TypeError("TitleIsland borrows a widget that lives in a Gtk.Box or a Revealer")
            self._was_island = child.has_css_class("luma-island")
            child.remove_css_class("luma-island")
            self._width_request = child.get_size_request()
            child.set_size_request(-1, self._width_request[1])
            child.set_visible(True)
        self._panel_child = child
        # A borrowed sidebar scrolls inside its own scroller, which asks for almost no height; while it is
        # the island's panel it asks for all its rows, and the panel (capped to the screen) scrolls instead.
        self._inner_scrollers = [(s, s.get_propagate_natural_height())
                                 for s in _descendants(child, Gtk.ScrolledWindow)]
        for scroller, _was in self._inner_scrollers:
            scroller.set_propagate_natural_height(True)
        self.panel.set_child(child)
        if self._kind() == "menu":
            # Picking a place folds it.
            lists = [child] if isinstance(child, Gtk.ListBox) else []
            for listbox in lists + list(_descendants(child, Gtk.ListBox)):
                self._row_handlers.append((listbox, listbox.connect("row-activated", lambda *_a: self.fold())))

    def _release_child(self) -> None:
        for widget, handler in self._row_handlers:
            widget.disconnect(handler)
        self._row_handlers = []
        child, self._panel_child = self._panel_child, None
        if child is None:
            return
        for scroller, was in getattr(self, "_inner_scrollers", ()):
            scroller.set_propagate_natural_height(was)
        self._inner_scrollers = []
        self.panel.set_child(None)
        home, self._home = self._home, None
        if home is None:
            return
        if getattr(self, "_was_island", False):
            child.add_css_class("luma-island")
        child.set_size_request(*self._width_request)
        parent, previous = home
        if isinstance(parent, Gtk.Revealer):
            parent.set_child(child)
        else:
            parent.insert_child_after(child, previous)

    def _queue_size(self, *_args) -> None:
        if not self._grown or self._size_clock is not None:
            return
        self._size()
        clock = self.get_frame_clock()
        if clock is None:
            return
        def after_paint(clock):
            clock.disconnect(self._size_clock[1])
            self._size_clock = None
            if self._grown:
                self._size()
        self._size_clock = (clock, clock.connect_after("after-paint", after_paint))
        self.queue_draw()

    def _size(self) -> None:
        width = window_width(self) or 0
        height = self._host.get_height() if self._host is not None else 0
        if not height:
            root = self.get_root()
            height = root.get_height() if root is not None else 0
        if not width:
            return
        left = _M["creative"]["left"] if self._variant == "creative" else LEFT
        side = 2 * left
        if self._kind() == "menu":
            grown = min(MENU_WIDTH, width - side)
            foot = MENU_FOOT
        else:
            phone = window_tier(self._tier_anchor()).tier == "phone"
            grown = width - side if phone else min(DETAILS_WIDTH, width - side)
            foot = DETAILS_FOOT
        self.set_size_request(max(0, grown + (left if self.has_css_class("floating") else 0)), -1)
        if height:
            self.panel.set_max_content_height(max(120, height - TOP - HEIGHT - foot))

    def _scrolled(self, adjustment: Gtk.Adjustment) -> None:
        # Once the list scrolls, the header row casts a soft shadow over it, like a sticky header.
        lumaui.set_css_class(self, "scrolled", self._grown and adjustment.get_value() > 2)

    def _key(self, _controller, keyval: int, _code: int, _state) -> bool:
        if keyval == Gdk.KEY_Escape and self._grown:
            self.fold()
            return True
        return False

    # ── where it sits ─────────────────────────────────────────────────────

    def float_over(self, where: Gtk.Widget) -> "TitleIsland":
        """Float at the top left of the window `where` is in (12 from the left, top 52).

        `where` is an overlay (a LayerHost, or the app's own Gtk.Overlay), or any
        widget inside a window, whose window layer host is used.
        """
        if isinstance(where, Gtk.Overlay):
            host = where
        else:
            from .structure_layers import LayerHost
            host = LayerHost.window_host(where)
        parent = self.get_parent()
        if parent is not None:
            if isinstance(parent, Gtk.Overlay):
                parent.remove_overlay(self)
            else:
                parent.remove(self)
        self._host = host
        self.add_css_class("floating")
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)
        host.add_overlay(self)
        host.set_clip_overlay(self, False)
        host.set_measure_overlay(self, False)
        return self

    def _show_scrim(self, on: bool) -> None:
        host = self._host
        if not on:
            if self._scrim is not None:
                scrim, self._scrim = self._scrim, None
                scrim.remove_css_class("shown")
                GLib.timeout_add(max(1, lumaui.duration("scrim")) + 20,
                                 lambda: (scrim.get_parent() is not None and scrim.unparent(), False)[1])
            return
        if host is None or self._scrim is not None or self.get_parent() is not host:
            return
        scrim = Gtk.Box(hexpand=True, vexpand=True, can_focus=False)
        scrim.add_css_class("lumaui-title-island-scrim")
        click = Gtk.GestureClick()
        click.connect("released", lambda *_a: self.fold())
        scrim.add_controller(click)
        # Under the island, over the page: a tap beside the grown island folds it.
        scrim.insert_before(host, self)
        self._scrim = scrim
        lumaui.on_next_frame(scrim, lambda: scrim.add_css_class("shown"))

    # ── tiers ─────────────────────────────────────────────────────────────

    def _tier_anchor(self) -> Gtk.Widget:
        from .structure_layers import LayerHost

        node = self.get_parent()
        while node is not None:
            if isinstance(node, LayerHost) and node.layer_name == "window":
                return node
            node = node.get_parent()
        return self.get_root() or self

    def _rooted(self, *_args) -> None:
        if self._tier_handler is not None:
            watch, handler = self._tier_handler
            watch.disconnect(handler)
            if self._width_handler is not None:
                watch.disconnect(self._width_handler)
                self._width_handler = None
            self._tier_handler = None
        self._width_handler = None
        if self.get_root() is None:
            return
        watch = window_tier(self._tier_anchor())
        self._tier_handler = (watch, watch.connect("tier-changed", lambda _w, tier: self._tiered(tier)))
        self._width_handler = watch.connect("width-changed", self._queue_size)
        watch.schedule()
        if watch._tier is not None:
            self._tiered(watch._tier)

    def _tiered(self, tier: str) -> None:
        lumaui.set_css_class(self, "phone", tier == "phone")
        # The narrow layout keeps the island's material on desktop windows too.
        # Device capability controls system chrome, not the app's floating surface.
        lumaui.set_css_class(self, "flat", self._phone_only and tier != "phone"
                             and not lumaui.mobile_form_factor())
        lumaui.set_css_class(self, "desk", not self._phone_only and tier != "phone")
        if self._phone_only:
            if tier != "phone" and self._grown:
                self.fold()
                self._folded()
            # The app owns visible; the tier only gates layout/mapping.
            self.set_child_visible(tier == "phone")
        if self._grown:
            self._size()

    @property
    def phone_only(self) -> bool:
        return self._phone_only

    def set_phone_only(self, phone_only: bool) -> None:
        self._phone_only = phone_only
        if not phone_only:
            self.set_child_visible(True)
        elif self._tier_handler is not None:
            self._tiered(self._tier_handler[0].tier)


def _descendants(widget: Gtk.Widget, kind: type):
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, kind):
            yield child
        yield from _descendants(child, kind)
        child = child.get_next_sibling()
