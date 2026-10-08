# SPDX-License-Identifier: Apache-2.0
"""The edge dock: the stack of tabs a Luma desktop keeps against its right edge.

How this surface is placed depends on what the session's compositor offers,
and the two answers are genuinely different:

* Under **phoc/wlroots** — Luma's handheld lane — ``wlr-layer-shell`` is
  available and is the correct, supported way to put a surface above ordinary
  windows without taking their keyboard focus. Prairie's call surface already
  does exactly this, so the optional-import idiom below is the one already in
  the tree rather than a new one.
* Under **Mutter** — Luma's desktop lane — there is no such protocol, and no
  application window of any kind can claim the layer an always-visible dock
  needs. The dock is then an ordinary undecorated window. It can be covered
  by other windows and it does take focus when it is clicked, and nothing
  here pretends otherwise: there is no re-raise timer, no focus-stealing
  workaround and no always-on-top hint, because the session has no such
  contract to honour and a loop that fights the compositor would be worse
  than an honest window.

The owned answer for the desktop is the Shelf — the dock Luma's own shell
draws through ``Main.layoutManager.addChrome`` — and a note stack belongs
there rather than in a client. That is a shell change, not an application
one, so it is deliberately not attempted here.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Graphene, Gtk, Pango  # noqa: E402

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell  # noqa: E402
except (ValueError, ImportError):  # pragma: no cover - depends on the session
    Gtk4LayerShell = None

from luma_appkit import Island, motion_duration  # noqa: E402

from .glyphs import CheckGlyph, PlusGlyph, TabTitle
from .store import COLOURS, Note, StickyNotesStore

TAB_WIDTH = 34
# At rest the stack is barely there: a few pixels of colour against the edge,
# the way a real stack of notes shows only its edges. Approaching it opens the
# tabs. Nothing else about the dock announces itself.
SLIVER_WIDTH = 7
TAB_MINIMUM_HEIGHT = 62
TAB_MAXIMUM_HEIGHT = 168
# Roughly the advance of the tab's uppercase tracking, so a longer name takes
# a longer tab and the stack keeps the design's uneven, hand-stuck rhythm
# rather than reading as a column of identical bars.
TAB_HEIGHT_PER_CHARACTER = 9

PEEK_WIDTH = 268
# The spine carries the same name the tab does, at the same width, so the card
# reads as that tab opened out rather than as a panel beside it.
PEEK_SPINE_WIDTH = TAB_WIDTH
PEEK_HOVER_DELAY_MS = 320
PEEK_HOVER_CLOSE_MS = 240
PEEK_HELD_CLOSE_MS = 900
UNDO_TOAST_SECONDS = 8


class NoteTab(Gtk.ToggleButton):
    """A note seen edge-on: a sliver of paper with its name down the spine."""

    def __init__(self, note: Note, *, peek, open_note) -> None:
        super().__init__()
        self.note = note
        self._peek = peek
        self._open = open_note
        self.add_css_class("sticky-tab")
        self.add_css_class(f"paper-{note.colour}")
        if note.completed:
            self.add_css_class("completed")
        if note.pinned:
            self.add_css_class("pinned")
        self.set_size_request(-1, _tab_height(note))

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.title = TabTitle(note.tab_title, width=TAB_WIDTH)
        self.title.set_visible(False)
        column.append(self.title)
        self._check = CheckGlyph(12) if note.completed else None
        if self._check is not None:
            self._check.set_visible(False)
            column.append(self._check)
        self.set_child(column)

        spoken = note.display_title
        if note.completed:
            spoken += ", complete"
        if note.pinned:
            spoken += ", pinned"
        self.update_property([Gtk.AccessibleProperty.LABEL], [spoken])
        self.set_tooltip_text(note.display_title)

        self.connect("toggled", self._toggled)
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", lambda *_: self._peek(self, held=False))
        self.add_controller(hover)
        double = Gtk.GestureClick(button=1)
        double.connect(
            "released",
            lambda _g, count, _x, _y: self._open(self.note) if count == 2 else None,
        )
        self.add_controller(double)

    def set_expanded(self, expanded: bool) -> None:
        """A name only shows on a tab wide enough to carry one."""
        # The width itself is the stylesheet's, so it can animate; only the
        # height is asked for here.
        self.set_size_request(-1, _tab_height(self.note))
        self.title.set_visible(expanded)
        if self._check is not None:
            self._check.set_visible(expanded)

    def _toggled(self, _button) -> None:
        if self.get_active():
            self._peek(self, held=True)


class PeekCard(Gtk.Button):
    """The note itself, slid out from behind its tab and still attached to it.

    It is a button because that is what it does: the whole card opens the
    note, so it is reachable from the keyboard and announces itself without
    a second control being invented for the purpose.
    """

    def __init__(self) -> None:
        super().__init__()
        self.add_css_class("sticky-peek")
        self.note: Note | None = None
        card = Island()
        card.add_css_class("sticky-peek-card")
        self._card = card
        # The card is the note drawn out of the stack, so it keeps the tab it
        # came from: the name stays on a spine down the left edge, with the
        # perforation it would have been torn along beside it.
        leaf = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.spine = TabTitle("", width=PEEK_SPINE_WIDTH)
        self.spine.add_css_class("sticky-peek-spine")
        leaf.append(self.spine)
        perforation = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        perforation.add_css_class("sticky-perforation")
        leaf.append(perforation)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                         hexpand=True)
        column.add_css_class("sticky-peek-leaf")
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.title.add_css_class("sticky-peek-title")
        column.append(self.title)
        rule = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        rule.add_css_class("sticky-rule")
        column.append(rule)
        self.body = Gtk.Label(
            xalign=0,
            yalign=0,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            lines=7,
            ellipsize=Pango.EllipsizeMode.END,
            vexpand=True,
        )
        self.body.add_css_class("sticky-peek-body")
        column.append(self.body)
        leaf.append(column)
        card.append(leaf)
        self.set_child(card)
        self.set_size_request(PEEK_WIDTH, -1)

    def show_note(self, note: Note) -> None:
        self.note = note
        for name in COLOURS:
            self._card.remove_css_class(f"paper-{name}")
        self._card.add_css_class(f"paper-{note.colour}")
        if note.completed:
            self._card.add_css_class("completed")
        else:
            self._card.remove_css_class("completed")
        self.title.set_label(note.display_title)
        self.spine.set_text(note.tab_title)
        self.body.set_label(note.body or "Nothing written yet")
        self.update_property(
            [Gtk.AccessibleProperty.LABEL], [f"Open {note.display_title}"]
        )


class StickyDock(Adw.ApplicationWindow):
    """The stack of tabs, the note that is peeking, and the new-note button."""

    def __init__(self, *, application, store: StickyNotesStore, open_note) -> None:
        super().__init__(application=application, title="Sticky Notes")
        self.store = store
        self._open_note = open_note
        self._peek_note_id: str | None = None
        self._held = False
        self._hover_source = 0
        self._close_source = 0
        self._setting_tabs = False
        self.layered = False
        self.expanded = False
        self._collapse_source = 0
        self._tab_widgets: list[NoteTab] = []

        self.add_css_class("sticky-dock")
        self.set_decorated(False)
        self.set_resizable(False)
        # Luma's libadwaita gives every application window a 360x200 floor,
        # which is right for an application and wrong for a rail: the dock is
        # about fifty pixels wide, and the floor left a third of a screen of
        # dead, opaque window sitting beside the tabs. A rail asks for nothing
        # and takes the width of what is in it.
        self.set_size_request(-1, -1)
        self._place_on_the_edge()

        self.toasts = Adw.ToastOverlay()
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.add_css_class("sticky-dock-row")

        self.revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
            transition_duration=motion_duration(200),
            valign=Gtk.Align.START,
        )
        self.peek = PeekCard()
        self.peek.connect("clicked", self._peek_activated)
        self.revealer.set_child(self.peek)
        row.append(self.revealer)

        # Without a title bar there is nothing to drag, and on the desktop
        # lane this is an ordinary window that a person has to be able to put
        # where they want it. The rail is that grip.
        handle = Gtk.WindowHandle()
        rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                       valign=Gtk.Align.CENTER)
        rail.add_css_class("sticky-rail")
        self.tabs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.tabs.add_css_class("sticky-tabs")
        # The stack is as tall as the notes in it, so without this the window
        # grows past the top and bottom of the screen and the tabs at the ends
        # become unreachable. It scrolls instead, and still asks for only the
        # height it actually needs while it fits.
        self.tab_scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            propagate_natural_height=True,
            propagate_natural_width=True,
        )
        self.tab_scroller.add_css_class("sticky-tab-scroll")
        self.tab_scroller.set_child(self.tabs)
        rail.append(self.tab_scroller)
        self.new_button = Gtk.Button(halign=Gtk.Align.CENTER)
        self.new_button.set_child(PlusGlyph(15))
        self.new_button.add_css_class("sticky-new")
        self.new_button.set_tooltip_text("New note")
        self.new_button.update_property([Gtk.AccessibleProperty.LABEL], ["New note"])
        self.new_button.connect("clicked", lambda *_: self.create_note())
        rail.append(self.new_button)
        handle.set_child(rail)
        row.append(handle)

        self.toasts.set_child(row)
        self.set_content(self.toasts)

        pointer = Gtk.EventControllerMotion()
        pointer.connect("enter", lambda *_: self._entered())
        pointer.connect("leave", lambda *_: self._left())
        row.add_controller(pointer)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key_pressed)
        self.add_controller(keys)
        self.connect("notify::is-active", self._activity_changed)
        self.connect("map", lambda *_: self._limit_height_to_the_screen())

        self.reload()

    # ── Resting and opening ──────────────────────────────────────────────

    def _entered(self) -> None:
        self._cancel_close()
        if self._collapse_source:
            GLib.source_remove(self._collapse_source)
            self._collapse_source = 0
        self.set_expanded(True)

    def _left(self) -> None:
        self._schedule_close()
        # A moment's grace: crossing between two tabs leaves the dock for long
        # enough to close it, and a stack that snaps shut under the pointer is
        # unusable.
        if self._collapse_source:
            GLib.source_remove(self._collapse_source)
        self._collapse_source = GLib.timeout_add(
            PEEK_HOVER_CLOSE_MS, self._collapse_elapsed
        )

    def _collapse_elapsed(self) -> bool:
        self._collapse_source = 0
        self.set_expanded(False)
        return GLib.SOURCE_REMOVE

    def set_expanded(self, expanded: bool) -> None:
        if expanded == self.expanded:
            return
        self.expanded = expanded
        if expanded:
            self.add_css_class("expanded")
        else:
            self.remove_css_class("expanded")
        for tab in self._tab_widgets:
            tab.set_expanded(expanded)
        self._sync_new_button()
        if not expanded:
            self.close_peek()

    def _sync_new_button(self) -> None:
        """The one thing that must never rest out of reach.

        A stack at rest is only its own edges, and the new-note button is not one
        of them — but with no notes there are no edges either, and hiding the
        button as well leaves a window with nothing in it at all: no stack, no
        way to start one, nothing to point at. So it rests visible exactly when
        there is nothing else to see.
        """
        self.new_button.set_visible(self.expanded or not self._tab_widgets)

    def _limit_height_to_the_screen(self) -> None:
        """Cap the tab stack at the monitor it is actually on."""
        surface = self.get_surface()
        display = self.get_display()
        monitor = display.get_monitor_at_surface(surface) if surface else None
        if monitor is None:
            monitors = display.get_monitors()
            monitor = monitors.get_item(0) if monitors.get_n_items() else None
        if monitor is None:
            return
        # Leave room for the new-note button and the rail's own padding, so the
        # button never scrolls away with the tabs.
        reserve = 96
        self.tab_scroller.set_max_content_height(
            max(TAB_MINIMUM_HEIGHT, monitor.get_geometry().height - reserve)
        )

    # ── Placement ────────────────────────────────────────────────────────

    def _place_on_the_edge(self) -> None:
        """Ask the session for the edge, and accept the answer it gives."""
        if Gtk4LayerShell is None:
            return
        try:
            Gtk4LayerShell.init_for_window(self)
            Gtk4LayerShell.set_layer(self, Gtk4LayerShell.Layer.TOP)
            # Anchoring one edge only leaves the stack centred against it.
            Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.RIGHT, True)
            # ON_DEMAND is the whole point: the dock never holds the keyboard,
            # and only accepts it while somebody is actually using the dock.
            Gtk4LayerShell.set_keyboard_mode(
                self, Gtk4LayerShell.KeyboardMode.ON_DEMAND
            )
            Gtk4LayerShell.set_namespace(self, "luma-sticky-notes")
            self.layered = True
        except Exception:
            # A session that has the library but refuses the protocol is an
            # ordinary window session. Nothing about the note is said here.
            self.layered = False

    # ── The stack ────────────────────────────────────────────────────────

    def reload(self) -> None:
        notes = self.store.list_notes()
        self._setting_tabs = True
        while child := self.tabs.get_first_child():
            self.tabs.remove(child)
        self._tab_widgets = []
        for note in notes:
            tab = NoteTab(note, peek=self._peek_tab, open_note=self._open_note)
            tab.set_active(note.id == self._peek_note_id)
            tab.set_expanded(self.expanded)
            self.tabs.append(tab)
            self._tab_widgets.append(tab)
        self._setting_tabs = False
        self._sync_new_button()
        if self._peek_note_id is not None:
            live = next((note for note in notes if note.id == self._peek_note_id), None)
            if live is None:
                self.close_peek()
            else:
                self.peek.show_note(live)

    def note_changed(self, _note: Note) -> None:
        """A note's own window changed it; the tab has to agree immediately."""
        self.reload()

    def create_note(self) -> None:
        note = self.store.create_note()
        self.reload()
        self._open_note(note)

    def note_deleted(self, note: Note) -> None:
        """Offer the way back before anyone has to go looking for it."""
        self.reload()
        toast = Adw.Toast(
            title="Note deleted",
            button_label="Undo",
            timeout=UNDO_TOAST_SECONDS,
        )
        toast.connect("button-clicked", lambda *_, note_id=note.id: self._undo(note_id))
        self.toasts.add_toast(toast)

    def _undo(self, note_id: str) -> None:
        try:
            self.store.restore_note(note_id)
        except KeyError:
            return
        self.reload()

    # ── Peeking ──────────────────────────────────────────────────────────

    def _peek_tab(self, tab: NoteTab, *, held: bool) -> None:
        if self._setting_tabs:
            return
        self._cancel_close()
        if held:
            self._cancel_hover()
            self._show_peek(tab.note, held=True)
            return
        if self._held:
            return
        self._cancel_hover()
        self._hover_source = GLib.timeout_add(
            PEEK_HOVER_DELAY_MS, self._hover_elapsed, tab.note
        )

    def _hover_elapsed(self, note: Note) -> bool:
        self._hover_source = 0
        self._show_peek(note, held=False)
        return GLib.SOURCE_REMOVE

    def _show_peek(self, note: Note, *, held: bool) -> None:
        self._held = held
        self._peek_note_id = note.id
        self.peek.show_note(note)
        self._align_peek_with_its_tab(note)
        self.revealer.set_reveal_child(True)
        self._sync_tab_states()

    def _align_peek_with_its_tab(self, note: Note) -> None:
        """Put the card beside the tab it belongs to.

        The card is the note coming out of the stack, so it leaves from where
        its own tab is. Centred on the rail it reads as a separate panel that
        happens to be next to some tabs.
        """
        tab = next((t for t in self._tab_widgets if t.note.id == note.id), None)
        parent = self.revealer.get_parent()
        if tab is None or parent is None:
            return
        ok, point = tab.compute_point(parent, Graphene.Point())
        if not ok:
            return
        self.revealer.set_margin_top(max(0, int(point.y)))

    def close_peek(self) -> None:
        self._cancel_hover()
        self._cancel_close()
        self._held = False
        self._peek_note_id = None
        self.revealer.set_reveal_child(False)
        self._sync_tab_states()

    def _sync_tab_states(self) -> None:
        self._setting_tabs = True
        child = self.tabs.get_first_child()
        while child is not None:
            child.set_active(child.note.id == self._peek_note_id)
            child = child.get_next_sibling()
        self._setting_tabs = False

    def _peek_activated(self, _button) -> None:
        if self.peek.note is not None:
            self._open_note(self.peek.note)

    def _schedule_close(self) -> None:
        self._cancel_hover()
        self._cancel_close()
        if self._peek_note_id is None:
            return
        delay = PEEK_HELD_CLOSE_MS if self._held else PEEK_HOVER_CLOSE_MS
        self._close_source = GLib.timeout_add(delay, self._close_elapsed)

    def _close_elapsed(self) -> bool:
        self._close_source = 0
        self.close_peek()
        return GLib.SOURCE_REMOVE

    def _cancel_hover(self) -> None:
        if self._hover_source:
            GLib.source_remove(self._hover_source)
            self._hover_source = 0

    def _cancel_close(self) -> None:
        if self._close_source:
            GLib.source_remove(self._close_source)
            self._close_source = 0

    def _key_pressed(self, _controller, keyval, _keycode, _state) -> bool:
        if keyval == Gdk.KEY_Escape and self._peek_note_id is not None:
            self.close_peek()
            return True
        return False

    def _activity_changed(self, *_arguments) -> None:
        # Clicking away is the gesture that puts the note back. On a session
        # that never gives this window activity at all, the pointer leaving
        # the dock does the same job.
        if not self.get_property("is-active"):
            self.close_peek()


def _tab_height(note: Note) -> int:
    length = len(note.tab_title)
    return max(
        TAB_MINIMUM_HEIGHT,
        min(TAB_MAXIMUM_HEIGHT, 26 + length * TAB_HEIGHT_PER_CHARACTER),
    )
