# SPDX-License-Identifier: Apache-2.0
"""LumaUI on TV: the one toast every TV app uses.

    TvToast.show(stage, "Added Lisbon", lead="map-pin")
    TvToast.show(stage, "Bengals", kicker="TOUCHDOWN", value="24–17", tint="#fb4f14", ms=5200)

Bottom centre on the 56 safe line, toast material, sliding up; it rises above
the action bar or the keyboard while either is docked (`TvStage.docked`). A
new toast replaces the one showing. Parts, all optional except the title:

    lead    an icon name or a widget (a mark, an app tile)
    kicker  small caps above the title (SAVED, UPDATED)
    title   what happened, in a few words
    value   a trailing figure (a score, a size)
    sub     a quiet line under the title; rarely: a toast says one thing
    tint    a colour the toast takes as soft light from its leading edge
    ms      how long it stays (TV motion toast_ms; toast_long_ms for a moment worth reading)
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

from . import icons  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402
from .tv_stage import TvStage, add_tv_style, tv_material  # noqa: E402

__all__ = ["TvToast"]

TV = tokens.TV


def _sheet(opaque: bool) -> str:
    t = TV["toast"]
    m = lambda name: tv_material(name, opaque=opaque)  # noqa: E731
    return f"""
.tv-toast {{ min-height: {t['min_height']}tv; border-radius: {t['radius']}tv; background-color: {m('toast')};
  padding: {t['padding_y']}tv {t['padding_end']}tv {t['padding_y']}tv {t['padding_start']}tv;
  box-shadow: inset 0 1.5tv 0 {m('bar_glint')}, 0 0 0 1.5tv {m('bar_rim')}, 0 18tv 44tv -10tv {m('bar_drop')}; }}
.tv-toast.tv-plain {{ padding-left: {t['padding_end']}tv; }}
.tv-toast > box {{ border-spacing: {t['gap']}tv; }}
.tv-toast-lead {{ min-width: {t['lead']}tv; min-height: {t['lead']}tv; border-radius: {t['lead_radius']}tv;
  background-color: rgba(255,255,255,.08); }}
.tv-toast-lead image {{ -gtk-icon-size: {t['lead_icon']}tv; }}
.tv-toast-kicker {{ font-size: {t['kicker_size']}tv; font-weight: {t['kicker_weight']}; letter-spacing: 1.7tv; color: {m('ink_2')}; }}
.tv-toast-title {{ font-size: {t['title_size']}tv; font-weight: {t['title_weight']}; }}
.tv-toast-value {{ font-size: {t['value_size']}tv; font-weight: {t['value_weight']}; letter-spacing: -0.8tv;
  font-feature-settings: "tnum"; margin-left: 14tv; }}
.tv-toast-sub {{ font-size: {t['sub_size']}tv; font-weight: {t['sub_weight']}; color: {m('ink_3')}; }}
"""


class TvToast(Gtk.Widget):
    """The TV toast; use `TvToast.show(stage, title, ...)`."""

    __gtype_name__ = "LumaUITvToast"

    def __init__(self) -> None:
        super().__init__(can_target=False, accessible_role=Gtk.AccessibleRole.STATUS)
        add_tv_style(_sheet)
        # The frame draws the toast material; the tint glows on it, under the words.
        self.box = Gtk.Overlay()
        self.box.add_css_class("tv-toast")
        self._glow = _Glow()
        self.box.set_child(self._glow)
        self.content = Gtk.Box()
        self.box.add_overlay(self.content)
        self.box.set_measure_overlay(self.content, True)
        self.box.set_parent(self)
        self._rise = 0.0
        self._animation = None
        self._hide_source = 0
        self._stage: TvStage | None = None

    @classmethod
    def show(cls, stage: TvStage, title: str, *, lead: str | Gtk.Widget | None = None, kicker: str = "",
             value: str = "", sub: str = "", tint: str | None = None, ms: int | None = None) -> "TvToast":
        toast = getattr(stage, "_tv_toast", None)
        if toast is None:
            toast = cls()
            toast._stage = stage
            stage._tv_toast = toast
            stage.connect("notify::docked", lambda *_a: toast._place())
        toast._fill(title, lead, kicker, value, sub, tint)
        toast._place()
        stage.raise_child(toast)
        toast._rise = 0.0
        toast._animate(1.0, TV["motion"]["toast_in_ms"])
        if toast._hide_source:
            GLib.source_remove(toast._hide_source)
        toast._hide_source = GLib.timeout_add(ms or TV["motion"]["toast_ms"], toast._hide)
        toast.update_property([Gtk.AccessibleProperty.LABEL], [" ".join(p for p in (kicker, title, value, sub) if p)])
        if hasattr(toast, "announce"):
            toast.announce(title, Gtk.AccessibleAnnouncementPriority.MEDIUM)
        return toast

    def hide(self) -> None:
        self._hide()

    def _hide(self) -> bool:
        self._hide_source = 0
        self._animate(0.0, 300)
        return False

    def _place(self) -> None:
        t = TV["toast"]
        docked = self._stage.docked if self._stage else ""
        bottom = t["above_keyboard"] if docked == "keyboard" else t["above_action_bar"] if docked == "action-bar" else t["bottom"]
        self._stage.place(self, bottom=bottom, center_x=True)

    def _fill(self, title, lead, kicker, value, sub, tint) -> None:
        while child := self.content.get_first_child():
            self.content.remove(child)
        self.box.remove_css_class("tv-plain")
        if lead is not None:
            holder = Gtk.Box(valign=Gtk.Align.CENTER)
            holder.add_css_class("tv-toast-lead")
            inner = icons.image(lead) if isinstance(lead, str) else lead
            inner.set_halign(Gtk.Align.CENTER)
            inner.set_hexpand(True)
            holder.append(inner)
            self.content.append(holder)
        else:
            self.box.add_css_class("tv-plain")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        for content, css in ((kicker, "tv-toast-kicker"), (title, "tv-toast-title"), (sub, "tv-toast-sub")):
            if content:
                label = Gtk.Label(label=content, xalign=0)
                label.add_css_class(css)
                text.append(label)
        self.content.append(text)
        if value:
            figure = Gtk.Label(label=value, valign=Gtk.Align.CENTER)
            figure.add_css_class("tv-toast-value")
            self.content.append(figure)
        rgba = Gdk.RGBA()
        self._glow.set_tint(rgba if tint and rgba.parse(tint) else None,
                            TV["toast"]["radius"] * (self._stage.unit if self._stage else 1.0))

    def _animate(self, to: float, ms: int) -> None:
        from .lumaui import reduced_motion
        if self._animation is not None:
            self._animation.pause()
        target = Adw.CallbackAnimationTarget.new(self._set_rise)
        self._animation = Adw.TimedAnimation.new(self, self._rise, to, 0 if reduced_motion() else ms, target)
        self._animation.set_easing(Adw.Easing.EASE_OUT_BACK if to > 0 else Adw.Easing.EASE_IN_CUBIC)
        self._animation.play()

    def _set_rise(self, value: float) -> None:
        self._rise = value
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        return self.box.measure(orientation, for_size)

    def do_size_allocate(self, width, height, baseline):
        self.box.allocate(width, height, baseline, None)

    def do_dispose(self) -> None:
        if self._hide_source:
            GLib.source_remove(self._hide_source)
            self._hide_source = 0
        if self.box is not None:
            self.box.unparent()
            self.box = None

    def do_snapshot(self, snapshot) -> None:
        if self._rise <= 0.001 or self.box is None:
            return
        unit = self._stage.unit if self._stage else 1.0
        snapshot.save()
        snapshot.push_opacity(max(0.0, min(1.0, self._rise)))
        snapshot.translate(Graphene.Point().init(0, (1 - self._rise) * TV["toast"]["rise"] * unit))
        self.snapshot_child(self.box, snapshot)
        snapshot.pop()
        snapshot.restore()


class _Glow(Gtk.Widget):
    """A tinted toast's soft light from its leading edge (radial, 55% at the edge to clear at 75%)."""

    def __init__(self) -> None:
        super().__init__(hexpand=True, vexpand=True, can_target=False)
        self._tint: Gdk.RGBA | None = None
        self._radius = 0.0

    def set_tint(self, tint: Gdk.RGBA | None, radius: float) -> None:
        self._tint, self._radius = tint, radius
        self.queue_draw()

    def do_snapshot(self, snapshot) -> None:
        if self._tint is None:
            return
        width, height = self.get_width(), self.get_height()
        clip = Gsk.RoundedRect()
        clip.init_from_rect(Graphene.Rect().init(0, 0, width, height), self._radius)
        snapshot.push_rounded_clip(clip)
        glow = self._tint.copy()
        glow.alpha = 0.55
        clear = self._tint.copy()
        clear.alpha = 0.0
        first, last = Gsk.ColorStop(), Gsk.ColorStop()
        first.offset, first.color = 0.0, glow
        last.offset, last.color = 0.75, clear
        snapshot.append_radial_gradient(Graphene.Rect().init(0, 0, width, height),
                                        Graphene.Point().init(0, height / 2), width * 0.7, height * 1.4,
                                        0.0, 1.0, [first, last])
        snapshot.pop()
