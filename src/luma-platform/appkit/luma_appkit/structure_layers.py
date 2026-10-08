# SPDX-License-Identifier: Apache-2.0
"""LumaUI structure: layers, the one place floating parts appear.

GTK has no standard for what floats over a window's content: toasts, the
destructive dialog, drawers, the selection bubble. LumaUI's primitive is the
`LayerHost`, an overlay that owns everything laid over one region.

- A window gets one at its root on first use (`LayerHost.for_widget`), so an
  application that does nothing still gets correct toasts and dialogs.
- An application (or the kit's own AppWindow, later) may put one around a
  region instead, such as the main island, so a toast centres on that island.
  Parts find the nearest host above the widget they were asked from.
- `present_modal()` shows a card over a scrim by default; `scrim=False`
  keeps an undimmed backdrop for map sheets. It centres on a computer and
  becomes a drawer from the bottom edge at phone width (grab handle, full
  width, 28 radius top). Esc cancels, Tab stays inside, the content behind can't take focus,
  focus returns to where it was. A click on the scrim nudges a dialog and
  closes a drawer; dragging a drawer down closes it. F2's MenuDrawer and
  F3's selection bubble present through the same host.

Motion and metrics come from the --lumaui-* tokens; reduced motion swaps the
movement for a fade (CSS `prefers-reduced-motion` plus `lumaui.duration`).
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import lumaui  # noqa: E402

__all__ = ["LayerHost", "ModalHandle"]


def _title_scrim_for(window: Gtk.Widget) -> Gtk.Widget | None:
    """The window's title-row scrim, laid over its whole content at the top, made once."""
    twin = getattr(window, "_lumaui_title_scrim", None)
    if twin is not None:
        return twin
    is_adw = hasattr(window, "set_content") and hasattr(window, "get_content")
    content = window.get_content() if is_adw else window.get_child()
    if content is None:
        return None
    overlay = Gtk.Overlay()
    overlay.add_css_class("lumaui-title-scrim-host")
    if is_adw:
        window.set_content(None)
    else:
        window.set_child(None)
    overlay.set_child(content)
    twin = Gtk.Box(valign=Gtk.Align.START, hexpand=True, can_target=False, can_focus=False, visible=False)
    twin.add_css_class("lumaui-title-scrim")
    overlay.add_overlay(twin)
    overlay.set_measure_overlay(twin, False)
    if is_adw:
        window.set_content(overlay)
    else:
        window.set_child(overlay)
    window._lumaui_title_scrim = twin
    return twin


class ModalHandle:
    """An open modal card. `close()` dismisses it without calling anything."""

    def __init__(self, host: "LayerHost", card: Gtk.Widget, scrim: Gtk.Widget,
                 on_cancel: Callable[[], None] | None, drawer: bool) -> None:
        self.host, self.card, self.scrim = host, card, scrim
        self.on_cancel, self.drawer = on_cancel, drawer
        self.closed = False
        self._return_focus: Gtk.Widget | None = None
        self._keys: Gtk.EventControllerKey | None = None
        self._title_scrim: Gtk.Widget | None = None
        self._title_sync: int = 0

    def cancel(self) -> None:
        """Close as Cancel/Esc would, then tell the owner."""
        if self.closed:
            return
        callback = self.on_cancel
        self.close()
        if callback:
            callback()

    def close(self, *, quiet: bool = False) -> None:
        if self.closed:
            return
        self.closed = True
        self.host._modal_closed(self, quiet=quiet)

    def nudge(self) -> None:
        """Shake the card sideways: the window behind is not available yet."""
        if lumaui.reduced_motion():
            return
        self.card.remove_css_class("nudge")
        lumaui.on_next_frame(self.card, lambda: self.card.add_css_class("nudge"))
        GLib.timeout_add(lumaui.duration("nudge") + 20, lambda: (self.card.remove_css_class("nudge"), False)[1])


class LayerHost(Gtk.Overlay):
    """An overlay that owns what floats over one region (toasts, dialogs, drawers)."""

    __gtype_name__ = "LumaUILayerHost"

    def __init__(self, child: Gtk.Widget | None = None, *, name: str = "content") -> None:
        super().__init__()
        self.add_css_class("lumaui-layer-host")
        self.layer_name = name
        self._modal: ModalHandle | None = None
        if child is not None:
            self.set_child(child)

    # ── finding a host ────────────────────────────────────────────────────

    @classmethod
    def for_widget(cls, widget: Gtk.Widget) -> "LayerHost":
        """The nearest host at or above `widget`; the window's own is made on first use."""
        node: Gtk.Widget | None = widget
        while node is not None:
            if isinstance(node, LayerHost):
                return node
            node = node.get_parent()
        root = widget.get_root()
        if root is None:
            raise ValueError("LumaUI layers need a widget that is inside a window")
        return cls.install(root)

    @classmethod
    def window_host(cls, widget: Gtk.Widget) -> "LayerHost":
        """The host of the window `widget` is in (modal cards dim the whole window).

        That is the nearest host named "window" above it: the one a window gets
        at its root, or one that stands for a window, such as a phone-sized
        preview frame.
        """
        node: Gtk.Widget | None = widget
        while node is not None:
            if isinstance(node, LayerHost) and node.layer_name == "window":
                return node
            node = node.get_parent()
        root = widget.get_root()
        if root is None:
            raise ValueError("LumaUI layers need a widget that is inside a window")
        return cls.install(root)

    @classmethod
    def install(cls, window: Gtk.Widget) -> "LayerHost":
        """The window's host: its own, or its content wrapped in one (once).

        A LumaUI `AppWindow` keeps its host under its title row (`layer_host`),
        so a modal dims the window below its title bar, as v70 does, and the
        title row, the identity and the window controls stay as they were.
        """
        own = getattr(window, "layer_host", None)
        if isinstance(own, LayerHost):
            return own
        getter = getattr(window, "get_content", None) if hasattr(window, "set_content") else None
        content = getter() if getter else window.get_child()
        if isinstance(content, LayerHost) and content.layer_name == "window":
            return content
        host = cls(name="window")
        if getter:
            window.set_content(None)
            host.set_child(content)
            window.set_content(host)
        else:
            window.set_child(None)
            host.set_child(content)
            window.set_child(host)
        lumaui.install(window.get_display())
        return host

    # ── modal cards ───────────────────────────────────────────────────────

    @property
    def modal(self) -> ModalHandle | None:
        return self._modal

    def present_modal(self, card: Gtk.Widget, *, on_cancel: Callable[[], None] | None = None,
                      initial_focus: Gtk.Widget | None = None, drawer: bool | None = None,
                      scrim: bool = True) -> ModalHandle:
        """Show `card`; an undimmed drawer can use `scrim=False`.

        The transparent backdrop still catches outside clicks and preserves
        drawer dismissal, focus containment, and focus return.
        """
        if self._modal is not None:
            self._modal.close(quiet=True)
        lumaui.install(self.get_display())
        drawer = lumaui.is_phone_width(self) if drawer is None else drawer
        backdrop = Gtk.Box(hexpand=True, vexpand=True, can_focus=False)
        if scrim:
            backdrop.add_css_class("lumaui-scrim")
        card.add_css_class("lumaui-modal")
        # A confirm keeps the modal scrim even as a drawer (v70 .lcscrim); a menu drawer dims deeper.
        lumaui.set_css_class(backdrop, "drawer", drawer and not card.has_css_class("lumaui-dialog"))
        lumaui.set_css_class(card, "drawer", drawer)
        if drawer:
            card.set_halign(Gtk.Align.FILL)
            card.set_valign(Gtk.Align.END)
        else:
            card.set_halign(Gtk.Align.CENTER)
            card.set_valign(Gtk.Align.CENTER)
        handle = ModalHandle(self, card, backdrop, on_cancel, drawer)
        self._modal = handle

        click = Gtk.GestureClick()
        click.connect("released", lambda *_a: handle.cancel() if handle.drawer else handle.nudge())
        backdrop.add_controller(click)
        if drawer:
            drag = Gtk.GestureDrag()
            drag.connect("drag-end", lambda _g, _x, dy: handle.cancel() if dy > 80 else None)
            card.add_controller(drag)

        root = self.get_root()
        handle._return_focus = root.get_focus() if root is not None else None
        content = self.get_child()
        if content is not None:
            content.set_can_focus(False)
            content.set_can_target(False)
        self.add_overlay(backdrop)
        self.add_overlay(card)
        if scrim:
            self._mirror_over_title_row(handle)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._modal_key, handle)
        if root is not None:
            root.add_controller(keys)
            handle._keys = keys

        def shown() -> None:
            backdrop.add_css_class("shown")
            card.add_css_class("shown")
            candidates = _focusables(card)
            target = initial_focus or (candidates[0] if candidates else None)
            if target is not None:
                target.grab_focus()

        lumaui.on_next_frame(card, shown)
        return handle

    # ── the title row under a modal ──────────────────────────────────────

    def _mirror_over_title_row(self, handle: ModalHandle) -> None:
        """Dim the title row with the content, so the window stays one surface.

        An AppWindow's host sits *under* its title row (cards hang from it and centre on the
        work surface), so its scrim never reached the row: with a sheet open the title bar
        stayed bright over a dimmed frame, two surfaces where there is one window. The row
        gets a twin of the scrim that copies the scrim's classes as they change (colour per
        treatment, `bar-frame`, `drawer`, `shown` and its fade), never takes input (the window
        still moves and its controls still work), and goes when the card does.
        """
        root = self.get_root()
        title_row = getattr(root, "title_bar", None)
        if root is None or getattr(root, "layer_host", None) is not self or title_row is None:
            return
        if not title_row.get_visible() or getattr(root, "phone_device", False):
            return
        twin = _title_scrim_for(root)
        if twin is None:
            return
        height = title_row.get_height()
        if height <= 0:
            return
        twin.set_size_request(-1, height)

        def sync(*_args) -> None:
            twin.set_css_classes([*handle.scrim.get_css_classes(), "lumaui-title-scrim"])

        sync()
        twin.set_visible(True)
        handle._title_scrim = twin
        handle._title_sync = handle.scrim.connect("notify::css-classes", sync)

    @staticmethod
    def _release_title_row(handle: ModalHandle) -> None:
        twin = handle._title_scrim
        if twin is None:
            return
        if handle._title_sync:
            handle.scrim.disconnect(handle._title_sync)
            handle._title_sync = 0
        handle._title_scrim = None
        host = handle.host
        if host._modal is None or host._modal._title_scrim is not twin:
            twin.set_css_classes(["lumaui-title-scrim"])
            twin.set_visible(False)

    def _modal_key(self, _controller: Gtk.EventControllerKey, keyval: int, _code: int,
                   state: object, handle: ModalHandle) -> bool:
        from gi.repository import Gdk
        if handle.closed:
            return False
        if keyval == Gdk.KEY_Escape:
            handle.cancel()
            return True
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab, Gdk.KEY_KP_Tab):
            items = _focusables(handle.card)
            if not items:
                return True
            root = self.get_root()
            focus = root.get_focus() if root is not None else None
            current = next((i for i, w in enumerate(items) if focus is not None and
                            (focus is w or focus.is_ancestor(w))), -1)
            backwards = keyval == Gdk.KEY_ISO_Left_Tab or bool(state & Gdk.ModifierType.SHIFT_MASK)
            items[(current + (-1 if backwards else 1)) % len(items)].grab_focus()
            return True
        return False

    def _modal_closed(self, handle: ModalHandle, *, quiet: bool) -> None:
        if self._modal is handle:
            self._modal = None
        root = self.get_root()
        if handle._keys is not None and root is not None:
            root.remove_controller(handle._keys)
        content = self.get_child()
        if content is not None and self._modal is None:
            content.set_can_focus(True)
            content.set_can_target(True)
        handle.scrim.remove_css_class("shown")
        handle.card.remove_css_class("shown")
        handle.card.set_can_target(False)

        def remove() -> bool:
            for widget in (handle.card, handle.scrim):
                if widget.get_parent() is self:
                    self.remove_overlay(widget)
            self._release_title_row(handle)
            return False

        delay = 0 if quiet else lumaui.duration("dialog_fade")
        if delay:
            GLib.timeout_add(delay, remove)
        else:
            remove()
        target = handle._return_focus
        if not quiet and target is not None and target.get_root() is root and target.get_mapped():
            target.grab_focus()


def _focusables(widget: Gtk.Widget) -> list[Gtk.Widget]:
    """Focusable, sensitive, visible controls inside `widget`, in order."""
    found: list[Gtk.Widget] = []

    def walk(node: Gtk.Widget) -> None:
        child = node.get_first_child()
        while child is not None:
            if child.get_visible() and child.is_sensitive():
                if child.get_focusable() and child.get_can_focus():
                    found.append(child)
                else:
                    walk(child)
            child = child.get_next_sibling()

    walk(widget)
    return found
