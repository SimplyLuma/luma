# SPDX-License-Identifier: Apache-2.0
"""LumaUI bar: the rest of the things an app puts in its action center bar.

All of these are `ActionCenter.show_bar()` items, drawn by the kit to v70's
metrics; the app says what they are and hears what happens.

- **BarSearch** (v70 `.fbar .srch`, `.mnq`, `.mpq`): a 36 search well, 250
  wide (`span="narrow"` 220 as Photos, `"wide"` 300 as Maps). It gives way
  when the bar is capped; on a phone the narrow and regular wells fold to
  their glyph (v70 `.fbar .srch`), Maps' wide well only narrows.
- **BarChip(lead=, meta=)** (`.msubj`, `.ctxc`): the selected subject with a
  face, thumbnail or app icon before it and a quiet second word; capped at
  240. **BarThumbnail**(paintable, duration=) is that thumbnail, or on its
  own a 44×36 session tile (Camera's strip).
- **BarReadout** (`.mpeta`, `.pager`): grouped text that is not a control:
  an ETA ("12 min" over "3.4 mi · arrive 4:12"), or a pager ("3 of 120")
  between Previous and Next when the app passes them (`tone="media"` over a
  photo or video, as v70's viewer).
- **SplitAction** (`.lsplit`): the key with a chevron half for the other
  ways to do it (Save · Save as…). **BarMenu** (`.nstyle`): a word and a
  chevron on a well that picks one of several (a text style).
- **ZoomControl** (`#vw-bar .vwzw`, `#p-bar .psize`, `#cv-zoompill`): minus ·
  percent (Fit) · plus on a well, a range between zoom glyphs, or a pill
  with a menu of levels.

    center.show_bar([BarSearch("Search places", on_change=search), SPACER, BarAction("navigation", tooltip="Where am I")])
    center.show_bar([BarChip("Selected message text", lead=Person("Priya Raman"), on_dismiss=done), …])
    center.show_bar([ZoomControl(1.0, on_zoom=zoom, on_fit=fit), SEPARATOR,
                     SplitAction("Save", save, [MenuItem("Save as…", on_activate=save_as)])])

CSS: luma-appkit-bar.css (`/* LumaUI: Bar search */` and the rest).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import bar_tokens, icons, lumaui  # noqa: E402
from .action_bubble import FloatingMenu, MenuItem  # noqa: E402
from .action_center import BarChip, register_item  # noqa: E402

__all__ = ["BarSearch", "BarThumbnail", "BarReadout", "SplitAction", "BarMenu", "ZoomControl", "BarProgress", "ZOOM_KINDS",
           "ZOOM_LEVELS", "zoom_step", "zoom_text", "subject_chip", "subject_name"]

ZOOM_KINDS = ("well", "range", "pill")
#: The levels the zoom keys step through (a fraction of actual size).
ZOOM_LEVELS = (0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 8.0)


def _m(group: str, key: str) -> int:
    return int(bar_tokens.metric(group, key))


def _clear(box: Gtk.Box) -> None:
    child = box.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        box.remove(child)
        child = following


def _plain_button(css: str, *, tooltip: str | None = None) -> Gtk.Button:
    button = Gtk.Button(valign=Gtk.Align.CENTER)
    button.add_css_class(css)
    if tooltip:
        button.set_tooltip_text(tooltip)
        button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
    return button


def _icon_key(icon: str, name: str, css: str, callback: Callable[[], None] | None) -> Gtk.Button:
    button = _plain_button(css, tooltip=name)
    button.set_child(icons.image(icon))
    if callback is not None:
        button.connect("clicked", lambda _b: callback())
    return button


def _follow_phone(widget: Gtk.Widget, apply: Callable[[bool], None]) -> None:
    """Call `apply(phone)` whenever the action center around `widget` turns phone-width or back."""
    from .action_center import ActionCenter

    def mapped(_w: Gtk.Widget) -> None:
        center = widget.get_ancestor(ActionCenter)
        if center is None or getattr(widget, "_phone_center", None) is center:
            return
        widget._phone_center = center
        center.connect("notify::css-classes", lambda c, _p: apply(c.has_css_class("phone")))
        apply(center.has_css_class("phone"))

    widget.connect("map", mapped)


class _SearchLayout(Gtk.BoxLayout):
    """The well is its span wide when there is room, and gives way when the bar is capped (v70 flex)."""

    def __init__(self, width: int) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.width = width

    def do_measure(self, widget, orientation, for_size):
        minimum, natural, b1, b2 = Gtk.BoxLayout.do_measure(self, widget, orientation, for_size)
        if orientation == Gtk.Orientation.HORIZONTAL and self.width > 0:
            natural = max(minimum, self.width)
        return minimum, natural, b1, b2


def _popup(menu: object, anchor: Gtk.Widget, label: str) -> None:
    """A menu given as rows (MenuItem, heading, None) or as a callable(anchor).

    v71: rows rise from the bar on a phone (`menus.bar_menu`): the bar grows into them."""
    if callable(menu):
        menu(anchor)
    else:
        from .menus import bar_menu
        bar_menu(anchor, list(menu), label=label)


# ── pure helpers ────────────────────────────────────────────────────────────

def zoom_text(value: float) -> str:
    """100%, 50%, 12.5% → "13%" (whole percent, as v70 shows it)."""
    # Python's round uses ties-to-even; the v70 percent display uses Math.round.
    return f"{math.floor(value * 100 + 0.5)}%"


def zoom_step(value: float, direction: int, levels: Sequence[float] = ZOOM_LEVELS) -> float:
    """The next level in `direction` (+1 in, −1 out) from `value`; the end level when there is none."""
    ordered = sorted(levels)
    if direction > 0:
        return next((level for level in ordered if level > value + 1e-9), ordered[-1])
    return next((level for level in reversed(ordered) if level < value - 1e-9), ordered[0])


def subject_name(chip: BarChip) -> str:
    """What a screen reader hears for the selected subject."""
    parts = [chip.label] + ([chip.meta] if chip.meta else [])
    return "Selected: " + ", ".join(parts)


# ── BarSearch ───────────────────────────────────────────────────────────────

class BarSearch:
    """A search well in the bar. `on_change(text)` on each edit, `on_activate(text)` on Return; Esc clears."""

    SPANS = ("narrow", "regular", "wide")

    def __init__(self, placeholder: str = "Search", *, text: str = "", label: str | None = None,
                 span: str = "regular", wide: bool = False, on_change: Callable[[str], None] | None = None,
                 on_activate: Callable[[str], None] | None = None, collapsed: bool = False, keep: bool = False,
                 on_close: Callable[[], None] | None = None, opens: bool = False) -> None:
        """v71: the placeholder is just "Search" (name the scope with `label`). `collapsed` is a glyph that
        expands in place: in an action center the field replaces the bar's bottom row, with ✕. `keep` stays a
        field on a phone, taking the bar's spare width, 6 before the next button (Filer, Charlie's list).
        `opens` (v71 Notes' `.nlq`) is that well at rest, but with its word ("Search") instead of a live field:
        tapped, the bar's row becomes the field with ✕ (`ActionCenter.open_search`), as `collapsed` does."""
        if not (placeholder or label):
            raise ValueError("a search needs a placeholder or a label to name it")
        span = "wide" if wide else span
        if span not in self.SPANS:
            raise ValueError(f"a search spans one of {self.SPANS}: {span!r}")
        self.placeholder, self.label, self.span = placeholder, label or placeholder, span
        self.on_change, self.on_activate, self.on_close = on_change, on_activate, on_close
        self.opens = opens
        self.collapsed, self.keep = collapsed, keep or opens
        self.on_empty_leave: Callable[[], None] | None = None
        self.on_escape_empty: Callable[[], None] | None = None
        self._text = text
        self.widget: Gtk.Widget | None = None
        self.entry: Gtk.Text | None = None
        self.clear_button: Gtk.Button | None = None

    @property
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text
        if self.entry is not None and self.entry.get_text() != text:
            self._syncing = True
            self.entry.set_text(text)
            self._syncing = False
        if self.clear_button is not None:
            self.clear_button.set_visible(bool(text))

    def focus(self) -> None:
        if self.entry is not None:
            self.entry.grab_focus()

    def _build(self) -> Gtk.Widget:
        bar_tokens.install()
        well = bar_tokens.Well(valign=Gtk.Align.CENTER,
                               accessible_role=Gtk.AccessibleRole.BUTTON if self.collapsed or self.opens
                               else Gtk.AccessibleRole.SEARCH)
        well.add_css_class("lumaui-bar-search")
        well.add_css_class(self.span)
        well.bar_search = True
        well.open_in_bar = lambda center: center.open_search(self)
        if self.keep:
            well.add_css_class("keep")
            well.bar_phone_wide = True
            well.set_hexpand(True)
            from .lumaui_tokens import ACTION_CENTER
            well.set_margin_end(ACTION_CENTER["search_gap"])  # v71: a clear 6 before the next button
        # v70's widths are the border box; GTK adds the padding outside the layout.
        width = (_m("search", {"narrow": "width_narrow", "regular": "width", "wide": "width_wide"}[self.span])
                 - _m("search", "padding_start") - _m("search", "padding_end"))
        layout = _SearchLayout(width)
        layout.set_spacing(_m("search", "gap"))
        well.set_layout_manager(layout)
        well.append(icons.image("search"))
        if self.opens:
            well.add_css_class("opens")
            word = Gtk.Label(label=self.placeholder, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
            word.add_css_class("lumaui-bar-search-word")
            well.append(word)
            well.word = word
        entry = Gtk.Text(hexpand=True, placeholder_text=self.placeholder, width_chars=1)
        entry.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.PLACEHOLDER],
                              [self.label, self.placeholder])
        entry.set_input_purpose(Gtk.InputPurpose.FREE_FORM)
        self._syncing = False
        entry.set_visible(not self.opens)  # an opens well shows its word; a tap opens the real field
        entry.set_text(self._text)
        entry.connect("changed", self._changed)
        entry.connect("activate", lambda t: self.on_activate(t.get_text()) if self.on_activate else None)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        entry.add_controller(keys)
        well.append(entry)
        clear_button = _icon_key("x", "Clear", "lumaui-bar-search-clear", lambda: entry.set_text(""))
        clear_button.set_visible(bool(self._text))
        well.append(clear_button)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_a: self._tapped(well, entry))
        well.add_controller(click)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_a: GLib.timeout_add(160, self._left_empty))
        entry.add_controller(focus)
        self.entry, self.clear_button, self.widget = entry, clear_button, well
        if self.collapsed or self.opens:
            well.update_property([Gtk.AccessibleProperty.LABEL], [self.label])
            well.set_tooltip_text(self.label)
            well.set_focusable(True)
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", lambda _c, kv, *_a: kv in (Gdk.KEY_Return, Gdk.KEY_space, Gdk.KEY_KP_Enter)
                         and (self._tapped(well, entry) or True))
            well.add_controller(keys)

        def phone(on: bool) -> None:
            # v70: on a phone the bar's search (.fbar .srch) folds to its glyph; Maps' own well only narrows.
            # v71: a kept field never folds; a collapsed one is always its glyph until tapped.
            folded = self.collapsed or (on and self.span != "wide" and not self.keep)
            lumaui.set_css_class(well, "folded", folded)
            entry.set_visible(not self.opens and (not folded or bool(entry.get_text())))
            layout.width = 0 if folded else width
            well.queue_resize()

        _follow_phone(well, phone)
        return well

    def _changed(self, entry: Gtk.Text) -> None:
        self._text = entry.get_text()
        if self.clear_button is not None:
            self.clear_button.set_visible(bool(self._text))
        if not self._syncing and self.on_change is not None:
            self.on_change(self._text)

    def _key(self, _controller, keyval: int, _code: int, _state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_Escape and self.entry is not None and self.entry.get_text():
            self.entry.set_text("")
            return True
        if keyval == Gdk.KEY_Escape and self.on_escape_empty is not None:
            self.on_escape_empty()
            return True
        return False

    def _tapped(self, well: Gtk.Widget, entry: Gtk.Text) -> None:
        """A folded glyph expands in place (v71): in an action center the bar becomes the field."""
        if (well.has_css_class("folded") or well.has_css_class("opens")) and not entry.get_text():
            from .action_center import ActionCenter
            center = well.get_ancestor(ActionCenter)
            if center is not None:
                center.open_search(self)
                return
        entry.grab_focus()

    def _left_empty(self) -> bool:
        if (self.on_empty_leave is not None and self.entry is not None and not self.entry.get_text()
                and not self.entry.has_focus()):
            self.on_empty_leave()
        return False


# ── the selected subject ────────────────────────────────────────────────────

@dataclass
class BarThumbnail:
    """A picture (a Gdk.Paintable) with an optional duration ("0:12"); a chip's lead or a session tile."""

    paintable: Gdk.Paintable | None
    duration: str | None = None
    label: str | None = None
    on_activate: Callable[[], None] | None = None
    size: str = "regular"

    def __post_init__(self):
        if self.size not in ("regular", "compact"):
            raise ValueError("thumbnail size must be regular or compact")


def _thumbnail(thumb: BarThumbnail, width: int, height: int, css: str) -> Gtk.Widget:
    frame = Gtk.Overlay(overflow=Gtk.Overflow.HIDDEN, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    frame.add_css_class(css)
    frame.set_size_request(width, height)
    picture = Gtk.Picture(paintable=thumb.paintable, content_fit=Gtk.ContentFit.COVER, can_shrink=True)
    picture.set_size_request(width, height)
    frame.set_child(picture)
    if thumb.duration:
        tag = Gtk.Label(label=thumb.duration, halign=Gtk.Align.START, valign=Gtk.Align.END)
        tag.add_css_class("lumaui-bar-thumb-duration")
        frame.add_overlay(tag)
    return frame


def _lead(lead: object) -> Gtk.Widget:
    from .content_cards import PersonAvatar
    from .content_contact import Person

    if isinstance(lead, Person):
        return PersonAvatar(lead.name, _m("subject", "face"), picture=lead.picture, hue=lead.hue)
    if isinstance(lead, BarThumbnail):
        size = _m("subject", "thumb")
        return _thumbnail(lead, size, size, "lumaui-bar-subject-thumb")
    if isinstance(lead, Gtk.Widget):
        return lead
    if isinstance(lead, str):
        display = Gdk.Display.get_default()
        theme = Gtk.IconTheme.get_for_display(display) if display is not None else None
        if "." in lead and theme is not None and theme.has_icon(lead):
            image = Gtk.Image.new_from_icon_name(lead)
            image.set_pixel_size(_m("subject", "app_icon"))
            image.add_css_class("lumaui-bar-subject-app")
            return image
        image = icons.image(lead if "." not in lead else "app-window")
        image.add_css_class("lumaui-bar-subject-icon")
        return image
    raise TypeError(f"a chip's lead is a Person, a PersonAvatar, a BarThumbnail or an icon name: {lead!r}")


def identity_chip(chip: BarChip) -> Gtk.Widget:
    """Flat app/document identity, with a title above its detail (v71 .dpappid)."""
    if chip.live or chip.well or chip.on_dismiss is not None:
        raise ValueError("an identity is static; recording and dismiss belong to subject chips")
    bar_tokens.install()
    box = Gtk.Box(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.STATUS)
    box.add_css_class("lumaui-bar-identity")
    if chip.lead is not None:
        box.append(_lead(chip.lead))
    elif chip.icon:
        box.append(icons.image(chip.icon, pixel_size=32))
    copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
    for value, css in ((chip.label, "title"), (chip.meta, "detail")):
        if value:
            label = Gtk.Label(label=value, xalign=0, ellipsize=Pango.EllipsizeMode.END,
                              width_chars=1, max_width_chars=30)
            label.add_css_class(css)
            copy.append(label)
    box.append(copy)
    box.update_property([Gtk.AccessibleProperty.LABEL], [", ".join(filter(None, (chip.label, chip.meta)))])
    return box


def subject_chip(chip: BarChip) -> Gtk.Widget:
    """The selected subject: lead, words (and meta), ×; capped at 240 (v70 .msubj)."""
    bar_tokens.install()
    box = Gtk.Box(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.STATUS)
    box.add_css_class("lumaui-bar-subject")
    box.update_property([Gtk.AccessibleProperty.LABEL], [subject_name(chip)])
    if chip.lead is not None:
        box.append(_lead(chip.lead))
    elif chip.icon:
        glyph = icons.image(chip.icon)
        glyph.add_css_class("lumaui-bar-subject-icon")
        box.append(glyph)
    text = Gtk.Label(label=chip.label, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                     width_chars=1, max_width_chars=30)
    text.add_css_class("lumaui-bar-subject-label")
    box.append(text)
    if chip.meta:
        meta = Gtk.Label(label=chip.meta)
        meta.add_css_class("lumaui-bar-subject-meta")
        box.append(meta)
    if chip.on_dismiss is not None:
        close = _icon_key("x", "Done", "lumaui-bar-subject-close", chip.on_dismiss)
        box.append(close)
    # v70's 240 is the border box; GTK adds the padding outside the layout.
    cap = _CapWidth(_m("subject", "max_width") - _m("subject", "padding_start") - _m("subject", "padding_end"))
    box.set_layout_manager(cap)
    return box


class _CapWidth(Gtk.BoxLayout):
    """A box layout whose natural width stops at `cap` (its label ellipsizes past it)."""

    def __init__(self, cap: int) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.cap = cap

    def do_measure(self, widget, orientation, for_size):
        minimum, natural, b1, b2 = Gtk.BoxLayout.do_measure(self, widget, orientation, for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            natural = min(natural, max(minimum, self.cap))
        return minimum, natural, b1, b2


def _thumbnail_item(thumb: BarThumbnail, _size: str) -> Gtk.Widget:
    bar_tokens.install()
    button = _plain_button("lumaui-bar-thumb-button", tooltip=thumb.label or "Open")
    width = _m("subject", "tile_height" if thumb.size == "compact" else "tile_width")
    button.set_child(_thumbnail(thumb, width, _m("subject", "tile_height"),
                                "lumaui-bar-thumb"))
    if thumb.on_activate is not None:
        button.connect("clicked", lambda _b: thumb.on_activate())
    return button


# ── BarReadout ──────────────────────────────────────────────────────────────

class BarReadout:
    """Text that reads, not acts: `primary` over `secondary` (an ETA), or a pager with Previous and Next."""

    TONES = ("bar", "media")

    def __init__(self, primary: str, secondary: str | None = None, *, label: str | None = None,
                 on_previous: Callable[[], None] | None = None, on_next: Callable[[], None] | None = None,
                 tone: str = "bar", note: str | None = None, fill: bool = False) -> None:
        if tone not in self.TONES:
            raise ValueError(f"a readout's tone is one of {self.TONES}: {tone!r}")
        self.tone = tone
        self.note = note
        self.fill = fill
        if (on_previous is None) != (on_next is None):
            raise ValueError("a pager has both Previous and Next")
        if secondary is not None and on_previous is not None:
            raise ValueError("a pager reads one line")
        self.primary, self.secondary, self.label = primary, secondary, label
        self.on_previous, self.on_next = on_previous, on_next
        self.widget: Gtk.Widget | None = None
        self._labels: tuple[Gtk.Label, Gtk.Label | None] | None = None
        self._keys: tuple[Gtk.Button, Gtk.Button] | None = None

    @property
    def pager(self) -> bool:
        return self.on_previous is not None

    def set(self, primary: str, secondary: str | None = None) -> None:
        self.primary, self.secondary = primary, secondary if not self.pager else None
        if self._labels is not None:
            self._labels[0].set_label(primary)
            if self._labels[1] is not None:
                self._labels[1].set_label(secondary or "")
                self._labels[1].set_visible(bool(secondary))
            self._announce()

    def set_bounds(self, has_previous: bool, has_next: bool) -> None:
        """A pager at its first or last item turns that key off."""
        if self._keys is not None:
            self._keys[0].set_sensitive(has_previous)
            self._keys[1].set_sensitive(has_next)

    def _announce(self) -> None:
        if self.widget is not None:
            words = ", ".join(x for x in (self.label, self.primary, self.note, self.secondary) if x)
            self.widget.update_property([Gtk.AccessibleProperty.LABEL], [words])

    def _build(self) -> Gtk.Widget:
        bar_tokens.install()
        if self.pager:
            box = Gtk.Box(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.GROUP)
            box.add_css_class("lumaui-bar-pager")
            box.add_css_class(self.tone)
            previous = _icon_key("chevron-left", "Previous", "lumaui-bar-pager-key", self.on_previous)
            following = _icon_key("chevron-right", "Next", "lumaui-bar-pager-key", self.on_next)
            count = Gtk.Label(label=self.primary, accessible_role=Gtk.AccessibleRole.STATUS)
            count.add_css_class("lumaui-bar-pager-count")
            count.add_css_class("numeric")
            for part in (previous, count, following):
                box.append(part)
            self._labels, self._keys = (count, None), (previous, following)
        else:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER,
                          accessible_role=Gtk.AccessibleRole.STATUS)
            box.add_css_class("lumaui-bar-readout")
            box.add_css_class(self.tone)
            first = Gtk.Label(label=self.primary, xalign=0)
            first.add_css_class("lumaui-bar-readout-primary")
            second = Gtk.Label(label=self.secondary or "", xalign=0, visible=bool(self.secondary), ellipsize=Pango.EllipsizeMode.END)
            second.add_css_class("lumaui-bar-readout-secondary")
            line = Gtk.Box()
            line.add_css_class("lumaui-bar-readout-first")
            first.set_valign(Gtk.Align.BASELINE_FILL)
            line.append(first)
            if self.note:
                note = Gtk.Label(label=self.note, valign=Gtk.Align.BASELINE_FILL)
                note.add_css_class("lumaui-bar-readout-note")
                line.append(note)
            box.append(line)
            box.append(second)
            self._labels = (first, second)
        box.set_hexpand(self.fill)
        box.bar_phone_wide = self.fill
        box.bar_flexible = self.fill
        self.widget = box
        self._announce()
        return box


# ── SplitAction and BarMenu ─────────────────────────────────────────────────

class SplitAction:
    """The key with a chevron half: `on_activate` does the usual thing, `menu` lists the others."""

    def __init__(self, label: str, on_activate: Callable[[], None], menu: object, *, icon: str | None = None,
                 menu_label: str | None = None, sensitive: bool = True, primary: bool = False) -> None:
        self.primary = primary  # keep the main action visible when the bar overflows
        self.label, self.on_activate, self.menu, self.icon = label, on_activate, menu, icon
        self.menu_label = menu_label or f"More ways to {label[:1].lower()}{label[1:]}"
        self.sensitive = sensitive
        self.widget: Gtk.Widget | None = None

    def set_sensitive(self, sensitive: bool) -> None:
        self.sensitive = sensitive
        if self.widget is not None:
            for child in (self.main, self.more):
                child.set_sensitive(sensitive)
            lumaui.set_css_class(self.widget, "off", not sensitive)

    def _build(self) -> Gtk.Widget:
        bar_tokens.install()
        box = Gtk.Box(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.GROUP)
        box.add_css_class("lumaui-bar-split")
        box.update_property([Gtk.AccessibleProperty.LABEL], [self.label])
        self.main = _plain_button("lumaui-bar-split-main")
        line = Gtk.Box()
        if self.icon:
            line.append(icons.image(self.icon))
        line.append(Gtk.Label(label=self.label))
        self.main.set_child(line)
        self.main.connect("clicked", lambda _b: self.on_activate())
        rule = Gtk.Box()
        rule.add_css_class("lumaui-bar-split-rule")
        self.more = _icon_key("chevron-down", self.menu_label, "lumaui-bar-split-more", None)
        self.more.update_property([Gtk.AccessibleProperty.HAS_POPUP], [True])
        self.more.connect("clicked", lambda b: _popup(self.menu, b, self.menu_label))
        for part in (self.main, rule, self.more):
            box.append(part)
        self.widget = box
        self.set_sensitive(self.sensitive)
        return box


class BarMenu:
    """A word and a chevron on a well that picks one of several ("Text ⌄" → Heading, Text, Quote)."""

    def __init__(self, label: str, menu: object = None, *, icon: str | None = None, name: str | None = None,
                 badge: int | None = None, panel: object = None) -> None:
        """`badge` is a count that needs attention after the words ("Discover ② ⌄"), hidden at 0.
        `panel` (a widget or a callable making one) grows the action center instead of a menu (v71 Weather's
        view picker); the button is raised while it is open and a second tap folds it."""
        if menu is None and panel is None:
            raise ValueError("a bar menu opens a menu or a panel")
        self.label, self.menu, self.icon, self.name = label, menu, icon, name or label
        self.panel = panel
        self.badge = badge
        self.widget: Gtk.Button | None = None
        self._text: Gtk.Label | None = None
        self._badge: Gtk.Widget | None = None

    def set_badge(self, count: int | None) -> None:
        self.badge = count
        if self._badge is not None:
            self._badge.set_count(count or 0)
            self._badge.set_visible(bool(count))

    def set_label(self, label: str) -> None:
        self.label = label
        if self._text is not None:
            self._text.set_label(label)
            self.widget.update_property([Gtk.AccessibleProperty.LABEL], [f"{self.name}: {label}"])

    def _build(self) -> Gtk.Widget:
        bar_tokens.install()
        button = _plain_button("lumaui-bar-menu")
        line = Gtk.Box()
        if self.icon:
            line.append(icons.image(self.icon))
        self._text = Gtk.Label(label=self.label, xalign=0, hexpand=True)
        line.append(self._text)
        from .content_badges import CountBadge
        self._badge = CountBadge(self.badge or 0, attention=True)
        self._badge.add_css_class("lumaui-bar-menu-badge")
        self._badge.set_valign(Gtk.Align.CENTER)
        self._badge.set_visible(bool(self.badge))
        self._text.set_hexpand(False)
        line.append(self._badge)
        line.append(Gtk.Box(hexpand=True))
        chevron = icons.image("chevron-down")
        chevron.add_css_class("lumaui-bar-menu-chevron")
        line.append(chevron)
        button.set_child(line)
        button.update_property([Gtk.AccessibleProperty.HAS_POPUP, Gtk.AccessibleProperty.LABEL],
                               [True, f"{self.name}: {self.label}"])
        button.connect("clicked", self._clicked)
        self.widget = button
        return button

    def _clicked(self, button: Gtk.Button) -> None:
        if self.panel is None:
            _popup(self.menu, button, self.name)
            return
        from .action_center import ActionCenter
        center = button.get_ancestor(ActionCenter)
        if center is None:
            raise ValueError("a bar menu with a panel lives in an ActionCenter")
        center.grow(f"barmenu:{self.name}", self.panel, anchor=button)


# ── BarProgress (v71) ───────────────────────────────────────────────────────

class BarProgress:
    """A running job as the bar's row (v71 Valet: "Downloading from Flathub"): a well, the fill, the current
    step centred on it; `on_cancel` adds Cancel beside it. `set_fraction()` / `set_label()` update in place."""

    def __init__(self, fraction: float = 0.0, label: str = "", *, on_cancel: Callable[[], None] | None = None,
                 name: str = "Progress") -> None:
        self.fraction, self.label, self.on_cancel, self.name = max(0.0, min(1.0, fraction)), label, on_cancel, name
        self.widget: Gtk.Widget | None = None
        self._bar: Gtk.ProgressBar | None = None
        self._text: Gtk.Label | None = None

    def set_fraction(self, fraction: float) -> None:
        self.fraction = max(0.0, min(1.0, fraction))
        if self._bar is not None:
            self._bar.set_fraction(self.fraction)
            self.widget.update_property([Gtk.AccessibleProperty.VALUE_NOW], [self.fraction * 100])

    def set_label(self, label: str) -> None:
        self.label = label
        if self._text is not None:
            self._text.set_label(label)

    def _build(self) -> Gtk.Widget:
        bar_tokens.install()
        row = Gtk.Box(hexpand=True, valign=Gtk.Align.CENTER)
        row.add_css_class("lumaui-bar-progress-row")
        well = Gtk.Overlay(hexpand=True, accessible_role=Gtk.AccessibleRole.PROGRESS_BAR)
        well.add_css_class("lumaui-bar-progress")
        self._bar = Gtk.ProgressBar(fraction=self.fraction, hexpand=True, valign=Gtk.Align.FILL)
        self._bar.add_css_class("lumaui-bar-progress-fill")
        well.set_child(self._bar)
        self._text = Gtk.Label(label=self.label, ellipsize=Pango.EllipsizeMode.END, can_target=False)
        self._text.add_css_class("lumaui-bar-progress-label")
        well.add_overlay(self._text)
        well.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.VALUE_MIN,
                              Gtk.AccessibleProperty.VALUE_MAX, Gtk.AccessibleProperty.VALUE_NOW],
                             [self.name, 0, 100, self.fraction * 100])
        row.append(well)
        if self.on_cancel is not None:
            from .action_center import BarAction, make_control
            row.append(make_control(BarAction("", "Cancel", on_activate=self.on_cancel)))
        row.bar_wide = True
        row.bar_phone_wide = True
        self.widget = well
        return row


# ── ZoomControl ─────────────────────────────────────────────────────────────

class ZoomControl:
    """Zoom: `value` is a fraction of actual size (1.0 = 100%); `on_zoom(value)` asks for a new one.

    kind "well" is minus · percent · plus (the percent is Fit when `on_fit` is
    given), "range" a slider between zoom glyphs (`minimum`…`maximum`), and
    "pill" a percent and chevron that opens the levels and Fit. `steps=True` (range only) puts a 44 minus
    and plus key at the slider's ends, for a panel's "Photo size" (v71 `.ppsize`): the slider fills the
    width, stays on a phone, and a key moves it a sixth of its span.
    """

    def __init__(self, value: float, on_zoom: Callable[[float], None], *, on_fit: Callable[[], None] | None = None,
                 kind: str = "well", minimum: float = 0.1, maximum: float = 8.0,
                 levels: Sequence[float] = ZOOM_LEVELS, label: str = "Zoom", steps: bool = False) -> None:
        if kind not in ZOOM_KINDS:
            raise ValueError(f"a zoom control is one of {ZOOM_KINDS}: {kind!r}")
        if not minimum < maximum:
            raise ValueError("minimum is less than maximum")
        if steps and kind != "range":
            raise ValueError("steps belongs to the range kind")
        self.value, self.on_zoom, self.on_fit, self.kind = value, on_zoom, on_fit, kind
        self.minimum, self.maximum, self.label, self.steps = minimum, maximum, label, steps
        self.levels = tuple(level for level in levels if minimum <= level <= maximum)
        self.widget: Gtk.Widget | None = None
        self._syncing = False

    def set_value(self, value: float) -> None:
        """Show a new zoom (without calling `on_zoom`)."""
        self.value = value
        if self.widget is None:
            return
        if self.kind == "range":
            self._syncing = True
            self.scale.set_value(value)
            self._syncing = False
        else:
            self.percent.set_label(zoom_text(value))
        self._update_keys()

    def zoom_in(self) -> None:
        self.on_zoom(zoom_step(self.value, +1, self.levels))

    def zoom_out(self) -> None:
        self.on_zoom(zoom_step(self.value, -1, self.levels))

    def _update_keys(self) -> None:
        if self.kind == "well":
            self.out_key.set_sensitive(self.value > min(self.levels) + 1e-9)
            self.in_key.set_sensitive(self.value < max(self.levels) - 1e-9)
            name = "Fit to window" if self.on_fit else f"{self.label}: {zoom_text(self.value)}"
            self.fit_key.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                                         [name, zoom_text(self.value)])
        elif self.kind == "pill":
            self.widget.update_property([Gtk.AccessibleProperty.LABEL], [f"{self.label}: {zoom_text(self.value)}"])

    def _build(self) -> Gtk.Widget:
        bar_tokens.install()
        if self.kind == "well":
            box = Gtk.Box(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.GROUP)
            box.add_css_class("lumaui-bar-zoom")
            box.update_property([Gtk.AccessibleProperty.LABEL], [self.label])
            self.out_key = _icon_key("minus", "Zoom out", "lumaui-bar-zoom-key", self.zoom_out)
            self.in_key = _icon_key("plus", "Zoom in", "lumaui-bar-zoom-key", self.zoom_in)
            self.fit_key = _plain_button("lumaui-bar-zoom-value", tooltip="Fit to window" if self.on_fit else None)
            self.fit_key.set_valign(Gtk.Align.CENTER)
            self.percent = Gtk.Label(label=zoom_text(self.value))
            self.percent.add_css_class("numeric")
            self.fit_key.set_child(self.percent)
            self.fit_key.connect("clicked", lambda _b: self.on_fit() if self.on_fit else self.on_zoom(1.0))
            for part in (self.out_key, self.fit_key, self.in_key):
                box.append(part)
        elif self.kind == "range":
            box = Gtk.Box(valign=Gtk.Align.CENTER)
            box.add_css_class("lumaui-bar-zoom-range")
            if self.steps:
                box.add_css_class("steps")
                self.out_key = _icon_key("zoom-out", "Smaller", "lumaui-bar-zoom-key", lambda: self._nudge(-1))
                box.append(self.out_key)
            else:
                box.append(icons.image("zoom-out"))
            self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, self.minimum, self.maximum,
                                                  (self.maximum - self.minimum) / 50)
            self.scale.set_draw_value(False)
            self.scale.set_value(self.value)
            if self.steps:
                self.scale.set_hexpand(True)
            else:
                self.scale.set_size_request(_m("zoom", "range_width"), -1)
            self.scale.update_property([Gtk.AccessibleProperty.LABEL], [self.label])
            self.scale.connect("value-changed", self._slid)
            box.append(self.scale)
            if self.steps:
                self.in_key = _icon_key("zoom-in", "Larger", "lumaui-bar-zoom-key", lambda: self._nudge(+1))
                box.append(self.in_key)
            else:
                box.append(icons.image("zoom-in"))
                # v70 .psize is one of the bar's `.wide` extras: a phone leaves it out.
                _follow_phone(box, lambda on: box.set_visible(not on))
        else:
            box = _plain_button("lumaui-bar-zoom-pill")
            line = Gtk.Box()
            self.percent = Gtk.Label(label=zoom_text(self.value))
            self.percent.add_css_class("numeric")
            line.append(self.percent)
            line.append(icons.image("chevron-down"))
            box.set_child(line)
            box.update_property([Gtk.AccessibleProperty.HAS_POPUP], [True])
            box.connect("clicked", self._levels)
        self.widget = box
        self._update_keys()
        return box

    def _nudge(self, direction: int) -> None:
        """A step key: a sixth of the slider's span (v71 moves 40 of 96…340), clamped; asks like the slider."""
        self.scale.set_value(min(self.maximum, max(self.minimum,
                                                   self.scale.get_value() + direction * (self.maximum - self.minimum) / 6)))

    def _slid(self, scale: Gtk.Scale) -> None:
        self.value = scale.get_value()
        if not self._syncing:
            self.on_zoom(self.value)

    def _levels(self, anchor: Gtk.Widget) -> None:
        rows: list = [MenuItem("Zoom in", icon="zoom-in", on_activate=self.zoom_in),
                      MenuItem("Zoom out", icon="zoom-out", on_activate=self.zoom_out), None]
        if self.on_fit is not None:
            rows.append(MenuItem("Fit", icon="scan", on_activate=self.on_fit))
        rows += [MenuItem(zoom_text(level), selected=abs(level - self.value) < 1e-9,
                          on_activate=lambda v=level: self.on_zoom(v)) for level in self.levels]
        FloatingMenu(rows, label=self.label).popup(anchor)


def _built(item, _size: str) -> Gtk.Widget:
    return item._build()


for _kind in (BarSearch, BarReadout, SplitAction, BarMenu, ZoomControl, BarProgress):
    register_item(_kind, _built)
register_item(BarThumbnail, _thumbnail_item)
