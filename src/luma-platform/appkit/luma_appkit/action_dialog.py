# SPDX-License-Identifier: Apache-2.0
"""LumaUI action: the destructive dialog.

For what can't come back. Centred on the asking window, which dims: the icon
on top, the question as the title ("Uninstall Kiln?", naming the thing), one
line of consequence, Cancel and the red action as two equal buttons. Cancel
has focus, so Enter never deletes by accident; Esc cancels; Tab stays inside;
focus returns where it was. Anything that can be undone gets a Toast with
Undo instead, never this. No "Are you sure?", no "OK". v70 `lConfirm`.

v71: on a phone it is an in-bar confirm in the bar's frame (16 a side, 34 up,
26 round): title and line left-aligned with no icon, Cancel and the red
action side by side at 48. Where the asking bar is right there, the confirm
grows the bar itself (`in_bar`): a title, one line, Cancel and the red action
in the panel above the row.

    DestructiveDialog.ask(button, title="Delete this conversation?",
                          body="It's removed from this computer. Others keep their copy.",
                          action="Delete", on_confirm=lambda also: delete())
    DestructiveDialog.in_bar(center, title="Delete 3 photos?", body="They're removed from every device.",
                             action="Delete", on_confirm=delete)
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from . import icons, lumaui  # noqa: E402
from .lumaui_tokens import DIALOG  # noqa: E402
from .structure_layers import LayerHost, ModalHandle  # noqa: E402

_COLUMN = DIALOG["width"] - 2 * DIALOG["padding_x"]  # v70 lConfirm: the words wrap at 300

__all__ = ["DestructiveDialog"]

_VAGUE = {"ok", "okay", "yes", "confirm", "continue"}


class DestructiveDialog(Gtk.Box):
    """The card itself. Use `DestructiveDialog.ask(...)` to show one."""

    __gtype_name__ = "LumaUIDestructiveDialog"

    def __init__(self, *, title: str, body: str, action: str = "Delete", icon: str = "trash-2",
                 option: str | None = None) -> None:
        if not title.strip() or title.strip().lower().startswith("are you sure"):
            raise ValueError("a destructive dialog's title is the question, naming the thing")
        if action.strip().lower() in _VAGUE:
            raise ValueError("name the action for what it does (Delete, Leave, Uninstall), never OK")
        super().__init__(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.ALERT_DIALOG)
        self.add_css_class("lumaui-dialog")
        self.on_confirm: Callable[[bool], None] | None = None
        self.on_cancel: Callable[[], None] | None = None
        self.handle: ModalHandle | None = None

        handle = Gtk.Box(halign=Gtk.Align.CENTER)
        handle.add_css_class("lumaui-drawer-handle")
        self.append(handle)

        badge = Gtk.Box(halign=Gtk.Align.CENTER)
        badge.add_css_class("lumaui-dialog-icon")
        glyph = icons.image(icon)
        glyph.set_hexpand(True)
        glyph.set_halign(Gtk.Align.CENTER)
        badge.append(glyph)
        self.append(badge)

        self.title_label = Gtk.Label(label=title, wrap=True, justify=Gtk.Justification.CENTER,
                                     wrap_mode=Pango.WrapMode.WORD, max_width_chars=1,
                                     width_request=_COLUMN)
        self.title_label.add_css_class("lumaui-dialog-title")
        self.body_label = Gtk.Label(label=body, wrap=True, justify=Gtk.Justification.CENTER,
                                    wrap_mode=Pango.WrapMode.WORD, max_width_chars=1,
                                    width_request=_COLUMN)
        self.body_label.add_css_class("lumaui-dialog-body")
        self.append(self.title_label)
        self.append(self.body_label)

        self.option: Gtk.CheckButton | None = None
        if option:
            self.option = Gtk.CheckButton(label=option, halign=Gtk.Align.CENTER)
            self.option.add_css_class("lumaui-dialog-option")
            self.append(self.option)

        self.buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
        self.buttons.add_css_class("lumaui-dialog-buttons")
        self.cancel_button = Gtk.Button(label="Cancel", hexpand=True)
        self.cancel_button.add_css_class("lumaui-dialog-cancel")
        self.action_button = Gtk.Button(label=action, hexpand=True)
        self.action_button.add_css_class("lumaui-dialog-action")
        self.cancel_button.connect("clicked", lambda _b: self._cancel())
        self.action_button.connect("clicked", lambda _b: self._confirm())
        self.buttons.append(self.cancel_button)
        self.buttons.append(self.action_button)
        self.append(self.buttons)

        # The alert dialog reads as its question and its consequence. (Relations
        # to the labels would say the same; PyGObject cannot pass their lists.)
        self.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION], [title, body])

    @staticmethod
    def in_bar(center: "object", *, title: str, body: str, action: str = "Delete",
               on_confirm: Callable[[], None] | None = None, on_cancel: Callable[[], None] | None = None,
               anchor: Gtk.Widget | None = None, key: str = "confirm") -> Gtk.Widget:
        """Confirm inside the grown bar (v71 rule 9): `center` (an ActionCenter) grows into it.

        Cancel has focus; Esc cancels; either button folds the bar. Returns the panel.
        """
        from .bar_panel import PanelConfirm
        if not title.strip() or title.strip().lower().startswith("are you sure"):
            raise ValueError("a destructive dialog's title is the question, naming the thing")
        if action.strip().lower() in _VAGUE:
            raise ValueError("name the action for what it does (Delete, Leave, Uninstall), never OK")
        panel = PanelConfirm(title=title, body=body, action=action, on_confirm=on_confirm, on_cancel=on_cancel)
        center.grow(key, panel, anchor=anchor)
        return panel

    @classmethod
    def ask(cls, where: Gtk.Widget, *, title: str, body: str, action: str = "Delete",
            icon: str = "trash-2", option: str | None = None,
            on_confirm: Callable[[bool], None] | None = None,
            on_cancel: Callable[[], None] | None = None) -> "DestructiveDialog":
        """Ask over the window `where` is in. `on_confirm(option_checked)` runs on the red action."""
        dialog = cls(title=title, body=body, action=action, icon=icon, option=option)
        dialog.on_confirm, dialog.on_cancel = on_confirm, on_cancel
        dialog.present(where)
        return dialog

    def present(self, where: Gtk.Widget) -> ModalHandle:
        host = LayerHost.window_host(where)
        phone = lumaui.mobile_form_factor() or lumaui.is_phone_width(host)
        if phone:
            # v71: the bar's frame, not a drawer with a grabber. Left-aligned words, no icon,
            # Cancel and the red action side by side; Cancel keeps focus.
            from .lumaui_tokens import ACTION_CENTER as metrics
            self.add_css_class("frame")
            for child in (self.get_first_child(), self.get_first_child().get_next_sibling()):
                child.set_visible(False)  # the grabber and the icon
            for label in (self.title_label, self.body_label):
                label.set_justify(Gtk.Justification.LEFT)
                label.set_xalign(0)
                label.set_size_request(-1, -1)
                label.set_halign(Gtk.Align.FILL)
            if self.option is not None:
                self.option.set_halign(Gtk.Align.START)
        self.handle = host.present_modal(self, on_cancel=self._cancelled, initial_focus=self.cancel_button,
                                         drawer=False)
        if phone:
            self.set_halign(Gtk.Align.FILL)
            self.set_valign(Gtk.Align.END)
            self.set_margin_start(metrics["frame_side"])
            self.set_margin_end(metrics["frame_side"])
            self.set_margin_bottom(metrics["frame_bottom"])
            self.handle.scrim.add_css_class("bar-frame")
        else:
            # v70 centres the card on the window, not on the work surface under the title row: lifted
            # once the host is laid out (a confirm asked for before the window is, finds it at 0).
            self.add_tick_callback(self._lift)
        return self.handle

    def _lift(self, _widget, _clock) -> bool:
        host, root = self.get_parent(), self.get_root()
        if host is None or root is None or host.get_height() <= 0:
            return True
        ok, bounds = host.compute_bounds(root)
        if not ok:
            return True
        self.set_margin_bottom(round(bounds.get_y()))
        return False


    @property
    def option_checked(self) -> bool:
        return bool(self.option and self.option.get_active())

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()

    def _cancel(self) -> None:
        if self.handle is not None:
            self.handle.cancel()

    def _cancelled(self) -> None:
        if self.on_cancel is not None:
            self.on_cancel()

    def _confirm(self) -> None:
        checked = self.option_checked
        if self.handle is not None:
            self.handle.close()
        if self.on_confirm is not None:
            self.on_confirm(checked)
