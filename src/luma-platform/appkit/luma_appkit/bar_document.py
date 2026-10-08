# SPDX-License-Identifier: Apache-2.0
"""LumaUI: the document header and the export sheet (the creative suite).

**DocumentHeader** (v70 `.sutop`/`.sutopr`: Stage, Reel, Grid, Canvas) says
which document is open and what can be done with all of it: the app's icon,
the title and a quiet line ("Stage · Saved"), who else is in it, then Share,
the one key (Export or Present) and More. At 720 or narrower the line and
the faces go and Share keeps only its glyph. It is the content of the title
row; the app places it there (or at the top of its island) and never
styles it.

**ExportSheet** (v70 `.mdsheet.rlsheet`, Reel's Export) asks how to export:
a title and a line, the choices (each with a shape, a name, a detail and a
size), Cancel and Export. A sheet 10 below the title row over a scrim that
takes clicks without closing it, on a computer and a phone alike (v70 has no
drawer for it); Cancel or Esc closes it.

    header = DocumentHeader("Launch film", icon="org.projectluma.Reel", subtitle="Reel · Saved",
                            people=[Person("Nora Feld")], share=share, primary="Export",
                            on_primary=lambda: ExportSheet.present(header, title="Export “Launch film”",
                                                                   subtitle="42 seconds, to Videos.",
                                                                   choices=CHOICES, selected="standard",
                                                                   on_export=export),
                            more=registry)

Export only reports the choice: the app does the work and says how it went;
the sheet never pretends an export happened.
CSS: luma-appkit-bar.css, `/* LumaUI: Document header */`, `/* LumaUI: Export sheet */`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk, Pango  # noqa: E402

from . import bar_tokens, icons, lumaui  # noqa: E402
from .content_cards import PersonAvatar  # noqa: E402
from .content_contact import Person  # noqa: E402
from .structure_adapt import WidthWatch  # noqa: E402
from .structure_layers import LayerHost  # noqa: E402

__all__ = ["DocumentHeader", "ExportSheet", "ExportChoice", "DOCUMENT_PRIMARY", "EXPORT_SHAPES"]

#: The key a document header offers, with its glyph (Export = `share`, as the kit's icon rule).
DOCUMENT_PRIMARY = {"Export": "share", "Present": "play"}
#: How an export choice is drawn: the frame it makes, or sound.
EXPORT_SHAPES = ("landscape", "master", "portrait", "audio")


def _m(group: str, key: str) -> int:
    return int(bar_tokens.metric(group, key))


def _app_icon(name: str, size: int) -> Gtk.Widget:
    display = Gdk.Display.get_default()
    theme = Gtk.IconTheme.get_for_display(display) if display is not None else None
    if "." in name and theme is not None and theme.has_icon(name):
        image = Gtk.Image.new_from_icon_name(name)
    else:
        image = icons.image(name if "." not in name else "file")
    image.set_pixel_size(size)
    return image


def _button(css: str, *, icon: str | None, label: str | None, tooltip: str | None = None) -> Gtk.Button:
    button = Gtk.Button(valign=Gtk.Align.CENTER)
    button.add_css_class(css)
    if label is None:  # a glyph alone sits in the middle of its key
        button.set_child(icons.image(icon))
        button.word = None
        button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip or icon or ""])
        if tooltip:
            button.set_tooltip_text(tooltip)
        return button
    line = Gtk.Box()
    if icon:
        line.append(icons.image(icon))
    text = None
    if label:
        text = Gtk.Label(label=label)
        line.append(text)
    button.set_child(line)
    button.word = text
    name = tooltip or label or icon or ""
    button.update_property([Gtk.AccessibleProperty.LABEL], [name])
    if tooltip:
        button.set_tooltip_text(tooltip)
    return button


class DocumentHeader(Gtk.Box):
    """The title row of a document: which one, who is in it, Share, the key and More."""

    __gtype_name__ = "LumaUIDocumentHeader"

    def __init__(self, title: str, *, icon: str | None = None, subtitle: str | None = None,
                 people: Sequence[Person] = (), share: Callable[[Gtk.Widget], None] | None = None,
                 primary: str | None = None, on_primary: Callable[[], None] | None = None,
                 more: object = None) -> None:
        super().__init__(valign=Gtk.Align.CENTER, hexpand=True)
        if primary is not None and primary not in DOCUMENT_PRIMARY:
            raise ValueError(f"a document's key is one of {tuple(DOCUMENT_PRIMARY)}: {primary!r}")
        if (primary is None) != (on_primary is None):
            raise ValueError("a key needs on_primary, and on_primary a key")
        bar_tokens.install()
        self.add_css_class("lumaui-doc-header")
        self.controls: dict[str, Gtk.Widget] = {}
        if icon:
            glyph = _app_icon(icon, _m("doc", "icon"))
            glyph.add_css_class("lumaui-doc-icon")
            self.append(glyph)
        self.title_label = Gtk.Label(label=title, ellipsize=Pango.EllipsizeMode.END, xalign=0)
        self.title_label.add_css_class("lumaui-doc-title")
        self.append(self.title_label)
        self.meta = Gtk.Label(label=subtitle or "", visible=bool(subtitle))
        self.meta.add_css_class("lumaui-doc-meta")
        self.append(self.meta)
        self.append(Gtk.Box(hexpand=True))

        actions = Gtk.Box(valign=Gtk.Align.CENTER)
        actions.add_css_class("lumaui-doc-actions")
        self.faces = Gtk.Box(valign=Gtk.Align.CENTER, visible=bool(people))
        self.faces.add_css_class("lumaui-doc-faces")
        self.set_people(people)
        actions.append(self.faces)
        if share is not None:
            button = _button("lumaui-doc-button", icon=icons.SHARE, label="Share")
            button.update_property([Gtk.AccessibleProperty.HAS_POPUP], [True])
            button.connect("clicked", lambda b: share(b))
            actions.append(button)
            self.controls["share"] = button
        if primary is not None:
            button = _button("lumaui-doc-button", icon=DOCUMENT_PRIMARY[primary], label=primary)
            button.add_css_class("key")
            button.connect("clicked", lambda _b: on_primary())
            actions.append(button)
            self.controls["primary"] = button
        if more is not None:
            button = _button("lumaui-doc-more", icon="ellipsis", label=None, tooltip="More")
            button.update_property([Gtk.AccessibleProperty.HAS_POPUP], [True])
            button.connect("clicked", lambda b: self._more(b, more))
            actions.append(button)
            self.controls["more"] = button
        self.append(actions)
        self._narrow = False
        self._watch = WidthWatch(self, self._width, threshold=_m("doc", "narrow_max_width"))

    def set_title(self, title: str, subtitle: str | None = None) -> None:
        self.title_label.set_label(title)
        self.meta.set_label(subtitle or "")
        self.meta.set_visible(bool(subtitle) and not self._narrow)

    def set_people(self, people: Sequence[Person]) -> None:
        """Who else is in the document (the app's own collaboration data; never invented)."""
        self.people = list(people)
        child = self.faces.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.faces.remove(child)
            child = following
        for person in self.people:
            face = PersonAvatar(person.name, _m("doc", "face"), picture=person.picture, hue=person.hue)
            face.add_css_class("lumaui-doc-face")
            self.faces.append(face)
        names = ", ".join(p.name for p in self.people)
        self.faces.update_property([Gtk.AccessibleProperty.LABEL], [f"Here: {names}" if names else ""])
        self.faces.set_visible(bool(self.people) and not getattr(self, "_narrow", False))

    @property
    def narrow(self) -> bool:
        return self._narrow

    def _width(self, width: int) -> None:
        narrow = 0 < width <= _m("doc", "narrow_max_width")
        self._narrow = narrow
        lumaui.set_css_class(self, "narrow", narrow)
        self.meta.set_visible(bool(self.meta.get_label()) and not narrow)
        self.faces.set_visible(bool(self.people) and not narrow)
        share = self.controls.get("share")
        if share is not None and share.word is not None:
            share.word.set_visible(not narrow)
            share.set_tooltip_text("Share" if narrow else None)

    def _more(self, anchor: Gtk.Widget, more: object) -> None:
        if callable(more):
            more(anchor)
            return
        from .structure_drawer import MenuDrawer
        from .commands import CommandRegistry

        if isinstance(more, CommandRegistry):
            if lumaui.is_phone_width(anchor):
                MenuDrawer.present(anchor, more)
                return
            from .structure_placement import CornerPill
            CornerPill._more(anchor, more)  # the one way a registry's menu opens from a button
            return
        from .action_bubble import FloatingMenu
        FloatingMenu(list(more), label="More").popup(anchor)


# ── ExportSheet ─────────────────────────────────────────────────────────────

@dataclass
class ExportChoice:
    """One way to export: `shape` draws what it makes; `size` is an estimate ("about 1.2 GB")."""

    key: str
    label: str
    detail: str = ""
    size: str = ""
    shape: str = "landscape"

    def __post_init__(self) -> None:
        if self.shape not in EXPORT_SHAPES:
            raise ValueError(f"an export shape is one of {EXPORT_SHAPES}: {self.shape!r}")


class ExportSheet(Gtk.Box):
    """How to export: choices, Cancel and Export. Open it with `ExportSheet.present(where, …)`."""

    __gtype_name__ = "LumaUIExportSheet"

    def __init__(self, *, title: str, choices: Sequence[ExportChoice], subtitle: str | None = None,
                 selected: str | None = None, on_export: Callable[[str], None] | None = None,
                 on_cancel: Callable[[], None] | None = None, action: str = "Export") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.DIALOG)
        if not choices:
            raise ValueError("an export sheet offers at least one choice")
        keys = [c.key for c in choices]
        if len(set(keys)) != len(keys):
            raise ValueError("export choices have unique keys")
        if selected is not None and selected not in keys:
            raise ValueError(f"unknown export choice {selected!r}")
        bar_tokens.install()
        self.add_css_class("lumaui-export")
        self.update_property([Gtk.AccessibleProperty.LABEL], [title])
        self.choices, self.selected = list(choices), selected or keys[0]
        self.on_export, self.on_cancel = on_export, on_cancel
        self._host: LayerHost | None = None
        self._scrim: Gtk.Widget | None = None
        self._keys: Gtk.EventControllerKey | None = None
        self._return: Gtk.Widget | None = None

        heading = Gtk.Label(label=title, xalign=0, wrap=True, accessible_role=Gtk.AccessibleRole.HEADING)
        heading.add_css_class("lumaui-export-title")
        self.append(heading)
        if subtitle:
            line = Gtk.Label(label=subtitle, xalign=0, wrap=True)
            line.add_css_class("lumaui-export-body")
            self.append(line)
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.RADIO_GROUP)
        self.list.add_css_class("lumaui-export-list")
        self.rows: dict[str, Gtk.ToggleButton] = {}
        group = None
        for choice in self.choices:
            row = Gtk.ToggleButton(group=group, accessible_role=Gtk.AccessibleRole.RADIO)
            group = group or row
            row.add_css_class("lumaui-export-choice")
            line = Gtk.Box()
            shape = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            shape.add_css_class("lumaui-export-shape")
            shape.add_css_class(choice.shape)
            holder = Gtk.Box(valign=Gtk.Align.CENTER)
            holder.add_css_class("lumaui-export-shape-box")
            holder.append(shape)
            line.append(holder)
            words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
            name = Gtk.Label(label=choice.label, xalign=0)
            name.add_css_class("lumaui-export-name")
            words.append(name)
            if choice.detail:
                detail = Gtk.Label(label=choice.detail, xalign=0, wrap=True)
                detail.add_css_class("lumaui-export-detail")
                words.append(detail)
            line.append(words)
            if choice.size:
                size = Gtk.Label(label=choice.size)
                size.add_css_class("lumaui-export-size")
                line.append(size)
            row.set_child(line)
            row.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                                [choice.label, " · ".join(x for x in (choice.detail, choice.size) if x)])
            row.set_active(choice.key == self.selected)
            lumaui.set_css_class(row, "on", choice.key == self.selected)
            row.connect("toggled", self._toggled, choice.key)
            self.rows[choice.key] = row
            self.list.append(row)
        self.append(self.list)
        from .structure_adapt import arrow_keys
        arrow_keys(self.list, orientation=Gtk.Orientation.VERTICAL, activate=True)

        buttons = Gtk.Box()
        buttons.add_css_class("lumaui-export-actions")
        buttons.append(Gtk.Box(hexpand=True))
        self.cancel_button = Gtk.Button(label="Cancel")
        self.cancel_button.add_css_class("lumaui-export-button")
        self.cancel_button.connect("clicked", lambda _b: self.cancel())
        self.export_button = Gtk.Button(label=action)
        self.export_button.add_css_class("lumaui-export-button")
        self.export_button.add_css_class("key")
        self.export_button.connect("clicked", lambda _b: self.export())
        buttons.append(self.cancel_button)
        buttons.append(self.export_button)
        self.append(buttons)

    @classmethod
    def present(cls, where: Gtk.Widget, **options) -> "ExportSheet":
        """Open over the window `where` is in: 10 below its title row, over a scrim (v70 `.rlscrim`).

        The scrim takes clicks without closing the sheet (Cancel or Esc does);
        on a phone the sheet is the same, 16 from each side.
        """
        sheet = cls(**options)
        host = LayerHost.window_host(where)
        sheet._host = host
        scrim = Gtk.Box(hexpand=True, vexpand=True)
        scrim.add_css_class("lumaui-export-scrim")
        swallow = Gtk.GestureClick()
        swallow.connect("pressed", lambda g, *_a: g.set_state(Gtk.EventSequenceState.CLAIMED))
        scrim.add_controller(swallow)
        sheet._scrim = scrim
        sheet.set_halign(Gtk.Align.CENTER)
        sheet.set_valign(Gtk.Align.START)
        sheet.set_margin_top(_m("export", "top"))
        width = host.get_width()
        side = _m("export", "phone_side")
        sheet.set_size_request(min(_m("export", "width"), max(0, width - 2 * side)) if width > 0 else _m("export", "width"), -1)
        content = host.get_child()
        if content is not None:
            content.set_can_focus(False)
        host.add_overlay(scrim)
        host.add_overlay(sheet)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", sheet._key)
        root = host.get_root()
        if root is not None:
            root.add_controller(keys)
        sheet._keys = keys
        sheet._return = where
        lumaui.on_next_frame(sheet, lambda: (scrim.add_css_class("shown"), sheet.add_css_class("shown")))
        sheet.rows[sheet.selected].grab_focus()
        return sheet

    def _key(self, _controller, keyval: int, _code: int, _state: object) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.cancel()
            return True
        return False

    def _toggled(self, row: Gtk.ToggleButton, key: str) -> None:
        if row.get_active():
            self.selected = key
        for name, other in self.rows.items():
            lumaui.set_css_class(other, "on", name == self.selected)

    def export(self) -> None:
        """Export the selected choice (the app does it and reports how it went)."""
        key = self.selected
        self.close()
        if self.on_export is not None:
            self.on_export(key)

    def cancel(self) -> None:
        self.close()
        if self.on_cancel is not None:
            self.on_cancel()

    @property
    def is_open(self) -> bool:
        return self._host is not None

    def close(self) -> None:
        host, self._host = self._host, None
        if host is None:
            return
        root = host.get_root()
        if self._keys is not None and root is not None:
            root.remove_controller(self._keys)
        self._keys = None
        for widget in (self._scrim, self):
            if widget is not None and widget.get_parent() is host:
                host.remove_overlay(widget)
        content = host.get_child()
        if content is not None:
            content.set_can_focus(True)
        if self._return is not None and self._return.get_mapped():
            self._return.grab_focus()
