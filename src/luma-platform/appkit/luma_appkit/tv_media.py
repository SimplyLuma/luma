# SPDX-License-Identifier: Apache-2.0
"""LumaUI on TV: the media parts (covers, transport, the now-playing pill, the room).

Every TV app that shows records, films, games or pictures builds on these, so a
cover, a play button or a backdrop is the same size and behaves the same way in
every app. Sizes are TV units (`lumaui.tv_unit()`); a part follows the unit of
the `TvStage` it sits on. Values: `lumaui_tokens.TV["cover"]`, `["button"]`
and `["media"]` (config/shared/design-tokens.d/tv-media.json).

    cover = TvCover(shape="square", artwork=path, title="Album", subtitle="Artist")
    focus.add(cover, on_activate=open_album)          # lifts 1.08, ring on the art only

    play = TvTransportButton("play", main=True, label="Play")   # 108; 84 without main
    pill = TvNowPlayingPill()                          # sleeve, song, equaliser
    bar.end.prepend(pill)                              # it rides in the shared top bar
    pill.show_track("Song", "Artist", artwork, playing=True)

    room = TvRoom()                                    # the item's own art, blurred, as the room
    stage.place(room, fill=True)
    room.set_artwork(path)                             # cross-fades over 1.1 s

    shelf = TvSlide(row, orientation=Gtk.Orientation.HORIZONTAL)
    shelf.reveal(cover)                                # slides only as far as needed

Covers: square 260 (music), portrait 230 wide at 3:4 (games, film), landscape
400 wide at 16:9 (screenshots, episodes), radius 20. With the remote's focus a
cover lifts 1.08 and a 5-unit white ring hugs the art, concentric with its
corner; the caption never carries the ring. A cover without art draws a
generated sleeve from three hues (a playlist's), with its name on it.

The room is never a live blur: the art is decoded small, blurred and graded
once off the main loop (`room_pixels()`), cached, and drawn as one texture;
changing it cross-fades two textures. That keeps a 4K TV on a small GPU smooth.

Nothing here depends on a pointer or on hover.
"""
from __future__ import annotations

import hashlib
import math
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from . import lumaui_tokens as tokens  # noqa: E402

__all__ = [
    "TvCover", "TvTransportButton", "TvNowPlayingPill", "TvEqualizer", "TvProgress", "TvRoom", "TvSlide",
    "cover_size", "room_pixels", "box_blur", "grade", "hue_sleeve_pixels", "hues_for",
]

TV = tokens.TV
MEDIA = TV["media"]


# ── Pure helpers (no GTK): sizes, the room's pixels ─────────────────────────

def cover_size(shape: str = "square", width: float | None = None) -> tuple[float, float]:
    """A cover's art in TV units: (width, height) for square, portrait or landscape."""
    if shape not in ("square", "portrait", "landscape"):
        raise ValueError(f"unknown cover shape {shape!r}")
    base = TV["cover"][shape]
    aspect = {"square": 1.0, "portrait": MEDIA["cover"]["portrait_aspect"],
              "landscape": MEDIA["cover"]["landscape_aspect"]}[shape]
    w = float(width if width is not None else base)
    return w, w * aspect


def hues_for(key: str) -> tuple[int, int, int]:
    """Three stable hues for something without art (a playlist, an album without a sleeve)."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    first = digest[0] * 360 // 256
    return first, (first + 25 + digest[1] % 40) % 360, (first + 300 + digest[2] % 50) % 360


def _blur_line(values: Sequence[int], radius: int) -> bytearray:
    span = 2 * radius + 1
    padded = [values[0]] * radius + list(values) + [values[-1]] * (radius + 1)
    total = sum(padded[:span])
    out = bytearray(len(values))
    for i in range(len(values)):
        out[i] = total // span
        total += padded[i + span] - padded[i]
    return out


def box_blur(pixels: bytearray, width: int, height: int, channels: int, radius: int, passes: int = 3) -> bytearray:
    """Approximate a Gaussian blur with repeated box blurs (edges extend). Returns new pixels."""
    data = bytearray(pixels)
    if radius < 1 or width < 1 or height < 1:
        return data
    stride = width * channels
    for _ in range(passes):
        for row in range(height):
            for c in range(channels):
                line = slice(row * stride + c, (row + 1) * stride, channels)
                data[line] = _blur_line(data[line], radius)
        for column in range(width):
            for c in range(channels):
                line = slice(column * channels + c, None, stride)
                data[line] = _blur_line(data[line], radius)
    return data


def grade(pixels: bytearray, channels: int, saturate: float, brightness: float) -> bytearray:
    """CSS `saturate()` then `brightness()` on 8-bit sRGB pixels (the room's grade)."""
    s = saturate
    m = ((0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s),
         (0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s),
         (0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s))
    out = bytearray(pixels)
    for i in range(0, len(out) - channels + 1, channels):
        r, g, b = out[i], out[i + 1], out[i + 2]
        for k in range(3):
            v = (m[k][0] * r + m[k][1] * g + m[k][2] * b) * brightness
            out[i + k] = 0 if v < 0 else 255 if v > 255 else int(v + 0.5)
    return out


def room_pixels(rgb: bytes, width: int, height: int) -> bytearray:
    """The room from small RGB art: blurred, saturated and darkened as tv.media.room says."""
    room = MEDIA["room"]
    blurred = box_blur(bytearray(rgb), width, height, 3, room["blur_radius"], room["blur_passes"])
    return grade(blurred, 3, room["saturate"], room["brightness"])


def _oklch(lightness: float, chroma: float, hue: float) -> tuple[float, float, float]:
    radians = math.radians(hue)
    a, b = chroma * math.cos(radians), chroma * math.sin(radians)
    l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    linear = (4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
              -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
              -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_)

    def encode(value: float) -> float:
        value = max(0.0, min(1.0, value))
        return 1.055 * value ** (1 / 2.4) - 0.055 if value > 0.0031308 else 12.92 * value
    return tuple(encode(v) for v in linear)  # type: ignore[return-value]


def _rgba(colour: Sequence[float]) -> Gdk.RGBA:
    """An OKLCH token [L, C, h, alpha] as a Gdk.RGBA."""
    r, g, b = _oklch(colour[0], colour[1], colour[2])
    value = Gdk.RGBA()
    value.red, value.green, value.blue, value.alpha = r, g, b, colour[3] if len(colour) > 3 else 1.0
    return value


def _material(name: str) -> Gdk.RGBA:
    value = Gdk.RGBA()
    value.parse(TV["materials"][name])
    return value


def hue_sleeve_pixels(hues: tuple[int, int, int], side: int) -> bytearray:
    """A generated sleeve's colours as small RGB pixels (the room of a playlist): base, two lights."""
    base, first, third = _oklch(0.5, 0.14, hues[1]), _oklch(0.72, 0.15, hues[0]), _oklch(0.6, 0.16, hues[2])
    out = bytearray(side * side * 3)
    for y in range(side):
        for x in range(side):
            u, v = (x + 0.5) / side, (y + 0.5) / side
            colour = base
            if (u - 0.2) ** 2 + (v - 0.2) ** 2 < 0.25:
                colour = first
            elif (u - 0.9) ** 2 + (v - 0.9) ** 2 < 0.25:
                colour = third
            i = (y * side + x) * 3
            out[i:i + 3] = bytes(int(c * 255 + 0.5) for c in colour)
    return out


# ── The stage's unit ─────────────────────────────────────────────────────────

class _UnitFollower:
    """Mixin: follow the TV unit of the stage a part sits on (`TvStage.unit`)."""

    _stage = None
    _stage_handler = 0

    def _unit(self) -> float:
        stage = self._stage
        if stage is not None:
            return float(stage.get_property("unit")) or 1.0
        root = self.get_root()
        if root is not None and root.get_width() > 0:
            from .lumaui import tv_unit
            return tv_unit(root.get_width(), root.get_height()) or 1.0
        return 1.0

    def _follow_stage(self) -> None:
        node = self.get_parent()
        while node is not None and node.find_property("unit") is None:
            node = node.get_parent()
        if node is self._stage:
            return
        self._leave_stage()
        self._stage = node
        if node is not None:
            self._stage_handler = node.connect("notify::unit", lambda *_a: self._unit_changed())
        self._unit_changed()

    def _leave_stage(self) -> None:
        if self._stage is not None and self._stage_handler:
            self._stage.disconnect(self._stage_handler)
        self._stage, self._stage_handler = None, 0

    def _unit_changed(self) -> None:
        self.queue_resize()


def _animate(widget: Gtk.Widget, start: float, end: float, ms: int, step: Callable[[float], None],
             previous: Adw.Animation | None = None) -> Adw.Animation:
    from .lumaui import reduced_motion
    if previous is not None:
        previous.pause()
    animation = Adw.TimedAnimation.new(widget, start, end, 0 if reduced_motion() else ms,
                                       Adw.CallbackAnimationTarget.new(step))
    animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
    animation.play()
    return animation


def _rounded(x: float, y: float, w: float, h: float, r: float) -> Gsk.RoundedRect:
    rect = Gsk.RoundedRect()
    rect.init_from_rect(Graphene.Rect().init(x, y, w, h), max(0.0, min(r, w / 2, h / 2)))
    return rect


def _stop(offset: float, colour: Gdk.RGBA) -> Gsk.ColorStop:
    stop = Gsk.ColorStop()
    stop.offset, stop.color = offset, colour
    return stop


def _clear(colour: Gdk.RGBA) -> Gdk.RGBA:
    value = colour.copy()
    value.alpha = 0.0
    return value


def _lift_ms() -> int:
    return int(TV["motion"].get("lift_ms", 200))


# ── Covers ───────────────────────────────────────────────────────────────────

class TvCover(_UnitFollower, Gtk.Widget):
    """A cover: art (square, portrait or landscape) and an optional caption under it.

    `artwork` is a local image path; without one the cover draws a generated
    sleeve from `hues` (and `name` on it, as a playlist's). `width` (TV units)
    overrides the shape's width, for a hero or a page's large sleeve; `radius`
    the corner. `set_tv_focus(True)` lifts it 1.08 with a white ring on the art.
    """

    __gtype_name__ = "LumaUITvCover"

    def __init__(self, *, shape: str = "square", width: float | None = None, artwork: str | None = None,
                 hues: tuple[int, int, int] | None = None, name: str = "", title: str | None = None,
                 subtitle: str | None = None, radius: float | None = None, shadow: bool = True,
                 accessible: str | None = None, lift: bool = True) -> None:
        super().__init__(overflow=Gtk.Overflow.VISIBLE, halign=Gtk.Align.START, valign=Gtk.Align.START,
                         accessible_role=Gtk.AccessibleRole.BUTTON if lift else Gtk.AccessibleRole.IMG)
        self.add_css_class("tv-cover")
        self._art_w, self._art_h = cover_size(shape, width)
        self._radius = TV["cover"]["radius"] if radius is None else radius
        self._shadow = shadow
        self._lift = lift
        self._hues = hues
        self._artwork = artwork
        self._texture: Gdk.Texture | None = None
        self._request = None
        self._requested = 0
        self._scale, self._focus, self._focused = 1.0, 0.0, False
        self._animation: Adw.Animation | None = None
        self._reveal, self._reveal_animation = 1.0, None  # the picture's cross-fade over its placeholder
        self.name_label = self._label(name, "tv-cover-name", wrap=True) if name else None
        self.title_label = self._label(title, "tv-caption") if title is not None else None
        self.subtitle_label = self._label(subtitle, "tv-caption-meta") if subtitle is not None else None
        label = accessible or " · ".join(x for x in (title or name, subtitle) if x)
        if label:
            self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        _install_style()

    def _label(self, value: str, css: str, *, wrap: bool = False) -> Gtk.Label:
        label = Gtk.Label(label=value or "", xalign=0, can_target=False)
        label.add_css_class(css)
        if wrap:
            label.set_wrap(True)
            label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_lines(3)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_yalign(1)
        else:
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_single_line_mode(True)
        label.set_parent(self)
        return label

    # public
    def set_artwork(self, artwork: str | None, hues: tuple[int, int, int] | None = None) -> None:
        if artwork == self._artwork and hues == self._hues:
            return
        self._artwork, self._hues = artwork, hues
        self._cancel()
        self._texture, self._requested, self._reveal = None, 0, 1.0
        if self.get_width() > 0:
            u = self._unit()
            self._load(max(self._art_w, self._art_h) * u)
        self.queue_draw()

    def set_caption(self, title: str | None = None, subtitle: str | None = None) -> None:
        if title is not None and self.title_label is not None:
            self.title_label.set_label(title)
        if subtitle is not None and self.subtitle_label is not None:
            self.subtitle_label.set_label(subtitle)

    def set_tv_focus(self, on: bool) -> None:
        if on == self._focused:
            return
        self._focused = on
        if not self._lift:
            return
        start = self._focus
        scale = TV["cover"]["focus_scale"]

        def step(value: float) -> None:
            self._focus = value
            self._scale = 1.0 + (scale - 1.0) * value
            self.queue_draw()
        self._animation = _animate(self, start, 1.0 if on else 0.0, _lift_ms(), step, self._animation)

    @property
    def art_rect(self) -> tuple[float, float, float, float]:
        u = self._unit()
        return 0.0, 0.0, self._art_w * u, self._art_h * u

    # GTK
    def do_root(self) -> None:
        Gtk.Widget.do_root(self)
        self._follow_stage()

    def do_unroot(self) -> None:
        self._leave_stage()
        self._cancel()
        Gtk.Widget.do_unroot(self)

    def do_dispose(self) -> None:
        self._cancel()
        for label in (self.name_label, self.title_label, self.subtitle_label):
            if label is not None and label.get_parent() is self:
                label.unparent()
        self.name_label = self.title_label = self.subtitle_label = None

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.CONSTANT_SIZE

    def _caption_height(self, width: int) -> int:
        labels = [x for x in (self.title_label, self.subtitle_label) if x is not None]
        if not labels:
            return 0
        u = self._unit()
        height = MEDIA["cover"]["caption_gap"] * u + MEDIA["cover"]["caption_line_gap"] * u * (len(labels) - 1)
        return int(math.ceil(height + sum(label.measure(Gtk.Orientation.VERTICAL, width)[1] for label in labels)))

    def do_measure(self, orientation: Gtk.Orientation, _for_size: int) -> tuple[int, int, int, int]:
        u = self._unit()
        w = int(round(self._art_w * u))
        if orientation == Gtk.Orientation.HORIZONTAL:
            return w, w, -1, -1
        h = int(round(self._art_h * u)) + self._caption_height(w)
        return h, h, -1, -1

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        u = self._unit()
        art_w, art_h = self._art_w * u, self._art_h * u
        if self.name_label is not None:
            inset = MEDIA["cover"]["name_inset"] * u
            inner = max(1, int(art_w - 2 * inset))
            nat = min(self.name_label.measure(Gtk.Orientation.VERTICAL, inner)[1], int(art_h - 2 * inset))
            self.name_label.allocate(inner, max(1, nat), -1, _at(inset, art_h - inset - nat))
        y = art_h + MEDIA["cover"]["caption_gap"] * u
        for label in (self.title_label, self.subtitle_label):
            if label is None:
                continue
            h = label.measure(Gtk.Orientation.VERTICAL, width)[1]
            label.allocate(width, h, -1, _at(0, y))
            y += h + MEDIA["cover"]["caption_line_gap"] * u
        self._load(max(art_w, art_h))

    def _load(self, side: float) -> None:
        if not self._artwork:
            return
        from .media_style import TextureLoader
        bucket = TextureLoader.bucket(side)
        if self._texture is not None and self._requested >= bucket:
            return
        if self._requested == bucket and self._request is not None:
            return
        self._cancel()
        self._requested = bucket
        artwork = self._artwork

        waiting = [False]

        def arrived(texture: Gdk.Texture | None) -> None:
            self._request = None
            if texture is None or artwork != self._artwork:
                return
            first = self._texture is None
            self._texture = texture
            if first and waiting[0] and self.get_mapped():
                # decoded off the main loop: the picture fades in over its placeholder colour
                def step(value: float) -> None:
                    self._reveal = value
                    self.queue_draw()
                self._reveal = 0.0
                self._reveal_animation = _animate(self, 0.0, 1.0, int(MEDIA["cover"].get("reveal_ms", 180)), step,
                                                  self._reveal_animation)
            self.queue_draw()
        self._request = TextureLoader.shared().request(artwork, side, arrived)
        waiting[0] = True

    def _cancel(self) -> None:
        if self._request is not None:
            self._request.cancel()
            self._request = None

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        u = self._unit()
        w, h = self.get_width(), self.get_height()
        art_w, art_h = self._art_w * u, self._art_h * u
        radius = self._radius * u
        art = _rounded(0, 0, art_w, art_h, radius)
        lifted = abs(self._scale - 1.0) > 1e-3
        if lifted:
            snapshot.save()
            snapshot.translate(Graphene.Point().init(w / 2, h / 2))
            snapshot.scale(self._scale, self._scale)
            snapshot.translate(Graphene.Point().init(-w / 2, -h / 2))
        c = MEDIA["cover"]
        if self._shadow:
            t = self._focus
            if t < 1e-3 or t > 1 - 1e-3:
                _cover_shadow(self, snapshot, art_w, art_h, radius, u, t > 0.5)
            else:
                # mid-lift: the one moving cover draws its shadow live
                snapshot.append_outset_shadow(art, *_shadow_args(t, u))
        if self._focus > 1e-3:
            ring = Gdk.RGBA()
            ring.parse(TV["materials"]["ring"])
            ring.alpha *= self._focus
            snapshot.append_outset_shadow(art, ring, 0, 0, TV["cover"]["ring"] * u, 0)
        snapshot.push_rounded_clip(art)
        bounds = Graphene.Rect().init(0, 0, art_w, art_h)
        texture = self._texture
        if texture is None or self._reveal < 0.999:
            self._placeholder(snapshot, bounds, art_w, art_h)
        if texture is not None:
            from .media_style import cover_rect
            target = cover_rect(texture.get_width(), texture.get_height(), bounds)
            # mipmaps only for a picture much larger than it is drawn; a near-size one filters linearly
            ratio = texture.get_width() / max(1.0, target.get_width() * self._scale)
            if self._reveal < 0.999:
                snapshot.push_opacity(self._reveal)
            snapshot.append_scaled_texture(texture, Gsk.ScalingFilter.TRILINEAR if ratio > 1.6
                                           else Gsk.ScalingFilter.LINEAR, target)
            if self._reveal < 0.999:
                snapshot.pop()
        snapshot.pop()
        for label in (self.name_label, self.title_label, self.subtitle_label):
            if label is not None:
                self.snapshot_child(label, snapshot)
        if lifted:
            snapshot.restore()

    def _placeholder(self, snapshot: Gtk.Snapshot, bounds: Graphene.Rect, w: float, h: float) -> None:
        """Before its picture: the picture's own dominant colour when known; a sleeve without one."""
        if self._artwork:
            from .media_style import TextureLoader
            colour = TextureLoader.shared().colour(self._artwork)
            if colour is not None:
                snapshot.append_color(Gdk.RGBA(red=colour[0] / 255, green=colour[1] / 255, blue=colour[2] / 255,
                                               alpha=1.0), bounds)
                return
        self._generated(snapshot, bounds, w, h)

    def _generated(self, snapshot: Gtk.Snapshot, bounds: Graphene.Rect, w: float, h: float) -> None:
        if self._hues is None:
            snapshot.append_color(_rgba((0.32, 0.01, 265, 1)), bounds)
            return
        first, second, third = self._hues
        snapshot.append_color(_rgba((0.5, 0.14, second, 1)), bounds)
        for (cx, cy, end), colour in (((0.2, 0.15, 0.6), (0.78, 0.14, first, 1)),
                                      ((0.85, 0.9, 0.65), (0.6, 0.17, third, 1))):
            ink = _rgba(colour)
            snapshot.append_radial_gradient(bounds, Graphene.Point().init(cx * w, cy * h), 0.9 * w, 0.9 * h, 0, 1,
                                            [_stop(0, ink), _stop(end, _clear(ink))])


def _at(x: float, y: float) -> Gsk.Transform:
    return Gsk.Transform.new().translate(Graphene.Point().init(round(x), round(y)))


def _shadow_args(t: float, u: float) -> tuple:
    """A cover's drop shadow at lift `t` (0 resting, 1 focused): colour, dx, dy, spread, blur."""
    c = MEDIA["cover"]
    return (_black(c["shadow_alpha"] + (c["focus_shadow_alpha"] - c["shadow_alpha"]) * t), 0,
            (c["shadow_y"] + (c["focus_shadow_y"] - c["shadow_y"]) * t) * u, 0,
            (c["shadow_blur"] + (c["focus_shadow_blur"] - c["shadow_blur"]) * t) * u)


_SHADOWS = None


def _cover_shadow(widget: Gtk.Widget, snapshot: Gtk.Snapshot, w: float, h: float, radius: float, u: float,
                  focused: bool) -> None:
    """A resting or focused cover's shadow, rendered once per size and shared by every cover.

    GSK computes a blurred shadow per pixel on every frame (~50 ms for a screen of
    covers on the Raspberry Pi 5 at 4K); a texture costs a fraction. Only the band
    around the art is drawn: the art covers the middle.
    """
    global _SHADOWS
    from .tv_stage import TvRaster
    if _SHADOWS is None:
        _SHADOWS = TvRaster(size=8)
    colour, _dx, dy, _spread, blur = _shadow_args(1.0 if focused else 0.0, u)
    margin = math.ceil(2 * blur + abs(dy))
    full_w, full_h = w + 2 * margin, h + 2 * margin

    def draw(s: Gtk.Snapshot) -> None:
        s.append_outset_shadow(_rounded(margin, margin, w, h, radius), colour, 0, dy, 0, blur)
    key = (round(w, 1), round(h, 1), round(radius, 1), round(u, 3), focused)
    texture = _SHADOWS.get(widget, key, full_w, full_h, draw, scale=.5)
    if texture is None:
        snapshot.append_outset_shadow(_rounded(0, 0, w, h, radius), colour, 0, dy, 0, blur)
        return
    whole = Graphene.Rect().init(-margin, -margin, full_w, full_h)
    inner = max(radius, 1.0)
    band = margin + inner
    for x, y, bw, bh in ((-margin, -margin, full_w, band), (-margin, h - inner, full_w, band),
                         (-margin, inner, band, h - 2 * inner), (w - inner, inner, band, h - 2 * inner)):
        if bw <= 0 or bh <= 0:
            continue
        snapshot.push_clip(Graphene.Rect().init(x, y, bw, bh))
        snapshot.append_texture(texture, whole)
        snapshot.pop()


# ── Transport ────────────────────────────────────────────────────────────────

class TvTransportButton(_UnitFollower, Gtk.Box):
    """A round transport button: 84 (previous, next), 108 for the main one (play/pause).

    Chip material at rest, the key with the remote's focus; the glyph is the
    filled Lucide transport glyph. `set_icon("pause")` swaps it in place.
    """

    __gtype_name__ = "LumaUITvTransportButton"

    def __init__(self, icon: str, *, main: bool = False, label: str = "") -> None:
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, hexpand=False,
                         accessible_role=Gtk.AccessibleRole.BUTTON)
        from .media_transport import MediaGlyph
        for name in ("tv-round", "tv-chip") + (("tv-main",) if main else ()):
            self.add_css_class(name)
        self._main = main
        self.glyph = MediaGlyph(icon, MEDIA["transport"]["main_icon" if main else "icon"], filled=True)
        self.set_homogeneous(True)
        self.append(self.glyph)
        if label:
            self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        _install_style()

    def set_icon(self, icon: str, label: str | None = None) -> None:
        self.glyph.set_name_(icon)
        if label:
            self.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def set_tv_focus(self, on: bool) -> None:
        _swap(self, "tv-key" if on else "tv-chip", "tv-chip" if on else "tv-key")

    def do_root(self) -> None:
        Gtk.Box.do_root(self)
        self._follow_stage()

    def do_unroot(self) -> None:
        self._leave_stage()
        Gtk.Box.do_unroot(self)

    def _unit_changed(self) -> None:
        self.glyph.set_size(MEDIA["transport"]["main_icon" if self._main else "icon"] * self._unit())


def _swap(widget: Gtk.Widget, add: str, remove: str) -> None:
    widget.remove_css_class(remove)
    widget.add_css_class(add)


# ── Progress ─────────────────────────────────────────────────────────────────

class TvProgress(_UnitFollower, Gtk.Widget):
    """How far through: a recessed well track with a key fill, or a hairline (`hairline=True`, idle views)."""

    __gtype_name__ = "LumaUITvProgress"

    def __init__(self, *, hairline: bool = False) -> None:
        super().__init__(hexpand=True, valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.PROGRESS_BAR,
                         can_target=False)
        self._hairline = hairline
        self._fraction = 0.0

    def set_fraction(self, fraction: float) -> None:
        fraction = max(0.0, min(1.0, fraction))
        if abs(fraction - self._fraction) > 1e-4:
            self._fraction = fraction
            self.update_property([Gtk.AccessibleProperty.VALUE_NOW], [fraction * 100])
            self.queue_draw()

    def do_root(self) -> None:
        Gtk.Widget.do_root(self)
        self._follow_stage()

    def do_unroot(self) -> None:
        self._leave_stage()
        Gtk.Widget.do_unroot(self)

    def do_measure(self, orientation: Gtk.Orientation, _for_size: int) -> tuple[int, int, int, int]:
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 0, 0, -1, -1
        p = MEDIA["progress"]
        h = int(math.ceil((p["hairline"] if self._hairline else p["height"]) * self._unit()))
        return h, h, -1, -1

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        u, w, h = self._unit(), self.get_width(), self.get_height()
        p = MEDIA["progress"]
        radius = h / 2 if self._hairline else p["radius"] * u
        track = _rounded(0, 0, w, h, radius)
        fill_w = w * self._fraction
        if self._hairline:
            snapshot.push_rounded_clip(track)
            snapshot.append_color(_rgba(p["hairline_track"]), track.bounds)
            snapshot.append_color(_rgba(p["hairline_fill"]), Graphene.Rect().init(0, 0, fill_w, h))
            snapshot.pop()
            return
        glint = _material("well_glint") if "well_glint" in TV["materials"] else _rgba((1, 0, 0, 0.07))
        snapshot.append_outset_shadow(track, glint, 0, 1.5 * u, 0, 0)
        snapshot.push_rounded_clip(track)
        snapshot.append_color(_material("well"), track.bounds)
        if fill_w > 0.5:
            fill = _rounded(0, 0, max(fill_w, h), h, radius)
            snapshot.push_rounded_clip(fill)
            snapshot.append_linear_gradient(fill.bounds, Graphene.Point().init(0, 0), Graphene.Point().init(0, h),
                                            [_stop(0, _material("key_top")), _stop(1, _material("key_bottom"))])
            snapshot.append_inset_shadow(fill, _rgba((1, 0, 0, 0.8)), 0, 1.5 * u, 0, 0)
            snapshot.pop()
        snapshot.pop()
        snapshot.append_inset_shadow(track, _material("well_rim"), 0, 0, 1.5 * u, 0)
        snapshot.append_inset_shadow(track, _material("well_shade"), 0, 2 * u, 0, 4 * u)


# ── The equaliser and the now-playing pill ───────────────────────────────────

class TvEqualizer(_UnitFollower, Gtk.Widget):
    """Three bars rising and falling in the widget's ink while something plays; still when paused."""

    __gtype_name__ = "LumaUITvEqualizer"

    def __init__(self, *, height: float | None = None) -> None:
        super().__init__(valign=Gtk.Align.CENTER, can_target=False, accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self._height = MEDIA["equalizer"]["height"] if height is None else height
        self._playing = False
        self._tick = 0

    def set_playing(self, playing: bool) -> None:
        if playing != self._playing:
            self._playing = playing
            self._run()
            self.queue_draw()

    def do_map(self) -> None:
        Gtk.Widget.do_map(self)
        self._run()

    def do_unmap(self) -> None:
        self._stop()
        Gtk.Widget.do_unmap(self)

    def do_root(self) -> None:
        Gtk.Widget.do_root(self)
        self._follow_stage()

    def do_unroot(self) -> None:
        self._leave_stage()
        Gtk.Widget.do_unroot(self)

    def _run(self) -> None:
        from .lumaui import reduced_motion
        if self._playing and self.get_mapped() and not reduced_motion():
            if not self._tick:
                self._tick = self.add_tick_callback(lambda *_a: self.queue_draw() or True)
        else:
            self._stop()

    def _stop(self) -> None:
        if self._tick:
            self.remove_tick_callback(self._tick)
            self._tick = 0

    def do_measure(self, orientation: Gtk.Orientation, _for_size: int) -> tuple[int, int, int, int]:
        e, u = MEDIA["equalizer"], self._unit()
        size = (e["bars"] * e["bar_width"] + (e["bars"] - 1) * e["bar_gap"]) if orientation == Gtk.Orientation.HORIZONTAL \
            else self._height
        px = int(math.ceil(size * u))
        return px, px, -1, -1

    @staticmethod
    def level(seconds: float, phase: float) -> float:
        """A bar's height (0-1) at a time: an eased rise and fall between `low` and full."""
        e = MEDIA["equalizer"]
        t = (seconds * 1000 / e["period_ms"] + phase) % 2.0
        x = t if t < 1 else 2 - t
        eased = x * x * (3 - 2 * x)
        return e["low"] + (1 - e["low"]) * eased

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        e, u = MEDIA["equalizer"], self._unit()
        h, ink = self.get_height(), self.get_color()
        frame = self.get_frame_clock()
        seconds = (frame.get_frame_time() if frame is not None else GLib.get_monotonic_time()) / 1_000_000
        for i in range(e["bars"]):
            level = self.level(seconds, e["phases"][i % len(e["phases"])]) if self._playing or self._tick else \
                self.level(0.35, e["phases"][i % len(e["phases"])])
            bar_h = max(1.0, h * level)
            x = i * (e["bar_width"] + e["bar_gap"]) * u
            snapshot.push_rounded_clip(_rounded(x, h - bar_h, e["bar_width"] * u, bar_h, e["bar_width"] * u / 2))
            snapshot.append_color(ink, Graphene.Rect().init(x, h - bar_h, e["bar_width"] * u, bar_h))
            snapshot.pop()


class TvNowPlayingPill(_UnitFollower, Gtk.Box):
    """What's playing, riding in the shared top bar: the sleeve, the song and artist, an equaliser.

    Bar material at rest, the key with the remote's focus; at most 520 wide.
    Put it before the clock (`TvTopBar.end.prepend(pill)`) and register it with
    the focus engine; OK on it opens the app's Now playing. Hidden until a
    track is shown.
    """

    __gtype_name__ = "LumaUITvNowPlayingPill"

    def __init__(self) -> None:
        super().__init__(valign=Gtk.Align.CENTER, halign=Gtk.Align.END, hexpand=False, visible=False,
                         accessible_role=Gtk.AccessibleRole.BUTTON)
        for name in ("tv-pill", "tv-bar"):
            self.add_css_class(name)
        p = MEDIA["pill"]
        self.sleeve = TvCover(width=p["sleeve"], radius=p["sleeve_radius"], shadow=False, lift=False)
        self.sleeve.set_valign(Gtk.Align.CENTER)
        self.append(self.sleeve)
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        words.add_css_class("tv-pill-words")
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, single_line_mode=True)
        self.title.add_css_class("tv-pill-title")
        self.artist = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, single_line_mode=True)
        self.artist.add_css_class("tv-pill-artist")
        words.append(self.title)
        words.append(self.artist)
        p_ = MEDIA["pill"]
        # the pill is at most 520 wide: the words take what the sleeve, equaliser and padding leave
        rest = p_["max_width"] - p_["padding_start"] - p_["padding_end"] - p_["sleeve"] - 2 * p_["gap"] - \
            MEDIA["equalizer"]["bars"] * (MEDIA["equalizer"]["bar_width"] + MEDIA["equalizer"]["bar_gap"])
        self.append(_Capped(words, lambda: rest * self._unit()))
        self.equalizer = TvEqualizer()
        self.append(self.equalizer)
        self._focused = False
        _install_style()

    def show_track(self, title: str | None, artist: str = "", artwork: str | None = None, *,
                   playing: bool = False, hues: tuple[int, int, int] | None = None) -> None:
        """Show a track (None hides the pill)."""
        if not title:
            self.set_visible(False)
            self.equalizer.set_playing(False)
            return
        self.title.set_label(title)
        self.artist.set_label(artist)
        self.sleeve.set_artwork(artwork, hues)
        self.set_playing(playing)
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"Now playing: {title}, {artist}" if artist else title])
        self.set_visible(True)

    def set_playing(self, playing: bool) -> None:
        self.equalizer.set_playing(playing)

    def set_tv_focus(self, on: bool) -> None:
        self._focused = on
        _swap(self, "tv-key" if on else "tv-bar", "tv-bar" if on else "tv-key")

    def do_root(self) -> None:
        Gtk.Box.do_root(self)
        self._follow_stage()

    def do_unroot(self) -> None:
        self._leave_stage()
        Gtk.Box.do_unroot(self)



class _Capped(Gtk.Widget):
    """Holds a child to at most `cap()` pixels wide (its natural width otherwise)."""

    def __init__(self, child: Gtk.Widget, cap: Callable[[], float]) -> None:
        super().__init__(valign=Gtk.Align.CENTER)
        self.child, self.cap = child, cap
        child.set_parent(self)

    def do_dispose(self) -> None:
        if self.child is not None and self.child.get_parent() is self:
            self.child.unparent()
        self.child = None

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        minimum, natural, mb, nb = self.child.measure(orientation, for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            natural = max(minimum, min(natural, int(self.cap())))
        return minimum, natural, mb, nb

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        self.child.allocate(width, height, baseline, None)


# ── The room ─────────────────────────────────────────────────────────────────

class _RoomCache:
    """Blurred room textures by key, made off the main loop; a small LRU shared by every room."""

    _shared: "_RoomCache | None" = None

    def __init__(self) -> None:
        self._cache: OrderedDict[str, Gdk.Texture] = OrderedDict()
        self._waiting: dict[str, list[Callable[[Gdk.Texture | None], None]]] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lumaui-tv-room")

    @classmethod
    def shared(cls) -> "_RoomCache":
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    def get(self, key: str, make: Callable[[], Gdk.Texture | None], done: Callable[[Gdk.Texture | None], None]) -> None:
        with self._lock:
            texture = self._cache.get(key)
            if texture is not None:
                self._cache.move_to_end(key)
            elif key in self._waiting:
                self._waiting[key].append(done)
                return
            else:
                self._waiting[key] = [done]
        if texture is not None:
            done(texture)
            return

        def work() -> None:
            try:
                made = make()
            except Exception:  # noqa: BLE001 - an unreadable picture leaves the room as it was
                made = None
            GLib.idle_add(self._deliver, key, made)
        self._pool.submit(work)

    def _deliver(self, key: str, texture: Gdk.Texture | None) -> bool:
        with self._lock:
            callbacks = self._waiting.pop(key, [])
            if texture is not None:
                self._cache[key] = texture
                while len(self._cache) > MEDIA["room"]["cache"]:
                    self._cache.popitem(last=False)
        for callback in callbacks:
            callback(texture)
        return False


def _room_texture_from_pixels(pixels: bytearray, side: int) -> Gdk.Texture:
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    graded = room_pixels(bytes(pixels), side, side)
    small = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(graded)), GdkPixbuf.Colorspace.RGB, False, 8,
                                           side, side, side * 3)
    big_side = MEDIA["room"]["texture"]
    big = small.scale_simple(big_side, big_side, GdkPixbuf.InterpType.BILINEAR)
    return Gdk.MemoryTexture.new(big.get_width(), big.get_height(), Gdk.MemoryFormat.R8G8B8,
                                 big.read_pixel_bytes(), big.get_rowstride())


def _room_from_file(path: str) -> Gdk.Texture:
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    side = MEDIA["room"]["side"]
    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, side, side, False)
    pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
    if pixbuf.get_has_alpha():
        flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, side, side)
        flat.fill(0x000000ff)
        pixbuf.composite(flat, 0, 0, side, side, 0, 0, 1, 1, GdkPixbuf.InterpType.BILINEAR, 255)
        pixbuf = flat
    raw, stride = pixbuf.read_pixel_bytes().get_data(), pixbuf.get_rowstride()
    pixels = bytearray()
    for row in range(side):
        pixels += raw[row * stride: row * stride + side * 3]
    return _room_texture_from_pixels(pixels, side)


class TvRoom(Gtk.Widget):
    """The room: the focused or playing item's own art, blurred, filling the screen, cross-faded.

    `set_artwork(path)` (or `hues=` for an item without art) changes it over
    1.1 s. The art is blurred once off the main loop and drawn as a texture,
    never with a live blur. A shade (darker top and bottom) keeps the bar and
    text legible. Place it first on the stage: `stage.place(room, fill=True)`.
    """

    __gtype_name__ = "LumaUITvRoom"

    def __init__(self) -> None:
        super().__init__(can_target=False, hexpand=True, vexpand=True, overflow=Gtk.Overflow.HIDDEN,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self._key: str | None = None
        self._current: Gdk.Texture | None = None
        self._previous: Gdk.Texture | None = None
        self._mix = 1.0
        self._animation: Adw.Animation | None = None
        from .tv_stage import TvRaster
        self._shade = TvRaster(size=1)

    def do_unroot(self) -> None:
        self._shade.clear()
        Gtk.Widget.do_unroot(self)

    def set_artwork(self, artwork: str | None = None, *, hues: tuple[int, int, int] | None = None) -> None:
        if artwork:
            key, make = "file:" + artwork, (lambda: _room_from_file(artwork))
        elif hues is not None:
            side = MEDIA["room"]["side"]
            key, make = "hues:%d,%d,%d" % tuple(hues), (lambda: _room_texture_from_pixels(hue_sleeve_pixels(hues, side), side))
        else:
            return
        if key == self._key:
            return
        self._key = key
        _RoomCache.shared().get(key, make, lambda texture, k=key: self._arrived(k, texture))

    def _arrived(self, key: str, texture: Gdk.Texture | None) -> None:
        if texture is None or key != self._key or texture is self._current:
            return
        self._previous, self._current = self._current, texture

        def step(value: float) -> None:
            self._mix = value
            if value >= 1.0:
                self._previous = None
            self.queue_draw()
        self._mix = 0.0
        self._animation = _animate(self, 0.0, 1.0, MEDIA["room"]["fade_ms"], step, self._animation)

    def do_measure(self, _orientation: Gtk.Orientation, _for_size: int) -> tuple[int, int, int, int]:
        return 0, 0, -1, -1

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        from .lumaui import tv_unit
        from .media_style import cover_rect
        w, h = self.get_width(), self.get_height()
        bounds = Graphene.Rect().init(0, 0, w, h)
        snapshot.append_color(_black(1.0), bounds)
        bleed = MEDIA["room"]["bleed"] * (tv_unit(w, h) or 1.0)
        box = Graphene.Rect().init(-bleed, -bleed, w + 2 * bleed, h + 2 * bleed)
        for texture, opacity in ((self._previous, 1.0), (self._current, self._mix)):
            if texture is None or opacity <= 0.001:
                continue
            target = cover_rect(texture.get_width(), texture.get_height(), box)
            if opacity < 0.999:
                snapshot.push_opacity(opacity)
            snapshot.append_texture(texture, target)
            if opacity < 0.999:
                snapshot.pop()
        # The shade is a full-screen gradient: rendered once per size at a quarter
        # of it (a 4K gradient costs the Raspberry Pi 5's V3D ~100 ms a frame).
        stops = [_stop(offset, _black(alpha)) for offset, alpha in MEDIA["room"]["shade"]]

        def shade(s: Gtk.Snapshot) -> None:
            s.append_linear_gradient(bounds, Graphene.Point().init(0, 0), Graphene.Point().init(0, h), stops)
        texture = self._shade.get(self, (w, h), w, h, shade, scale=.25)
        if texture is None:
            shade(snapshot)
        else:
            snapshot.append_texture(texture, bounds)


def _black(alpha: float) -> Gdk.RGBA:
    """Black at an alpha (a bare Gdk.RGBA() is not guaranteed to be black)."""
    value = Gdk.RGBA()
    value.red = value.green = value.blue = 0.0
    value.alpha = alpha
    return value


# ── Sliding collections ──────────────────────────────────────────────────────

class TvSlide(Gtk.Widget):
    """A row or page that slides to keep the remote's focus in view; no scrollbars, no pointer.

    Horizontal (a shelf): slides only as far as needed to bring the focused item
    in, with `peek` TV units of the next showing. Vertical (a page): the same,
    keeping `lead` units above the focused row. `clip=False` lets a shelf run to
    the screen's edges (the stage clips there). Call `reveal(widget)` from the
    focus engine's change callback.

    A vertical slide fades out at an edge while its content runs on past it
    (`tv.scroll`: long eased fades, never a hard cut, as the C island's page);
    The top fade is on by default and the bottom one off (grids end at the screen);
    `fade_top`/`fade_bottom` choose. Only the edge strips are masked.
    """

    __gtype_name__ = "LumaUITvSlide"

    def __init__(self, child: Gtk.Widget, *, orientation: Gtk.Orientation = Gtk.Orientation.VERTICAL,
                 peek: float = 120, lead: float = 40, clip: bool = True, fade_top: bool = True,
                 fade_bottom: bool = False) -> None:
        super().__init__(overflow=Gtk.Overflow.HIDDEN if clip else Gtk.Overflow.VISIBLE,
                         hexpand=orientation == Gtk.Orientation.HORIZONTAL or clip,
                         vexpand=orientation == Gtk.Orientation.VERTICAL)
        self.child = child
        self.orientation = orientation
        self.peek, self.lead = peek, lead
        self.offset = 0.0
        self._length = 0.0  # the content's length along the slide, as last allocated
        self._fades = (fade_top, fade_bottom) if orientation == Gtk.Orientation.VERTICAL else (False, False)
        self._animation: Adw.Animation | None = None
        child.set_parent(self)

    def do_dispose(self) -> None:
        if self.child is not None and self.child.get_parent() is self:
            self.child.unparent()
        self.child = None

    def _content(self, width: int, height: int) -> float:
        if self.child is None:
            return 0.0
        if self.orientation == Gtk.Orientation.HORIZONTAL:
            return float(max(width, self.child.measure(Gtk.Orientation.HORIZONTAL, height)[1]))
        return float(max(height, self.child.measure(Gtk.Orientation.VERTICAL, width)[1]))

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        if self.child is None:
            return 0, 0, -1, -1
        if orientation == self.orientation:
            return 0, 0, -1, -1
        return self.child.measure(orientation, -1)[:2] + (-1, -1)

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        if self.child is None:
            return
        content = self._content(width, height)
        self._length = content
        view = width if self.orientation == Gtk.Orientation.HORIZONTAL else height
        self.offset = max(0.0, min(self.offset, max(0.0, content - view)))
        if self.orientation == Gtk.Orientation.HORIZONTAL:
            self.child.allocate(int(content), height, -1, _at(-self.offset, 0))
        else:
            self.child.allocate(width, int(content), -1, _at(0, -self.offset))

    def _unit(self) -> float:
        from .lumaui import tv_unit
        root = self.get_root()
        node = self.get_parent()
        while node is not None and node.find_property("unit") is None:
            node = node.get_parent()
        if node is not None:
            return float(node.get_property("unit")) or 1.0
        return tv_unit(root.get_width(), root.get_height()) if root is not None else 1.0

    def reveal(self, widget: Gtk.Widget, *, animate: bool = True) -> None:
        """Slide (only as far as needed) so `widget`, inside this slide, is in view."""
        if self.child is None or not widget.is_ancestor(self.child) and widget is not self.child:
            return
        ok, bounds = widget.compute_bounds(self.child)
        if not ok:
            return
        horizontal = self.orientation == Gtk.Orientation.HORIZONTAL
        start = bounds.get_x() if horizontal else bounds.get_y()
        length = bounds.get_width() if horizontal else bounds.get_height()
        view = self.get_width() if horizontal else self.get_height()
        # the length as allocated; measuring again here would re-measure a whole grid on every move
        content = self._length or self._content(self.get_width(), self.get_height())
        u = self._unit()
        margin = (self.peek if horizontal else self.lead) * u
        tail = self.peek * u if horizontal else self.lead * u
        target = self.offset
        if start + length + tail > target + view:
            target = start + length + tail - view
        if start - margin < target:
            target = start - margin
        target = max(0.0, min(target, max(0.0, content - view)))
        self.slide_to(target, animate=animate)

    def slide_to(self, target: float, *, animate: bool = True) -> None:
        if abs(target - self.offset) < 0.5:
            return

        def step(value: float) -> None:
            self.offset = value
            self.queue_allocate()
        if not animate:
            if self._animation is not None:
                self._animation.pause()
            step(target)
            return
        self._animation = _animate(self, self.offset, target, int(TV["motion"].get("page_ms", 250)) + 100, step,
                                   self._animation)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        child = self.child
        if child is None:
            return
        width, height = self.get_width(), self.get_height()
        scroll, u = TV["scroll"], self._unit()
        # each edge fades in over its own length as content passes it (none at the top of a page)
        above = min(1.0, self.offset / (scroll["fade_top"] * u)) if self._fades[0] else 0.0
        below = (min(1.0, max(0.0, self._length - height - self.offset) / (scroll["fade_bottom"] * u))
                 if self._fades[1] else 0.0)
        top = min(0.45 * height, scroll["fade_top"] * u) if above > 1e-3 else 0.0
        bottom = min(0.45 * height, scroll["fade_bottom"] * u) if below > 1e-3 else 0.0
        if top <= 0 and bottom <= 0 or width <= 0 or height <= 0:
            self.snapshot_child(child, snapshot)
            return
        # The child's nodes are recorded once and drawn three times: plain in the middle, and masked
        # in the two edge strips, so only the strips go through an offscreen.
        recorder = Gtk.Snapshot()
        self.snapshot_child(child, recorder)
        node = recorder.to_node()
        if node is None:
            return
        middle = Graphene.Rect().init(0, top, width, height - top - bottom)
        snapshot.push_clip(middle)
        snapshot.append_node(node)
        snapshot.pop()
        for length, strength, edge, inward in ((top, above, 0.0, 1.0), (bottom, below, height, -1.0)):
            if length <= 0:
                continue
            strip = Graphene.Rect().init(0, edge if inward > 0 else edge - length, width, length)
            snapshot.push_clip(strip)
            snapshot.push_mask(Gsk.MaskMode.ALPHA)
            snapshot.append_linear_gradient(strip, Graphene.Point().init(0, edge),
                                            Graphene.Point().init(0, edge + inward * length),
                                            _fade_stops(strength, scroll["fade_stops"]))
            snapshot.pop()
            snapshot.append_node(node)
            snapshot.pop()
            snapshot.pop()


def _fade_stops(strength: float, count: int) -> list[Gsk.ColorStop]:
    """An edge fade from the edge inwards: ease-out, steep only at the very edge, gentle into 1."""
    stops = []
    for i in range(count):
        t = i / (count - 1)
        eased = 1.0 - (1.0 - t) * (1.0 - t)
        stops.append(_stop(t, Gdk.RGBA(red=0, green=0, blue=0, alpha=1.0 - strength * (1.0 - eased))))
    return stops


# ── Styles ───────────────────────────────────────────────────────────────────

def _sheet(_opaque: bool = False) -> str:
    c, p, b = MEDIA["cover"], MEDIA["pill"], TV["button"]
    from .lumaui import oklch_rgba
    key_ink = TV["materials"]["key_ink"]
    return f"""
.tv-cover-name {{ font-size: {c['name_size']}tv; font-weight: {c['name_weight']}; letter-spacing: -0.6tv;
  color: {TV['materials']['ink']}; }}
.tv-round {{ min-width: {b['round']}tv; min-height: {b['round']}tv; padding: 0; border-radius: 9999px; }}
.tv-round.tv-main {{ min-width: {b['round_main']}tv; min-height: {b['round_main']}tv; }}
.tv-pill {{ min-height: {p['height'] - 2 * p['padding_y']}tv; border-radius: {p['radius']}tv;
  padding: {p['padding_y']}tv {p['padding_end']}tv {p['padding_y']}tv {p['padding_start']}tv;
  border-spacing: {p['gap']}tv; margin-right: {p['margin_end']}tv; }}
.tv-pill-title {{ font-size: {p['title_size']}tv; font-weight: {p['title_weight']}; }}
.tv-pill-artist {{ font-size: {p['artist_size']}tv; color: {TV['materials']['ink_3']}; }}
.tv-pill.tv-key .tv-pill-artist {{ color: alpha({key_ink}, .6); }}
.tv-playing, .lumaui-tv-stage label.tv-playing {{ color: {oklch_rgba(*MEDIA['playing'])}; }}
.tv-key .tv-playing, .lumaui-tv-stage .tv-key label.tv-playing {{ color: {key_ink}; }}
"""


_styled = False


def _install_style() -> None:
    """Add the media parts' sheet to the TV style (once). The stage keeps it in TV units."""
    global _styled
    if _styled or Gdk.Display.get_default() is None:
        return
    try:
        from .tv_stage import add_tv_style
    except ImportError:
        return
    _styled = True
    add_tv_style(_sheet)
