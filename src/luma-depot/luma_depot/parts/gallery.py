# SPDX-License-Identifier: Apache-2.0
"""The gallery (8.3): the chosen picture across the reading column, thumbnails under it, the lightbox."""
from __future__ import annotations

from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from luma_appkit import icons, lumaui

from ..pictures.frames import Picture

__all__ = ["Gallery", "Lightbox"]


class Gallery(Gtk.Box):
    def __init__(self, pictures: Sequence[Picture], *, chosen: int = 0, compact: bool = False,
                 on_choose: Callable[[int], None] | None = None, on_open: Callable[[int], None] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("dq-gallery")
        self.pictures, self.chosen, self.compact = list(pictures), chosen, compact
        self.on_choose, self.on_open = on_choose, on_open
        self.big = Gtk.Button()
        self.big.add_css_class("dq-galv")
        self.big.update_property([Gtk.AccessibleProperty.LABEL], ["Open the picture larger"])
        self.big.connect("clicked", lambda _b: on_open(self.chosen) if on_open else None)
        overlay = Gtk.Overlay()
        self.slot = Gtk.Box()
        overlay.set_child(self.slot)
        expand = icons.image("maximize-2")
        expand.add_css_class("dq-picx")
        expand.set_halign(Gtk.Align.END)
        expand.set_valign(Gtk.Align.START)
        overlay.add_overlay(expand)
        self.big.set_child(overlay)
        self.append(self.big)
        self.thumbs: list[Gtk.Button] = []
        if len(self.pictures) > 1:
            row = Gtk.Box()
            row.add_css_class("dq-thumbs")
            for index, picture in enumerate(self.pictures):
                thumb = Gtk.Button()
                thumb.add_css_class("dq-th2")
                frame = picture.make(10)
                width, height = (76, 43) if compact else (96, 54)
                frame.set_size_request(width, height)
                if compact:
                    frame.set_aspect(4, 3)
                thumb.set_child(frame)
                thumb.update_property([Gtk.AccessibleProperty.LABEL], [f"Picture {index + 1}: {picture.label}"])
                thumb.connect("clicked", lambda _b, i=index: self.choose(i))
                row.append(thumb)
                self.thumbs.append(thumb)
            self.append(row)
        self.choose(chosen, notify=False)

    def choose(self, index: int, *, notify: bool = True) -> None:
        self.chosen = index
        while (child := self.slot.get_first_child()) is not None:
            self.slot.remove(child)
        frame = self.pictures[index].make(18)
        frame.set_hexpand(True)
        if self.compact:
            frame.set_aspect(4, 3)
        self.slot.append(frame)
        for i, thumb in enumerate(self.thumbs):
            lumaui.set_css_class(thumb, "on", i == index)
        if notify and self.on_choose is not None:
            self.on_choose(index)


class Lightbox(Gtk.Box):
    """The picture large over a darkened window; a small frosted bar: ‹ · "1 of 3" · › · ×.
    Arrow keys page, Escape or a click outside closes (8.3)."""

    def __init__(self, pictures: Sequence[Picture], index: int, on_close: Callable[[], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True, focusable=True,
                         accessible_role=Gtk.AccessibleRole.DIALOG)
        self.add_css_class("dq-lightbox")
        self.pictures, self.index, self.on_close = list(pictures), index, on_close
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Pictures"])
        self.stage = Gtk.Box(vexpand=True, hexpand=True, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.stage.add_css_class("dq-lb-stage")
        self.append(self.stage)
        bar = Gtk.Box(halign=Gtk.Align.CENTER)
        bar.add_css_class("dq-lb-bar")
        self.prev = self._button("chevron-left", "Previous picture", lambda: self.page(-1))
        self.count = Gtk.Label()
        self.count.add_css_class("dq-lb-count")
        self.next = self._button("chevron-right", "Next picture", lambda: self.page(1))
        close = self._button("x", "Close", self.close)
        for widget in (self.prev, self.count, self.next, close):
            bar.append(widget)
        self.append(bar)
        click = Gtk.GestureClick()
        click.connect("released", self._clicked)
        self.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.show_index(index)

    def _button(self, icon: str, name: str, action: Callable[[], None]) -> Gtk.Button:
        button = Gtk.Button()
        button.add_css_class("dq-lb-btn")
        button.set_child(icons.image(icon))
        button.update_property([Gtk.AccessibleProperty.LABEL], [name])
        button.set_tooltip_text(name)
        button.connect("clicked", lambda _b: action())
        return button

    def show_index(self, index: int) -> None:
        count = len(self.pictures)
        self.index = index % count
        while (child := self.stage.get_first_child()) is not None:
            self.stage.remove(child)
        frame = self.pictures[self.index].make(18)
        frame.set_size_request(880, -1)
        self.stage.append(frame)
        self.count.set_label(f"{self.index + 1} of {count}")
        self.prev.set_sensitive(count > 1)
        self.next.set_sensitive(count > 1)

    def page(self, step: int) -> None:
        self.show_index(self.index + step)

    def close(self) -> None:
        self.on_close()

    def _clicked(self, gesture, _n, x, y) -> None:
        picked = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        if picked is self or picked is None:
            self.close()

    def _key(self, _controller, keyval, _code, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            self.page(-1 if keyval == Gdk.KEY_Left else 1)
            return True
        return False
