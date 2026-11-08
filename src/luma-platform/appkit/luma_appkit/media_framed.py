# SPDX-License-Identifier: Apache-2.0
"""LumaUI media: the framed picture, every screenshot shown in a Luma setting.

Developers send flat screenshots (a desktop window, a phone screen); a store,
a gallery or a lightbox always shows them framed (Depot section 9). One
renderer draws every frame at any size from one description: every measure is
a fraction of the frame (the mockup's `cqw`/`cqh`), so the big picture, a
96 px thumbnail and the lightbox are the same picture.

- **desktop**: the wallpaper blurred (2.2% of the width) and a little darker;
  the window at 64% of the width from 9% down, 1.6% round, a 1 px dark rim and a
  deep soft shadow; a frosted dock at the foot with icons of 3.8% (the app's own
  icon with a small white "running" dot).
- **phone**: a real place blurred (3.2%) and darker; Luma's reference phone
  upright (390×844 screen in a 12 bezel, 58/47 round, dark metal, a punch-hole
  camera) at 82% of the frame's height; the screenshot fills its screen.
- **specimen**: a widget (a theme drawn as a window, icon swatches, a waveform)
  scaled into a rounded panel floating on a blurred wallpaper.

    FramedPicture.desktop("tide-desktop-dark.png", backdrop="prism.png",
                          dock=["filer.svg", "tide.svg", …], running=1)
    FramedPicture.phone("tide-phone-dark.png", backdrop="terrace.jpg")
    FramedPicture.specimen_of(theme_window, backdrop="prism.png")
    FramedPicture.phone("ember.jpg", backdrop="ember.jpg", screen=lock_screen)   # a widget over the screen
    FramedPicture.desktop(shot, backdrop=wall, dock=icons, banner=chip)           # a frosted banner near the foot
    picture.set_aspect(4, 3)           # compact widths
    picture.set_radius(10)             # a thumbnail
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk  # noqa: E402

__all__ = ["FRAME_KINDS", "FrameSpec", "FramedPicture", "phone_rect", "window_rect"]

FRAME_KINDS = ("desktop", "phone", "specimen")

#: Depot 9.5, fractions of the frame (w = width, h = height).
WINDOW_WIDTH, WINDOW_TOP, WINDOW_RADIUS = 0.64, 0.09, 0.016
DOCK_ICON, DOCK_GAP, DOCK_BOTTOM, DOCK_RADIUS, ICON_RADIUS = 0.038, 0.011, 0.034, 0.024, 0.011
BLUR_DESKTOP, BLUR_PHONE = 0.022, 0.032
PHONE_HEIGHT, PHONE_TOP = 0.82, 0.09
#: The reference phone in its own points: 390×844 screen, 12 bezel, 58/47 round.
PHONE_W, PHONE_H, BEZEL, PHONE_R, SCREEN_R = 414, 868, 12, 58, 47
SPEC_INSET_X, SPEC_INSET_Y, SPEC_RADIUS = 0.16, 0.12, 0.03

_textures: dict[str, Gdk.Texture | None] = {}


def texture(path: str | None) -> Gdk.Texture | None:
    """A cached texture for `path`, or None when there is no file (missing data stays missing)."""
    if not path:
        return None
    if path not in _textures:
        try:
            _textures[path] = Gdk.Texture.new_from_filename(path)
        except Exception:  # noqa: BLE001 - an unreadable picture is drawn as no picture
            _textures[path] = None
        if len(_textures) > 96:
            _textures.pop(next(iter(_textures)))
    return _textures[path]


def window_rect(width: float, height: float, aspect: float) -> tuple[float, float, float, float]:
    """Where the desktop window goes in a frame: 64% wide, centred, 9% down; `aspect` = w/h of the shot."""
    w = width * WINDOW_WIDTH
    return (width - w) / 2, height * WINDOW_TOP, w, w / aspect


def phone_rect(width: float, height: float) -> tuple[float, float, float, float, float]:
    """The reference phone's box in a frame and its scale: (x, y, w, h, s)."""
    s = height * PHONE_HEIGHT / PHONE_H
    w, h = PHONE_W * s, PHONE_H * s
    return (width - w) / 2, height * PHONE_TOP, w, h, s


@dataclass
class FrameSpec:
    """What to draw. Paths are files; anything missing is simply not drawn."""

    kind: str
    picture: str | None = None
    backdrop: str | None = None
    dock: Sequence[str] = field(default_factory=tuple)
    running: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in FRAME_KINDS:
            raise ValueError(f"a frame is {', '.join(FRAME_KINDS)}, not {self.kind!r}")


def _rgba(r: float, g: float, b: float, a: float) -> Gdk.RGBA:
    colour = Gdk.RGBA()
    colour.red, colour.green, colour.blue, colour.alpha = r, g, b, a
    return colour


def _rect(x: float, y: float, w: float, h: float) -> Graphene.Rect:
    return Graphene.Rect().init(x, y, w, h)


def _rounded(x: float, y: float, w: float, h: float, r: float) -> Gsk.RoundedRect:
    rounded = Gsk.RoundedRect()
    rounded.init_from_rect(_rect(x, y, w, h), r)
    return rounded


def _matrix(saturate: float, brightness: float) -> tuple[Graphene.Matrix, Graphene.Vec4]:
    """CSS saturate() then brightness() as one colour matrix."""
    s, b = saturate, brightness
    rows = [
        [0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s],
        [0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s],
        [0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s],
    ]
    # Graphene matrices are column-major for the colour-matrix node: out = M * in.
    values = [rows[0][0] * b, rows[1][0] * b, rows[2][0] * b, 0,
              rows[0][1] * b, rows[1][1] * b, rows[2][1] * b, 0,
              rows[0][2] * b, rows[1][2] * b, rows[2][2] * b, 0,
              0, 0, 0, 1]
    return Graphene.Matrix().init_from_float(values), Graphene.Vec4().init(0, 0, 0, 0)


def _cover(snapshot: Gtk.Snapshot, tex: Gdk.Texture, x: float, y: float, w: float, h: float) -> None:
    """Draw `tex` to fill the box, cropping the overflow (object-fit: cover)."""
    tw, th = tex.get_width(), tex.get_height()
    scale = max(w / tw, h / th)
    dw, dh = tw * scale, th * scale
    snapshot.push_clip(_rect(x, y, w, h))
    snapshot.append_scaled_texture(tex, Gsk.ScalingFilter.TRILINEAR, _rect(x + (w - dw) / 2, y + (h - dh) / 2, dw, dh))
    snapshot.pop()


class FramedPicture(Gtk.Widget):
    """The frame. Height follows width at the frame's aspect (16:9 unless set)."""

    __gtype_name__ = "LumaUIFramedPicture"

    #: The frame width a banner's own CSS sizes are written for; it scales with the frame.
    BANNER_REFERENCE = 572

    def __init__(self, spec: FrameSpec, *, specimen: Gtk.Widget | None = None, radius: float = 18,
                 specimen_width: int = 360, label: str | None = None, screen: Gtk.Widget | None = None,
                 banner: Gtk.Widget | None = None) -> None:
        super().__init__(overflow=Gtk.Overflow.HIDDEN, accessible_role=Gtk.AccessibleRole.IMG)
        self.add_css_class("lumaui-framed-picture")
        self.add_css_class(spec.kind)
        self.spec = spec
        self.radius = radius
        self.aspect = (16, 9)
        self.specimen_width = specimen_width
        self.specimen: Gtk.Widget | None = None
        if specimen is not None:
            self.specimen = specimen
            specimen.set_parent(self)
            specimen.set_can_target(False)
        # `screen` lies over a phone's screen at its own 390×844 points; `banner` sits centred near the foot.
        self.screen, self.banner = screen, banner
        for extra in (screen, banner):
            if extra is not None:
                extra.set_parent(self)
                extra.set_can_target(False)
        if label:
            self.update_property([Gtk.AccessibleProperty.LABEL], [label])

    # ── constructors ──────────────────────────────────────────────────────

    @classmethod
    def desktop(cls, window: str | None, *, backdrop: str | None, dock: Sequence[str] = (),
                running: int | None = None, **kwargs) -> "FramedPicture":
        return cls(FrameSpec("desktop", window, backdrop, tuple(dock), running), **kwargs)

    @classmethod
    def phone(cls, picture: str | None, *, backdrop: str | None, **kwargs) -> "FramedPicture":
        return cls(FrameSpec("phone", picture, backdrop), **kwargs)

    @classmethod
    def specimen_of(cls, widget: Gtk.Widget, *, backdrop: str | None, **kwargs) -> "FramedPicture":
        return cls(FrameSpec("specimen", None, backdrop), specimen=widget, **kwargs)

    # ── settings ──────────────────────────────────────────────────────────

    def set_aspect(self, width: int, height: int) -> None:
        self.aspect = (width, height)
        self.queue_resize()

    def set_radius(self, radius: float) -> None:
        self.radius = radius
        self.queue_draw()

    # ── layout ────────────────────────────────────────────────────────────

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        aw, ah = self.aspect
        if orientation == Gtk.Orientation.HORIZONTAL:
            # A thumbnail sized with set_size_request keeps that width as its natural one.
            requested = self.get_size_request()[0]
            return 0, requested if requested > 0 else 640, -1, -1
        # The height follows the width; the minimum stays 0 so GTK's unbounded-width check holds.
        width = for_size if for_size > 0 else 640
        return 0, round(width * ah / aw), -1, -1

    def _specimen_box(self, width: float, height: float) -> tuple[float, float, float, float]:
        return width * SPEC_INSET_X, height * SPEC_INSET_Y, width * (1 - 2 * SPEC_INSET_X), height * (1 - 2 * SPEC_INSET_Y)

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        if self.screen is not None:
            px, py, pw, ph, s = phone_rect(width, height)
            screen_w, screen_h = PHONE_W - 2 * BEZEL, PHONE_H - 2 * BEZEL
            self.screen.allocate(screen_w, screen_h, -1, Gsk.Transform().translate(
                Graphene.Point().init(px + BEZEL * s, py + BEZEL * s)).scale(s, s))
        if self.banner is not None:
            scale = width / self.BANNER_REFERENCE
            bw, bh = (max(1, v) for v in (self.banner.measure(Gtk.Orientation.HORIZONTAL, -1)[1],
                                          self.banner.measure(Gtk.Orientation.VERTICAL, -1)[1]))
            foot = height * (0.12 if self.spec.kind == "phone" else 0.18)
            self.banner.allocate(bw, bh, -1, Gsk.Transform().translate(
                Graphene.Point().init((width - bw * scale) / 2, height - foot - bh * scale)).scale(scale, scale))
        if self.specimen is None:
            return
        x, y, w, h = self._specimen_box(width, height)
        design_w = self.specimen_width
        design_h = max(1, round(design_w * h / max(w, 1)))
        scale = w / design_w
        transform = Gsk.Transform().translate(Graphene.Point().init(x, y)).scale(scale, scale)
        self.specimen.allocate(design_w, design_h, -1, transform)

    # ── drawing ───────────────────────────────────────────────────────────

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = float(self.get_width()), float(self.get_height())
        if width <= 0 or height <= 0:
            return
        snapshot.push_rounded_clip(_rounded(0, 0, width, height, self.radius))
        self._backdrop(snapshot, width, height)
        kind = self.spec.kind
        if kind == "desktop":
            self._window(snapshot, width, height)
            self._dock(snapshot, width, height)
        elif kind == "phone":
            self._phone(snapshot, width, height)
        else:
            self._specimen(snapshot, width, height)
        if self.banner is not None:
            self.snapshot_child(self.banner, snapshot)
        snapshot.pop()

    def _backdrop(self, snapshot: Gtk.Snapshot, width: float, height: float) -> None:
        tex = texture(self.spec.backdrop)
        if tex is None:
            snapshot.append_color(_rgba(0.1, 0.11, 0.13, 1), _rect(0, 0, width, height))
            return
        phone = self.spec.kind == "phone"
        blur = width * (BLUR_PHONE if phone else BLUR_DESKTOP)
        bleed = width * 0.06  # .dq-fbg { inset: -6cqw }: the blur never shows a soft edge
        matrix, offset = _matrix(1.1 if phone else 1.35, 0.8 if phone else 0.88)
        snapshot.push_color_matrix(matrix, offset)
        snapshot.push_blur(2 * blur)  # CSS blur(r) is a standard deviation; GSK takes twice it
        _cover(snapshot, tex, -bleed, -bleed, width + 2 * bleed, height + 2 * bleed)
        snapshot.pop()
        snapshot.pop()

    def _window(self, snapshot: Gtk.Snapshot, width: float, height: float) -> None:
        tex = texture(self.spec.picture)
        if tex is None:
            return
        x, y, w, h = window_rect(width, height, tex.get_width() / tex.get_height())
        r = width * WINDOW_RADIUS
        outline = _rounded(x, y, w, h, r)
        # 0 4cqh 9cqh -2cqh rgba(0,0,0,.55): the deep soft shadow
        snapshot.append_outset_shadow(outline, _rgba(0, 0, 0, 0.55), 0, height * 0.04, -height * 0.02, height * 0.09)
        snapshot.push_rounded_clip(outline)
        snapshot.append_scaled_texture(tex, Gsk.ScalingFilter.TRILINEAR, _rect(x, y, w, h))
        snapshot.pop()
        # 0 0 0 1px rgba(0,0,0,.35): the rim, outside the window
        snapshot.append_outset_shadow(outline, _rgba(0, 0, 0, 0.35), 0, 0, 1, 0)

    def _dock(self, snapshot: Gtk.Snapshot, width: float, height: float) -> None:
        icons = [texture(path) for path in self.spec.dock]
        icons = [icon for icon in icons]
        if not icons:
            return
        size, gap = width * DOCK_ICON, width * DOCK_GAP
        dock_w = len(icons) * size + (len(icons) - 1) * gap + 2 * gap
        dock_h = size + 2 * gap
        x, y = (width - dock_w) / 2, height - height * DOCK_BOTTOM - dock_h
        outline = _rounded(x, y, dock_w, dock_h, width * DOCK_RADIUS)
        snapshot.append_outset_shadow(outline, _rgba(0, 0, 0, 0.5), 0, 10, -10, 30)
        # frosted: the backdrop under the dock, blurred again, then the tint
        snapshot.push_rounded_clip(outline)
        tex = texture(self.spec.backdrop)
        if tex is not None:
            snapshot.push_blur(32)
            bleed = width * 0.06
            _cover(snapshot, tex, -bleed, -bleed, width + 2 * bleed, height + 2 * bleed)
            snapshot.pop()
        snapshot.append_color(_rgba(30 / 255, 30 / 255, 36 / 255, 0.45), _rect(x, y, dock_w, dock_h))
        snapshot.pop()
        snapshot.append_inset_shadow(outline, _rgba(1, 1, 1, 0.14), 0, 0, 1, 0)
        ix = x + gap
        for index, icon in enumerate(icons):
            box = _rounded(ix, y + gap, size, size, width * ICON_RADIUS)
            if icon is not None:
                snapshot.push_rounded_clip(box)
                snapshot.append_scaled_texture(icon, Gsk.ScalingFilter.TRILINEAR, _rect(ix, y + gap, size, size))
                snapshot.pop()
            if index == self.spec.running:
                dot = width * 0.0045
                cx, cy = ix + size / 2, y + gap + size + width * 0.0075 - dot / 2
                snapshot.push_rounded_clip(_rounded(cx - dot / 2, cy - dot / 2, dot, dot, dot / 2))
                snapshot.append_color(_rgba(1, 1, 1, 0.85), _rect(cx - dot / 2, cy - dot / 2, dot, dot))
                snapshot.pop()
            ix += size + gap

    def _phone(self, snapshot: Gtk.Snapshot, width: float, height: float) -> None:
        x, y, w, h, s = phone_rect(width, height)
        body = _rounded(x, y, w, h, PHONE_R * s)
        snapshot.append_outset_shadow(body, _rgba(0, 0, 0, 0.35), 0, height * 0.01, 0, height * 0.024)
        snapshot.append_outset_shadow(body, _rgba(0, 0, 0, 0.6), 0, height * 0.04, -height * 0.02, height * 0.08)
        # linear-gradient(160deg, #2b2c30, #141518 60%, #0c0d0f)
        angle = math.radians(160)
        dx, dy = math.sin(angle), -math.cos(angle)
        half = (abs(w * dx) + abs(h * dy)) / 2
        cx, cy = x + w / 2, y + h / 2
        stops = []
        for offset, hexes in ((0.0, (0x2b, 0x2c, 0x30)), (0.6, (0x14, 0x15, 0x18)), (1.0, (0x0c, 0x0d, 0x0f))):
            stop = Gsk.ColorStop()
            stop.offset, stop.color = offset, _rgba(*(v / 255 for v in hexes), 1)
            stops.append(stop)
        snapshot.push_rounded_clip(body)
        snapshot.append_linear_gradient(_rect(x, y, w, h), Graphene.Point().init(cx - dx * half, cy - dy * half),
                                        Graphene.Point().init(cx + dx * half, cy + dy * half), stops)
        snapshot.pop()
        snapshot.append_inset_shadow(body, _rgba(1, 1, 1, 0.12), 0, 0, 1.5 * s, 0)
        snapshot.append_inset_shadow(body, _rgba(1, 1, 1, 0.08), 0, 2 * s, 0, 1)
        screen = _rounded(x + BEZEL * s, y + BEZEL * s, w - 2 * BEZEL * s, h - 2 * BEZEL * s, SCREEN_R * s)
        snapshot.append_outset_shadow(screen, _rgba(5 / 255, 5 / 255, 6 / 255, 1), 0, 0, 2 * s, 0)
        snapshot.push_rounded_clip(screen)
        bounds = screen.bounds
        snapshot.append_color(_rgba(0, 0, 0, 1), bounds)
        tex = texture(self.spec.picture)
        if tex is not None:
            _cover(snapshot, tex, bounds.get_x(), bounds.get_y(), bounds.get_width(), bounds.get_height())
        if self.screen is not None:
            self.snapshot_child(self.screen, snapshot)
        snapshot.pop()
        hole = 12 * s
        hx, hy = x + w / 2 - hole / 2, y + 24 * s
        stops = []
        for offset, hexes in ((0.0, (0x26, 0x30, 0x4a)), (0.62, (0x05, 0x06, 0x08)), (1.0, (0x05, 0x06, 0x08))):
            stop = Gsk.ColorStop()
            stop.offset, stop.color = offset, _rgba(*(v / 255 for v in hexes), 1)
            stops.append(stop)
        snapshot.push_rounded_clip(_rounded(hx, hy, hole, hole, hole / 2))
        snapshot.append_radial_gradient(_rect(hx, hy, hole, hole), Graphene.Point().init(hx + hole * 0.35, hy + hole * 0.35),
                                        hole * 0.8, hole * 0.8, 0, 1, stops)
        snapshot.pop()

    def _specimen(self, snapshot: Gtk.Snapshot, width: float, height: float) -> None:
        x, y, w, h = self._specimen_box(width, height)
        panel = _rounded(x, y, w, h, width * SPEC_RADIUS)
        snapshot.append_outset_shadow(panel, _rgba(0, 0, 0, 0.6), 0, height * 0.04, -height * 0.02, height * 0.09)
        snapshot.push_rounded_clip(panel)
        if self.specimen is not None:
            self.snapshot_child(self.specimen, snapshot)
        snapshot.pop()
        snapshot.append_outset_shadow(panel, _rgba(1, 1, 1, 0.1), 0, 0, 1, 0)

    def do_dispose(self) -> None:
        for child in (self.specimen, self.screen, self.banner):
            if child is not None and child.get_parent() is self:
                child.unparent()
        Gtk.Widget.do_dispose(self)
