# SPDX-License-Identifier: Apache-2.0
"""LumaUI structure: list-first on a phone (v71 phone.js `stackStart`, `.phstack`).

Mail, Messages, Contacts, Settings, Memos and Notes open on a phone on their
full-screen list with a large title. Picking an item pushes its page in from
the right; Back (top left: the page's title island, or the floating ‹) goes
up one level to the list. On a computer the two sit side by side, as the app
lays them out.

    stack = ListFirst(sidebar, page, title="Messages")
    window.body.append(stack)
    stack.attach_island(thread_island)   # the page's TitleIsland ‹ returns to the list
    stack.show_detail()                  # an app that opens an item itself

Picking a row in a Gtk.ListBox inside the list pushes the page by itself.
`stack.showing` is "list" or "detail"; the `showing` signal follows it.
CSS lives in luma-appkit-base.css under `/* LumaUI: List first */`.
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk, Pango  # noqa: E402

from . import icons, lumaui  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402
from .structure_adapt import window_tier  # noqa: E402

__all__ = ["ListFirst"]


class ListFirst(Gtk.Box):
    """A list and its page: side by side on a computer, list first on a phone."""

    __gtype_name__ = "LumaUIListFirst"
    __gsignals__ = {"showing": (GObject.SignalFlags.RUN_LAST, None, (str,))}

    def __init__(self, list_page: Gtk.Widget, detail_page: Gtk.Widget, title: str = "", *,
                 on_back: Callable[[], None] | None = None, push_on_activate: bool = True,
                 back_label: str | None = None, status_inset: bool = True) -> None:
        """`status_inset=False`: the list page runs to the top on a phone (it floats a title island over
        itself at 52 and pads its own rows, as v71 Charlie's does); otherwise the list sits under the
        phone's 44 status area."""
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.add_css_class("lumaui-list-first")
        self.list_page, self.detail_page = list_page, detail_page
        self._on_back = on_back
        self._showing = "list"
        self._phone = False
        self._handlers: list[tuple[Gtk.Widget, int]] = []

        # A computer: the list and its page side by side (the app's own widths).
        self.split = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)
        self.split.add_css_class("lumaui-list-first-split")
        # A phone: the list full screen under its large title, the page pushed over it.
        self.stack = Gtk.Stack(hexpand=True, vexpand=True, transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
                               transition_duration=tokens.LIST_FIRST["push_ms"])
        self.stack.add_css_class("lumaui-list-first-stack")
        self.list_screen = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.list_screen.add_css_class("lumaui-list-first-list")
        if not status_inset:
            self.list_screen.add_css_class("full-height")
        self.title_label = Gtk.Label(label=title, xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                     accessible_role=Gtk.AccessibleRole.HEADING)
        self.title_label.add_css_class("lumaui-list-first-title")
        self.title_label.set_visible(bool(title))
        self.list_screen.append(self.title_label)
        self.stack.add_named(self.list_screen, "list")
        self.detail_screen = Gtk.Overlay(hexpand=True, vexpand=True)
        self.detail_screen.add_css_class("lumaui-list-first-page")
        self.stack.add_named(self.detail_screen, "detail")
        # The floating ‹ (v71 .phback), until the page has a title island of its own.
        self.back_button = Gtk.Button(halign=Gtk.Align.START, valign=Gtk.Align.START, tooltip_text="Back")
        self.back_button.add_css_class("lumaui-list-first-back")
        self.back_button.update_property([Gtk.AccessibleProperty.LABEL], [f"Back to {title}" if title else "Back"])
        self.set_back_label(back_label)
        self.back_button.connect("clicked", lambda _b: self.show_list())
        self.detail_screen.add_overlay(self.back_button)
        self._island = None

        self.append(self.split)
        # Keep the first minimum size narrow until the root has an allocation.
        # Otherwise the split can reject a phone compositor's first configure.
        self._place(True)
        if push_on_activate:
            lists = [list_page] if isinstance(list_page, Gtk.ListBox) else []
            for listbox in lists + list(_descendants(list_page, Gtk.ListBox)):
                self._handlers.append((listbox, listbox.connect("row-activated", lambda *_a: self.show_detail())))
        self._watch = None
        self.connect("notify::root", self._rooted)

    # ── where things are ──────────────────────────────────────────────────

    @property
    def phone(self) -> bool:
        return self._phone

    @property
    def showing(self) -> str:
        """"list" or "detail": what a phone shows (a computer shows both)."""
        return self._showing

    def set_title(self, title: str) -> None:
        self.title_label.set_label(title)
        self.title_label.set_visible(bool(title))

    def set_back_label(self, text: str | None) -> None:
        """The floating ‹ with the list's name beside it, "‹ Recents" as one pill (v71 .pnlback); None: ‹ alone."""
        self.back_label = text or None
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.CENTER)
        row.append(icons.image("chevron-left"))
        if text:
            word = Gtk.Label(label=text, ellipsize=Pango.EllipsizeMode.END)
            word.add_css_class("lumaui-list-first-back-label")
            row.append(word)
            self.back_button.update_property([Gtk.AccessibleProperty.LABEL], [f"Back to {text}"])
        self.back_button.set_child(row)
        lumaui.set_css_class(self.back_button, "labelled", bool(text))

    def attach_island(self, island) -> None:
        """The page's TitleIsland: its ‹ goes back to the list, and the floating ‹ steps aside."""
        self._island = island
        island.connect("lead", lambda *_a: self.show_list() if self._phone else None)
        self._sync_back()

    def show_detail(self) -> None:
        """Push the page (a phone); a computer already shows it."""
        self._show("detail")

    def show_list(self) -> None:
        """Back to the list (a phone)."""
        if self._showing == "list":
            return
        self._show("list")
        if self._on_back is not None:
            self._on_back()

    def _show(self, which: str) -> None:
        self._showing = which
        if self._phone:
            self.stack.set_transition_duration(lumaui.duration("morph") and tokens.LIST_FIRST["push_ms"])
            self.stack.set_visible_child_name(which)
        self._sync_back()
        self.emit("showing", which)

    def _sync_back(self) -> None:
        self.back_button.set_visible(self._phone and self._island is None)

    # ── tiers ─────────────────────────────────────────────────────────────

    def _rooted(self, *_args) -> None:
        root = self.get_root()
        if root is None or self._watch is not None:
            return
        from .structure_layers import LayerHost

        anchor = self.get_parent()
        while anchor is not None and not (isinstance(anchor, LayerHost) and anchor.layer_name == "window"):
            anchor = anchor.get_parent()
        self._watch = window_tier(anchor or root)
        self._watch.connect("tier-changed", lambda _w, tier: self._place(tier == "phone"))
        self._watch.schedule()
        if self._watch._tier is not None:
            self._place(self._watch._tier == "phone")

    def _place(self, phone: bool) -> None:
        """Move the list and the page into the phone's stack, or side by side."""
        if phone == self._phone and self.list_page.get_parent() is not None:
            return
        self._phone = phone
        for page in (self.list_page, self.detail_page):
            parent = page.get_parent()
            if parent is None:
                continue
            if isinstance(parent, Gtk.Overlay) and parent.get_child() is page:
                parent.set_child(None)
            else:
                parent.remove(page)
        if phone:
            self.remove(self.split) if self.split.get_parent() is self else None
            self.list_screen.append(self.list_page)
            self.detail_screen.set_child(self.detail_page)
            if self.stack.get_parent() is None:
                self.append(self.stack)
            self.stack.set_transition_duration(0)
            self.stack.set_visible_child_name(self._showing)
        else:
            self.remove(self.stack) if self.stack.get_parent() is self else None
            self.split.append(self.list_page)
            self.split.append(self.detail_page)
            if self.split.get_parent() is None:
                self.append(self.split)
        lumaui.set_css_class(self, "phone", phone)
        self._sync_back()


def _descendants(widget: Gtk.Widget, kind: type):
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, kind):
            yield child
        yield from _descendants(child, kind)
        child = child.get_next_sibling()
