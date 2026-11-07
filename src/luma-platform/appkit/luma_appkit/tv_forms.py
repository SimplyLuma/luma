# SPDX-License-Identifier: Apache-2.0
"""LumaUI on TV: parts of a form that a TV draws its own way, with no TV code in the app.

- `set_ways(box, *ways)`: alternatives (how to get a code: another device, or the web). On a TV
  they are equal cards side by side, each led by its first sentence as a title.
- `set_link(button, uri)`: a button that opens a website. On a TV, where a browser is rarely what
  you want, a code for your phone stands in its place: the QR and "Scan with your phone, or go
  to <place>".
- `set_code_field(field, length=8, group=4, next=None, status=None)`: a one-time code. On a TV the
  field is drawn as wells (4 + gap + 4), the current one ringed with a caret; typing is A-Z and
  0-9, uppercased, at most `length`; the TV keyboard's action moves to `next` once the code is
  whole; `status` (the form's message) shows under the wells, a problem in amber.

Outside TV posture nothing changes: the widgets are the app's own, as it built them.
"""

from __future__ import annotations

import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from .tv_frame import set_part  # noqa: E402

__all__ = ["set_ways", "set_link", "set_code_field", "set_busy"]


def _follow_posture(widget: Gtk.Widget, apply) -> None:
    """Call `apply(tv)` once the widget is in a window, and whenever the window's posture changes."""
    state = {"window": None, "handler": 0, "tv": None}

    def changed(*_args) -> None:
        window = state["window"]
        tv = bool(getattr(window, "tv_posture", False)) if window is not None else False
        if tv != state["tv"]:
            state["tv"] = tv
            apply(tv)

    def mapped(*_args) -> None:
        window = widget.get_root()
        if window is not state["window"]:
            if state["window"] is not None and state["handler"]:
                state["window"].disconnect(state["handler"])
            state["window"] = window
            state["handler"] = window.connect("notify::tv-posture", changed) if hasattr(window, "tv_posture") else 0
        changed()

    widget.connect("map", mapped)
    if widget.get_mapped():
        mapped()


# ── ways ───────────────────────────────────────────────────────────────────

def _title_first_sentence(label: Gtk.Label, on: bool) -> None:
    if not on:
        if hasattr(label, "_lumaui_way_attrs"):
            label.set_attributes(label._lumaui_way_attrs)
            del label._lumaui_way_attrs
        return
    if hasattr(label, "_lumaui_way_attrs"):
        return
    label._lumaui_way_attrs = label.get_attributes()
    text = label.get_text()
    match = re.search(r"[?.!:](\s|$)", text)
    if match is None:
        return
    end = len(text[:match.end()].rstrip().encode("utf-8"))
    attrs = Pango.AttrList()
    for attr in (Pango.attr_weight_new(Pango.Weight.BOLD), Pango.attr_foreground_new(65535, 65535, 65535),
                 Pango.attr_scale_new(1.25)):
        attr.start_index, attr.end_index = 0, end
        attrs.insert(attr)
    label.set_attributes(attrs)


def set_ways(box: Gtk.Box, *ways: Gtk.Widget) -> None:
    """Name `box` the alternatives and each of `ways` one of them."""
    set_part(box, "ways")
    for way in ways:
        set_part(way, "way")

    def apply(tv: bool) -> None:
        for way in ways:
            first = way if isinstance(way, Gtk.Label) else way.get_first_child()
            while first is not None and not isinstance(first, Gtk.Label):
                first = first.get_first_child()
            if isinstance(first, Gtk.Label):
                _title_first_sentence(first, tv)
    _follow_posture(box, apply)


# ── links ──────────────────────────────────────────────────────────────────

def set_link(button: Gtk.Widget, uri: str) -> None:
    """On a TV, a code for your phone stands in for a button that opens `uri` in a browser."""
    parent = button.get_parent()
    if not isinstance(parent, Gtk.Box):
        return
    holder = {"code": None}

    def apply(tv: bool) -> None:
        if tv and holder["code"] is None:
            from .tv_link import _qr
            code = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.START)
            code.add_css_class("lumaui-link-code")
            qr = _qr(uri, 200)
            if qr is not None:
                code.append(qr)
            place = uri.split("://", 1)[-1].rstrip("/")
            words = Gtk.Label(label=(f"Scan with your phone, or go to {place}" if qr is not None
                                     else f"On your phone, go to {place}"), xalign=0, wrap=True)
            words.add_css_class("lumaui-link-place")
            code.append(words)
            parent.insert_child_after(code, button)
            holder["code"] = code
        if holder["code"] is not None:
            holder["code"].set_visible(tv)
        button.set_visible(not tv)
    _follow_posture(parent, apply)


# ── one-time codes ─────────────────────────────────────────────────────────

class _CodeBoxes(Gtk.Box):
    """The wells a TV draws for a one-time code, following the field's own entry."""

    def __init__(self, entry: Gtk.Entry, label: str, length: int, group: int) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.FILL)
        self.add_css_class("lumaui-code")
        self.entry, self.length = entry, length
        heading = Gtk.Label(label=label.upper(), halign=Gtk.Align.CENTER)
        heading.add_css_class("lumaui-code-label")
        self.append(heading)
        row = Gtk.Box(halign=Gtk.Align.CENTER)
        row.add_css_class("lumaui-code-boxes")
        self.boxes = []
        for i in range(length):
            if group and i and i % group == 0:
                gap = Gtk.Box()
                gap.add_css_class("lumaui-code-gap")
                row.append(gap)
            box = Gtk.Label(xalign=0.5, yalign=0.5)
            box.add_css_class("lumaui-code-box")
            row.append(box)
            self.boxes.append(box)
        self.append(row)
        self.status = Gtk.Label(halign=Gtk.Align.CENTER, wrap=True, visible=False, justify=Gtk.Justification.CENTER)
        self.status.add_css_class("lumaui-code-status")
        self.append(self.status)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_a: entry.grab_focus())
        self.add_controller(click)
        self._caret_on = True
        GLib.timeout_add(530, self._blink)
        entry.connect("changed", lambda *_a: self.redraw())
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda *_a: self.redraw())
        focus.connect("leave", lambda *_a: self.redraw())
        entry.add_controller(focus)
        self.redraw()

    def _blink(self) -> bool:
        if self.get_root() is None and not self.get_mapped() and self.get_parent() is None:
            return GLib.SOURCE_REMOVE
        self._caret_on = not self._caret_on
        if self.get_mapped():
            self.redraw()
        return GLib.SOURCE_CONTINUE

    def redraw(self) -> None:
        text = self.entry.get_text()
        root = self.entry.get_root()
        focus = root.get_focus() if root is not None else None
        focused = focus is not None and (focus is self.entry or focus.is_ancestor(self.entry))
        for i, box in enumerate(self.boxes):
            now = focused and i == min(len(text), self.length - 1) and len(text) < self.length
            box.set_label(text[i] if i < len(text) else ("|" if now and self._caret_on else ""))
            (box.add_css_class if now else box.remove_css_class)("now")
        (self.add_css_class if focused else self.remove_css_class)("lumaui-code-focus")


# Base32 codes (A-Z, 2-7): the digits that cannot occur read as the letters they look like.
_BASE32_TYPED = str.maketrans({"0": "O", "1": "I", "8": "B"})


def _clean_code(text: str, alphabet: str | None, length: int) -> str:
    text = text.upper()
    if alphabet == "base32":
        return re.sub(r"[^A-Z2-7]", "", text.translate(_BASE32_TYPED))[:length]
    return re.sub(r"[^A-Z0-9]", "", text)[:length]


def set_code_field(field: Gtk.Widget, *, length: int = 8, group: int = 4, next: Gtk.Widget | None = None,
                   status: Gtk.Label | None = None, alphabet: str | None = None) -> None:
    """On a TV, draw `field` (a luma_appkit TextField) as a one-time code of `length` wells.

    `alphabet="base32"` keeps A-Z and 2-7 in every posture: 0, 1 and 8 typed become O, I and B,
    other characters are dropped."""
    entry = getattr(field, "entry", field)
    words = getattr(status, "label", status) if status is not None else None  # a TypeLabel's own label
    label = getattr(getattr(field, "label", None), "get_text", lambda: "Code")()
    parent = field.get_parent()
    holder = {"boxes": None, "tv": False, "fixing": False, "hiding": False, "status_shown": False}

    def normalised(*_args) -> None:
        if holder["fixing"] or not (holder["tv"] or alphabet):
            return
        text = entry.get_text()
        clean = _clean_code(text, alphabet, length)
        if clean != text:
            holder["fixing"] = True
            entry.set_text(clean)
            entry.set_position(-1)
            holder["fixing"] = False
    entry.connect("changed", normalised)

    def activated(*_args) -> bool:
        # The TV keyboard's action: on to the form's next step once the code is whole.
        if holder["tv"] and next is not None:
            if len(entry.get_text()) >= length and next.get_sensitive():
                next.grab_focus()
            else:
                # OK on the code with the keyboard put away: focus the field afresh, which opens it
                from .tv_keyboard import tv_keyboard_state
                if not tv_keyboard_state()[0] and entry.get_root() is not None:
                    entry.get_root().set_focus(None)
                    GLib.idle_add(lambda: (entry.grab_focus(), False)[1])
            entry.stop_emission_by_name("activate")
            return True
        return False
    entry.connect("activate", activated)

    def mirror(*_args) -> None:
        boxes = holder["boxes"]
        if boxes is None or status is None:
            return
        if holder["tv"] and status.get_visible() and not holder["hiding"]:
            # the form shows its message: on a TV it shows under the wells instead
            holder["hiding"] = True
            holder["status_shown"] = True
            status.set_visible(False)
            holder["hiding"] = False
        elif not holder["hiding"] and not holder["tv"]:
            return
        text = words.get_text()
        boxes.status.set_label(text)
        boxes.status.set_visible(bool(text) and holder.get("status_shown", False))
        busy = text.endswith("\u2026") or text.endswith("...")
        (boxes.status.remove_css_class if busy else boxes.status.add_css_class)("lumaui-code-problem")

    def status_visible(*_args) -> None:
        # Only the form showing its message counts: hiding it here notifies too (after a freeze).
        if holder["hiding"] or not status.get_visible():
            return
        holder["status_shown"] = True
        mirror()

    def apply(tv: bool) -> None:
        holder["tv"] = tv
        if tv and holder["boxes"] is None and isinstance(parent, Gtk.Box):
            holder["boxes"] = _CodeBoxes(entry, label, length, group)
            parent.insert_child_after(holder["boxes"], field)
            if status is not None:
                words.connect("notify::label", mirror)
                status.connect("notify::visible", status_visible)
        boxes = holder["boxes"]
        if boxes is None:
            return
        boxes.set_visible(tv)
        # the field's own entry keeps the focus and the typing; on a TV the wells draw it
        (field.add_css_class if tv else field.remove_css_class)("lumaui-code-source")
        for child in (getattr(field, "label", None),):  # the wells carry the field's name
            if child is not None:
                child.set_visible(not tv)
        if tv:
            from .tv_frame import LumaUI
            setter = getattr(LumaUI, "ui_set_keyboard_action", None)
            if setter is not None:
                setter(entry, "Next")
            normalised()
        if status is not None:
            holder["hiding"] = True
            if tv:
                if status.get_visible():  # shown by the form (not hidden here): it moves under the wells
                    holder["status_shown"] = True
                status.set_visible(False)
            else:
                status.set_visible(holder.get("status_shown", False))
            holder["hiding"] = False
            if tv:
                text = words.get_text()
                boxes.status.set_label(text)
                boxes.status.set_visible(bool(text) and holder["status_shown"])
        boxes.redraw()
    _follow_posture(parent if parent is not None else field, apply)


# ── busy buttons ───────────────────────────────────────────────────────────

def set_busy(button: Gtk.Widget, busy: bool, label: str | None = None) -> None:
    """Say on `button` what it is doing while busy ("Signing In…"). On a TV the button carries
    the words (a TV has no room for a separate status line beside it); elsewhere nothing changes.
    `set_busy(button, False)` gives the button its own label back."""
    words = getattr(button, "label_widget", None)
    if words is None:
        child = button.get_child() if hasattr(button, "get_child") else None
        words = child if isinstance(child, Gtk.Label) else None
    if words is None:
        return
    if not hasattr(button, "_lumaui_own_label"):
        button._lumaui_own_label = words.get_label()
    root = button.get_root()
    tv = bool(getattr(root, "tv_posture", False))
    words.set_label(label if busy and tv and label else button._lumaui_own_label)
