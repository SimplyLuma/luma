# SPDX-License-Identifier: Apache-2.0
"""The Notes page: paragraphs, lists and pictures in one text view.

A paragraph's style — heading, quote, bulleted or numbered, how deep it is
indented, how it is aligned — is a set of tags covering the whole line,
newline included, so an empty list item still carries its bullet. The one line
with no newline, the last, holds its style on a zero-width carrier character
while it is empty; the carrier never reaches the saved note.

Bullets and numbers are drawn beside the text, never typed into it: they
cannot be selected, copied or deleted by accident, they follow the text colour
in light and dark, and numbering is worked out from the list itself.

Keys follow the editors people already know: Enter continues a list and, on an
empty item, leaves it (or steps out one level); Backspace at the start of an
item takes the bullet off (or steps out one level) before it joins lines; Tab
and Shift+Tab indent and outdent list items.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, GObject, Graphene, Gtk, Pango  # noqa: E402

from .notes_attachments import (  # noqa: E402
    MAX_PICTURE_BYTES, NAME_PATTERN, STORAGE_FULL, TOO_LARGE, PictureError, picture_digest, picture_kind,
    picture_path, read_sync_status, shrink_picture, store_picture, sync_status_path,
)

# What a picture the sync host would not take says, quietly, on the picture.
SYNC_NOTICES = {
    STORAGE_FULL: "Not synced · Your Luma storage for note images is full. Remove images from notes to free space.",
    TOO_LARGE: "Not synced · This picture is too large to sync.",
}

INLINE_STYLES = ("bold", "italic", "underline", "strike", "highlight")
LIST_STYLES = frozenset({"bulleted", "numbered", "checklist"})
MAX_LEVEL = 4
INDENT_STYLES = tuple(f"indent-{level}" for level in range(1, MAX_LEVEL + 1))
ALIGN_STYLES = ("align-center", "align-right")
BLOCK_STYLES = frozenset({"heading", "heading-1", "quote", "checked", "divider", *LIST_STYLES, *INDENT_STYLES, *ALIGN_STYLES})
# What a new line takes from the one it was split from when Enter is pressed at
# its end: a list and a quote go on; a heading and an alignment do not.
CONTINUED_STYLES = frozenset({"quote", *LIST_STYLES, *INDENT_STYLES})
CARRIER = "\u200b"
OBJECT = "\ufffc"

LIST_INDENT = 26      # text inset of a first-level list item
LEVEL_INDENT = 24     # each level deeper
MARKER_GAP = 8        # between a bullet or number and its text
BULLETS = ("•", "◦", "▪")
MIN_PICTURE_WIDTH = 48


def level_of(block: frozenset[str]) -> int:
    return max((int(style.rsplit("-", 1)[1]) for style in block if style.startswith("indent-")), default=0)


def with_level(block: frozenset[str], level: int) -> frozenset[str]:
    level = max(0, min(MAX_LEVEL, level))
    kept = {style for style in block if not style.startswith("indent-")}
    if level:
        kept.add(f"indent-{level}")
    return frozenset(kept)


def continued(block: frozenset[str]) -> frozenset[str]:
    return frozenset(block & CONTINUED_STYLES)


def number_label(level: int, value: int) -> str:
    """1. at the top level, then a. and i., as documents number nested lists."""
    kind = level % 3
    if kind == 1:
        text, n = "", value
        while n > 0:
            n, remainder = divmod(n - 1, 26)
            text = chr(ord("a") + remainder) + text
        return f"{text}."
    if kind == 2:
        numerals = ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
                    (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"))
        text, n = "", value
        for amount, letters in numerals:
            while n >= amount:
                text += letters
                n -= amount
        return f"{text}."
    return f"{value}."


def list_markers(blocks: list[frozenset[str]]) -> dict[int, tuple[str, int]]:
    """The marker each line shows, by line number: (text, level).

    A numbered item counts on from the item before it at the same level; a
    deeper item does not interrupt its parent's count; a bullet at the same
    level, or any paragraph that is not in a list, starts the count again.
    """
    markers: dict[int, tuple[str, int]] = {}
    counts = [0] * (MAX_LEVEL + 1)
    for line, block in enumerate(blocks):
        kind = "numbered" if "numbered" in block else "bulleted" if "bulleted" in block else "checklist" if "checklist" in block else None
        if kind is None:
            counts = [0] * (MAX_LEVEL + 1)
            continue
        level = level_of(block)
        for deeper in range(level + 1, MAX_LEVEL + 1):
            counts[deeper] = 0
        if kind == "numbered":
            counts[level] += 1
            markers[line] = (number_label(level, counts[level]), level)
        elif kind == "checklist":
            counts[level] = 0
            markers[line] = ("circle-check" if "checked" in block else "circle", level)
        else:
            counts[level] = 0
            markers[line] = (BULLETS[level % len(BULLETS)], level)
    return markers


def display_size(natural: tuple[int, int], chosen: int, column: int) -> tuple[int, int]:
    """How big a picture is shown: its own width, or the width the person gave
    it, never wider than the text column, keeping its shape.

    A picture is never enlarged past its own width unless the person chose a
    width. Before the page is laid out (``column`` 0) it is shown at its
    chosen or own width; nothing takes that as a width to give the window.
    """
    natural_width = max(1, int(natural[0]))
    wanted = int(chosen) if chosen > 0 else natural_width
    if column > 0:
        wanted = min(wanted, column)
    wanted = max(min(MIN_PICTURE_WIDTH, column) if column > 0 else MIN_PICTURE_WIDTH, wanted)
    return wanted, height_for_width(natural, wanted)


def height_for_width(natural: tuple[int, int], width: int) -> int:
    natural_width, natural_height = natural
    if natural_width <= 0 or natural_height <= 0:
        return max(1, width)
    return max(1, round(width * natural_height / natural_width))


class NotesTextView(Gtk.TextView):
    """A text view that draws list markers beside the text and keeps pictures
    as wide as the text column allows."""

    __gtype_name__ = "LumaNotesTextView"

    def __init__(self, **properties) -> None:
        super().__init__(**properties)
        self.page: "RichTextPage | None" = None
        self._last_width = 0

    def do_snapshot_layer(self, layer: Gtk.TextViewLayer, snapshot: Gtk.Snapshot) -> None:
        if layer == Gtk.TextViewLayer.BELOW_TEXT and self.page is not None:
            self.page.draw_markers(snapshot)
        elif layer == Gtk.TextViewLayer.ABOVE_TEXT and self.page is not None:
            self.page.draw_collaborators(snapshot)

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        minimum, natural, minimum_baseline, natural_baseline = Gtk.TextView.do_measure(
            self, orientation, for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            # Text wraps to any width and pictures follow the column, so
            # nothing in a page is a reason for the window to be wider. GTK
            # would count every picture's width in the text view's minimum,
            # and so the window's: a wide screenshot pushed the window past
            # the screen, then walked it back a few pixels a frame as the
            # pictures followed a column they were themselves holding open.
            minimum = min(minimum, self.get_left_margin() + self.get_right_margin())
            natural = max(minimum, natural)
            minimum_baseline = natural_baseline = -1
        return minimum, natural, minimum_baseline, natural_baseline

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        Gtk.TextView.do_size_allocate(self, width, height, baseline)
        if width != self._last_width and self.page is not None:
            self._last_width = width
            # Pictures size themselves from the text column when measured.
            # A new column asks them to measure again; GTK runs that layout
            # again before this frame is drawn, so they never lag a frame.
            self.page.fit_pictures()


class NotePicture(Gtk.Widget):
    """A picture in a note: the image, a frame when chosen, a resize handle.

    Its size comes from :meth:`do_measure`, never from a size request, so the
    text column decides how wide it is and not the other way round.
    """

    __gtype_name__ = "LumaNotePicture"

    def __init__(self, page: "RichTextPage", name: str, width: int = 0) -> None:
        super().__init__()
        self.overlay = Gtk.Overlay()
        self.overlay.set_parent(self)
        self.page = page
        self.name = name
        self.width = width          # 0: its own size within the text column
        self.natural = (0, 0)
        self.texture: Gdk.Texture | None = None
        self.anchor: Gtk.TextChildAnchor | None = None
        self.add_css_class("notes-picture")
        self.set_cursor(Gdk.Cursor.new_from_name("default"))
        self.picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.FILL)
        frame = Gtk.Box()
        frame.add_css_class("notes-picture-frame")
        frame.set_overflow(Gtk.Overflow.HIDDEN)
        self.picture.set_hexpand(True)
        frame.append(self.picture)
        self.overlay.set_child(frame)
        # Draw the selection above the picture and inside its allocation.
        # TextView clips child anchors; an outside shadow is cut off and
        # leaves damage behind when the pointer changes the widget state.
        self.selection = Gtk.Box()
        self.selection.add_css_class("notes-picture-selection")
        self.selection.set_can_target(False)
        self.selection.set_visible(False)
        self.overlay.add_overlay(self.selection)
        self.handle = Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.END)
        self.handle.add_css_class("notes-picture-handle")
        self.handle.set_cursor(Gdk.Cursor.new_from_name("nwse-resize"))
        self.handle.set_visible(False)
        self.handle.update_property([Gtk.AccessibleProperty.LABEL], ["Resize picture"])
        grip = Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.END,
                       margin_end=3, margin_bottom=3)
        grip.add_css_class("notes-picture-grip")
        self.handle.append(grip)
        self.overlay.add_overlay(self.handle)
        # A quiet line along the bottom when the sync host would not take it.
        self.sync_reason: str | None = None
        self.status = Gtk.Box(valign=Gtk.Align.END, spacing=8)
        self.status.add_css_class("notes-picture-status")
        self.status_label = Gtk.Label(xalign=0, wrap=True, hexpand=True)
        self.status_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.status.append(self.status_label)
        self.shrink_button = Gtk.Button(label="Shrink and attach", valign=Gtk.Align.CENTER)
        self.shrink_button.add_css_class("notes-picture-status-button")
        self.shrink_button.connect("clicked", lambda _button: self.page.shrink(self))
        self.status.append(self.shrink_button)
        self.status.set_visible(False)
        self.overlay.add_overlay(self.status)
        drag = Gtk.GestureDrag()
        drag.set_button(Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._resize_begins)
        drag.connect("drag-update", self._resize_moves)
        drag.connect("drag-end", self._resize_ends)
        self.handle.add_controller(drag)
        click = Gtk.GestureClick(button=0)
        click.connect("pressed", self._pressed)
        click.connect("released", self._released)
        # Keep picture selection on the image itself. A click controller on
        # the parent also sees handle presses and steals its drag sequence.
        frame.add_controller(click)
        self._start_width = 0
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Picture"])
        self.load()

    def load(self) -> None:
        try:
            path = (self.page.picture_paths[self.name] if hasattr(self.page, "picture_paths")
                    else picture_path(self.name))
            self.texture = Gdk.Texture.new_from_filename(str(path))
        except (GLib.Error, PictureError):
            self.texture = None
        if self.texture is not None:
            self.natural = (self.texture.get_width(), self.texture.get_height())
            self.picture.set_paintable(self.texture)
            self.remove_css_class("missing")
        else:
            self.natural = (320, 120)
            self.picture.set_paintable(None)
            self.add_css_class("missing")
        self.fit()

    def set_sync_state(self, reason: str | None, usage: str = "") -> None:
        """Mark the picture as not synced, and why; None clears the mark."""
        reason = reason if reason in SYNC_NOTICES else None
        self.sync_reason = reason
        self.status.set_visible(reason is not None)
        if reason is None:
            self.status.set_tooltip_text(None)
            return
        self.status_label.set_label(SYNC_NOTICES[reason])
        self.shrink_button.set_visible(reason == TOO_LARGE)
        self.status.set_tooltip_text(usage or None)

    def display_size(self) -> tuple[int, int]:
        """The size the picture is shown at in the page's current text column."""
        return display_size(self.natural, self.width, self.page.column_width() if self.page else 0)

    def fit(self) -> None:
        """Measure again for the current text column and chosen width."""
        self.queue_resize()

    # The text view lays a child out at its minimum size, so the minimum is
    # the shown size: what fits the current text column. The text view does
    # not pass that on as a width of its own (NotesTextView.do_measure).
    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        width, height = self.display_size()
        if orientation == Gtk.Orientation.HORIZONTAL:
            return width, width, -1, -1
        if for_size >= 0:
            height = height_for_width(self.natural, min(for_size, width))
        return height, height, -1, -1

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        self.overlay.allocate(width, height, baseline, None)

    def do_dispose(self) -> None:
        overlay = getattr(self, "overlay", None)
        if overlay is not None and overlay.get_parent() is self:
            overlay.unparent()
        Gtk.Widget.do_dispose(self)

    def select(self, selected: bool) -> None:
        (self.add_css_class if selected else self.remove_css_class)("selected")
        self.selection.set_visible(selected)
        self.handle.set_visible(selected)

    def _pressed(self, gesture: Gtk.GestureClick, _count: int, x: float, y: float) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if gesture.get_current_button() == Gdk.BUTTON_SECONDARY:
            self.page.picture_menu(self, x, y)

    def _released(self, gesture: Gtk.GestureClick, _count: int, _x: float, _y: float) -> None:
        # A normal click selects the image. Only a secondary click opens its
        # context menu, so a resize release cannot turn into a menu request.
        if gesture.get_current_button() == Gdk.BUTTON_PRIMARY:
            self.page.choose_picture(self, show_bar=False)

    def _resize_begins(self, gesture, _x: float, _y: float) -> None:
        if not self.page.view.get_editable():
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._start_width = self.get_width()
        self.page.choose_picture(self, show_bar=False)

    def _resize_moves(self, _gesture, dx: float, _dy: float) -> None:
        if not self.page.view.get_editable():
            return
        column = self.page.column_width() or 10_000
        self.width = int(max(MIN_PICTURE_WIDTH, min(column, self._start_width + dx)))
        self.fit()

    def _resize_ends(self, _gesture, _dx: float, _dy: float) -> None:
        self.page.changed()
        self.page.choose_picture(self, show_bar=False)


@dataclass
class _Insertion:
    offset: int
    block: frozenset[str]
    inline: frozenset[str]


class RichTextPage:
    """Everything the Notes editor knows about paragraphs, lists and pictures.

    The window owns the toolbar and the saving; this owns the text view and the
    rules for what a key does and how a note is written down.
    """

    def __init__(self, on_changed: Callable[[], None], on_caret: Callable[[], None],
                 picture_commands: Callable[[NotePicture], object] | None = None,
                 on_problem: Callable[[str], None] | None = None) -> None:
        self.on_changed = on_changed
        self.on_caret = on_caret
        self.picture_commands = picture_commands
        self.on_problem = on_problem or (lambda _message: None)
        self.sync_status: dict = {"pictures": {}}
        self._status_monitor: Gio.FileMonitor | None = None
        self.buffer = Gtk.TextBuffer()
        if hasattr(self.buffer, "set_enable_undo"):
            self.buffer.set_enable_undo(True)
        self.tags: dict[str, Gtk.TextTag] = {
            "bold": self.buffer.create_tag("bold", weight=Pango.Weight.BOLD),
            "italic": self.buffer.create_tag("italic", style=Pango.Style.ITALIC),
            "underline": self.buffer.create_tag("underline", underline=Pango.Underline.SINGLE),
            "strike": self.buffer.create_tag("strike", strikethrough=True),
            "highlight": self.buffer.create_tag("highlight"),
            "heading-1": self.buffer.create_tag("heading-1", scale=1.6, weight=Pango.Weight.BOLD),
            "heading": self.buffer.create_tag("heading", scale=1.28, weight=Pango.Weight.BOLD,
                                              pixels_above_lines=6, pixels_below_lines=2),
            "quote": self.buffer.create_tag("quote", left_margin=16),
            "bulleted": self.buffer.create_tag("bulleted", left_margin=LIST_INDENT),
            "numbered": self.buffer.create_tag("numbered", left_margin=LIST_INDENT),
            "checklist": self.buffer.create_tag("checklist", left_margin=LIST_INDENT),
            "checked": self.buffer.create_tag("checked", strikethrough=True),
            "divider": self.buffer.create_tag("divider"),
        }
        # Created after the list tags so their margin wins where both apply.
        for level, style in enumerate(INDENT_STYLES, start=1):
            self.tags[style] = self.buffer.create_tag(style, left_margin=LIST_INDENT + LEVEL_INDENT * level)
        self.tags["align-center"] = self.buffer.create_tag("align-center", justification=Gtk.Justification.CENTER)
        self.tags["align-right"] = self.buffer.create_tag("align-right", justification=Gtk.Justification.RIGHT)
        self.carrier_tag = self.buffer.create_tag("carrier", invisible=True)
        self.link_tags: list[Gtk.TextTag] = []
        self.pictures: list[NotePicture] = []
        self.chosen: NotePicture | None = None
        self.picture_bar: Gtk.Popover | None = None
        self.files = []
        self.file_widget_factory = None
        # Inline formats set with no selection: on (True) or off (False) for
        # the next thing typed, until the caret moves.
        self.pending: dict[str, bool] = {}
        self.loading = False
        self._structural = 0
        self._insertion: _Insertion | None = None
        self._deletion: tuple[int, frozenset[str], bool] | None = None
        self._markers: dict[int, tuple[str, int]] | None = None
        self._quotes: list[int] = []

        self.view = NotesTextView(
            buffer=self.buffer, wrap_mode=Gtk.WrapMode.WORD_CHAR,
            left_margin=0, right_margin=0, top_margin=0, bottom_margin=18,
            pixels_below_lines=3, vexpand=True,
        )
        self.view.page = self
        self.view.add_css_class("notes-editor")
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key_pressed)
        self.view.add_controller(keys)
        checks = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        checks.connect("released", self._check_clicked)
        self.view.add_controller(checks)
        self.view.connect("paste-clipboard", self._paste)
        drop = Gtk.DropTarget.new(GObject.TYPE_NONE, Gdk.DragAction.COPY)
        drop.set_gtypes([Gdk.FileList, Gdk.Texture])
        drop.connect("drop", self._dropped)
        self.view.add_controller(drop)

        self.buffer.connect("insert-text", self._insert_begins)
        self.buffer.connect_after("insert-text", self._insert_finished)
        self.buffer.connect("delete-range", self._delete_begins)
        self.buffer.connect_after("delete-range", self._delete_finished)
        self.buffer.connect("changed", self._modified)
        self.buffer.connect_after("apply-tag", self._tags_changed)
        self.buffer.connect_after("remove-tag", self._tags_changed)
        self.buffer.connect("mark-set", self._mark_set)

    # ----------------------------------------------------------- structure

    def _check_clicked(self, gesture, _count, x, y):
        if not self.loading and not self.view.get_editable():
            return
        bx, by = self.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        _found, at = self.view.get_iter_at_location(bx, by)
        line = at.get_line()
        block = self.block_of_line(line)
        marker_x = self.view.get_left_margin() + LEVEL_INDENT * level_of(block)
        if 'checklist' not in block or not marker_x <= bx < marker_x + LIST_INDENT:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.buffer.begin_user_action()
        try:
            self.set_line_block(line, block - {'checked'} if 'checked' in block else block | {'checked'})
        finally:
            self.buffer.end_user_action()
        self.changed()

    def _line_start(self, line: int) -> Gtk.TextIter:
        found, start = self.buffer.get_iter_at_line(line)
        return start if found else self.buffer.get_end_iter()

    def _line_region(self, line: int) -> tuple[Gtk.TextIter, Gtk.TextIter]:
        """The whole line, its newline included when it has one."""
        start = self._line_start(line)
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        if not end.is_end():
            end.forward_char()
        return start, end

    def _content_is_empty(self, line: int) -> bool:
        start = self._line_start(line)
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        return self.buffer.get_text(start, end, True).replace(CARRIER, "") == ""

    def block_of_line(self, line: int) -> frozenset[str]:
        start = self._line_start(line)
        if start.is_end():
            return frozenset()
        return frozenset(tag.get_property("name") for tag in start.get_tags()
                         if tag.get_property("name") in BLOCK_STYLES)

    def set_line_block(self, line: int, block: frozenset[str]) -> None:
        """Give a whole line a paragraph style, carrier included when needed."""
        if not self.loading and not self.view.get_editable():
            return
        block = frozenset(block)
        if "numbered" in block and "bulleted" in block:
            block = block - {"bulleted"}
        if not block & LIST_STYLES:
            block = with_level(block, 0)
        self._structural += 1
        try:
            start, end = self._line_region(line)
            text = self.buffer.get_text(start, end, True)
            last_line = end.is_end()
            # The last line has no newline to hold a style while it is empty.
            if last_line and text.replace(CARRIER, "") == "" and block and CARRIER not in text:
                self.buffer.insert(start, CARRIER)
                start, end = self._line_region(line)
            elif CARRIER in text and (not block or text.replace(CARRIER, "").strip("\n") != ""):
                self._remove_carriers(line)
                start, end = self._line_region(line)
            for style in BLOCK_STYLES:
                self.buffer.remove_tag(self.tags[style], start, end)
            for style in block:
                self.buffer.apply_tag(self.tags[style], start, end)
            carrier = self._carrier_in(line)
            if carrier is not None:
                after = carrier.copy()
                after.forward_char()
                self.buffer.apply_tag(self.carrier_tag, carrier, after)
        finally:
            self._structural -= 1
        self._invalidate()

    def _carrier_in(self, line: int) -> Gtk.TextIter | None:
        start, end = self._line_region(line)
        found = start.forward_search(CARRIER, Gtk.TextSearchFlags.TEXT_ONLY, end)
        return found[0] if found else None

    def _remove_carriers(self, line: int) -> None:
        while (carrier := self._carrier_in(line)) is not None:
            after = carrier.copy()
            after.forward_char()
            cursor_offset = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset()
            carrier_offset = carrier.get_offset()
            self.buffer.delete(carrier, after)
            if cursor_offset > carrier_offset:
                cursor_offset -= 1
            self.buffer.place_cursor(self.buffer.get_iter_at_offset(cursor_offset))

    def cursor_line(self) -> int:
        return self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_line()

    def selected_lines(self) -> range:
        bounds = self.buffer.get_selection_bounds()
        if bounds:
            start, end = bounds
            last = end.get_line()
            if end.starts_line() and end.get_line() > start.get_line():
                last -= 1
            return range(start.get_line(), last + 1)
        line = self.cursor_line()
        return range(line, line + 1)

    def toggle_block(self, style: str) -> None:
        """Turn a paragraph style on or off for the caret's line or the selection."""
        if not self.loading and not self.view.get_editable():
            return
        lines = self.selected_lines()
        on = all(style in self.block_of_line(line) for line in lines)
        self.buffer.begin_user_action()
        for line in lines:
            block = set(self.block_of_line(line))
            if on:
                block.discard(style)
            else:
                block.add(style)
                if style in LIST_STYLES:
                    block -= (LIST_STYLES - {style}) | {"quote"}
                elif style == "quote":
                    block -= LIST_STYLES
            self.set_line_block(line, frozenset(block))
        self.buffer.end_user_action()
        self.changed()

    def set_alignment(self, lines: range, alignment: str) -> None:
        if not self.loading and not self.view.get_editable():
            return
        for line in lines:
            block = set(self.block_of_line(line)) - set(ALIGN_STYLES)
            if alignment in ALIGN_STYLES:
                block.add(alignment)
            self.set_line_block(line, frozenset(block))
        self.changed()

    def indent(self, delta: int) -> bool:
        if not self.loading and not self.view.get_editable():
            return False
        lines = [line for line in self.selected_lines() if self.block_of_line(line) & LIST_STYLES]
        if not lines:
            return False
        for line in lines:
            block = self.block_of_line(line)
            self.set_line_block(line, with_level(block, level_of(block) + delta))
        self.changed()
        return True

    # ---------------------------------------------------------------- keys

    def _key_pressed(self, _controller, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        if not self.loading and not self.view.get_editable():
            return False
        modifiers = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK
                             | Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.SUPER_MASK)
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not modifiers:
            return self.handle_return()
        if keyval == Gdk.KEY_BackSpace and not modifiers:
            return self.handle_backspace()
        if keyval == Gdk.KEY_Tab and not modifiers:
            return self.indent(1)
        if keyval == Gdk.KEY_ISO_Left_Tab or (keyval == Gdk.KEY_Tab and modifiers == Gdk.ModifierType.SHIFT_MASK):
            return self.indent(-1)
        return False

    def handle_return(self) -> bool:
        """Enter: continue the list or quote, or step out of it on an empty item."""
        if not self.loading and not self.view.get_editable():
            return False
        line = self.cursor_line()
        block = self.block_of_line(line)
        if not block:
            return False
        self.buffer.begin_user_action()
        try:
            if self.buffer.get_has_selection():
                self.buffer.delete_selection(True, True)
                line = self.cursor_line()
                block = self.block_of_line(line)
            if block & (LIST_STYLES | {"quote"}) and self._content_is_empty(line):
                if block & LIST_STYLES and level_of(block) > 0:
                    self.set_line_block(line, with_level(block, level_of(block) - 1))
                else:
                    self.set_line_block(line, block - LIST_STYLES - {"quote"})
                self.changed()
                return True
            cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            ahead = cursor.copy()
            while not ahead.ends_line() and ahead.get_char() == CARRIER:
                ahead.forward_char()
            at_end = ahead.ends_line()
            self._structural += 1
            try:
                self.buffer.insert_at_cursor("\n")
            finally:
                self._structural -= 1
            self.set_line_block(line, block)
            self.set_line_block(line + 1, continued(block) if at_end else block)
            carrier = self._carrier_in(line + 1)
            self.buffer.place_cursor(carrier if carrier is not None else self._line_start(line + 1))
        finally:
            self.buffer.end_user_action()
        self.view.scroll_mark_onscreen(self.buffer.get_insert())
        self.changed()
        return True

    def handle_backspace(self) -> bool:
        """Backspace at the start of an item: outdent, then take the bullet off."""
        if not self.loading and not self.view.get_editable():
            return False
        if self.buffer.get_has_selection():
            return False
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        before = cursor.copy()
        while not before.starts_line():
            before.backward_char()
            if before.get_char() != CARRIER:
                return False
        line = cursor.get_line()
        block = self.block_of_line(line)
        if block & LIST_STYLES:
            if level_of(block) > 0:
                self.set_line_block(line, with_level(block, level_of(block) - 1))
            else:
                self.set_line_block(line, block - LIST_STYLES)
        elif "quote" in block:
            self.set_line_block(line, block - {"quote"})
        elif CARRIER in self.buffer.get_text(*self._line_region(line), True) and block:
            self.set_line_block(line, frozenset())
        else:
            return False
        self.changed()
        return True

    # ------------------------------------------------------ edits and tags

    def _inline_at(self, offset: int) -> frozenset[str]:
        """The inline formats text typed at an offset takes from its neighbour."""
        at = self.buffer.get_iter_at_offset(offset)
        probe = at.copy()
        if not probe.starts_line() and probe.backward_char() and probe.get_char() not in (CARRIER, OBJECT):
            source = probe
        elif not at.ends_line() and at.get_char() not in (CARRIER, OBJECT, "\n"):
            source = at
        else:
            return frozenset()
        return frozenset(style for style in INLINE_STYLES if source.has_tag(self.tags[style]))

    def inline_state(self) -> dict[str, bool]:
        """Which inline formats are on at the caret or selection, armed ones included."""
        bounds = self.buffer.get_selection_bounds()
        if bounds:
            start, end = bounds
            state = {}
            for style in INLINE_STYLES:
                tag = self.tags[style]
                on = start.has_tag(tag)
                if on:
                    probe = start.copy()
                    # On for the whole selection, not just its first letter.
                    if not probe.forward_to_tag_toggle(tag) or probe.compare(end) < 0:
                        on = probe.compare(end) >= 0
                state[style] = on
            return state
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset()
        inherited = self._inline_at(cursor)
        return {style: self.pending.get(style, style in inherited) for style in INLINE_STYLES}

    def toggle_inline(self, style: str) -> None:
        if not self.loading and not self.view.get_editable():
            return
        bounds = self.buffer.get_selection_bounds()
        if bounds:
            on = self.inline_state()[style]
            (self.buffer.remove_tag if on else self.buffer.apply_tag)(self.tags[style], *bounds)
            self.pending.pop(style, None)
            self.changed()
        else:
            self.pending[style] = not self.inline_state()[style]
        self.on_caret()

    def _insert_begins(self, buffer: Gtk.TextBuffer, location: Gtk.TextIter, _text: str, _length: int) -> None:
        if self.loading or self._structural:
            self._insertion = None
            return
        offset = location.get_offset()
        self._insertion = _Insertion(offset, self.block_of_line(location.get_line()), self._inline_at(offset))

    def _insert_finished(self, buffer: Gtk.TextBuffer, location: Gtk.TextIter, text: str, _length: int) -> None:
        insertion, self._insertion = self._insertion, None
        if insertion is None or not text or text == CARRIER:
            return
        start = buffer.get_iter_at_offset(insertion.offset)
        end = location.copy()
        if "\n" not in text:
            wanted = {style for style in INLINE_STYLES
                      if self.pending.get(style, style in insertion.inline)}
            for style in INLINE_STYLES:
                (buffer.apply_tag if style in wanted else buffer.remove_tag)(self.tags[style], start, end)
        first, last = start.get_line(), end.get_line()
        if first == last:
            # Typing within a line: the new letters take the line's style.
            for style in insertion.block:
                buffer.apply_tag(self.tags[style], start, end)
            if insertion.block and CARRIER in buffer.get_text(*self._line_region(first), True):
                self.set_line_block(first, insertion.block)
            return
        remainder = not end.ends_line()
        for line in range(first, last + 1):
            keep = line == first or (line == last and remainder)
            wanted_block = insertion.block if keep else continued(insertion.block)
            if wanted_block or self.block_of_line(line) or CARRIER in text:
                self.set_line_block(line, wanted_block)
        if CARRIER in buffer.get_text(*self._line_region(last), True):
            self.set_line_block(last, self.block_of_line(last))

    def _delete_begins(self, buffer: Gtk.TextBuffer, start: Gtk.TextIter, end: Gtk.TextIter) -> None:
        if self.loading or self._structural:
            self._deletion = None
            return
        first, last = start.get_line(), end.get_line()
        # A join keeps the upper paragraph's style, unless all of the upper
        # paragraph went with it.
        block = self.block_of_line(last if (first != last and start.starts_line()) else first)
        self._deletion = (start.get_offset(), block, first != last)
        for picture in list(self.pictures):
            anchor = picture.anchor
            if anchor is not None and anchor.get_deleted() is False:
                found = self._anchor_iter(anchor)
                if found is not None and start.compare(found) <= 0 < end.compare(found):
                    self.pictures.remove(picture)
                    if picture is self.chosen:
                        self.choose_picture(None)

    def _delete_finished(self, buffer: Gtk.TextBuffer, start: Gtk.TextIter, _end: Gtk.TextIter) -> None:
        deletion, self._deletion = self._deletion, None
        if deletion is None:
            return
        offset, block, joined = deletion
        line = buffer.get_iter_at_offset(offset).get_line()
        if joined or (block and self._content_is_empty(line)):
            self.set_line_block(line, block)

    def _tags_changed(self, *_arguments) -> None:
        self._invalidate()

    def _modified(self, _buffer) -> None:
        self._invalidate()
        if not self.loading:
            self.on_changed()

    def changed(self) -> None:
        self._invalidate()
        if not self.loading:
            self.on_changed()

    def _mark_set(self, buffer: Gtk.TextBuffer, location: Gtk.TextIter, mark: Gtk.TextMark) -> None:
        if mark is not buffer.get_insert():
            return
        # Moving the caret puts armed formats away, as it does everywhere.
        if self._insertion is None and not self._structural:
            self.pending.clear()
        # The caret never rests on the right of a carrier.
        if location.get_char() != CARRIER and not location.starts_line():
            probe = location.copy()
            if probe.backward_char() and probe.get_char() == CARRIER and not self._structural:
                GLib.idle_add(self._step_off_carrier)
        self.on_caret()

    def _step_off_carrier(self) -> bool:
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        probe = cursor.copy()
        if probe.backward_char() and probe.get_char() == CARRIER and not self.buffer.get_has_selection():
            self.buffer.place_cursor(probe)
        return GLib.SOURCE_REMOVE

    def _invalidate(self) -> None:
        self._markers = None
        self.view.queue_draw()

    # ------------------------------------------------------------- markers

    def markers(self) -> dict[int, tuple[str, int]]:
        if self._markers is None:
            blocks = [self.block_of_line(line) for line in range(self.buffer.get_line_count())]
            self._markers = list_markers(blocks)
            self._quotes = [line for line, block in enumerate(blocks) if "quote" in block]
        return self._markers

    def set_collaborator(self, key, *, name, hue, offset=None):
        """Set or remove a live peer position without changing saved document text."""
        if not hasattr(self, '_collaborators'):
            self._collaborators = {}
        previous = self._collaborators.pop(key, None)
        if previous and not previous[0].get_deleted():
            self.buffer.delete_mark(previous[0])
        if offset is not None:
            position = self.buffer.get_iter_at_offset(max(0, min(offset, self.buffer.get_char_count())))
            self._collaborators[key] = (self.buffer.create_mark(None, position, False), name, hue)
        self.view.queue_draw()

    def clear_collaborators(self):
        for key in tuple(getattr(self, '_collaborators', {})):
            self.set_collaborator(key, name='', hue=0)

    def draw_collaborators(self, snapshot):
        from luma_appkit import snapshot_document_caret
        from luma_appkit.lumaui_tokens import DOCUMENT_CARET
        text_size = self.view.get_pango_context().get_font_description().get_size() / Pango.SCALE
        visible = self.view.get_visible_rect()
        label_bounds = (visible.x, visible.x + visible.width)
        for mark, name, hue in getattr(self, '_collaborators', {}).values():
            if mark.get_deleted():
                continue
            location = self.view.get_iter_location(self.buffer.get_iter_at_mark(mark))
            snapshot_document_caret(snapshot, self.view, location.x,
                                    location.y + max(0, location.height - text_size * DOCUMENT_CARET['height_ratio']),
                                    name=name, hue=hue, text_size=text_size, label_bounds=label_bounds)

    def draw_markers(self, snapshot: Gtk.Snapshot) -> None:
        markers = self.markers()
        view = self.view
        visible = view.get_visible_rect()
        color = view.get_color()
        bottom = visible.y + visible.height
        if self._quotes:
            from luma_appkit import snapshot_document_quote
            from luma_appkit.lumaui_tokens import DOCUMENT_TEXT
            for line in self._quotes:
                first = self._line_start(line)
                top, height = view.get_line_yrange(first)
                if top + height < visible.y or top > bottom:
                    continue
                location = view.get_iter_location(first)
                padding = DOCUMENT_TEXT["quote_padding_y"]
                y = location.y - padding
                snapshot_document_quote(snapshot, view, view.get_left_margin(), y,
                                        top + height - y + padding,
                                        hue=getattr(self, "check_hue", None))
        # Dividers are paragraph blocks, separate from inline formatting.
        for line in range(self.buffer.get_line_count()):
            if 'divider' not in self.block_of_line(line):
                continue
            top, height = view.get_line_yrange(self._line_start(line))
            if top + height < visible.y or top > bottom:
                continue
            found, border = view.get_style_context().lookup_color('luma_border')
            if not found:
                border = color
            thickness = max(1, view.get_scale_factor())
            rect = Graphene.Rect().init(view.get_left_margin(), top + height / 2,
                                        max(0, view.get_width() - view.get_left_margin() - view.get_right_margin()),
                                        thickness)
            snapshot.append_color(border, rect)
        if not markers:
            return
        for line, (text, level) in sorted(markers.items()):
            start = self._line_start(line)
            location = view.get_iter_location(start)
            if location.y + location.height < visible.y:
                continue
            if location.y > bottom:
                break
            if text in ("circle", "circle-check"):
                from luma_appkit import snapshot_document_check
                from luma_appkit.lumaui_tokens import DOCUMENT_CHECK
                size = DOCUMENT_CHECK['size']
                snapshot_document_check(snapshot, view,
                    view.get_left_margin() + LEVEL_INDENT * level + DOCUMENT_CHECK['marker_offset'],
                    location.y + (location.height - size) / 2,
                    checked=text == "circle-check", hue=getattr(self, "check_hue", None))
                continue
            layout = view.create_pango_layout(text)
            attributes = Pango.AttrList()
            if text[0].isalnum():
                # Numbers line up on their full stops.
                attributes.insert(Pango.attr_font_features_new("tnum 1"))
            else:
                # A bullet set at text size is a speck; this is the size the
                # text's own weight asks for.
                attributes.insert(Pango.attr_scale_new(1.35))
            layout.set_attributes(attributes)
            _ink, logical = layout.get_pixel_extents()
            text_x = view.get_left_margin() + LIST_INDENT + LEVEL_INDENT * level
            x = text_x - MARKER_GAP - logical.width
            y = location.y + (location.height - logical.height) / 2
            snapshot.save()
            snapshot.translate(Graphene.Point().init(x, y))
            snapshot.append_layout(layout, color)
            snapshot.restore()

    # ------------------------------------------------------------ pictures

    def column_width(self) -> int:
        """The width text wraps at, or 0 before the page has been laid out."""
        width = self.view.get_width()
        if width <= 0:
            return 0
        return max(1, width - self.view.get_left_margin() - self.view.get_right_margin() - 2)

    def fit_pictures(self) -> bool:
        for picture in self.pictures:
            picture.fit()
        return GLib.SOURCE_REMOVE

    def _anchor_iter(self, anchor: Gtk.TextChildAnchor) -> Gtk.TextIter | None:
        if anchor.get_deleted():
            return None
        return self.buffer.get_iter_at_child_anchor(anchor)

    def insert_picture(self, name: str, at: Gtk.TextIter | None = None, width: int = 0) -> NotePicture:
        """Put a picture in its own paragraph at a place, or at the caret."""
        if not self.loading and not self.view.get_editable():
            return
        buffer = self.buffer
        buffer.begin_user_action()
        if at is None:
            if buffer.get_has_selection():
                buffer.delete_selection(True, True)
            at = buffer.get_iter_at_mark(buffer.get_insert())
        offset = at.get_offset()
        line = at.get_line()
        block = self.block_of_line(line)
        # A picture sits on a line of its own, as it does in every editor.
        if not at.starts_line():
            buffer.insert(at, "\n")
            offset += 1
            line += 1
        at = buffer.get_iter_at_offset(offset)
        anchor = self._place_picture(at, name, width)
        after = buffer.get_iter_at_child_anchor(anchor)
        after.forward_char()
        if not after.ends_line():
            buffer.insert(after, "\n")
            after = buffer.get_iter_at_child_anchor(anchor)
            after.forward_char()
        elif after.is_end():
            buffer.insert(after, "\n")
        self.set_line_block(line, frozenset(block & set(ALIGN_STYLES)))
        after = buffer.get_iter_at_child_anchor(anchor)
        after.forward_line()
        # v70 follows a figure with a blank editable paragraph. Keep the
        # following existing text separate and put the caret in that paragraph.
        if not after.ends_line():
            buffer.insert(after, "\n")
            after = buffer.get_iter_at_child_anchor(anchor)
            after.forward_line()
        buffer.place_cursor(after)
        buffer.end_user_action()
        self.changed()
        return self.pictures[-1]

    def insert_file(self, metadata):
        if not self.loading and not self.view.get_editable():
            return
        from .notes_files import validate_file
        validate_file(metadata)
        self.buffer.begin_user_action()
        try:
            if self.buffer.get_has_selection():
                self.buffer.delete_selection(True, True)
            at = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            if not at.starts_line():
                self.buffer.insert(at, '\n')
            anchor = self._place_file(at, metadata)
            after = self.buffer.get_iter_at_child_anchor(anchor)
            after.forward_char()
            self.buffer.insert(after, '\n')
            self.buffer.place_cursor(after)
        finally:
            self.buffer.end_user_action()
        self.changed()

    def _place_file(self, at, metadata):
        from .notes_files import validate_file, file_path
        validate_file(metadata)
        if self.file_widget_factory is not None:
            widget = self.file_widget_factory(metadata)
        else:
            from luma_appkit import FileCard
            widget = FileCard(str(file_path(metadata['src'])), name=metadata['name'], size=metadata['size'])
        anchor = self.buffer.create_child_anchor(at)
        self.view.add_child_at_anchor(widget, anchor)
        self.files.append((anchor, dict(metadata)))
        return anchor

    def _place_picture(self, at: Gtk.TextIter, name: str, width: int) -> Gtk.TextChildAnchor:
        anchor = self.buffer.create_child_anchor(at)
        picture = NotePicture(self, name, width)
        picture.anchor = anchor
        self.view.add_child_at_anchor(picture, anchor)
        self.pictures.append(picture)
        self._mark(picture)
        return anchor

    # ------------------------------------------------------- sync status

    def watch_sync_status(self) -> None:
        """Follow the sync service's list of pictures it could not send."""
        if self._status_monitor is not None:
            return
        path = sync_status_path()
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._status_monitor = Gio.File.new_for_path(str(path.parent)).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None)
            self._status_monitor.connect("changed", lambda *_args: self.refresh_sync_status())
        except (GLib.Error, OSError):
            self._status_monitor = None
        self.refresh_sync_status()

    def stop_watching_sync_status(self) -> None:
        if self._status_monitor is not None:
            self._status_monitor.cancel()
            self._status_monitor = None

    def refresh_sync_status(self) -> None:
        if hasattr(self, "picture_paths"):
            return
        self.sync_status = read_sync_status()
        for picture in self.pictures:
            self._mark(picture)

    def _mark(self, picture: NotePicture) -> None:
        reason = self.sync_status.get("pictures", {}).get(picture_digest(picture.name))
        usage = ""
        quota, used = self.sync_status.get("quota_bytes"), self.sync_status.get("used_bytes")
        if isinstance(quota, int) and isinstance(used, int) and quota > 0:
            usage = f"Note images use {GLib.format_size(used)} of {GLib.format_size(quota)}."
        picture.set_sync_state(reason, usage)

    def shrink(self, picture: NotePicture) -> bool:
        if not self.loading and not self.view.get_editable():
            return False
        if hasattr(self, "picture_paths"):
            return False
        """Replace a picture too large to sync with a smaller copy of it."""
        try:
            data = picture_path(picture.name).read_bytes()
            name = store_picture(shrink_picture(data, MAX_PICTURE_BYTES))
        except (OSError, PictureError) as error:
            self.on_problem(str(error) if isinstance(error, PictureError) else "The picture could not be read.")
            return False
        self.replace_picture(picture, name)
        return True

    def import_bytes(self, data: bytes, at: Gtk.TextIter | None = None) -> bool:
        if not self.loading and not self.view.get_editable():
            return False
        if hasattr(self, "picture_paths"):
            self.on_problem("Fixture images are read only.")
            return False
        try:
            name = store_picture(data)
        except (PictureError, OSError) as error:
            self.on_problem(str(error) if isinstance(error, PictureError) else "The picture could not be saved.")
            return False
        self.insert_picture(name, at)
        return True

    def import_texture(self, texture: Gdk.Texture, at: Gtk.TextIter | None = None) -> bool:
        if not self.loading and not self.view.get_editable():
            return False
        return self.import_bytes(texture.save_to_png_bytes().get_data(), at)

    def import_files(self, files, at: Gtk.TextIter | None = None) -> int:
        if not self.loading and not self.view.get_editable():
            return 0
        count = 0
        offset = at.get_offset() if at is not None else None
        for file in files:
            path = file.get_path()
            if not path:
                continue
            try:
                data = Path(path).read_bytes() if Path(path).stat().st_size <= MAX_PICTURE_BYTES else b""
            except OSError:
                continue
            if not data:
                self.on_problem("This picture is larger than 20 MB.")
                continue
            where = self.buffer.get_iter_at_offset(offset) if offset is not None else None
            if self._import_any(data, where):
                count += 1
                offset = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset()
        return count

    def _import_any(self, data: bytes, at: Gtk.TextIter | None) -> bool:
        if picture_kind(data) is None:
            # Anything else GTK can read (TIFF, BMP, AVIF...) is kept as PNG.
            try:
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
            except GLib.Error:
                self.on_problem("That file is not a picture Notes can show.")
                return False
            return self.import_texture(texture, at)
        return self.import_bytes(data, at)

    def _paste(self, view: Gtk.TextView) -> None:
        if not self.loading and not self.view.get_editable():
            return
        clipboard = view.get_clipboard()
        formats = clipboard.get_formats()
        has_text = formats.contain_mime_type("text/plain") or formats.contain_mime_type("text/plain;charset=utf-8")
        # Another app offers a picture as image/* bytes; this process offers
        # the texture object itself (a GdkMemoryTexture, a kind of Texture).
        has_picture = any(GObject.type_is_a(gtype, Gdk.Texture) for gtype in (formats.get_gtypes() or ())) or any(
            mime.startswith("image/") for mime in (formats.get_mime_types() or ()))
        has_files = formats.contain_gtype(Gdk.FileList)
        if has_files:
            GObject.signal_stop_emission_by_name(view, "paste-clipboard")
            clipboard.read_value_async(Gdk.FileList, GLib.PRIORITY_DEFAULT, None, self._pasted_files)
            return
        if has_picture and not has_text:
            GObject.signal_stop_emission_by_name(view, "paste-clipboard")
            offered = [mime for mime in (formats.get_mime_types() or ()) if mime in _KEPT_AS_IS]
            if offered:
                # The picture's own bytes, as the other app wrote them: the same
                # picture pasted twice is then the same stored file.
                clipboard.read_async(offered, GLib.PRIORITY_DEFAULT, None, self._pasted_stream)
            else:
                clipboard.read_texture_async(None, self._pasted_texture)

    def _pasted_stream(self, clipboard: Gdk.Clipboard, result) -> None:
        try:
            stream, _mime = clipboard.read_finish(result)
        except GLib.Error:
            clipboard.read_texture_async(None, self._pasted_texture)
            return
        # The clipboard's writer runs on this same main loop (always for this
        # process, and for X11 transfers too), so the bytes are read without
        # blocking: a blocking read of a picture larger than a pipe's buffer
        # would wait for a writer that can never run.
        self._read_chunk(clipboard, stream, [])

    def _read_chunk(self, clipboard: Gdk.Clipboard, stream: Gio.InputStream, chunks: list[bytes]) -> None:
        def done(source: Gio.InputStream, result) -> None:
            try:
                chunk = source.read_bytes_finish(result).get_data() or b""
            except GLib.Error:
                source.close_async(GLib.PRIORITY_DEFAULT, None, None)
                clipboard.read_texture_async(None, self._pasted_texture)
                return
            if not chunk:
                source.close_async(GLib.PRIORITY_DEFAULT, None, None)
                self._import_any(b"".join(chunks), None)
                return
            chunks.append(chunk)
            if sum(len(part) for part in chunks) > MAX_PICTURE_BYTES:
                source.close_async(GLib.PRIORITY_DEFAULT, None, None)
                self.on_problem("This picture is larger than 20 MB.")
                return
            self._read_chunk(clipboard, source, chunks)

        stream.read_bytes_async(1024 * 1024, GLib.PRIORITY_DEFAULT, None, done)

    def _pasted_texture(self, clipboard: Gdk.Clipboard, result) -> None:
        try:
            texture = clipboard.read_texture_finish(result)
        except GLib.Error:
            texture = None
        if texture is None:
            self.on_problem("The picture on the clipboard could not be read.")
            return
        self.import_texture(texture)

    def _pasted_files(self, clipboard: Gdk.Clipboard, result) -> None:
        try:
            files = clipboard.read_value_finish(result).get_files()
        except (GLib.Error, AttributeError):
            files = []
        pictures = [file for file in files if _looks_like_picture(file)]
        if pictures:
            self.import_files(pictures)
            return
        # Not pictures: paste the names as text, as the text view would have.
        clipboard.read_text_async(None, self._pasted_text)

    def _pasted_text(self, clipboard: Gdk.Clipboard, result) -> None:
        if not self.loading and not self.view.get_editable():
            return
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error:
            text = None
        if text:
            self.buffer.insert_interactive_at_cursor(text, -1, True)

    def _dropped(self, _target: Gtk.DropTarget, value: object, x: float, y: float) -> bool:
        if not self.loading and not self.view.get_editable():
            return False
        bx, by = self.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        found, at = self.view.get_iter_at_location(bx, by)
        at = at if found else self.buffer.get_end_iter()
        if isinstance(value, Gdk.Texture):
            return self.import_texture(value, at)
        files = value.get_files() if hasattr(value, "get_files") else []
        return self.import_files([file for file in files if _looks_like_picture(file)], at) > 0

    def choose_picture(self, picture: NotePicture | None, *, show_bar: bool = True) -> None:
        if not show_bar and self.picture_bar is not None:
            self.picture_bar.popdown()
        if self.chosen is not None and self.chosen is not picture:
            self.chosen.select(False)
        self.chosen = picture
        if picture is None:
            if self.picture_bar is not None:
                self.picture_bar.popdown()
            return
        picture.select(True)
        if show_bar and self.picture_commands is not None:
            self.picture_commands(picture)

    def picture_menu(self, picture: NotePicture, x: float, y: float) -> None:
        self.choose_picture(picture, show_bar=False)
        self.on_picture_menu(picture, x, y)

    def on_picture_menu(self, picture: NotePicture, x: float, y: float) -> None:
        """Replaced by the window, which owns menus."""

    def picture_line(self, picture: NotePicture) -> int | None:
        found = self._anchor_iter(picture.anchor) if picture.anchor else None
        return found.get_line() if found is not None else None

    def delete_picture(self, picture: NotePicture) -> None:
        if not self.loading and not self.view.get_editable():
            return
        found = self._anchor_iter(picture.anchor) if picture.anchor else None
        if found is None:
            return
        end = found.copy()
        end.forward_char()
        # Take the picture's own line with it.
        if end.get_char() == "\n":
            end.forward_char()
        self.choose_picture(None)
        self.buffer.begin_user_action()
        self.buffer.delete(found, end)
        self.buffer.end_user_action()
        if picture in self.pictures:
            self.pictures.remove(picture)
        self.changed()

    def replace_picture(self, picture: NotePicture, name: str) -> None:
        if not self.loading and not self.view.get_editable():
            return
        picture.name = name
        picture.width = 0
        picture.load()
        self._mark(picture)
        self.changed()

    # --------------------------------------------------------- persistence

    def clear(self) -> None:
        self.clear_collaborators()
        self.choose_picture(None)
        self.loading = True
        try:
            self.buffer.set_text("")
            table = self.buffer.get_tag_table()
            for tag in self.link_tags:
                table.remove(tag)
            self.link_tags.clear()
            self.pictures.clear()
            self.files.clear()
        finally:
            self.loading = False
        self._invalidate()

    def load(self, body: str, runs: tuple[dict[str, object], ...]) -> None:
        """Show a saved note: its text, its pictures, then its formatting."""
        self.clear()
        self.loading = True
        try:
            pictures = sorted(
                (run for run in runs if run.get("style") in ('image', 'file')
                 and isinstance(run.get("start"), int) and 0 <= int(run["start"]) < len(body)
                 and body[int(run["start"])] == OBJECT),
                key=lambda run: int(run["start"]))
            placed = {int(run["start"]): run for run in pictures}
            cursor = 0
            for offset in sorted(placed):
                self.buffer.insert(self.buffer.get_end_iter(), body[cursor:offset].replace(OBJECT, ""))
                cursor = offset + 1
                run = placed[offset]
                if run['style'] == 'file':
                    self._place_file(self.buffer.get_end_iter(), run)
                elif NAME_PATTERN.match(str(run.get('src', ''))):
                    self._place_picture(self.buffer.get_end_iter(), str(run["src"]), int(run.get("width", 0) or 0))
            self.buffer.insert(self.buffer.get_end_iter(), body[cursor:].replace(OBJECT, ""))
            length = self.buffer.get_char_count()
            trailing: set[str] = set()
            for run in runs:
                style = str(run.get("style"))
                start, end = int(run.get("start", 0)), int(run.get("end", 0))
                if style in ('image', 'file'):
                    continue
                if start == end == len(body) and style in BLOCK_STYLES:
                    trailing.add(style)
                    continue
                if not 0 <= start < end <= length:
                    continue
                first, last = self.buffer.get_iter_at_offset(start), self.buffer.get_iter_at_offset(end)
                if style == "link":
                    self.buffer.apply_tag(self.link_tag(str(run.get("href", ""))), first, last)
                elif style in self.tags:
                    self.buffer.apply_tag(self.tags[style], first, last)
            # Older notes styled only a paragraph's letters; a paragraph style
            # now covers its whole line.
            for line in range(self.buffer.get_line_count()):
                block = self.block_of_line(line)
                if block:
                    self.set_line_block(line, block)
            last = self.buffer.get_line_count() - 1
            if trailing and self._content_is_empty(last):
                self.set_line_block(last, frozenset(trailing))
        finally:
            self.loading = False
        self.buffer.place_cursor(self.buffer.get_start_iter())
        self._invalidate()
        self.fit_pictures()

    def link_tag(self, href: str) -> Gtk.TextTag:
        tag = self.buffer.create_tag(None, underline=Pango.Underline.SINGLE)
        tag.luma_link_href = href
        self.link_tags.append(tag)
        return tag

    def serialize(self) -> tuple[str, tuple[dict[str, object], ...]]:
        """The note as saved: text without carriers, and its formatting runs."""
        start, end = self.buffer.get_bounds()
        raw = self.buffer.get_slice(start, end, True)
        placed = {found.get_offset() for picture in self.pictures
                  if picture.anchor is not None and (found := self._anchor_iter(picture.anchor)) is not None}
        placed.update(found.get_offset() for anchor, _run in self.files
                      if (found := self._anchor_iter(anchor)) is not None)
        # Carriers, and any object character that is not one of the pictures
        # (undo can bring one back without its picture), are not the note's.
        carriers = [index for index, char in enumerate(raw)
                    if char == CARRIER or (char == OBJECT and index not in placed)]
        dropped = set(carriers)

        def plain(offset: int) -> int:
            return offset - sum(1 for index in carriers if index < offset)

        body = "".join(char for index, char in enumerate(raw) if index not in dropped)
        runs: list[dict[str, object]] = []
        for style, tag in self.tags.items():
            for first, last in _tag_ranges(self.buffer, tag):
                a, b = plain(first), plain(last)
                if a < b:
                    runs.append({"start": a, "end": b, "style": style})
                elif a == b == len(body) and style in BLOCK_STYLES:
                    runs.append({"start": a, "end": b, "style": style})
        for tag in self.link_tags:
            for first, last in _tag_ranges(self.buffer, tag):
                a, b = plain(first), plain(last)
                if a < b:
                    runs.append({"start": a, "end": b, "style": "link", "href": tag.luma_link_href})
        for picture in self.pictures:
            found = self._anchor_iter(picture.anchor) if picture.anchor else None
            if found is None:
                continue
            at = plain(found.get_offset())
            run = {"start": at, "end": at + 1, "style": "image", "src": picture.name}
            if picture.width > 0:
                run["width"] = int(picture.width)
            runs.append(run)
        for anchor, metadata in self.files:
            found = self._anchor_iter(anchor)
            if found is not None:
                at = plain(found.get_offset())
                runs.append(dict(metadata, start=at, end=at + 1, style='file'))
        return body, tuple(sorted(runs, key=lambda run: (int(run["start"]), int(run["end"]), str(run["style"]))))


def _tag_ranges(buffer: Gtk.TextBuffer, tag: Gtk.TextTag) -> list[tuple[int, int]]:
    """Every run of a tag, as (start, end) offsets."""
    ranges: list[tuple[int, int]] = []
    cursor = buffer.get_start_iter()
    while True:
        if not cursor.has_tag(tag):
            if not cursor.forward_to_tag_toggle(tag) or cursor.is_end():
                break
        start = cursor.get_offset()
        cursor.forward_to_tag_toggle(tag)
        ranges.append((start, cursor.get_offset()))
        if cursor.is_end():
            break
    return ranges


_KEPT_AS_IS = ("image/png", "image/jpeg", "image/gif", "image/webp")


def _looks_like_picture(file) -> bool:
    name = (file.get_basename() or "").lower()
    return name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".avif", ".heic"))
