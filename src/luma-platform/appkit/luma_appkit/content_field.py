# SPDX-License-Identifier: Apache-2.0
"""LumaUI content: the labelled text field.

TextField — a label over a recessed well (the search well's look), the
accent ring on focus, an optional caption hint below (v70 `lField`, `.lfld`,
`.lfhint`). The label names the entry for screen readers; the hint
describes it. `purpose` sets the on-screen keyboard (email, URL, number…).

    server = TextField("Server address", placeholder="music.example.com", purpose="url")
    user = TextField("Username", value="nick", hint="Luma signs in with a token; the password isn’t kept.")
    user.text

HeroTitleField — the name at the top of a person or thing, edited in place
(v70 `.chero h1`, `.cname`): the hero type (30/700), looking exactly like
the title until it has focus, when it becomes a recessed well with the
accent ring. Enter commits, Esc puts back what was there and leaves.

    HeroTitleField("Priya Raman", placeholder="Name", on_commit=rename)
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from .content_type import apply_type  # noqa: E402

__all__ = ["TextField", "HeroTitleField", "ParagraphField", "FIELD_PURPOSES"]

#: What the field holds → the input purpose the on-screen keyboard uses.
FIELD_PURPOSES = {
    "text": Gtk.InputPurpose.FREE_FORM, "name": Gtk.InputPurpose.NAME, "email": Gtk.InputPurpose.EMAIL,
    "url": Gtk.InputPurpose.URL, "phone": Gtk.InputPurpose.PHONE, "number": Gtk.InputPurpose.NUMBER,
    "password": Gtk.InputPurpose.PASSWORD, "pin": Gtk.InputPurpose.PIN,
}


class TextField(Gtk.Box):
    """A label over a recessed well, with an optional hint under it."""

    __gtype_name__ = "LumaUITextField"

    def __init__(self, label: str, *, value: str = "", placeholder: str | None = None, hint: str | None = None,
                 purpose: str = "text", on_changed: Callable[[str], None] | None = None,
                 on_activate: Callable[[str], None] | None = None, size: str = "regular") -> None:
        if size not in ("regular", "panel"):
            raise ValueError("field size must be regular or panel")
        if purpose not in FIELD_PURPOSES:
            raise ValueError(f"a field's purpose is one of {', '.join(FIELD_PURPOSES)}")
        if not label.strip():
            raise ValueError("a text field needs a label")
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("lumaui-field")
        self.set_size(size)
        self.label = Gtk.Label(label=label, xalign=0)
        self.label.add_css_class("lumaui-field-label")
        self.append(self.label)
        self.entry = Gtk.Entry(text=value, input_purpose=FIELD_PURPOSES[purpose],
                               visibility=purpose not in ("password", "pin"))
        if placeholder:
            self.entry.set_placeholder_text(placeholder)
        self.entry.add_css_class("lumaui-field-well")
        self.entry.update_relation([Gtk.AccessibleRelation.LABELLED_BY], [Gtk.AccessibleList.new_from_list([self.label])])
        self.append(self.entry)
        self.hint = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.hint.add_css_class("lumaui-field-hint")
        self.append(self.hint)
        self.set_hint(hint)
        if on_changed is not None:
            self.entry.connect("changed", lambda e: on_changed(e.get_text()))
        if on_activate is not None:
            self.entry.connect("activate", lambda e: on_activate(e.get_text()))

    def set_size(self, size: str) -> None:
        """Use the 48px touch field in a grown action panel, or the regular 38px well."""
        if size not in ("regular", "panel"):
            raise ValueError("field size must be regular or panel")
        self.size = size
        (self.add_css_class if size == "panel" else self.remove_css_class)("panel")

    @property
    def text(self) -> str:
        return self.entry.get_text()

    @text.setter
    def text(self, value: str) -> None:
        self.entry.set_text(value)

    def set_hint(self, hint: str | None) -> None:
        self.hint.set_label(hint or "")
        self.hint.set_visible(bool(hint))
        if hint:
            self.entry.update_relation([Gtk.AccessibleRelation.DESCRIBED_BY], [Gtk.AccessibleList.new_from_list([self.hint])])

    def do_grab_focus(self) -> bool:
        return self.entry.grab_focus()


class HeroTitleField(Gtk.Entry):
    """A hero title that is also its own editor. See the module docstring."""

    __gtype_name__ = "LumaUIHeroTitleField"

    def __init__(self, text: str = "", *, editable: bool = True, placeholder: str = "Name", role: str = "hero",
                 on_changed: Callable[[str], None] | None = None,
                 on_commit: Callable[[str], None] | None = None) -> None:
        # A title that can't be edited is a heading to assistive technology,
        # not a text box (v70's h1); the role is fixed at construction.
        a11y = Gtk.AccessibleRole.TEXT_BOX if editable else Gtk.AccessibleRole.HEADING
        super().__init__(text=text, xalign=0.5, halign=Gtk.Align.CENTER, placeholder_text=placeholder,
                         input_purpose=Gtk.InputPurpose.NAME, width_chars=16, accessible_role=a11y)
        self.add_css_class("lumaui-hero-field")
        apply_type(self, role)  # "hero" (30/700), or "detail-title" (30/650: Memos) and the like
        self.update_property([Gtk.AccessibleProperty.LABEL], [placeholder if editable else text or placeholder])
        if not editable:
            self.update_property([Gtk.AccessibleProperty.LEVEL], [1])
        self._on_commit = on_commit
        self._before = text
        self.set_editable_title(editable)
        if on_changed is not None:
            self.connect("changed", lambda e: on_changed(e.get_text()))
        self.connect("activate", lambda _e: self.commit())
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda *_a: setattr(self, "_before", self.get_text()))
        self.add_controller(focus)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

    @property
    def text(self) -> str:
        return self.get_text()

    def set_editable_title(self, editable: bool) -> None:
        """Editable: a field that looks like the title until focused. Not: just the title."""
        self.set_editable(editable)
        self.set_can_focus(editable)
        self.set_focusable(editable)
        (self.add_css_class if editable else self.remove_css_class)("editable")

    def commit(self) -> None:
        self._before = self.get_text()
        if self._on_commit is not None:
            self._on_commit(self._before)
        root = self.get_root()
        if root is not None:
            root.set_focus(None)

    def _key(self, _controller, keyval: int, _code: int, _state: object) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.set_text(self._before)
            root = self.get_root()
            if root is not None:
                root.set_focus(None)
            return True
        return False


class ParagraphField(Gtk.TextView):
    """A paragraph that is its own editor: body text, wrapping, no frame (v70 Contacts' private note).

    It reads as the paragraph it edits; the placeholder shows, muted, while it
    is empty. `text` is its content. For a labelled single line, use TextField.
    """

    __gtype_name__ = "LumaUIParagraphField"

    def __init__(self, text: str = "", *, placeholder: str = "", label: str | None = None) -> None:
        super().__init__(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, hexpand=True,
                         top_margin=0, bottom_margin=0, left_margin=0, right_margin=0)
        self.add_css_class("lumaui-paragraph-field")
        apply_type(self, "body")
        self.get_buffer().set_text(text)
        self._placeholder = Gtk.Label(label=placeholder, xalign=0, can_target=False)
        self._placeholder.add_css_class("lumaui-paragraph-placeholder")
        self.add_overlay(self._placeholder, 0, 0)
        self.get_buffer().connect("changed", lambda _b: self._sync())
        self.update_property([Gtk.AccessibleProperty.LABEL], [label or placeholder])
        self._sync()

    def _sync(self) -> None:
        self._placeholder.set_visible(not self.text)

    @property
    def text(self) -> str:
        buffer = self.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
