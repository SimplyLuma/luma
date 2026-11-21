# SPDX-License-Identifier: Apache-2.0
"""LumaUI: a section's link to another's look, at the foot of an editor section.

One thing shown two ways (a scene's Landscape and Vertical canvases, a page's
light and dark styles) either follows the other or has its own values:

    key = SyncKey("Landscape", "Vertical", synced=True, on_unsync=..., on_sync=...)
    key.set_synced(False, changes=3)      # after the app gave Vertical its own look
    SyncKey("Landscape", "Vertical", primary=True, changes=0)   # on Landscape's own editor: information

- Synced: a closed lock and "In sync with Landscape"; the row is a button,
  and pressing it is the deliberate unsync (``on_unsync``; values unchanged).
- Own: an open lock, "Vertical has its own look" and the action "Sync with
  Landscape" (``on_sync``).
- ``primary=True`` is the other side's information: "Vertical follows this
  look", or "Vertical has its own look (3 changes)"; nothing to press.

``OverrideDot`` marks a single field that differs from the other side (a small
accent dot beside its label, with a tooltip).
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from . import icons, lumaui  # noqa: E402

__all__ = ["SyncKey", "OverrideDot"]


class SyncKey(Gtk.Box):
    """Whether this side follows another's look; the way to unsync or sync again."""

    __gtype_name__ = "LumaUISyncKey"

    def __init__(self, source: str, target: str, *, synced: bool = True, primary: bool = False, changes: int = 0,
                 noun: str = "look", on_unsync: Callable[[], None] | None = None,
                 on_sync: Callable[[], None] | None = None) -> None:
        super().__init__(accessible_role=Gtk.AccessibleRole.GROUP)
        self.add_css_class("lumaui-sync-key")
        self.source, self.target, self.noun, self.primary = source, target, noun, primary
        self.on_unsync, self.on_sync = on_unsync, on_sync
        self.synced, self.changes = synced, changes
        self.row = Gtk.Button(hexpand=True)
        self.row.add_css_class("lumaui-sync-key-row")
        line = Gtk.Box(spacing=8)
        self.lock = icons.image("lock")
        self.lock.add_css_class("lumaui-sync-key-lock")
        line.append(self.lock)
        self.words = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        self.words.add_css_class("lumaui-sync-key-words")
        line.append(self.words)
        self.row.set_child(line)
        self.row.connect("clicked", lambda _b: self._unsync())
        self.append(self.row)
        from .content_controls import TextButton
        self.action = TextButton(f"Sync with {source}", size="small", on_click=self._sync)
        self.action.add_css_class("lumaui-sync-key-action")
        self.append(self.action)
        self._show()

    def set_synced(self, synced: bool, *, changes: int | None = None) -> None:
        """Show the state the app now has (the key never changes the values itself)."""
        self.synced = bool(synced)
        if changes is not None:
            self.changes = max(0, int(changes))
        self._show()

    def _show(self) -> None:
        source, target, noun = self.source, self.target, self.noun
        if self.primary:
            words = (f"{target} follows this {noun}" if self.synced else
                     f"{target} has its own {noun}" + (f" ({self.changes} change{'s' if self.changes != 1 else ''})"
                                                       if self.changes else ""))
        else:
            words = f"In sync with {source}" if self.synced else f"{target} has its own {noun}"
        self.words.set_label(words)
        self.lock.set_from_icon_name(icons.icon_name("lock" if self.synced else "lock-open"))
        lumaui.set_css_class(self, "synced", self.synced)
        lumaui.set_css_class(self, "primary", self.primary)
        # Only the synced, secondary side's row is a button (the deliberate unsync).
        pressable = self.synced and not self.primary and self.on_unsync is not None
        self.row.set_can_target(pressable)
        self.row.set_focusable(pressable)
        lumaui.set_css_class(self.row, "static", not pressable)
        self.row.set_tooltip_text(f"Give {target} its own {noun}" if pressable else None)
        self.row.update_property([Gtk.AccessibleProperty.LABEL],
                                 [words + (f". Press to give {target} its own {noun}" if pressable else "")])
        self.action.set_visible(not self.synced and not self.primary and self.on_sync is not None)

    def _unsync(self) -> None:
        if self.synced and not self.primary and self.on_unsync is not None:
            self.on_unsync()

    def _sync(self) -> None:
        if self.on_sync is not None:
            self.on_sync()


class OverrideDot(Gtk.Box):
    """A small accent dot beside a field's label: this value differs from the other side's."""

    __gtype_name__ = "LumaUIOverrideDot"

    def __init__(self, *, active: bool = False, tooltip: str | None = None) -> None:
        super().__init__(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER,
                         accessible_role=Gtk.AccessibleRole.IMG)
        self.add_css_class("lumaui-override-dot")
        self._tooltip = tooltip or "Its own value"
        self.set_active(active)

    def set_active(self, active: bool) -> None:
        self.active = bool(active)
        lumaui.set_css_class(self, "active", self.active)
        self.set_tooltip_text(self._tooltip if self.active else None)
        self.update_property([Gtk.AccessibleProperty.LABEL], [self._tooltip if self.active else ""])
        self.set_opacity(1.0 if self.active else 0.0)
