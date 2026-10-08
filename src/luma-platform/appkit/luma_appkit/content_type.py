# SPDX-License-Identifier: Apache-2.0
"""LumaUI content: the type scale.

Figtree only. Five roles plus the small label, each with one size, weight and
tracking; numbers that change (display, numeric) are tabular. No one-off
weights, sizes or letter-spacing: use these. v70 `.t-display` … `.t-numeric`.

    display   72/600  tabular      a clock face, a big temperature
    hero      30/700               the name at the top of a person or thing (Contacts)
    title-1   24/650               a page's name
    title-2   15/650               a card's or pane's name
    body      13/400               running text
    caption   11.5/500  muted      metadata under a name
    label     11.5/600  muted      a section's name
    numeric   30/500  tabular      a changing number (speed, a total)

`apply_type(widget, "caption")` styles any label; `TypeLabel("9:41",
role="display", unit=":07 AM")` adds the small unit (seconds, AM/PM, mph) at
40% of the size, baseline-aligned, as the design does.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from . import lumaui_tokens as tokens  # noqa: E402
from .rows_type import type_metrics  # noqa: E402

__all__ = ["TYPE_ROLES", "TypeLabel", "apply_type", "type_class", "WEIGHTS"]

#: Role names as apps write them.
TYPE_ROLES = tuple(role.replace("_", "-") for role in tokens.TYPE_SCALE)


def type_class(role: str) -> str:
    """The CSS class for a role: `type_class("title-1")` is `lumaui-t-title-1`."""
    key = role.replace("_", "-")
    if key not in TYPE_ROLES:
        raise ValueError(f"unknown LumaUI type role {role!r}; use one of {', '.join(TYPE_ROLES)}")
    return f"lumaui-t-{key}"


WEIGHTS = (300, 400, 450, 500, 550, 600, 650, 700, 750)


def apply_type(widget: Gtk.Widget, role: str, *, muted: bool = False, weight: int | None = None,
               ink: str | None = None) -> Gtk.Widget:
    """Give `widget` one type role, replacing any other; `muted` sets it in the quiet ink (v71 --ink-3).

    `ink="secondary"`: the second ink (v71 --ink-2: a mail row's subject); `ink="accent"`: the accent's
    text ink (v71 oklch(0.78 0.11 --acc): an unread mail's time)."""
    if ink not in (None, "secondary", "accent"):
        raise ValueError("ink is 'secondary' or 'accent'")
    if muted and ink:
        raise ValueError("an ink is muted or another, not both")
    wanted = type_class(role)
    for other in TYPE_ROLES:
        name = f"lumaui-t-{other}"
        if name != wanted:
            widget.remove_css_class(name)
    widget.add_css_class(wanted)
    (widget.add_css_class if muted else widget.remove_css_class)("lumaui-t-muted")
    for name in ("secondary", "accent"):
        (widget.add_css_class if ink == name else widget.remove_css_class)(f"lumaui-t-{name}")
    # A role with a phone size (album-title 46, 34 on a phone) switches at phone width.
    spec = tokens.TYPE_SCALE.get(role.replace("-", "_"), {})
    if spec.get("phone_size") and getattr(widget, "_lumaui_type_watch", None) is None:
        from .structure_adapt import WidthWatch

        def crossed(width: int) -> None:
            (widget.add_css_class if width <= tokens.PHONE_MAX_WIDTH else widget.remove_css_class)("lumaui-t-phone")
        widget._lumaui_type_watch = WidthWatch(widget, crossed, threshold=tokens.PHONE_MAX_WIDTH)
    # A role's own weight, or v70's for that one surface (13/550 fact values, 11.5/400 captions).
    if weight is not None and weight not in WEIGHTS:
        raise ValueError(f"a LumaUI weight is one of {WEIGHTS}")
    for w in WEIGHTS:
        (widget.add_css_class if w == weight else widget.remove_css_class)(f"lumaui-w-{w}")
    if isinstance(widget, Gtk.Label) and (wanted == "lumaui-t-display" or
                                           getattr(widget, "_lumaui_display_line_height", False)):
        # GTK's CSS line-height is not realized uniformly by every packaged
        # renderer. Keep this label's single-line measure at the token value.
        attributes = widget.get_attributes()
        attributes = attributes.copy() if attributes is not None else Pango.AttrList()
        removed = attributes.filter(lambda attr: attr.klass.type in (
            Pango.AttrType.LINE_HEIGHT, Pango.AttrType.ABSOLUTE_LINE_HEIGHT))
        if wanted == "lumaui-t-display":
            height = round(type_metrics("display")["line_height"] * Pango.SCALE)
            attributes.insert(Pango.attr_line_height_new_absolute(height))
        widget.set_attributes(attributes)
        widget._lumaui_display_line_height = wanted == "lumaui-t-display"
    return widget


class TypeLabel(Gtk.Box):
    """A label in one type role, with an optional small unit after it."""

    __gtype_name__ = "LumaUITypeLabel"

    def __init__(self, text: str = "", *, role: str = "body", unit: str | None = None, weight: int | None = None,
                 wrap: bool = False) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0,
                         baseline_position=Gtk.BaselinePosition.CENTER)
        self.add_css_class("lumaui-type")
        self.label = Gtk.Label(label=text, xalign=0, valign=Gtk.Align.BASELINE_FILL)
        if wrap:
            self.label.set_wrap(True)
            self.label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.unit = Gtk.Label(xalign=0, valign=Gtk.Align.BASELINE_FILL)
        self.unit.add_css_class("lumaui-t-unit")
        self.append(self.label)
        self.append(self.unit)
        self._role = "body"
        self._weight = weight
        self.set_role(role)
        self.set_unit(unit)

    @property
    def role(self) -> str:
        return self._role

    def set_role(self, role: str) -> None:
        apply_type(self.label, role, weight=self._weight)
        apply_type(self, role, weight=self._weight)
        self._role = role.replace("_", "-")

    def set_text(self, text: str) -> None:
        self.label.set_label(text)

    def get_text(self) -> str:
        return self.label.get_label()

    def set_unit(self, unit: str | None) -> None:
        self.unit.set_label(unit or "")
        self.unit.set_visible(bool(unit))
        # Read as one phrase: "9:41 :07 AM" would be two.
        whole = self.label.get_label() + (f" {unit}" if unit else "")
        self.update_property([Gtk.AccessibleProperty.LABEL], [whole])
