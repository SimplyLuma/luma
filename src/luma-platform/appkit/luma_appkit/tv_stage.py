# SPDX-License-Identifier: Apache-2.0
"""LumaUI on TV: the stage every TV presentation is built on.

A TV presentation is a view inside the same application (docs/developer-
platform/tv-mode.md). It is laid out in TV units, one pixel of a 1920x1080
layout scaled to the window (`lumaui.tv_unit()`), and pinned to the screen's
edges or centre, so it fills any size or aspect:

    stage = TvStage()
    stage.place(TvTopBar(), left=96, right=96, top=56, height=74)
    stage.place(hero, left=96, top=190)
    stage.place(days, left=96, right=96, bottom=56)

Style sheets for TV parts are written in TV units with a `tv` suffix
(`font-size: 26tv; border-radius: 22tv;`); `add_tv_style()` turns them into
pixels for the current unit and rewrites them when the unit changes. The kit's
own sheet gives every TV app the shared scale and materials:

    type        .tv-h1 52/750 · .tv-h2 30/700 · .tv-body 26/500 · .tv-meta 20/600
                .tv-caption 22/650 · .tv-caption-meta 19
    materials   .tv-well recessed · .tv-chip chosen · .tv-key the focus · .tv-bar floating
    parts       .tv-btn (66 tall), .tv-sheet, .tv-field, .tv-row, .tv-action, .tv-ring

Materials are translucent over the room. Reduce Transparency and High
Contrast make them opaque, as on the shell's TV home. All values come from
the `tv` block of config/shared/design-tokens.json (`lumaui_tokens.TV`).
"""
from __future__ import annotations

import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
from gi.repository import Adw, Gdk, Gio, GObject, Graphene, Gsk, Gtk  # noqa: E402

from . import lumaui_tokens as tokens  # noqa: E402

__all__ = ["TvStage", "TvLift", "TvRaster", "add_tv_style", "tv_css", "tv_material", "tv_fade", "TV"]

TV = tokens.TV
_LENGTH = re.compile(r"(?<![\w.#-])(-?\d+(?:\.\d+)?)tv\b")
# The materials that are surfaces: Reduce Transparency makes these opaque.
_SURFACES = frozenset({"well", "chip", "bar", "sheet", "toast"})


def tv_css(template: str, unit: float) -> str:
    """A TV-unit sheet in pixels: every `<number>tv` becomes `<number * unit>px`."""
    return _LENGTH.sub(lambda m: f"{float(m.group(1)) * unit:.2f}px", template)


def tv_material(name: str, *, opaque: bool = False) -> str:
    """A TV material colour as CSS; surfaces turn opaque under Reduce Transparency."""
    value = TV["materials"][name]
    if opaque and name in _SURFACES:
        return re.sub(r"rgba\(([^,]+),([^,]+),([^,]+),[^)]+\)", r"rgba(\1,\2,\3, 1)", value)
    return value


def _sheet(opaque: bool) -> str:
    m = lambda name: tv_material(name, opaque=opaque)  # noqa: E731
    ty, bar, btn, sheet, qp = TV["type"], TV["bar"], TV["button"], TV["sheet"], TV["quick_panel"]
    focus = TV["focus"]
    return f"""
.lumaui-tv-stage {{ font-family: Figtree, sans-serif; color: {m('ink')}; }}
.lumaui-tv-stage label {{ color: inherit; }}
.lumaui-tv-stage *:focus-visible {{ outline: none; }}
.tv-h1 {{ font-size: {ty['h1']['size']}tv; font-weight: {ty['h1']['weight']}; letter-spacing: -1.56tv; }}
.tv-h2 {{ font-size: {ty['h2']['size']}tv; font-weight: {ty['h2']['weight']}; letter-spacing: -0.3tv; }}
.tv-body {{ font-size: {ty['body']['size']}tv; font-weight: {ty['body']['weight']}; color: {m('ink_2')}; }}
.tv-meta {{ font-size: {ty['meta']['size']}tv; font-weight: {ty['meta']['weight']}; color: {m('ink_3')}; }}
.tv-caption {{ font-size: {ty['caption_title']['size']}tv; font-weight: {ty['caption_title']['weight']}; }}
.tv-caption-meta {{ font-size: {ty['caption_meta']['size']}tv; font-weight: {ty['caption_meta']['weight']}; color: {m('ink_3')}; }}
.tv-ink-2 {{ color: {m('ink_2')}; }}
.tv-ink-3 {{ color: {m('ink_3')}; }}
.tv-num {{ font-feature-settings: "tnum"; }}

.tv-well {{ background-color: {m('well')};
  box-shadow: inset 0 0 0 1.5tv {m('well_rim')}, inset 0 2tv 4tv {m('well_shade')}, 0 1.5tv 0 {m('well_glint')}; }}
.tv-chip {{ background-color: {m('chip')}; color: {m('ink')};
  box-shadow: inset 0 1.5tv 0 {m('chip_glint')}, 0 0 0 1.5tv {m('chip_rim')}, 0 2tv 5tv {m('chip_drop')}; }}
.tv-key {{ background-image: linear-gradient(180deg, {m('key_top')}, {m('key_bottom')}); color: {m('key_ink')};
  box-shadow: inset 0 1.5tv 0 {m('key_glint')}, 0 0 0 1.5tv {m('key_rim')}, 0 3tv 10tv {m('key_drop')}; }}
.tv-bar {{ background-color: {m('bar')};
  box-shadow: inset 0 1.5tv 0 {m('bar_glint')}, 0 0 0 1.5tv {m('bar_rim')}, 0 18tv 44tv -10tv {m('bar_drop')}; }}
.tv-key .tv-ink-2, .tv-key .tv-ink-3, .tv-key .tv-meta, .tv-key .tv-caption-meta {{ color: alpha({m('key_ink')}, .6); }}

/* the shared top bar (tv_parts.TvTopBar): 96 in, 56 down, 74 tall */
.tv-topbar {{ min-height: {bar['height']}tv; }}
.tv-track {{ min-height: {bar['height'] - 2 * bar['track_padding']}tv; padding: {bar['track_padding']}tv;
  border-radius: {bar['track_radius']}tv; }}
.tv-track > .tv-tab {{ margin-right: {bar['tab_gap']}tv; }}
.tv-track > .tv-tab:last-child {{ margin-right: 0; }}
.tv-tab {{ min-height: {bar['tab_height']}tv; padding: 0 {bar['tab_padding_x']}tv; border-radius: {bar['tab_radius']}tv;
  color: {m('ink_2')}; font-size: {bar['tab_size']}tv; font-weight: {bar['tab_weight']}; }}
.tv-tab > box {{ border-spacing: {bar['tab_icon_gap']}tv; }}
.tv-tab image {{ -gtk-icon-size: {bar['tab_icon']}tv; }}
.tv-tab.tv-chip {{ color: {m('ink')}; }}
.tv-tab.tv-key {{ color: {m('key_ink')}; }}
.tv-clock {{ font-size: {bar['clock_size']}tv; font-weight: {bar['clock_weight']}; color: {m('ink')};
  font-feature-settings: "tnum"; }}
/* the bar's cluster (the clock): a key with the TV home's padding when focused; the clock stays put */
.tv-cluster {{ min-height: {qp['cluster_height']}tv; padding: 0 {qp['cluster_padding_x']}tv;
  margin-right: -{qp['cluster_padding_x']}tv; border-radius: {qp['cluster_radius']}tv; }}
.tv-cluster.tv-focus {{ background-image: linear-gradient(180deg, {m('key_top')}, {m('key_bottom')});
  box-shadow: inset 0 1.5tv 0 {m('key_glint')}, 0 0 0 1.5tv {m('key_rim')}, 0 3tv 10tv {m('key_drop')}; }}
.tv-cluster.tv-focus .tv-clock {{ color: {m('key_ink')}; }}

/* buttons: 66 tall, radius 22, 25/650, icon 26 */
.tv-btn {{ min-height: {btn['height']}tv; padding: 0 {btn['padding_x']}tv; border-radius: {btn['radius']}tv;
  font-size: {btn['size']}tv; font-weight: {btn['weight']}; }}
.tv-btn > box {{ border-spacing: 12tv; }}
.tv-btn image {{ -gtk-icon-size: {btn['icon']}tv; }}
.tv-btn.tv-accent {{ background-image: linear-gradient(180deg, {m('accent')}, {m('accent_low')}); color: {m('accent_ink')}; }}
.tv-btn.tv-accent.tv-key {{ background-image: linear-gradient(180deg, {m('accent_focus')}, {m('accent_focus_low')}); color: {m('accent_ink')}; }}
.tv-btn.tv-danger {{ color: {m('danger')}; }}
.tv-btn.tv-danger.tv-key {{ color: {m('danger_on_key')}; }}

/* a focus ring around a recessed thing (a track, a field) */
.tv-ring.tv-focus {{ outline: {focus['ring']}tv solid {m('key_top')}; outline-offset: {focus['ring_offset']}tv; }}

/* sheets: a floating panel over a dimmed screen */
.tv-scrim {{ background-color: {m('scrim')}; }}
.tv-sheet {{ background-color: {m('sheet')}; border-radius: {sheet['radius']}tv; padding: {sheet['padding']}tv;
  box-shadow: inset 0 1.5tv 0 {m('bar_glint')}, 0 0 0 1.5tv {m('bar_rim')}, 0 18tv 44tv -10tv {m('bar_drop')}; }}
.tv-sheet-title {{ font-size: {sheet['title_size']}tv; font-weight: {sheet['title_weight']}; letter-spacing: -0.88tv; }}
.tv-field {{ min-height: {sheet['field_height']}tv; padding: 0 26tv; border-radius: {sheet['field_radius']}tv;
  font-size: {sheet['field_size']}tv; color: {m('ink_3')}; border-spacing: 16tv; }}
.tv-field image {{ -gtk-icon-size: 28tv; }}
.tv-field text {{ background: none; color: {m('ink')}; caret-color: {m('accent')}; font-size: {sheet['field_size']}tv; }}
.tv-field text placeholder {{ color: {m('ink_3')}; }}
.tv-field:focus-within {{ box-shadow: inset 0 0 0 1.5tv {m('well_rim')}, inset 0 2tv 4tv {m('well_shade')},
  0 0 0 {focus['ring']}tv {m('key_top')}; }}
.tv-row {{ min-height: {sheet['row_height']}tv; padding: 0 22tv; border-radius: {sheet['row_radius']}tv;
  font-size: {sheet['row_size']}tv; }}
.tv-switch-row {{ border-spacing: 20tv; }}
.tv-row-title {{ font-size: {sheet['row_size']}tv; font-weight: 600; color: inherit; }}
.tv-switch {{ min-width: 64tv; min-height: 34tv; padding: 4tv; border-radius: 21tv; background-color: {m('well')};
  box-shadow: inset 0 0 0 1.5tv {m('well_rim')}, inset 0 2tv 4tv {m('well_shade')}, 0 1.5tv 0 {m('well_glint')};
  transition: background-color 200ms; }}
.tv-switch-knob {{ min-width: 34tv; min-height: 34tv; border-radius: 17tv; background-image: linear-gradient(#fff, #e6e7ea);
  box-shadow: 0 2tv 5tv rgba(0, 0, 0, 0.35); transition: margin-left 220ms cubic-bezier(.3, 1.4, .5, 1); margin-left: 0; }}
.tv-switch.on {{ background-color: {m('accent')}; box-shadow: inset 0 1.5tv 3tv rgba(0, 0, 0, 0.25); }}
.tv-switch.on .tv-switch-knob {{ margin-left: 30tv; }}
.tv-key .tv-switch:not(.on) {{ background-color: rgba(0, 0, 0, 0.14); }}
.tv-action {{ min-height: {sheet['action_height']}tv; border-radius: {sheet['action_radius']}tv;
  font-size: {sheet['action_size']}tv; font-weight: 650; }}
"""


class _Style:
    """One provider per display, holding the kit sheet and every app sheet at the current unit."""

    def __init__(self, display: Gdk.Display) -> None:
        self.display = display
        self.unit = 1.0
        self.provider = Gtk.CssProvider()
        self.templates: list[Callable[[bool], str] | str] = [_sheet]
        self.opaque = False
        self._settings = []
        for schema, key in (("org.project_luma.shell-state", "reduce-transparency"),
                            ("org.gnome.desktop.a11y.interface", "high-contrast")):
            settings = _optional_settings(schema, key)
            if settings is not None:
                settings.connect(f"changed::{key}", lambda *_a: self.refresh())
                self._settings.append((settings, key))
        self.opaque = self._opaque()
        Gtk.StyleContext.add_provider_for_display(display, self.provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
        self.render()

    def _opaque(self) -> bool:
        return any(settings.get_boolean(key) for settings, key in self._settings)

    def refresh(self) -> None:
        self.opaque = self._opaque()
        self.render()

    def render(self) -> None:
        text = "\n".join(t(self.opaque) if callable(t) else t for t in self.templates)
        self.provider.load_from_string(tv_css(text, self.unit))

    def set_unit(self, unit: float) -> None:
        if abs(unit - self.unit) > 1e-4:
            self.unit = unit
            self.render()


_styles: dict[Gdk.Display, _Style] = {}


def _optional_settings(schema_id: str, key: str) -> Gio.Settings | None:
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(schema_id, True) if source else None
    return Gio.Settings.new(schema_id) if schema is not None and schema.has_key(key) else None


def _style(display: Gdk.Display | None = None) -> _Style | None:
    display = display or Gdk.Display.get_default()
    if display is None:
        return None
    if display not in _styles:
        _styles[display] = _Style(display)
    return _styles[display]


def add_tv_style(template: str | Callable[[bool], str]) -> None:
    """Add an application's TV sheet, written in `tv` units (`font-size: 26tv`).

    A callable receives `opaque` (Reduce Transparency or High Contrast) and
    returns the sheet, for a part whose colours follow it. Sheets stay in step
    with the stage's unit; adding the same template twice adds it once.
    """
    style = _style()
    if style is not None and template not in style.templates:
        style.templates.append(template)
        style.render()


def tv_fade(widget: Gtk.Widget, to: float, ms: int, *, done: Callable[[], None] | None = None) -> Adw.Animation:
    """Fade a widget's opacity (the idle cross-fade, sheets); instant under reduced motion."""
    from .lumaui import reduced_motion
    previous = getattr(widget, "_tv_fade", None)
    if previous is not None:
        previous.pause()
    target = Adw.PropertyAnimationTarget.new(widget, "opacity")
    animation = Adw.TimedAnimation.new(widget, widget.get_opacity(), to, 0 if reduced_motion() else ms, target)
    animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
    if done is not None:
        animation.connect("done", lambda *_a: done())
    widget._tv_fade = animation
    animation.play()
    return animation


@dataclass
class _Pin:
    left: float | None = None
    top: float | None = None
    right: float | None = None
    bottom: float | None = None
    width: float | None = None
    height: float | None = None
    center_x: bool = False
    center_y: bool = False
    fill: bool = False


class TvStage(Gtk.Widget):
    """The root of a TV presentation: one unit, children pinned in TV units.

    `place(child, left=, top=, right=, bottom=, width=, height=)` pins a child
    to the edges (both left and right stretches it; neither, with
    `center_x=True`, centres it); `fill=True` covers the whole stage (a
    background, a scrim). Children paint in the order they were placed. The
    `unit` property is the current TV unit; parts that draw themselves follow
    `notify::unit`.

    `docked` names what sits at the bottom edge ("", "action-bar" or
    "keyboard"), so the toast and the action bar rise above it.
    """

    __gtype_name__ = "LumaUITvStage"

    unit = GObject.Property(type=float, default=1.0)
    docked = GObject.Property(type=str, default="")

    def __init__(self) -> None:
        super().__init__(hexpand=True, vexpand=True, overflow=Gtk.Overflow.HIDDEN)
        self.add_css_class("lumaui-tv-stage")
        self._pins: dict[Gtk.Widget, _Pin] = {}
        _style()
        # The system's TV keyboard docks here while a field types (tv_keyboard).
        from .tv_keyboard import dock_stage
        self._keyboard_stop = dock_stage(self)

    def place(self, child: Gtk.Widget, **pin) -> Gtk.Widget:
        """Pin `child` in TV units; placing a placed child again moves it."""
        self._pins[child] = _Pin(**pin)
        if child.get_parent() is not self:
            child.set_parent(self)
        self.queue_allocate()
        return child

    def raise_child(self, child: Gtk.Widget) -> None:
        """Paint `child` above every other (a sheet, the keyboard, a toast)."""
        if child.get_parent() is self:
            child.insert_before(self, None)

    def remove(self, child: Gtk.Widget) -> None:
        if child.get_parent() is self:
            self._pins.pop(child, None)
            child.unparent()

    def px(self, value: float) -> float:
        """TV units to pixels at the current unit."""
        return value * self.unit

    def do_dispose(self) -> None:
        if self._keyboard_stop is not None:
            self._keyboard_stop()
            self._keyboard_stop = None
        for child in list(self._pins):
            child.unparent()
        self._pins.clear()

    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        from .lumaui import tv_unit
        u = tv_unit(width, height) or 1.0
        if abs(u - self.unit) > 1e-4:
            self.unit = u
            style = _style(self.get_display())
            if style is not None:
                style.set_unit(u)
        for child, pin in list(self._pins.items()):
            if not child.should_layout():
                continue
            x, y, w, h = self._rect(child, pin, width, height, u)
            transform = Gsk.Transform.new().translate(Graphene.Point().init(round(x), round(y)))
            child.allocate(max(0, round(w)), max(0, round(h)), -1, transform)

    @staticmethod
    def _rect(child, pin: _Pin, width: int, height: int, u: float):
        if pin.fill:
            return 0.0, 0.0, float(width), float(height)
        if pin.left is not None and pin.right is not None:
            w = width - (pin.left + pin.right) * u
        elif pin.width is not None:
            w = pin.width * u
        else:
            w = child.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
            w = min(w, width)
        w = max(w, child.measure(Gtk.Orientation.HORIZONTAL, -1)[0]) if pin.left is None or pin.right is None else w
        if pin.top is not None and pin.bottom is not None:
            h = height - (pin.top + pin.bottom) * u
        elif pin.height is not None:
            h = pin.height * u
        else:
            h = child.measure(Gtk.Orientation.VERTICAL, max(0, round(w)))[1]
        if pin.left is not None:
            x = pin.left * u
        elif pin.right is not None:
            x = width - pin.right * u - w
        else:
            x = (width - w) / 2
        if pin.top is not None:
            y = pin.top * u
        elif pin.bottom is not None:
            y = height - pin.bottom * u - h
        else:
            y = (height - h) / 2
        return x, y, w, h


class TvLift(Gtk.Widget):
    """Lifts its child (scale about the centre) when it has the remote's focus.

    Cards lift 1.06 (`TV["tile"]["focus_scale"]`); covers use their own 1.08. The child
    is drawn scaled, so nothing around it moves. `set_lifted(True)` animates.
    """

    __gtype_name__ = "LumaUITvLift"

    def __init__(self, child: Gtk.Widget, *, scale: float = 1.06) -> None:
        super().__init__(overflow=Gtk.Overflow.VISIBLE)
        self._child = child
        self._target = scale
        self._scale = 1.0
        self._animation = None
        child.set_parent(self)

    @property
    def child(self) -> Gtk.Widget:
        return self._child

    def set_lifted(self, lifted: bool) -> None:
        from .lumaui import reduced_motion
        to = self._target if lifted else 1.0
        if self._animation is not None:
            self._animation.pause()
        target = Adw.CallbackAnimationTarget.new(self._set_scale)
        ms = 0 if reduced_motion() else TV["motion"]["lift_ms"]
        self._animation = Adw.TimedAnimation.new(self, self._scale, to, ms, target)
        self._animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._animation.play()

    def _set_scale(self, value: float) -> None:
        self._scale = value
        self.queue_draw()

    def do_dispose(self) -> None:
        if self._child is not None:
            self._child.unparent()
            self._child = None

    def do_measure(self, orientation, for_size):
        return self._child.measure(orientation, for_size) if self._child else (0, 0, -1, -1)

    def do_size_allocate(self, width, height, baseline):
        if self._child is not None:
            self._child.allocate(width, height, baseline, None)

    def do_snapshot(self, snapshot) -> None:
        if self._child is None:
            return
        if abs(self._scale - 1.0) < 1e-3:
            self.snapshot_child(self._child, snapshot)
            return
        w, h = self.get_width() / 2, self.get_height() / 2
        snapshot.save()
        snapshot.translate(Graphene.Point().init(w, h))
        snapshot.scale(self._scale, self._scale)
        snapshot.translate(Graphene.Point().init(-w, -h))
        self.snapshot_child(self._child, snapshot)
        snapshot.restore()


class TvRaster:
    """Renders snapshot content once into a texture and reuses it while its key holds.

    GSK fills gradients and blurred shadows per pixel on every frame. On a
    tile-based GPU at 4K (the Raspberry Pi 5's V3D) one full-screen gradient
    costs tens of milliseconds, a full-screen texture a few. Content that
    changes rarely, a sky's light or a band's well, is rendered once,
    optionally at a reduced scale (smooth gradients lose nothing), and drawn
    as a texture:

        tex = raster.get(widget, key, w, h, lambda s: paint(s, w, h), scale=.25)
        if tex is None:
            paint(snapshot, w, h)       # not realized yet: draw directly
        else:
            snapshot.append_texture(tex, rect)

    Draw it with `append_texture` (linear when scaled up), not
    `append_scaled_texture`: GSK takes an opacity over a texture-scale node
    through an offscreen (with Vulkan a new full-size image every frame),
    over a texture node it does not. Pass `opaque=True` for content that
    covers its rectangle, so GSK skips what lies beneath. Keep `key` to the
    inputs of the drawing. Holds the `size` most recent
    textures; `clear()` them when the widget leaves its window.
    """

    def __init__(self, size: int = 4) -> None:
        self._size = size
        self._textures: OrderedDict = OrderedDict()

    def get(self, widget: Gtk.Widget, key, width: float, height: float,
            draw: Callable[[Gtk.Snapshot], None], *, scale: float = 1.0,
            opaque: bool = False) -> Gdk.Texture | None:
        texture = self._textures.get(key)
        if texture is not None:
            self._textures.move_to_end(key)
            return texture
        native = widget.get_native()
        renderer = native.get_renderer() if native is not None else None
        if renderer is None or width <= 0 or height <= 0:
            return None
        tw, th = max(1, math.ceil(width * scale)), max(1, math.ceil(height * scale))
        snapshot = Gtk.Snapshot()
        snapshot.scale(tw / width, th / height)
        draw(snapshot)
        node = snapshot.to_node()
        if node is None:
            return None
        texture = renderer.render_texture(node, Graphene.Rect().init(0, 0, tw, th))
        if opaque:
            # An opaque format lets GSK skip whatever lies under the texture
            # (a window's background gradient: ~100 ms a 4K frame on the Pi 5).
            downloader = Gdk.TextureDownloader.new(texture)
            downloader.set_format(Gdk.MemoryFormat.R8G8B8X8)
            data, stride = downloader.download_bytes()
            texture = Gdk.MemoryTexture.new(tw, th, Gdk.MemoryFormat.R8G8B8X8, data, stride)
        self._textures[key] = texture
        while len(self._textures) > self._size:
            self._textures.popitem(last=False)
        return texture

    def clear(self) -> None:
        self._textures.clear()
