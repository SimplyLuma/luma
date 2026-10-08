# SPDX-License-Identifier: Apache-2.0
"""LumaUI action: the toast.

One place, one look, for every application: centred on the visible island,
just above its bottom bar; a plain past-tense line; the icon for what
happened; Undo when it can be undone (4 s, otherwise 2.4 s). Announced
politely to screen readers. A new toast replaces the one showing. Never a
question (use DestructiveDialog) and never a per-app toast widget. v70
`lToast(win, msg, {icon, undo})`; this replaces ad-hoc `Adw.Toast` uses.

    Toast.show(button, "Conversation deleted", kind="deleted", undo=restore)
    Toast.show(page, "Google signed you out of nick@gmail.com", kind="error",
               action=("Sign in again", sign_in))   # the one way to fix it, in its own word
    Toast.show(island, "Added Kansas City, MO", kind="place")
    busy = Toast.show(window, "Preparing export…", busy=True); …; busy.dismiss()

The toast appears in the nearest `LayerHost` above the widget it was asked
from (an application may wrap its main island in one, `ToastHost`), or the
window's own. A bar the toast must clear is registered once with
`ToastHost.track_bar(bar)`; the kit's action center will do that itself.
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import GLib, Graphene, Gtk, Pango  # noqa: E402

from . import icons, lumaui  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402
from .structure_layers import LayerHost  # noqa: E402

__all__ = ["Toast", "ToastHost", "TOAST_KINDS"]

#: What happened → (Lucide glyph, tone). Tones: good (the default green),
#: accent, love, muted (routine: copied, deleted, archived …), warning, danger.
TOAST_KINDS: dict[str, tuple[str, str]] = {
    "done": ("check", "good"),
    "added": ("plus", "good"),
    "saved": ("check", "good"),
    "sent": ("send", "good"),
    "place": ("map-pin", "accent"),
    "favourite": ("heart-filled", "love"),
    "unfavourite": ("heart", "muted"),
    "copied": ("copy", "muted"),
    "deleted": ("trash-2", "muted"),
    "archived": ("archive", "muted"),
    "downloaded": ("download", "muted"),
    "notified": ("bell", "muted"),
    "signed-out": ("log-out", "muted"),
    "paused": ("pause", "muted"),
    "undone": ("undo-2", "muted"),
    "opening": ("square-arrow-out-up-right", "good"),
    "warning": ("triangle-alert", "warning"),
    "error": ("circle-alert", "danger"),
}


class ToastHost(LayerHost):
    """A layer host around a region (usually the main island) that toasts centre on."""

    __gtype_name__ = "LumaUIToastHost"

    def __init__(self, child: Gtk.Widget | None = None, *, edge: str = "bottom", offset: int = 0) -> None:
        super().__init__(child, name="content")
        self._bars: list[Gtk.Widget] = []
        self.set_edge(edge, offset)

    def set_edge(self, edge: str, offset: int = 0) -> None:
        """Where toasts appear in this region: "bottom" (above its bar, the default) or "top", hanging
        `offset` below the region's top (v71 Camera: it has no bar, so toasts sit in the band at 96)."""
        if edge not in ("bottom", "top"):
            raise ValueError('toasts sit at the "bottom" or the "top"')
        self.toast_edge, self.toast_offset = edge, offset

    def track_bar(self, bar: Gtk.Widget) -> None:
        """Keep toasts above `bar` (a floating bar at the foot of this region) while it shows."""
        if bar not in self._bars:
            self._bars.append(bar)


class Toast(Gtk.Box):
    """One toast. Make it with `Toast.show(...)`; keep the result to `dismiss()` a busy one."""

    __gtype_name__ = "LumaUIToast"
    _current: dict[int, "Toast"] = {}

    def __init__(self, message: str, *, kind: str = "done", undo: Callable[[], None] | None = None,
                 busy: bool = False, action: tuple[str, Callable[[], None]] | None = None) -> None:
        if undo is not None and action is not None:
            raise ValueError("a toast offers Undo or one action, not both")
        if kind not in TOAST_KINDS:
            raise ValueError(f"unknown toast kind {kind!r}; use one of {', '.join(TOAST_KINDS)}")
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.CENTER,
                         valign=Gtk.Align.END, accessible_role=Gtk.AccessibleRole.STATUS)
        glyph, tone = TOAST_KINDS[kind]
        self.kind, self.message, self._undo = kind, message, undo
        self._timeout = 0
        self._host: LayerHost | None = None
        self.add_css_class("lumaui-toast")
        self.add_css_class(tone)
        if busy:
            lead: Gtk.Widget = Gtk.Spinner(spinning=True)
            lead.add_css_class("lumaui-toast-spinner")
        else:
            lead = icons.image(glyph)
            lead.add_css_class("lumaui-toast-icon")
        self.append(lead)
        text = Gtk.Label(label=message, xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        text.add_css_class("lumaui-toast-text")
        self.append(text)
        if action is not None:
            # A failure's one fix ("Sign in again", "Update password"), drawn as Undo is, and kept as long.
            word, undo = action
            self._undo = undo
        if undo is not None:
            button = Gtk.Button(label=word if action is not None else "Undo", valign=Gtk.Align.CENTER)
            button.add_css_class("lumaui-toast-undo")
            button.connect("clicked", self._on_undo)
            self.append(button)
            self.add_css_class("has-action")
        self.busy = busy
        self.update_property([Gtk.AccessibleProperty.LABEL], [message])

    # ── public API ────────────────────────────────────────────────────────

    @classmethod
    def show(cls, where: Gtk.Widget, message: str, *, kind: str = "done",
             undo: Callable[[], None] | None = None, busy: bool = False,
             action: tuple[str, Callable[[], None]] | None = None) -> "Toast":
        """Show `message` for what just happened, in the region `where` belongs to.

        `action=(word, callback)`: a failure's one fix in its own word, in Undo's place."""
        toast = cls(message, kind=kind, undo=undo, busy=busy, action=action)
        toast._present(LayerHost.for_widget(where))
        return toast

    def dismiss(self) -> None:
        """Take the toast away now (a busy toast stays until this is called)."""
        if self._timeout:
            GLib.source_remove(self._timeout)
            self._timeout = 0
        host = self._host
        if host is None:
            return
        self._host = None
        if Toast._current.get(id(host)) is self:
            Toast._current.pop(id(host), None)
        self.remove_css_class("shown")
        self.set_can_target(False)

        def remove() -> bool:
            if self.get_parent() is host:
                host.remove_overlay(self)
            return False

        GLib.timeout_add(max(1, lumaui.duration("fade")), remove)

    @property
    def timeout_ms(self) -> int:
        """How long it stays: 4 s with Undo, 2.4 s without, until dismissed when busy."""
        if self.busy:
            return 0
        return lumaui.duration("toast_undo" if self._undo else "toast")

    # ── internals ─────────────────────────────────────────────────────────

    def _present(self, host: LayerHost) -> None:
        lumaui.install(host.get_display())
        previous = Toast._current.get(id(host))
        if previous is not None:
            previous.dismiss()
        Toast._current[id(host)] = self
        self._host = host
        self._place(host)
        host.add_overlay(self)
        lumaui.on_next_frame(self, lambda: self.add_css_class("shown"))
        if hasattr(host, "announce"):
            host.announce(self.message, Gtk.AccessibleAnnouncementPriority.MEDIUM)
        if self.timeout_ms:
            self._timeout = GLib.timeout_add(self.timeout_ms, self._expire)

    def _place(self, host: LayerHost) -> None:
        metrics = tokens.TOAST
        if getattr(host, "toast_edge", "bottom") == "top":
            self.set_valign(Gtk.Align.START)
            self.set_margin_top(getattr(host, "toast_offset", 0) or metrics["inset"])
            self.add_css_class("top")
        else:
            bottom = metrics["inset"]
            height = host.get_height()
            phone = lumaui.is_phone_width(host)
            for bar in getattr(host, "_bars", ()):
                if not (bar.get_mapped() and bar.get_visible()):
                    continue
                # v71: clear the whole bar, a grown panel included (it is part of the bar); on a phone any height.
                ok, point = bar.compute_point(host, Graphene.Point())
                if ok and height > 0 and (height - point.y < 320 or phone):
                    bottom = max(bottom, int(height - point.y) + metrics["above_bar"])
            self.set_margin_bottom(bottom)
        if lumaui.is_phone_width(host):
            margin = metrics["phone_margin"]
            self.set_margin_start(margin)
            self.set_margin_end(margin)
            self.add_css_class("phone")

    def _expire(self) -> bool:
        self._timeout = 0
        self.dismiss()
        return False

    def _on_undo(self, _button: Gtk.Button) -> None:
        callback, self._undo = self._undo, None
        self.dismiss()
        if callback is not None:
            callback()
