# SPDX-License-Identifier: Apache-2.0
"""One note, open: a small square window of coloured paper.

The frame, the identity corner and the window controls are the toolkit's, as
they are for every Luma window. What this file lays out is what the design
asks for inside that frame — the note's name, when it was last saved, whether
it is pinned, a rule, the writing, and the row of paper colours and decisions
along the bottom.
"""

from __future__ import annotations

from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from luma_appkit import (  # noqa: E402
    AppWindow,
    ColorSwatch,
    Command,
    CommandGroup,
    CommandRegistry,
    Island,
)

from .glyphs import PinGlyph
from .store import COLOURS, Note, StickyNotesStore

APP_ID = "org.projectluma.StickyNotes"

# Notes' own autosave beat. The same number in both applications means a
# person who types in one and looks away has the same expectation in the other.
AUTOSAVE_DELAY_MS = 520
# "Saved · 4 minutes ago" has to keep being true while the window sits open.
SAVED_LABEL_REFRESH_MS = 30_000

_SWATCH_LABELS = {
    "yellow": "Yellow paper",
    "coral": "Coral paper",
    "green": "Green paper",
    "blue": "Blue paper",
    "purple": "Purple paper",
}


class NoteWindow(AppWindow):
    """A single sticky note's window."""

    def __init__(
        self,
        *,
        application,
        store: StickyNotesStore,
        note: Note,
        on_changed=None,
        on_deleted=None,
    ) -> None:
        # The GObject is initialised before anything is hung off it, and the
        # command registry is built from bound methods that do not read state
        # yet — so nothing here touches an instance that is not yet a window.
        super().__init__(
            application=application,
            app_id=APP_ID,
            title="Sticky Notes",
            icon_name=APP_ID,
            commands=self._build_commands(),
            subtitle=note.display_title,
            # Square-ish, and small. The real lower bound is set by the
            # footer's natural width — five swatches and three text buttons —
            # not by this request; GTK takes whichever is larger. There is no
            # AdwBreakpoint anywhere in this application, so the pathology
            # where a breakpoint bin reports a zero minimum and then clips its
            # own content cannot arise here.
            default_width=408,
            default_height=372,
            minimum_width=396,
            minimum_height=300,
        )
        self.store = store
        self.note = note
        self._on_changed = on_changed
        self._on_deleted = on_deleted
        self._save_source = 0
        self._clock_source = 0
        self._applying = False
        self.add_css_class("sticky-note-window")
        # A note is a piece of paper, not a document inside a window frame. The
        # toolkit dresses any window carrying `luma-app-window` with its own
        # title row, which would sit above the paper as a second header; this
        # window gives that up so the paper reaches all four edges, and carries
        # the window controls in the note's own header instead.
        self.remove_css_class("luma-app-window")
        self.set_decorated(False)
        # Two rows sit above the body: the toolkit's, which goes with the
        # decoration, and the kit's own, whose visibility was decided once when
        # AppWindow was built and does not follow a later set_decorated.
        self.title_bar.set_visible(False)
        self.set_body(self._build_body())
        self._apply_colour(note.colour)
        self._apply_completion(note.completed)
        self.connect("close-request", self._closing)
        self._clock_source = GLib.timeout_add(
            SAVED_LABEL_REFRESH_MS, self._refresh_saved_label
        )

    # ── Layout ───────────────────────────────────────────────────────────

    def _build_body(self) -> Gtk.Widget:
        self.paper = Island()
        self.paper.add_css_class("sticky-note")
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        head.add_css_class("sticky-note-head")
        # GtkWindowControls draws nothing on an undecorated window, so the two
        # dots are the note's own: close, and out of the way.
        dots = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7,
                       valign=Gtk.Align.CENTER)
        dots.add_css_class("sticky-dots")
        for label, action in (("Close note", self.close),
                              ("Minimise note", self.minimize)):
            dot = Gtk.Button(valign=Gtk.Align.CENTER)
            dot.add_css_class("sticky-dot")
            dot.update_property([Gtk.AccessibleProperty.LABEL], [label])
            dot.set_tooltip_text(label)
            dot.connect("clicked", lambda _b, run=action: run())
            dots.append(dot)
        head.append(dots)
        self.title_entry = Gtk.Entry(
            text=self.note.title,
            placeholder_text="Untitled note",
            hexpand=True,
            has_frame=False,
            xalign=0,
        )
        self.title_entry.add_css_class("sticky-note-title")
        self.title_entry.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Note title"]
        )
        self.title_entry.connect("changed", lambda *_: self._schedule_save())
        self.title_entry.connect("activate", lambda *_: self._flush_save())
        head.append(self.title_entry)

        self.saved_label = Gtk.Label(xalign=1, valign=Gtk.Align.CENTER)
        self.saved_label.add_css_class("sticky-saved")
        head.append(self.saved_label)

        self.pin_glyph = PinGlyph(16, filled=self.note.pinned)
        self.pin_button = Gtk.ToggleButton(
            active=self.note.pinned, valign=Gtk.Align.CENTER
        )
        self.pin_button.set_child(self.pin_glyph)
        self.pin_button.add_css_class("luma-icon-button")
        self.pin_button.add_css_class("quiet")
        self.pin_button.add_css_class("sticky-pin")
        self.pin_button.connect("toggled", self._pin_toggled)
        head.append(self.pin_button)
        # An undecorated window has no frame to drag, and a note that cannot be
        # moved is not a note. The header is the grip.
        grip = Gtk.WindowHandle()
        grip.set_child(head)
        column.append(grip)

        rule = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        rule.add_css_class("sticky-rule")
        column.append(rule)

        self.body_view = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            accepts_tab=False,
            top_margin=10,
            bottom_margin=10,
            left_margin=14,
            right_margin=14,
        )
        self.body_view.add_css_class("sticky-body")
        self.body_view.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Note text"]
        )
        self.body_view.get_buffer().set_text(self.note.body)
        self.body_view.get_buffer().connect("changed", lambda *_: self._schedule_save())
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hexpand=True,
            vexpand=True,
        )
        scroller.add_css_class("sticky-body-scroll")
        scroller.set_child(self.body_view)
        column.append(scroller)

        column.append(self._build_footer())
        self.paper.append(column)
        self._refresh_saved_label()
        return self.paper

    def _build_footer(self) -> Gtk.Widget:
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.add_css_class("sticky-note-foot")

        # Left to fill, each swatch takes the whole height of the footer row and
        # a dot becomes a tall pill with a dot inside it.
        palette = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=2,
            valign=Gtk.Align.CENTER,
        )
        palette.add_css_class("sticky-palette")
        palette.update_property([Gtk.AccessibleProperty.LABEL], ["Note colour"])
        self.swatches: dict[str, ColorSwatch] = {}
        leader: ColorSwatch | None = None
        for colour in COLOURS:
            swatch = ColorSwatch(
                _PAPER_HEX[colour], _SWATCH_LABELS[colour], context=self.context
            )
            swatch.add_css_class("sticky-swatch")
            if leader is None:
                leader = swatch
            else:
                swatch.set_group(leader)
            swatch.set_active(colour == self.note.colour)
            swatch.connect("toggled", self._swatch_toggled, colour)
            palette.append(swatch)
            self.swatches[colour] = swatch
        footer.append(palette)

        spacer = Gtk.Box(hexpand=True)
        footer.append(spacer)

        self.delete_button = self._text_button("Delete", self._delete)
        self.delete_button.add_css_class("sticky-destructive")
        footer.append(self.delete_button)
        self.complete_button = self._text_button("Mark complete", self._toggle_complete)
        footer.append(self.complete_button)
        close_button = self._text_button("Close", self.close)
        close_button.add_css_class("primary")
        footer.append(close_button)
        return footer

    @staticmethod
    def _text_button(label: str, activate) -> Gtk.Button:
        button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
        button.add_css_class("luma-button")
        button.add_css_class("small")
        button.connect("clicked", lambda *_: activate())
        return button

    def _build_commands(self) -> CommandRegistry:
        return CommandRegistry((
            CommandGroup(None, (
                Command("note.pin", "Pin note", self._toggle_pin,
                        shortcut=("Ctrl", "P")),
                Command("note.complete", "Mark complete", self._toggle_complete,
                        shortcut=("Ctrl", "Return")),
                Command("note.delete", "Delete note", self._delete,
                        "user-trash-symbolic", destructive=True,
                        shortcut=("Ctrl", "Delete")),
            )),
            CommandGroup("STICKY NOTES", (
                Command("note.close", "Close note", self.close,
                        shortcut=("Ctrl", "W")),
            )),
        ))

    # ── State ────────────────────────────────────────────────────────────

    def _swatch_toggled(self, swatch: ColorSwatch, colour: str) -> None:
        if self._applying or not swatch.get_active() or colour == self.note.colour:
            return
        self._apply(self.store.set_colour(self.note.id, colour))
        self._apply_colour(colour)

    def _pin_toggled(self, button: Gtk.ToggleButton) -> None:
        if self._applying or button.get_active() == self.note.pinned:
            return
        self._apply(self.store.set_pinned(self.note.id, button.get_active()))
        self.pin_glyph.set_filled(self.note.pinned)

    def _toggle_pin(self) -> None:
        self.pin_button.set_active(not self.pin_button.get_active())

    def _toggle_complete(self) -> None:
        self._flush_save()
        self._apply(self.store.set_complete(self.note.id, not self.note.completed))
        self._apply_completion(self.note.completed)

    def _delete(self) -> None:
        """Delete is recoverable, so it does not stop to ask.

        A modal over a sticky note costs more than the mistake it prevents:
        the note goes to the trash, the dock offers Undo, and the store keeps
        it for thirty days either way. Nothing here removes a row.
        """
        self._cancel_save()
        deleted = self.store.delete_note(self.note.id)
        if self._on_deleted is not None:
            self._on_deleted(deleted)
        self.close()

    def _apply(self, note: Note) -> None:
        self.note = note
        self.set_identity_subtitle(note.display_title)
        self._refresh_saved_label()
        if self._on_changed is not None:
            self._on_changed(note)

    def _apply_colour(self, colour: str) -> None:
        for name in COLOURS:
            self.paper.remove_css_class(f"paper-{name}")
        self.paper.add_css_class(f"paper-{colour}")
        self._applying = True
        for name, swatch in self.swatches.items():
            swatch.set_active(name == colour)
        self._applying = False

    def _apply_completion(self, completed: bool) -> None:
        self.complete_button.set_label("Mark active" if completed else "Mark complete")
        if completed:
            self.paper.add_css_class("completed")
        else:
            self.paper.remove_css_class("completed")
        attributes = Pango.AttrList()
        if completed:
            attributes.insert(Pango.attr_strikethrough_new(True))
        self.title_entry.set_attributes(attributes)

    # ── Saving ───────────────────────────────────────────────────────────

    def _schedule_save(self) -> None:
        self._cancel_save()
        self.saved_label.set_label("Saving…")
        self._save_source = GLib.timeout_add(AUTOSAVE_DELAY_MS, self._save)

    def _cancel_save(self) -> None:
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0

    def _flush_save(self) -> None:
        if self._save_source:
            self._cancel_save()
            self._save()

    def _save(self) -> bool:
        self._save_source = 0
        buffer = self.body_view.get_buffer()
        body = buffer.get_text(
            buffer.get_start_iter(), buffer.get_end_iter(), False
        )
        try:
            self._apply(
                self.store.update_note(
                    self.note.id, title=self.title_entry.get_text(), body=body
                )
            )
        except KeyError:
            # The note was deleted from elsewhere while this window was open.
            # Say so in the one place that is about saving, and stop trying.
            self.saved_label.set_label("Note no longer exists")
            self.saved_label.add_css_class("error")
        except ValueError:
            # The text itself is never echoed — only the fact that it is too
            # long for the store to take.
            self.saved_label.set_label("Too long to save")
            self.saved_label.add_css_class("error")
        return GLib.SOURCE_REMOVE

    def _refresh_saved_label(self) -> bool:
        self.saved_label.remove_css_class("error")
        self.saved_label.set_label(f"Saved · {relative_time(self.note.modified_at)}")
        return GLib.SOURCE_CONTINUE

    def _closing(self, _window) -> bool:
        self._flush_save()
        if self._clock_source:
            GLib.source_remove(self._clock_source)
            self._clock_source = 0
        return False


# The paper colours, as the swatch control needs them. The sheet owns what a
# note actually looks like; the swatch is a Cairo dot and has to be handed a
# literal, so the two are kept in step here and nowhere else.
_PAPER_HEX = {
    "yellow": "#f3e3a3",
    "coral": "#f4c3b4",
    "green": "#c3ddbf",
    "blue": "#bcd4e9",
    "purple": "#d5c6e7",
}


def relative_time(stamp: str) -> str:
    """How long ago, in the words a person would use for a sticky note."""
    try:
        moment = datetime.fromisoformat(stamp).astimezone()
    except ValueError:
        return "just now"
    seconds = (datetime.now().astimezone() - moment).total_seconds()
    if seconds < 45:
        return "just now"
    minutes = round(seconds / 60)
    if minutes < 60:
        return "1 minute ago" if minutes == 1 else f"{minutes} minutes ago"
    hours = round(minutes / 60)
    if hours < 24:
        return "1 hour ago" if hours == 1 else f"{hours} hours ago"
    if seconds < 172_800:
        return "yesterday"
    if moment.year == datetime.now().astimezone().year:
        return moment.strftime("%-d %b")
    return moment.strftime("%-d %b %Y")
