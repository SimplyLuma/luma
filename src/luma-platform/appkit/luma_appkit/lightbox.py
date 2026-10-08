# SPDX-License-Identifier: Apache-2.0
"""The lightbox: a picture enlarged inside its own window.

Opening a picture never leaves the application. The window's content dims
behind an overlay, the picture grows out of the thumbnail it was opened from
and shrinks back into it on close, and a person can zoom, pan and step through
every picture in the same place (a conversation, an album, a document).
Handing a picture to another application is an explicit choice: Open With….

Only the shown picture and its neighbours hold decoded pixels, at the size they
are displayed; zooming past the fitted size decodes the shown picture at full
resolution. Everything is released when the lightbox closes.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
import shutil
from typing import Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402

MAX_ZOOM = 8.0
MIN_ZOOM = 1.0
FULL_DECODE_LIMIT = 8192          # longest side decoded for zooming in
MAX_SOURCE_PIXELS = 100_000_000   # never decode anything larger than this
OPEN_MS = 260
STEP_MS = 180
SWIPE_DISTANCE = 72
EDGE_MARGIN = 24


# ── Geometry, kept pure so it can be tested without a display ─────────────────

def fit_rect(width: float, height: float, area_width: float, area_height: float) -> tuple[float, float, float, float]:
    """The largest rectangle with the picture's shape centred in the area, never enlarged past 1:1."""
    if width <= 0 or height <= 0 or area_width <= 0 or area_height <= 0:
        return (max(area_width, 0) / 2, max(area_height, 0) / 2, 0.0, 0.0)
    scale = min(area_width / width, area_height / height, 1.0)
    shown_width, shown_height = width * scale, height * scale
    return ((area_width - shown_width) / 2, (area_height - shown_height) / 2, shown_width, shown_height)


def zoom_about(zoom: float, pan: tuple[float, float], factor: float, point: tuple[float, float],
               centre: tuple[float, float]) -> tuple[float, tuple[float, float]]:
    """Zoom by a factor keeping the picture point under ``point`` where it is."""
    new_zoom = min(MAX_ZOOM, max(MIN_ZOOM, zoom * factor))
    applied = new_zoom / zoom
    # A point p on screen maps to picture offset (p - centre - pan); keep it fixed.
    px, py = point[0] - centre[0] - pan[0], point[1] - centre[1] - pan[1]
    return new_zoom, (pan[0] - px * (applied - 1), pan[1] - py * (applied - 1))


def clamp_pan(pan: tuple[float, float], shown: tuple[float, float], area: tuple[float, float]) -> tuple[float, float]:
    """Keep a zoomed picture covering the area it can cover: no panning a picture off screen."""
    limit_x = max(0.0, (shown[0] - area[0]) / 2)
    limit_y = max(0.0, (shown[1] - area[1]) / 2)
    return (min(limit_x, max(-limit_x, pan[0])), min(limit_y, max(-limit_y, pan[1])))


def neighbours(index: int, count: int) -> set[int]:
    """The pictures that hold decoded pixels: the shown one and one on each side."""
    return {i for i in (index - 1, index, index + 1) if 0 <= i < count}


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# ── What is shown ────────────────────────────────────────────────────────────

@dataclass
class LightboxItem:
    """One picture. ``state`` is ready, loading or unavailable; the message says why."""
    path: Path | None
    content_type: str = ""
    name: str = ""
    title: str = ""
    subtitle: str = ""
    state: str = "ready"
    message: str = ""
    source: Gtk.Widget | None = field(default=None, repr=False)
    retry: Callable[[], None] | None = field(default=None, repr=False)

    @property
    def accessible_name(self) -> str:
        parts = [self.name or "Picture", self.title, self.subtitle]
        return ", ".join(part for part in parts if part)


def _decode(path: Path, width: int, height: int) -> tuple | None:
    """Pixels at about ``width`` x ``height``; runs off the main thread."""
    try:
        _format, natural_width, natural_height = GdkPixbuf.Pixbuf.get_file_info(str(path))
        if natural_width <= 0 or natural_height <= 0 or natural_width * natural_height > MAX_SOURCE_PIXELS:
            return None
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), max(1, width), max(1, height), True)
        pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
    except (GLib.Error, OSError, TypeError, ValueError):
        return None
    oriented = (natural_height, natural_width) if (pixbuf.get_width() > pixbuf.get_height()) != (
        natural_width > natural_height) and natural_width != natural_height else (natural_width, natural_height)
    return (pixbuf.read_pixel_bytes().get_data(), pixbuf.get_width(), pixbuf.get_height(), pixbuf.get_rowstride(),
            pixbuf.get_has_alpha(), oriented)


def _texture(pixels) -> Gdk.Texture:
    data, width, height, stride, alpha, _natural = pixels
    memory_format = Gdk.MemoryFormat.R8G8B8A8 if alpha else Gdk.MemoryFormat.R8G8B8
    return Gdk.MemoryTexture.new(width, height, memory_format, GLib.Bytes.new(data), stride)


class _Canvas(Gtk.Widget):
    """Draws the shown picture at its animated, zoomed and panned rectangle."""

    __gtype_name__ = "LumaLightboxCanvas"

    def __init__(self, lightbox: "Lightbox") -> None:
        super().__init__(hexpand=True, vexpand=True)
        self._lightbox = lightbox
        self.set_overflow(Gtk.Overflow.HIDDEN)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        box = self._lightbox
        paintable = box.current_paintable()
        rect = box.picture_rect()
        if paintable is None or rect is None or rect[2] < 1 or rect[3] < 1:
            return
        x, y, width, height = rect
        radius = lerp(14.0, 4.0, box.progress)
        bounds = Graphene.Rect().init(x, y, width, height)
        clip = Gsk.RoundedRect()
        clip.init_from_rect(bounds, radius)
        snapshot.push_rounded_clip(clip)
        if isinstance(paintable, Gdk.Texture) and hasattr(snapshot, "append_scaled_texture"):
            snapshot.append_scaled_texture(paintable, Gsk.ScalingFilter.TRILINEAR, bounds)
        else:
            snapshot.save()
            snapshot.translate(Graphene.Point().init(x, y))
            paintable.snapshot(snapshot, width, height)
            snapshot.restore()
        snapshot.pop()


class Lightbox(Gtk.Widget):
    """A window-local picture viewer. Use :meth:`Lightbox.for_window` to get one.

    ``open(items, index)`` shows ``items[index]`` grown from its ``source``
    widget; closing returns focus there. ``closed`` is emitted when done.
    """

    __gtype_name__ = "LumaLightbox"
    __gsignals__ = {"closed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self) -> None:
        super().__init__(visible=False, can_focus=True, focusable=False,
                         accessible_role=Gtk.AccessibleRole.DIALOG)
        self.add_css_class("luma-lightbox")
        self.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.MODAL], ["Picture viewer", True])
        self.items: list[LightboxItem] = []
        self.index = 0
        self.progress = 0.0
        self.zoom = 1.0
        self.pan = (0.0, 0.0)
        self._textures: dict[tuple, Gdk.Texture] = {}
        self._natural: dict[int, tuple[int, int]] = {}
        self._pending: set[tuple] = set()
        self._generation = 0
        self._worker: ThreadPoolExecutor | None = None
        self._animation: Adw.TimedAnimation | None = None
        self._return_focus: Gtk.Widget | None = None
        self._host: Gtk.Widget | None = None
        self._frames: GdkPixbuf.PixbufAnimationIter | None = None
        self._frame_source = 0
        self._frame_texture: Gdk.Texture | None = None
        self._closing = False

        self.backdrop = Gtk.Box(hexpand=True, vexpand=True)
        self.backdrop.add_css_class("luma-lightbox-backdrop")
        self.backdrop.set_parent(self)

        stage = Gtk.Overlay(hexpand=True, vexpand=True)
        stage.set_parent(self)
        self._stage = stage
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.canvas = _Canvas(self)
        column.append(self.canvas)
        self.bar = self._build_bar()
        column.append(self.bar)
        stage.set_child(column)

        self.status = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER,
                              valign=Gtk.Align.CENTER, visible=False)
        self.status.add_css_class("luma-lightbox-status")
        self.spinner = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)
        self.spinner.set_size_request(32, 32)
        self.status_label = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=40)
        self.status_label.add_css_class("luma-lightbox-status-text")
        self.retry_button = Gtk.Button(label="Try Again", halign=Gtk.Align.CENTER, visible=False)
        self.retry_button.add_css_class("luma-lightbox-action")
        self.retry_button.connect("clicked", self._retry)
        for widget in (self.spinner, self.status_label, self.retry_button):
            self.status.append(widget)
        stage.add_overlay(self.status)

        self.close_button = self._round_button("window-close-symbolic", "Close", Gtk.Align.END, Gtk.Align.START)
        self.close_button.connect("clicked", lambda *_: self.close())
        stage.add_overlay(self.close_button)
        self.previous_button = self._round_button("pan-start-symbolic", "Previous picture", Gtk.Align.START, Gtk.Align.CENTER)
        self.previous_button.connect("clicked", lambda *_: self.step(-1))
        stage.add_overlay(self.previous_button)
        self.next_button = self._round_button("pan-end-symbolic", "Next picture", Gtk.Align.END, Gtk.Align.CENTER)
        self.next_button.connect("clicked", lambda *_: self.step(1))
        stage.add_overlay(self.next_button)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key_pressed)
        self.add_controller(keys)
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect("released", self._clicked)
        self.canvas.add_controller(click)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._scrolled)
        self.canvas.add_controller(scroll)
        self._pointer = (0.0, 0.0)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, y: setattr(self, "_pointer", (x, y)))
        self.canvas.add_controller(motion)
        pinch = Gtk.GestureZoom()
        pinch.connect("begin", lambda *_: setattr(self, "_pinch_zoom", self.zoom))
        pinch.connect("scale-changed", self._pinched)
        self.canvas.add_controller(pinch)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", lambda *_: setattr(self, "_drag_pan", self.pan))
        drag.connect("drag-update", self._dragged)
        drag.connect("drag-end", self._drag_ended)
        self.canvas.add_controller(drag)

    # ── Construction helpers ────────────────────────────────────────────────

    @staticmethod
    def _round_button(icon: str, label: str, halign: Gtk.Align, valign: Gtk.Align) -> Gtk.Button:
        button = Gtk.Button(icon_name=icon, halign=halign, valign=valign)
        button.add_css_class("luma-lightbox-round")
        button.set_tooltip_text(label)
        button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        return button

    def _build_bar(self) -> Gtk.Widget:
        bar = Gtk.Box(spacing=12)
        bar.add_css_class("luma-lightbox-bar")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True, valign=Gtk.Align.CENTER)
        self.title_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, single_line_mode=True)
        self.title_label.add_css_class("luma-lightbox-title")
        self.subtitle_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, single_line_mode=True)
        self.subtitle_label.add_css_class("luma-lightbox-subtitle")
        text.append(self.title_label)
        text.append(self.subtitle_label)
        bar.append(text)
        actions = Adw.WrapBox(child_spacing=6, line_spacing=6, halign=Gtk.Align.END) if hasattr(Adw, "WrapBox") \
            else Gtk.Box(spacing=6, halign=Gtk.Align.END)
        self.save_button = Gtk.Button(label="Save As…")
        self.save_button.connect("clicked", self._save_as)
        self.copy_button = Gtk.Button(label="Copy")
        self.copy_button.connect("clicked", self._copy)
        self.open_with_button = Gtk.Button(label="Open With…")
        self.open_with_button.connect("clicked", self._open_with)
        for button in (self.save_button, self.copy_button, self.open_with_button):
            button.add_css_class("luma-lightbox-action")
            actions.append(button)
        bar.append(actions)
        return bar

    @classmethod
    def for_window(cls, window: Gtk.Window) -> "Lightbox":
        """The window's lightbox, laid over all of its content, created on first use."""
        existing = getattr(window, "_luma_lightbox", None)
        if existing is not None:
            return existing
        content = window.get_content() if isinstance(window, Adw.ApplicationWindow | Adw.Window) else window.get_child()
        overlay = Gtk.Overlay()
        if content is not None:
            if isinstance(window, Adw.ApplicationWindow | Adw.Window):
                window.set_content(None)
            else:
                window.set_child(None)
            overlay.set_child(content)
        lightbox = cls()
        overlay.add_overlay(lightbox)
        overlay.set_measure_overlay(lightbox, False)
        if isinstance(window, Adw.ApplicationWindow | Adw.Window):
            window.set_content(overlay)
        else:
            window.set_child(overlay)
        lightbox._host = content
        window._luma_lightbox = lightbox
        return lightbox

    # ── Layout ──────────────────────────────────────────────────────────────

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        child = self.get_first_child()
        while child is not None:
            child.allocate(width, height, baseline, None)
            child = child.get_next_sibling()

    def do_measure(self, orientation, for_size):
        # Laid over the window's content; it never asks the window for space.
        return 0, 0, -1, -1

    def area(self) -> tuple[float, float, float, float]:
        """Where a fitted picture may go, in canvas coordinates."""
        width, height = self.canvas.get_width(), self.canvas.get_height()
        margin = EDGE_MARGIN if width > 520 else 8
        return (margin, margin, max(1.0, width - 2 * margin), max(1.0, height - 2 * margin))

    def natural_size(self, index: int | None = None) -> tuple[int, int]:
        index = self.index if index is None else index
        return self._natural.get(index, (0, 0))

    def fitted_rect(self) -> tuple[float, float, float, float] | None:
        width, height = self.natural_size()
        if not width:
            return None
        ax, ay, aw, ah = self.area()
        x, y, w, h = fit_rect(width, height, aw, ah)
        return (ax + x, ay + y, w, h)

    def picture_rect(self) -> tuple[float, float, float, float] | None:
        fitted = self.fitted_rect()
        if fitted is None:
            return None
        fx, fy, fw, fh = fitted
        w, h = fw * self.zoom, fh * self.zoom
        cx, cy = fx + fw / 2 + self.pan[0], fy + fh / 2 + self.pan[1]
        zoomed = (cx - w / 2, cy - h / 2, w, h)
        origin = self._origin_rect()
        if origin is None or self.progress >= 1:
            return zoomed
        return tuple(lerp(o, z, self.progress) for o, z in zip(origin, zoomed))

    def _origin_rect(self) -> tuple[float, float, float, float] | None:
        item = self.items[self.index] if 0 <= self.index < len(self.items) else None
        source = item.source if item else None
        if source is None or not source.get_mapped():
            return None
        found, bounds = source.compute_bounds(self.canvas)
        if not found:
            return None
        # Grow from the thumbnail with the picture's shape, as the thumbnail crops it.
        width, height = self.natural_size()
        if width and height:
            x, y, w, h = fit_rect(width, height, bounds.get_width(), bounds.get_height())
            scale = max(bounds.get_width() / max(w, 1), bounds.get_height() / max(h, 1))
            w, h = w * scale, h * scale
            return (bounds.get_x() + (bounds.get_width() - w) / 2, bounds.get_y() + (bounds.get_height() - h) / 2, w, h)
        return (bounds.get_x(), bounds.get_y(), bounds.get_width(), bounds.get_height())

    # ── Opening and closing ─────────────────────────────────────────────────

    @property
    def is_open(self) -> bool:
        return self.get_visible() and not self._closing

    def open(self, items: list[LightboxItem], index: int = 0, *, return_focus: Gtk.Widget | None = None) -> None:
        if not items:
            return
        self._cancel_animation()
        self._closing = False
        self.items = list(items)
        self.index = max(0, min(index, len(self.items) - 1))
        self._return_focus = return_focus or self.items[self.index].source
        self._worker = self._worker or ThreadPoolExecutor(max_workers=2, thread_name_prefix="luma-lightbox")
        self._generation += 1
        self._reset_view()
        self._set_background_inert(True)
        self.set_visible(True)
        self._show_current()
        self.progress = 0.0
        self._animate(0.0, 1.0, OPEN_MS, None)
        self.close_button.grab_focus()

    def close(self) -> None:
        if not self.get_visible() or self._closing:
            return
        self._closing = True
        self._stop_frames()
        self.zoom, self.pan = 1.0, (0.0, 0.0)
        self._animate(self.progress, 0.0, OPEN_MS, self._finish_close)

    def _finish_close(self) -> None:
        self.set_visible(False)
        self._closing = False
        self._set_background_inert(False)
        self._generation += 1
        self._textures.clear()
        self._natural.clear()
        self._pending.clear()
        self._frame_texture = None
        if self._worker is not None:
            self._worker.shutdown(wait=False, cancel_futures=True)
            self._worker = None
        focus, self._return_focus = self._return_focus, None
        self.items = []
        if focus is not None and focus.get_mapped():
            focus.grab_focus()
        self.emit("closed")

    def _set_background_inert(self, inert: bool) -> None:
        """Keep focus and pointer inside the lightbox while it is open."""
        host = self._host
        if host is None:
            return
        host.set_can_focus(not inert)
        host.set_can_target(not inert)
        host.update_state([Gtk.AccessibleState.HIDDEN], [inert])

    def _animate(self, start: float, end: float, milliseconds: int, done: Callable[[], None] | None) -> None:
        self._cancel_animation()
        settings = Gtk.Settings.get_default()
        if settings is None or not settings.get_property("gtk-enable-animations") or milliseconds <= 0:
            self._set_progress(end)
            if done:
                done()
            return
        target = Adw.CallbackAnimationTarget.new(self._set_progress)
        animation = Adw.TimedAnimation.new(self, start, end, milliseconds, target)
        animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        if done:
            animation.connect("done", lambda *_: done())
        self._animation = animation
        animation.play()

    def _cancel_animation(self) -> None:
        if self._animation is not None:
            self._animation.pause()
            self._animation = None

    def _set_progress(self, value: float) -> None:
        self.progress = max(0.0, min(1.0, value))
        self.backdrop.set_opacity(self.progress)
        for widget in (self.bar, self.close_button, self.previous_button, self.next_button, self.status):
            widget.set_opacity(self.progress)
        self.canvas.queue_draw()

    # ── Navigation ──────────────────────────────────────────────────────────

    def step(self, delta: int) -> bool:
        target = self.index + delta
        if not self.is_open or not 0 <= target < len(self.items):
            return False
        self.index = target
        self._reset_view()
        self._show_current()
        return True

    def _reset_view(self) -> None:
        self.zoom, self.pan = 1.0, (0.0, 0.0)

    def _show_current(self) -> None:
        item = self.items[self.index]
        count = len(self.items)
        self.title_label.set_label(item.title)
        self.subtitle_label.set_label(item.subtitle if count == 1 else
                                      " · ".join(part for part in (item.subtitle, f"{self.index + 1} of {count}") if part))
        self.previous_button.set_visible(self.index > 0)
        self.next_button.set_visible(self.index < count - 1)
        ready = item.state == "ready" and item.path is not None
        for button in (self.save_button, self.copy_button, self.open_with_button):
            button.set_sensitive(ready)
        self.canvas.update_property([Gtk.AccessibleProperty.LABEL], [item.accessible_name])
        self._stop_frames()
        # Only the shown picture and its neighbours keep pixels.
        keep = neighbours(self.index, count)
        for key in list(self._textures):
            if key[0] not in keep:
                del self._textures[key]
        for index in keep:
            self._request(index, full=False)
        self._sync_status()
        self.canvas.queue_draw()

    def _sync_status(self) -> None:
        item = self.items[self.index]
        waiting = item.state == "ready" and self.current_paintable() is None and item.path is not None
        failed = item.state == "ready" and item.path is not None and ("failed", self.index) in self._pending
        show = item.state != "ready" or item.path is None or waiting or failed
        self.status.set_visible(show)
        self.spinner.set_visible(item.state == "loading" or waiting)
        if failed:
            text = "This picture can't be shown here. Open With… lets another app try."
        elif waiting:
            text = ""
        else:
            text = item.message or ("Loading…" if item.state == "loading" else "This picture is no longer available.")
        self.status_label.set_label(text)
        self.status_label.set_visible(bool(text))
        self.retry_button.set_visible(item.state == "unavailable" and item.retry is not None)
        if failed:
            self.open_with_button.set_sensitive(True)

    def update_item(self, index: int, item: LightboxItem) -> None:
        """Replace one picture, as when a download finishes while it is shown."""
        if not 0 <= index < len(self.items):
            return
        item.source = item.source or self.items[index].source
        self.items[index] = item
        for key in [key for key in self._textures if key[0] == index]:
            del self._textures[key]
        self._natural.pop(index, None)
        if index == self.index:
            self._show_current()
        elif index in neighbours(self.index, len(self.items)):
            self._request(index, full=False)

    # ── Pixels ──────────────────────────────────────────────────────────────

    def decoded_indices(self) -> set[int]:
        return {key[0] for key in self._textures}

    def current_paintable(self) -> Gdk.Paintable | None:
        if self._frame_texture is not None:
            return self._frame_texture
        full = self._textures.get((self.index, True))
        return full or self._textures.get((self.index, False))

    def _request(self, index: int, *, full: bool) -> None:
        item = self.items[index]
        key = (index, full)
        if item.state != "ready" or item.path is None or key in self._textures or key in self._pending or self._worker is None:
            return
        if item.content_type == "image/gif" and index == self.index and not full:
            if self._start_frames(item.path):
                return
        scale = max(1, self.get_scale_factor())
        if full:
            width = height = FULL_DECODE_LIMIT
        else:
            _x, _y, aw, ah = self.area()
            width, height = int(max(aw, 320) * scale), int(max(ah, 240) * scale)
        self._pending.add(key)
        generation = self._generation
        future = self._worker.submit(_decode, item.path, width, height)
        future.add_done_callback(lambda done: GLib.idle_add(self._decoded, generation, key, done))

    def _decoded(self, generation: int, key: tuple, future) -> bool:
        self._pending.discard(key)
        if generation != self._generation or future.cancelled():
            return GLib.SOURCE_REMOVE
        pixels = future.result() if future.exception() is None else None
        index = key[0]
        if pixels is None:
            self._pending.add(("failed", index))
        elif index in neighbours(self.index, len(self.items)) and (not key[1] or index == self.index):
            self._textures[key] = _texture(pixels)
            self._natural[index] = pixels[5]
        if index == self.index:
            self._sync_status()
            self.canvas.queue_draw()
        return GLib.SOURCE_REMOVE

    def _start_frames(self, path: Path) -> bool:
        try:
            animation = GdkPixbuf.PixbufAnimation.new_from_file(str(path))
        except GLib.Error:
            return False
        if animation.is_static_image():
            return False
        self._natural[self.index] = (animation.get_width(), animation.get_height())
        self._frames = animation.get_iter(None)
        self._advance_frame()
        return True

    def _advance_frame(self) -> bool:
        self._frame_source = 0
        frames = self._frames
        if frames is None or not self.get_visible():
            return GLib.SOURCE_REMOVE
        frames.advance(None)
        pixbuf = frames.get_pixbuf()
        memory_format = Gdk.MemoryFormat.R8G8B8A8 if pixbuf.get_has_alpha() else Gdk.MemoryFormat.R8G8B8
        self._frame_texture = Gdk.MemoryTexture.new(pixbuf.get_width(), pixbuf.get_height(), memory_format,
                                                    pixbuf.read_pixel_bytes(), pixbuf.get_rowstride())
        self._sync_status()
        self.canvas.queue_draw()
        delay = frames.get_delay_time()
        if delay >= 0:
            self._frame_source = GLib.timeout_add(max(20, delay), self._advance_frame)
        return GLib.SOURCE_REMOVE

    def _stop_frames(self) -> None:
        if self._frame_source:
            GLib.source_remove(self._frame_source)
            self._frame_source = 0
        self._frames = None
        self._frame_texture = None

    # ── Zoom and pan ────────────────────────────────────────────────────────

    def _canvas_centre(self) -> tuple[float, float]:
        fitted = self.fitted_rect()
        if fitted is None:
            return (self.canvas.get_width() / 2, self.canvas.get_height() / 2)
        return (fitted[0] + fitted[2] / 2, fitted[1] + fitted[3] / 2)

    def set_zoom(self, zoom: float, point: tuple[float, float] | None = None) -> None:
        fitted = self.fitted_rect()
        if fitted is None:
            return
        point = point or self._canvas_centre()
        self.zoom, self.pan = zoom_about(self.zoom, self.pan, zoom / self.zoom, point, self._canvas_centre())
        if self.zoom <= MIN_ZOOM:
            self.zoom, self.pan = MIN_ZOOM, (0.0, 0.0)
        _ax, _ay, aw, ah = self.area()
        self.pan = clamp_pan(self.pan, (fitted[2] * self.zoom, fitted[3] * self.zoom), (aw, ah))
        if self.zoom > MIN_ZOOM:
            self._request(self.index, full=True)
        self.canvas.queue_draw()

    def actual_size_zoom(self) -> float:
        """The zoom at which one picture pixel is one device pixel."""
        fitted = self.fitted_rect()
        width, _height = self.natural_size()
        if fitted is None or not fitted[2]:
            return 1.0
        return max(MIN_ZOOM, width / max(1, self.get_scale_factor()) / fitted[2])

    def toggle_actual_size(self, point: tuple[float, float] | None = None) -> None:
        target = MIN_ZOOM if self.zoom > MIN_ZOOM else max(2.0, self.actual_size_zoom())
        self.set_zoom(min(MAX_ZOOM, target), point)

    def _clicked(self, gesture: Gtk.GestureClick, presses: int, x: float, y: float) -> None:
        rect = self.picture_rect()
        inside = rect is not None and rect[0] <= x <= rect[0] + rect[2] and rect[1] <= y <= rect[1] + rect[3]
        if presses == 2 and inside:
            self.toggle_actual_size((x, y))
        elif presses == 1 and not inside and not getattr(self, "_dragged_far", False):
            self.close()

    def _scrolled(self, controller: Gtk.EventControllerScroll, dx: float, dy: float) -> bool:
        state = controller.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            self.set_zoom(self.zoom * (0.9 if dy > 0 else 1.1), self._pointer)
            return True
        if self.zoom > MIN_ZOOM:
            fitted = self.fitted_rect()
            _ax, _ay, aw, ah = self.area()
            self.pan = clamp_pan((self.pan[0] - dx * 30, self.pan[1] - dy * 30),
                                 (fitted[2] * self.zoom, fitted[3] * self.zoom), (aw, ah))
            self.canvas.queue_draw()
            return True
        return False

    def _pinched(self, gesture: Gtk.GestureZoom, scale: float) -> None:
        ok, x, y = gesture.get_bounding_box_center()
        self.set_zoom(getattr(self, "_pinch_zoom", self.zoom) * scale, (x, y) if ok else None)

    def _dragged(self, gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        self._dragged_far = abs(dx) > 6 or abs(dy) > 6
        if self.zoom > MIN_ZOOM:
            fitted = self.fitted_rect()
            _ax, _ay, aw, ah = self.area()
            start = getattr(self, "_drag_pan", self.pan)
            self.pan = clamp_pan((start[0] + dx, start[1] + dy), (fitted[2] * self.zoom, fitted[3] * self.zoom), (aw, ah))
            self.canvas.queue_draw()

    def _drag_ended(self, gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        if self.zoom <= MIN_ZOOM and abs(dx) > SWIPE_DISTANCE and abs(dx) > abs(dy) * 1.5:
            forward = dx < 0
            if self.get_direction() == Gtk.TextDirection.RTL:
                forward = not forward
            self.step(1 if forward else -1)
        GLib.idle_add(lambda: setattr(self, "_dragged_far", False))

    def _key_pressed(self, _controller, keyval: int, _code: int, state) -> bool:
        if not self.is_open:
            return False
        rtl = self.get_direction() == Gtk.TextDirection.RTL
        if keyval == Gdk.KEY_Escape:
            if self.zoom > MIN_ZOOM:
                self.set_zoom(MIN_ZOOM)
            else:
                self.close()
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Right) and self.zoom <= MIN_ZOOM:
            self.step((-1 if keyval == Gdk.KEY_Left else 1) * (-1 if rtl else 1))
            return True
        if keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self.set_zoom(self.zoom * 1.5)
            return True
        if keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            self.set_zoom(self.zoom / 1.5)
            return True
        if keyval in (Gdk.KEY_0, Gdk.KEY_KP_0):
            self.set_zoom(MIN_ZOOM)
            return True
        return False

    # ── Actions ─────────────────────────────────────────────────────────────

    def _retry(self, *_args) -> None:
        item = self.items[self.index]
        if item.retry is not None:
            item.retry()

    def _save_as(self, *_args) -> None:
        item = self.items[self.index]
        if item.path is None:
            return
        dialog = Gtk.FileDialog(title="Save Picture", initial_name=item.name or item.path.name, modal=True)
        source = item.path

        def chosen(dialog, result):
            try:
                target = dialog.save_finish(result)
            except GLib.Error:
                return
            if target is not None and target.get_path():
                try:
                    shutil.copyfile(source, target.get_path())
                except OSError:
                    pass
        dialog.save(self.get_root(), None, chosen)

    def _copy(self, *_args) -> None:
        item = self.items[self.index]
        if item.path is None:
            return
        try:
            texture = Gdk.Texture.new_from_filename(str(item.path))
        except GLib.Error:
            texture = self.current_paintable() if isinstance(self.current_paintable(), Gdk.Texture) else None
        if texture is not None:
            self.get_clipboard().set_texture(texture)

    def _open_with(self, *_args) -> None:
        item = self.items[self.index]
        if item.path is None:
            return
        launcher = Gtk.FileLauncher(file=Gio.File.new_for_path(str(item.path)), always_ask=True)
        launcher.launch(self.get_root(), None, None)


__all__ = ["Lightbox", "LightboxItem", "clamp_pan", "fit_rect", "neighbours", "zoom_about"]
