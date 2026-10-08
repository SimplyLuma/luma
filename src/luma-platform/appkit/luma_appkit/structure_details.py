# SPDX-License-Identifier: Apache-2.0
"""LumaUI structure: the details pane.

DetailsPane — one pane for Details and Information in every app (v70
`aside.ldp`, `lDP`, `lDPShow`). It sits beside the island, recessed into the
frame like it (the same top, 8 from the edges), with a header (a title, a
hairline under it, a close button) over one scrolling body. Facts get
hairlines between them; lists of people or things get none, but every row
highlights across the pane's full width. Stacked buttons go last.

Behaviour, owned here so no app re-implements it:
- Closed by default. Info opens it (`info_button()` or `CornerPill(info=pane)`).
- It closes itself when its subject goes away: `show(subject=None)`.
- `main=True` is a pane that *is* the app's main view (Calendar's day): it
  stays open, has no close button and ignores Esc.
- Esc closes it (unless the focus is in a text field, which keeps Esc).
- It remembers that Info asked for it while the subject changes (Photos'
  `info_visible`, promoted), so stepping to the next photo keeps it open.
- At phone width it is a bottom drawer (through `LayerHost`), with the same
  content; widening the window puts it back beside the island.

    pane = DetailsPane("Details")
    pane.add_hero("Launch crew", "4 people", lead=Avatar("Launch crew", size=64))
    pane.add_section("Conversation")
    pane.add_facts([("Created", "Sep 2"), ("Encryption", "End-to-end")])
    pane.add_section("People")
    pane.add_list([DetailsRow("Priya Raman", "@priya", lead=…, actions=[("phone", "Call", call)]),
                   AddRow("Add people", on_activate=add)])
    pane.add_section("Photos and files", action=("View all", show_all))
    pane.add_photos(["terrace.webp", "studio.webp", "street.webp"])
    pane.add(StackedButtons([...]))
    body.append(island); body.append(pane)        # beside the island, no spacing
    pane.show(open=True, subject=conversation)     # Info
    pane.show(subject=None)                        # the conversation was deleted: it closes

Rules every part follows: docs/developer/kit/lumaui-principles.md and
behaviour.md. CSS lives in luma-appkit-base.css under `/* LumaUI: Details
pane */` and the item, photos and add-row banners.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import icons, lumaui  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402
from .structure_adapt import WidthWatch, icon_button, is_phone  # noqa: E402
from .structure_layers import LayerHost, ModalHandle  # noqa: E402

__all__ = ["DetailsFacts", "DetailsPane", "DetailsRow", "DetailsItem", "AddRow", "DetailsPhotos", "FactRow"]

_KEEP = object()
Action = tuple[str, str, Callable[[], None]]


def _label(text: str, css: str, *, xalign: float = 0, ellipsize: bool = True, **props) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=xalign, **props)
    if ellipsize:
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(1)  # ask for no width: the pane's width decides
    label.add_css_class(css)
    return label


class DetailsFacts(Gtk.Box):
    """Selectable key/value facts, reusable in details panes and action panels."""

    def __init__(self, rows, *, card: bool = False, prominent: bool = False):
        """`prominent`: a grown title island's facts (v71 Charlie .crinfo2 dl: 13.5 px, values at 400)."""
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        facts = self
        facts.add_css_class("lumaui-details-facts")
        if prominent:
            facts.add_css_class("prominent")
        if card:
            facts.add_css_class("card")  # v71 Calendar: one raised card, 48 rows
        for fact in rows:
            if len(fact) not in (2, 3):
                raise ValueError("a fact is (key, value) or (key, value, lead)")
            key, value = fact[0], fact[1]
            lead = fact[2] if len(fact) == 3 else None
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            row.add_css_class("lumaui-details-fact")
            row.append(Gtk.Label(label=key, xalign=0, valign=Gtk.Align.START, css_classes=["lumaui-details-key"]))
            # The key keeps its natural width; the value takes the rest and wraps by word
            # (a word longer than the line breaks, nothing else): natural up to 40
            # characters, so the box never squeezes it to a character (Nick, 26 Sep).
            shown = Gtk.Label(label=value, xalign=1, hexpand=True, wrap=True, selectable=True,
                              wrap_mode=Pango.WrapMode.WORD_CHAR, width_chars=8, max_width_chars=40,
                              justify=Gtk.Justification.RIGHT, css_classes=["lumaui-details-value"])
            shown.set_can_focus(False)  # selectable by pointer; Tab goes to controls
            if lead is not None:
                value_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, halign=Gtk.Align.END)
                value_line.add_css_class("lumaui-details-value-line")
                lead.set_valign(Gtk.Align.CENTER)
                lead.set_hexpand(False)
                lead.set_halign(Gtk.Align.END)
                value_line.append(lead)
                shown.set_hexpand(False)
                shown.set_width_chars(1)
                value_line.append(shown)
                row.append(value_line)
            else:
                row.append(shown)
            row.update_property([Gtk.AccessibleProperty.LABEL], [f"{key}: {value}"])
            if card:
                child = row.get_first_child()
                while child is not None:
                    child.set_valign(Gtk.Align.CENTER)
                    child = child.get_next_sibling()
            facts.append(row)


class DetailsRow(Gtk.Box):
    """A person or thing in a details list: lead, name, one quiet line, quiet actions.

    The whole row highlights; its actions appear on hover or focus (always on
    a phone). v70 `lDP.row({ av, n, sub, attr, acts })` and `lDP.act`.
    """

    __gtype_name__ = "LumaUIDetailsRow"

    def __init__(self, title: str, subtitle: str = "", *, lead: Gtk.Widget | None = None,
                 on_activate: Callable[[], None] | None = None, actions: Sequence[Action] = (),
                 prominent: bool = False) -> None:
        """`prominent`: a person in a grown title island (v71 Charlie .crprow: 14.5/650 over 12.5/400)."""
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.add_css_class("lumaui-details-row")
        if prominent:
            self.add_css_class("prominent")
        self.button = Gtk.Button(hexpand=True)
        self.button.add_css_class("lumaui-details-who")
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        line.add_css_class("lumaui-details-line")
        if lead is not None:
            lead.set_valign(Gtk.Align.CENTER)
            line.append(lead)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        text.append(_label(title, "lumaui-details-name"))
        if subtitle:
            sub = _label(subtitle, "lumaui-details-sub")
            sub.set_tooltip_text(subtitle)
            text.append(sub)
        line.append(text)
        self.button.set_child(line)
        self.button.update_property([Gtk.AccessibleProperty.LABEL], [f"{title}, {subtitle}" if subtitle else title])
        if on_activate is not None:
            self.button.connect("clicked", lambda _b: on_activate())
        self.append(self.button)
        self.actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, valign=Gtk.Align.CENTER)
        self.actions.add_css_class("lumaui-details-actions")
        for icon, label, callback in actions:
            button = icon_button(icon, label, "lumaui-details-action")
            button.connect("clicked", lambda _b, cb=callback: cb())
            self.actions.append(button)
        self.actions.set_visible(bool(actions))
        self.append(self.actions)


class DetailsItem(Gtk.Button):
    """A thing in a details list (an event, a thread): its mark, a title, one quiet line, a trail.

    The same row, hover and type as the people rows. v70 `lDPItem({ lead, n, sub, trail })`.
    """

    __gtype_name__ = "LumaUIDetailsItem"

    def __init__(self, title: str, subtitle: str = "", *, icon: str | None = None,
                 lead: Gtk.Widget | None = None, trail: Gtk.Widget | None = None,
                 on_activate: Callable[[], None] | None = None, selected: bool = False,
                 when: str | None = None) -> None:
        if trail is not None and when is not None:
            raise ValueError("a details item has one trail: a widget or `when`")
        super().__init__(hexpand=True)
        self.add_css_class("lumaui-details-item")
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        line.add_css_class("lumaui-details-line")
        if icon is not None and lead is not None:
            raise ValueError("a details item has one lead: an icon or a widget")
        if icon is not None:
            glyph = icons.image(icon)
            glyph.add_css_class("lumaui-details-item-icon")
            glyph.add_css_class("lumaui-hue-tint")    # takes the hue of the page it is on, if any
            line.append(glyph)
        elif lead is not None:
            lead.set_valign(Gtk.Align.CENTER)
            line.append(lead)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        text.append(_label(title, "lumaui-details-name"))
        if subtitle:
            text.append(_label(subtitle, "lumaui-details-sub"))
        line.append(text)
        if when is not None:
            # When it happened, quiet at the end of the row (v70 .tgi em, lDPItem trail).
            trail = Gtk.Label(label=when)
            trail.add_css_class("lumaui-details-when")
        if trail is not None:
            trail.set_valign(Gtk.Align.CENTER)
            line.append(trail)
        self.set_child(line)
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"{title}, {subtitle}" if subtitle else title])
        lumaui.set_css_class(self, "on", selected)
        if on_activate is not None:
            self.connect("clicked", lambda _b: on_activate())

    def set_selected(self, selected: bool) -> None:
        lumaui.set_css_class(self, "on", selected)


class AddRow(Gtk.Button):
    """"Add people", "Add source", "Add account": a list row, not a framed button.

    The + sits in the avatar column and the label lines up with the names; an
    optional shortcut shows the menus' way (never on a phone). v70 `lAddRow`.
    """

    __gtype_name__ = "LumaUIAddRow"

    def __init__(self, label: str, *, icon: str = "user-plus", shortcut: str | None = None,
                 on_activate: Callable[[], None] | None = None, appearance: str = "row",
                 compact: bool = False) -> None:
        """`compact`: a sidebar's foot; `appearance="document"`: a document insertion row."""
        if appearance not in ("row", "document"):
            raise ValueError("add row appearance must be row or document")
        super().__init__(hexpand=appearance == "row", halign=Gtk.Align.FILL if appearance == "row" else Gtk.Align.START)
        self.add_css_class("lumaui-add-row")
        if appearance == "document":
            self.add_css_class("document")
        if compact:
            self.add_css_class("compact")
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        line.add_css_class("lumaui-details-line")
        mark = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, hexpand=False)
        mark.add_css_class("lumaui-add-mark")
        glyph = icons.image(icon)
        glyph.set_hexpand(True)
        glyph.set_halign(Gtk.Align.CENTER)
        mark.append(glyph)
        line.append(mark)
        text = _label(label, "lumaui-add-label", hexpand=True)
        if appearance == "document":
            text.set_max_width_chars(-1)
        line.append(text)
        self.shortcut: Gtk.Label | None = None
        if shortcut:
            self.shortcut = Gtk.Label(label=shortcut, valign=Gtk.Align.CENTER,
                                      accessible_role=Gtk.AccessibleRole.PRESENTATION)
            self.shortcut.add_css_class("lumaui-add-shortcut")
            line.append(self.shortcut)
            self.update_property([Gtk.AccessibleProperty.KEY_SHORTCUTS], [shortcut])
            WidthWatch(self, lambda _w: self.shortcut.set_visible(not is_phone(self)),
                       threshold=tokens.PHONE_MAX_WIDTH)
        self.set_child(line)
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        if on_activate is not None:
            self.connect("clicked", lambda _b: on_activate())


class FactRow(Gtk.Box):
    """A labelled fact: its icon, a small label over the value, and Copy on hover.

    The contact card's phone, email and link rows (v70 `.crow2`), used in a
    Card or a DetailsPane list. Copy puts the value on the clipboard and a
    toast confirms it; the button shows on hover or keyboard focus (always
    in a phone drawer). `copy=False` for a fact nobody copies.
    """

    __gtype_name__ = "LumaUIFactRow"

    def __init__(self, icon: str, label: str, value: str, *, copy: bool = True, editable: bool = False,
                 placeholder: str | None = None, purpose: str = "text") -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, accessible_role=Gtk.AccessibleRole.GROUP)
        self.add_css_class("lumaui-fact-row")
        self.value = value
        glyph = icons.image(icon)
        glyph.add_css_class("lumaui-fact-icon")
        if editable:
            # The caption sits above the input; centre the icon on the input
            # rather than on the combined caption-and-input block.
            glyph.set_margin_top(18)
        self.append(glyph)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        name = _label(label, "lumaui-fact-label")
        text.append(name)
        self.entry: Gtk.Entry | None = None
        if editable:
            # The same row, its value a field (v70 Contacts in edit mode: .crow2 input).
            from .content_field import FIELD_PURPOSES
            self.add_css_class("editable")
            self.entry = Gtk.Entry(text=value, hexpand=True, input_purpose=FIELD_PURPOSES[purpose],
                                   placeholder_text=placeholder or f"Add {label.lower()}")
            self.entry.add_css_class("lumaui-fact-input")
            self.entry.update_relation([Gtk.AccessibleRelation.LABELLED_BY], [Gtk.AccessibleList.new_from_list([name])])
            text.append(self.entry)
            self.value_label = None
            copy = False
        else:
            self.value_label = _label(value, "lumaui-fact-value")
            self.value_label.set_selectable(True)
            self.value_label.set_can_focus(False)
            self.value_label.set_tooltip_text(value)
            text.append(self.value_label)
        self.append(text)
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"{label}: {value}"])
        self.copy_button: Gtk.Button | None = None
        if copy:
            self.copy_button = icon_button("copy", f"Copy {label.lower()}", "lumaui-fact-copy")
            self.copy_button.connect("clicked", lambda _b: self.copy())
            self.append(self.copy_button)

    @property
    def text(self) -> str:
        """What the row says now: the field's text while editing, otherwise its value."""
        return self.entry.get_text() if self.entry is not None else self.value

    def do_grab_focus(self) -> bool:
        return self.entry.grab_focus() if self.entry is not None else Gtk.Box.do_grab_focus(self)

    def copy(self) -> None:
        """Put the value on the clipboard and say so."""
        self.get_clipboard().set(self.value)
        from .action_toast import Toast
        if self.get_root() is not None:
            Toast.show(self, "Copied", kind="copied")


class DetailsPhotos(Gtk.Grid):
    """A conversation's or thread's photos: three square thumbnails a row, never one giant image.

    Items are Gdk.Paintable, Gio.File or paths. v70 `.ldpph`.
    """

    __gtype_name__ = "LumaUIDetailsPhotos"

    COLUMNS = 3

    def __init__(self, items: Iterable[object] = (), *, on_activate: Callable[[int], None] | None = None,
                 columns: int = 3) -> None:
        """`columns`: 3 in a details pane (v70 .ldpph), 4 in Messages' grown title island (v71 .mislph)."""
        if columns < 1:
            raise ValueError("a photo grid has at least one column")
        self.columns = columns
        gap = tokens.STRUCTURE["details"]["photo_gap"]
        super().__init__(column_homogeneous=True, row_homogeneous=True, column_spacing=gap, row_spacing=gap,
                         overflow=Gtk.Overflow.HIDDEN)
        self.add_css_class("lumaui-details-photos")
        self._on_activate = on_activate
        self.set_items(items)

    def set_items(self, items: Iterable[object]) -> None:
        while (child := self.get_first_child()) is not None:
            self.remove(child)
        for index, item in enumerate(items):
            picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
            if isinstance(item, Gdk.Paintable):
                picture.set_paintable(item)
            elif isinstance(item, Gio.File):
                picture.set_file(item)
            else:
                picture.set_filename(str(Path(item)))
            frame = Gtk.AspectFrame(ratio=1.0, obey_child=False, child=picture, hexpand=True)
            frame.add_css_class("lumaui-details-photo")
            if self._on_activate is not None:
                click = Gtk.GestureClick()
                click.connect("released", lambda *_a, i=index: self._on_activate(i))
                frame.add_controller(click)
            self.attach(frame, index % self.columns, index // self.columns, 1, 1)


class _PaneIsland(Gtk.Box):
    """The pane's island: the frame's island look, with the island's inner edge and shade drawn over
    what it holds (v70 .ldp::after, --isle-in), as luma_appkit.Island draws it. Local, so the
    structure parts run where widgets.py can't (stock GTK, no Luma typelibs)."""

    _INNER_EDGE = (("luma_island_edge", 0, 0, 1, 0), ("luma_island_rim", 0, 1, 0, 0),
                   ("luma_island_shade", 0, 3, -2, 8))

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.REGION)
        self.add_css_class("luma-island")
        self.set_overflow(Gtk.Overflow.HIDDEN)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        Gtk.Box.do_snapshot(self, snapshot)
        width, height = self.get_width(), self.get_height()
        if (width <= 0 or height <= 0 or self.has_css_class("drawer") or
                self.has_css_class("embedded") or
                (self.get_root() is not None and self.get_root().has_css_class("lumaui-phone-device"))):
            return
        gi.require_version("Gsk", "4.0")
        gi.require_version("Graphene", "1.0")
        from gi.repository import Graphene, Gsk

        radius = float(tokens.WINDOW["island_radius"])
        corner = Graphene.Size().init(radius, radius)
        outline = Gsk.RoundedRect()
        outline.init(Graphene.Rect().init(0, 0, width, height), corner, corner, corner, corner)
        context = self.get_style_context()
        for token, dx, dy, spread, blur in self._INNER_EDGE:
            found, colour = context.lookup_color(token)
            if found and colour.alpha > 0:
                snapshot.append_inset_shadow(outline, colour, dx, dy, spread, blur)


class DetailsPane(Gtk.Box):
    """The details pane. Put it beside the island; `show()` opens and closes it."""

    __gtype_name__ = "LumaUIDetailsPane"

    def __init__(self, title: str = "Details", *, main: bool = False,
                 on_close: Callable[[], None] | None = None, closable: bool | None = None,
                 back: tuple[str, Callable[[], None]] | None = None, embedded: bool = False) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=False)
        self.add_css_class("lumaui-details-slot")
        self._main = main
        self._embedded = False
        # DP1 (rows family): a main pane may still be closable (Calendar's day); closed
        # by hand it stays closed until shown again.
        self._closable_wish = closable
        self._dismissed = False
        self._wanted = False
        self._subject: object = None
        self._shown = False
        self._drawer: ModalHandle | None = None
        self._presenting_drawer = False
        self._esc: Gtk.EventControllerKey | None = None
        self._esc_root: Gtk.Widget | None = None
        self._return_focus: Gtk.Widget | None = None
        self._info_buttons: list[Gtk.ToggleButton] = []
        self.on_close = on_close

        # The sheet is the pane itself: beside the island on a computer, the
        # drawer's card on a phone. The slot stays in the app's layout.
        # An island of its own, set into the frame with the island's inner edge and shade
        # (v70 .ldp::after, --isle-in), not a flat panel (Nick, 26 Sep).
        self.sheet = _PaneIsland()
        self.sheet.set_hexpand(False)
        self.sheet.set_vexpand(True)
        self.sheet.update_property([Gtk.AccessibleProperty.LABEL], [title])
        self.sheet.add_css_class("lumaui-details")
        self.handle = Gtk.Box(halign=Gtk.Align.CENTER)
        self.handle.add_css_class("lumaui-drawer-handle")
        self.handle.set_visible(False)
        self.sheet.append(self.handle)

        self.header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.header.add_css_class("lumaui-details-header")
        self.leading: Gtk.Widget | None = None
        self.back_button = icon_button("chevron-left", "Back", "lumaui-details-close")
        self.back_button.add_css_class("lumaui-details-back")
        self.back_button.set_visible(False)
        self.back_button.connect("clicked", lambda _b: self._on_back and self._on_back())
        self._on_back: Callable[[], None] | None = None
        self.header.append(self.back_button)
        self.title_label = _label(title, "lumaui-details-title", hexpand=True)
        self.title_content: Gtk.Widget | None = None
        self.header.append(self.title_label)
        self.close_button = icon_button("x", "Close", "lumaui-details-close")
        self.close_button.connect("clicked", lambda _b: self.close())
        self.close_button.set_visible(self._closable)
        self.header.append(self.close_button)
        self.sheet.append(self.header)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.body.add_css_class("lumaui-details-body")
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                           vscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                           vexpand=True, child=self.body)
        self.scroller.add_css_class("lumaui-details-scroll")
        self.sheet.append(self.scroller)
        self.footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.footer.add_css_class("lumaui-details-footer")
        self.footer.set_visible(False)
        self.sheet.append(self.footer)
        self.set_title(title)
        self.append(self.sheet)
        self.sheet.set_visible(False)
        self._watch = WidthWatch(self, lambda _w: self._reflow(), threshold=tokens.PHONE_MAX_WIDTH)
        # A pane in a view that is not showing (another page, a closed tab) never
        # floats over the window: its drawer waits until the view shows again.
        self.connect("map", lambda *_a: self._reflow())
        self.connect("unmap", lambda *_a: self.is_drawer and self._reclaim(quiet=True))
        if back is not None:
            self.set_back(*back)
        self.set_embedded(embedded)
        if main:
            self.show(main=True)

    # ── content ───────────────────────────────────────────────────────────

    @property
    def _closable(self) -> bool:
        """A side pane closes; a main one only when made closable (DP1)."""
        return (not self._main) if self._closable_wish is None else bool(self._closable_wish)

    def set_embedded(self, embedded: bool) -> None:
        """Keep details inside an owning panel, including at phone widths."""
        self._embedded = bool(embedded)
        if self._embedded:
            self.sheet.add_css_class("embedded")
            self.sheet.remove_css_class("luma-island")
        else:
            self.sheet.remove_css_class("embedded")
            self.sheet.add_css_class("luma-island")
        self.sheet.set_vexpand(not self._embedded)
        self.scroller.set_vexpand(not self._embedded)
        self.scroller.set_propagate_natural_height(self._embedded)
        self._reflow()

    def set_title(self, title: str) -> None:
        self.title_label.set_label(title)
        self.sheet.update_property([Gtk.AccessibleProperty.LABEL], [title])

    def set_leading(self, leading: Gtk.Widget | None) -> None:
        """Set a small icon or widget before the header title, or remove it."""
        if leading is self.leading:
            return
        if leading is not None and leading.get_parent() is not None:
            raise ValueError("the leading widget already has a parent")
        if self.leading is not None:
            self.header.remove(self.leading)
        self.leading = leading
        if leading is not None:
            leading.set_valign(Gtk.Align.CENTER)
            leading.add_css_class("lumaui-details-leading")
            self.header.insert_child_after(leading, self.back_button)

    def set_title_content(self, content: Gtk.Widget | None) -> None:
        """Show a custom header title widget, or restore the title label."""
        if content is self.title_content:
            return
        if content is not None and content.get_parent() is not None:
            raise ValueError("the title widget already has a parent")
        if self.title_content is not None:
            self.header.remove(self.title_content)
        self.title_content = content
        self.title_label.set_visible(content is None)
        if content is not None:
            content.set_valign(Gtk.Align.CENTER)
            content.set_hexpand(True)
            content.add_css_class("lumaui-details-title-content")
            self.header.insert_child_after(content, self.title_label)

    def add(self, widget: Gtk.Widget) -> Gtk.Widget:
        """Append any part (a file card, StackedButtons) to the body, in order."""
        self.body.append(widget)
        return widget

    def set_footer(self, widget: Gtk.Widget | None) -> None:
        """Keep one action below the scrolling body, in the pane or drawer."""
        current = self.footer.get_first_child()
        if widget is current:
            return
        if widget is not None and widget.get_parent() is not None:
            raise ValueError("the footer widget already has a parent")
        if current is not None:
            self.footer.remove(current)
        if widget is not None:
            self.footer.append(widget)
        self.footer.set_visible(widget is not None)
        if self.is_drawer:
            self._limit_drawer_body(LayerHost.window_host(self).get_height())

    def add_hero(self, title: str, subtitle: str = "", *, lead: Gtk.Widget | None = None) -> Gtk.Widget:
        """The subject at the top: its picture, its name, one caption."""
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.FILL)
        hero.add_css_class("lumaui-details-hero")
        if lead is not None:
            lead.set_halign(Gtk.Align.CENTER)
            hero.append(lead)
        name = Gtk.Label(label=title, wrap=True, justify=Gtk.Justification.CENTER,
                         wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=1, hexpand=True)
        name.add_css_class("lumaui-details-hero-title")
        hero.append(name)
        if subtitle:
            hero.append(_label(subtitle, "lumaui-details-hero-sub", xalign=0.5))
        return self.add(hero)

    def set_header_visible(self, visible: bool) -> None:
        """Omit a redundant heading when the containing bar provides dismissal."""
        self.header.set_visible(visible)

    def add_subject(self, title: str, subtitle: str = "", *, lead: Gtk.Widget | None = None) -> Gtk.Widget:
        """A compact horizontal subject; supply a compact picture or icon."""
        subject = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        subject.add_css_class("lumaui-details-subject")
        if lead is not None:
            lead.set_valign(Gtk.Align.CENTER)
            subject.append(lead)
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        name = Gtk.Label(label=title, xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1)
        name.add_css_class("lumaui-file-summary-title")
        words.append(name)
        if subtitle:
            caption = Gtk.Label(label=subtitle, xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1)
            caption.add_css_class("lumaui-file-summary-subtitle")
            words.append(caption)
        subject.append(words)
        return self.add(subject)

    def set_back(self, label: str | None, on_back: Callable[[], None] | None = None) -> None:
        """A back button before the title ("Back to Tuesday"), for a pane that went one level in; None removes it."""
        self._on_back = on_back if label else None
        self.back_button.set_visible(bool(label))
        header = self.back_button.get_parent()
        (header.add_css_class if label else header.remove_css_class)("with-back")
        self.back_button.set_tooltip_text(f"Back to {label}" if label else None)
        self.back_button.update_property([Gtk.AccessibleProperty.LABEL], [f"Back to {label}" if label else "Back"])

    def add_section(self, label: str, *, action: tuple[str, Callable[[], None]] | None = None,
                    count: int | str | None = None) -> Gtk.Widget:
        """A section label; `action=("View all", fn)` puts a quiet button at its end; `count` a
        count badge (a number) or a quiet tally ("3 of 5") after the label."""
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, accessible_role=Gtk.AccessibleRole.HEADING)
        heading.add_css_class("lumaui-details-section")
        spoken = label
        if count is None:
            heading.append(_label(label, "lumaui-details-section-label", hexpand=True))
        else:
            if isinstance(count, bool):
                raise TypeError("a section's count is a number or a tally")
            if isinstance(count, int):
                from .content_badges import CountBadge
                heading.append(_label(label, "lumaui-details-section-label", ellipsize=False))
                badge = CountBadge(count)
                badge.set_valign(Gtk.Align.CENTER)
                badge.add_css_class("lumaui-details-section-count")
                heading.append(badge)
                heading.append(Gtk.Box(hexpand=True))
            else:
                # v71 lDP.lab('Steps', '<em>3 of 5</em>'): the label takes the room, the tally sits at the end.
                heading.append(_label(label, "lumaui-details-section-label", hexpand=True))
                tally = _label(count, "lumaui-details-section-tally", ellipsize=False, xalign=1)
                tally.add_css_class("numeric")
                heading.append(tally)
            spoken = f"{label}, {count}"
        heading.update_property([Gtk.AccessibleProperty.LABEL], [spoken])
        if action is not None:
            text, callback = action
            button = Gtk.Button(label=text, valign=Gtk.Align.CENTER)
            button.add_css_class("lumaui-details-section-action")
            button.connect("clicked", lambda _b: callback())
            heading.append(button)
        return self.add(heading)

    def add_facts(self, rows: Sequence[tuple[str, str] | tuple[str, str, Gtk.Widget]], *,
                  card: bool = False) -> Gtk.Widget:
        """Key and value facts, a hairline between each. Values are selectable and wrap.

        A third element leads the value: a mark, a dot (v70 Calendar's category: "Work" with
        its colour), e.g. `("Calendar", "Work", Mark(hue=250))`. `card=True` is v71
        Calendar's one raised card: 48 px rows, the key ink-2, the value 600.
        """
        return self.add(DetailsFacts(rows, card=card))

    def add_list(self, rows: Sequence[Gtk.Widget]) -> Gtk.Widget:
        """People, things or facts (DetailsRow, DetailsItem, FactRow, AddRow): no dividers, full-width hover."""
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.LIST)
        group.add_css_class("lumaui-details-list")
        for row in rows:
            if not isinstance(row, (DetailsRow, DetailsItem, AddRow, FactRow)):
                raise TypeError("a details list holds DetailsRow, DetailsItem, FactRow and AddRow")
            group.append(row)
        return self.add(group)

    def add_photos(self, items: Iterable[object], *, on_activate: Callable[[int], None] | None = None) -> DetailsPhotos:
        return self.add(DetailsPhotos(items, on_activate=on_activate))

    def clear(self) -> None:
        """Empty the body (to fill it again for a new subject)."""
        while (child := self.body.get_first_child()) is not None:
            self.body.remove(child)
        self.set_footer(None)

    # ── open and close ────────────────────────────────────────────────────

    @GObject.Property(type=bool, default=False, flags=GObject.ParamFlags.READABLE)
    def shown(self) -> bool:
        """Whether the pane is showing (beside the island or as a drawer)."""
        return self._shown

    @property
    def subject(self) -> object:
        return self._subject

    @property
    def main(self) -> bool:
        return self._main

    @property
    def is_drawer(self) -> bool:
        return self._drawer is not None and not self._drawer.closed

    def show(self, open: bool | None = None, subject: object = _KEEP, main: bool | None = None) -> bool:
        """Open or close; returns whether it shows. v70 `lDPShow(pane, { open, subject, main })`.

        `open` is Info's wish and is remembered while the subject changes;
        a subject of None closes the pane (and forgets the wish), unless it
        is the view's main pane.
        """
        if main is not None:
            self._main = bool(main)
            self.close_button.set_visible(self._closable)
        if open:
            self._dismissed = False
        if subject is not _KEEP:
            self._subject = subject
        if open is not None:
            self._wanted = bool(open)
        if self._subject is None and not self._main:
            self._wanted = False
        on = (self._main and not self._dismissed) or (self._wanted and self._subject is not None)
        self._apply(on)
        return on

    def open(self, subject: object = _KEEP) -> bool:
        return self.show(open=True, subject=subject)

    def close(self) -> None:
        """Close (the close button, Esc). A main pane stays."""
        if self._main and not self._closable:
            return
        was = self._shown
        if self._main:
            self._dismissed = True
        self._wanted = False
        self._apply(False)
        if was and self.on_close is not None:
            self.on_close()

    def toggle(self) -> bool:
        return self.show(open=not self._shown)

    def info_button(self) -> Gtk.ToggleButton:
        """The Info control for this pane: pressed while it shows. CornerPill(info=pane) uses it."""
        button = icon_button("info", "Information", "lumaui-corner-button", toggle=True)
        button.set_active(self._shown)
        button.connect("toggled", self._info_toggled)
        self._info_buttons.append(button)
        return button

    # ── internals ─────────────────────────────────────────────────────────

    def _info_toggled(self, button: Gtk.ToggleButton) -> None:
        if button.get_active() != self._shown:
            self.show(open=button.get_active())
            if button.get_active() != self._shown:  # nothing to describe: it stays closed
                button.set_active(self._shown)

    def _apply(self, on: bool) -> None:
        if not on:
            self._hide()
        elif not self._embedded and is_phone(self):
            if self.get_mapped():
                self._as_drawer()
        else:
            self._as_side()
        if on != self._shown:
            self._shown = on
            self.notify("shown")
        for button in self._info_buttons:
            if button.get_active() != on:
                button.set_active(on)
        if not on:
            self._return()

    def _reflow(self) -> None:
        if self._shown:
            self._apply(True)

    def _as_side(self) -> None:
        entering = not self.sheet.get_visible() or self.is_drawer
        self._reclaim(quiet=True)
        self.sheet.set_visible(True)
        self._listen_esc(not self._embedded)
        self.sheet.set_vexpand(not self._embedded)
        self.scroller.set_vexpand(not self._embedded)
        self.scroller.set_propagate_natural_height(self._embedded)
        if entering:
            self._remember_focus()
            if not lumaui.reduced_motion():
                self.sheet.add_css_class("entering")
                lumaui.on_next_frame(self.sheet, lambda: self.sheet.remove_css_class("entering"))

    def _as_drawer(self) -> None:
        if self._presenting_drawer or self.is_drawer:
            return
        # Mapping the host can synchronously reflow this pane before the
        # modal handle is returned. Keep that transition atomic.
        self._presenting_drawer = True
        try:
            self._remember_focus()
            self._listen_esc(False)
            host = LayerHost.window_host(self)
            self._reclaim(quiet=True)
            self.remove(self.sheet)
            self.sheet.set_visible(True)
            self.sheet.remove_css_class("luma-island")
            self.sheet.add_css_class("drawer")
            self.handle.set_visible(True)
            height = host.get_height()
            self._limit_drawer_body(height)
            self.scroller.set_propagate_natural_height(True)
            self.sheet.set_vexpand(False)
            self._drawer = host.present_modal(self.sheet, on_cancel=self._drawer_cancelled, drawer=True)
            self.sheet.add_tick_callback(self._drawer_resized)
        finally:
            self._presenting_drawer = False

    def _limit_drawer_body(self, height: int) -> None:
        if height > 0:
            share = tokens.STRUCTURE["details"]["phone_max_height_pct"] / 100
            footer_height = self.footer.measure(Gtk.Orientation.VERTICAL, -1)[1] if self.footer.get_visible() else 0
            maximum = max(120, int(height * share) - tokens.STRUCTURE["details"]["header_height"] - 24 - footer_height)
            if self.scroller.get_max_content_height() != maximum:
                self.scroller.set_max_content_height(maximum)

    def _drawer_resized(self, *_args) -> bool:
        if not self.is_drawer:
            return False
        self._limit_drawer_body(LayerHost.window_host(self).get_height())
        return True

    def _drawer_cancelled(self) -> None:
        # Tapped outside, swiped down or Esc: the drawer fades, then the sheet comes home.
        self._wanted = False
        if self._shown:
            self._shown = False
            self.notify("shown")
            for button in self._info_buttons:
                button.set_active(False)
            if self.on_close is not None:
                self.on_close()
        self._reclaim_later()

    def _hide(self) -> None:
        self._listen_esc(False)
        if self.is_drawer:
            self._drawer.close()  # it fades; the sheet comes home afterwards
            self._reclaim_later()
        else:
            self._reclaim(quiet=True)
            self.sheet.set_visible(False)

    def _reclaim_later(self) -> None:
        def later() -> bool:
            if not self.is_drawer and self.sheet.get_parent() is not self:
                self._reclaim(quiet=True)
            return False
        GLib.timeout_add(max(1, lumaui.duration("dialog_fade")) + 20, later)

    def _reclaim(self, *, quiet: bool) -> None:
        """Bring the sheet back beside the island (from a drawer)."""
        handle, self._drawer = self._drawer, None
        if handle is not None and not handle.closed:
            handle.close(quiet=quiet)
        parent = self.sheet.get_parent()
        if parent is self:
            return
        if parent is not None:
            if isinstance(parent, Gtk.Overlay):
                parent.remove_overlay(self.sheet)
            else:
                self.sheet.unparent()
        for css in ("lumaui-modal", "drawer", "shown", "nudge"):
            self.sheet.remove_css_class(css)
        if not self._embedded:
            self.sheet.add_css_class("luma-island")
        self.sheet.set_can_target(True)
        self.sheet.set_halign(Gtk.Align.FILL)
        self.sheet.set_valign(Gtk.Align.FILL)
        self.sheet.set_vexpand(True)
        self.handle.set_visible(False)
        self.scroller.set_propagate_natural_height(False)
        self.scroller.set_max_content_height(-1)
        self.sheet.set_visible(self._shown and not is_phone(self))
        self.append(self.sheet)

    def _listen_esc(self, on: bool) -> None:
        root = self.get_root()
        if self._esc is not None and (not on or self._esc_root is not root):
            if self._esc_root is not None:
                self._esc_root.remove_controller(self._esc)
            self._esc = self._esc_root = None
        if on and self._esc is None and root is not None and self._closable:
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", self._key)
            root.add_controller(keys)
            self._esc, self._esc_root = keys, root

    def _key(self, _c: Gtk.EventControllerKey, keyval: int, _code: int, _state: Gdk.ModifierType) -> bool:
        if keyval != Gdk.KEY_Escape or not self._closable or not self._shown or self.is_drawer:
            return False
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        if isinstance(focus, Gtk.Editable) or (focus is not None and isinstance(focus.get_parent(), Gtk.Editable)):
            return False
        self.close()
        return True

    def _remember_focus(self) -> None:
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        if focus is not None and not focus.is_ancestor(self.sheet):
            self._return_focus = focus

    def _return(self) -> None:
        target, self._return_focus = self._return_focus, None
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        in_pane = focus is None or focus.is_ancestor(self.sheet) or focus is self.sheet
        if in_pane and target is not None and target.get_root() is root and target.get_mapped():
            target.grab_focus()
