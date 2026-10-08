# SPDX-License-Identifier: Apache-2.0
"""LumaUI media family: shared plumbing for the media, image and measure parts.

- The family's tokens live in config/shared/design-tokens.d/media.json: the
  generator turns them into `--lumaui-media-*` variables, `@luma_*` colours
  and `lumaui_tokens.MEDIA`; the kit loads luma-appkit-media.css after the
  base sheet (install_lumaui()).
- `colour(widget, role)` resolves an `@luma_*` role for drawing (snapshot or
  Cairo), so parts that draw their own pixels never hold a literal.
- `glyph_path(name)` is a Lucide glyph as a `Gsk.Path`, for glyphs a part must
  fill (a favourite heart) or draw thousands of times.
- `TextureLoader` decodes pictures off the main loop into a small LRU cache,
  with a generation check so a recycled tile never shows a stale picture.
"""
from __future__ import annotations

import os
import re
import threading
import warnings
import weakref
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

# ── Drawing helpers ────────────────────────────────────────────────────────

# GTK 4.10 deprecated the style context, but it is still the only way to read
# a named colour for drawing; the warning is silenced for this module only.
warnings.filterwarnings("ignore", category=DeprecationWarning, module=__name__)

def colour(widget: Gtk.Widget, role: str, alpha: float | None = None) -> Gdk.RGBA:
    """An `@luma_*` colour role for drawing; the widget's ink when the role is missing."""
    found, value = False, None
    try:
        found, value = widget.get_style_context().lookup_color(role)
    except (AttributeError, TypeError):
        pass
    if not found or value is None:
        value = widget.get_color()
    if alpha is not None:
        value = value.copy()
        value.alpha *= alpha
    return value


def rgba(red: float, green: float, blue: float, alpha: float = 1.0) -> Gdk.RGBA:
    value = Gdk.RGBA()
    value.red, value.green, value.blue, value.alpha = red, green, blue, alpha
    return value


def rect(x: float, y: float, width: float, height: float) -> Graphene.Rect:
    return Graphene.Rect().init(x, y, max(0.0, width), max(0.0, height))


def rounded(bounds: Graphene.Rect, tl: float, tr: float | None = None, br: float | None = None,
            bl: float | None = None) -> Gsk.RoundedRect:
    """A rounded rectangle; one radius for all corners, or four (top-left, clockwise)."""
    tr = tl if tr is None else tr
    br = tl if br is None else br
    bl = tl if bl is None else bl
    shape = Gsk.RoundedRect()
    shape.init(bounds, *(Graphene.Size().init(r, r) for r in (tl, tr, br, bl)))
    return shape


_glyphs: dict[str, Gsk.Path | None] = {}
_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def _shape_path(tag: str, attributes: str) -> str:
    """An SVG rect, circle or ellipse as path data (Lucide draws pause, square and some dots this way)."""
    values = {k: float(v) for k, v in re.findall(r'(?:^|\s)(x|y|width|height|rx|ry|cx|cy|r)="([-\d.]+)"', attributes)}
    if tag == "rect":
        x, y, w, h = values.get("x", 0.0), values.get("y", 0.0), values.get("width", 0.0), values.get("height", 0.0)
        rx = min(values.get("rx", values.get("ry", 0.0)), w / 2, h / 2)
        if rx <= 0:
            return f" M{x} {y} h{w} v{h} h{-w} Z"
        return (f" M{x + rx} {y} h{w - 2 * rx} a{rx} {rx} 0 0 1 {rx} {rx} v{h - 2 * rx}"
                f" a{rx} {rx} 0 0 1 {-rx} {rx} h{-(w - 2 * rx)} a{rx} {rx} 0 0 1 {-rx} {-rx}"
                f" v{-(h - 2 * rx)} a{rx} {rx} 0 0 1 {rx} {-rx} Z")
    cx, cy = values.get("cx", 0.0), values.get("cy", 0.0)
    rx = values.get("r", values.get("rx", 0.0))
    ry = values.get("r", values.get("ry", rx))
    if rx <= 0:
        return ""
    return f" M{cx - rx} {cy} a{rx} {ry} 0 1 0 {2 * rx} 0 a{rx} {ry} 0 1 0 {-2 * rx} 0 Z"


def _absolute_start(d: str) -> str:
    """A path's first moveto is absolute even when written "m"; once joined to another path it would not be.

    "m17 2 4 4-4 4" becomes "M17 2 l4 4-4 4" (the pairs after a moveto are linetos of the same case).
    """
    d = d.strip()
    if not d.startswith("m"):
        return d
    match = re.match(rf"m\s*({_NUMBER})[\s,]*({_NUMBER})(.*)", d, re.S)
    if not match:
        return "M" + d[1:]
    rest = match.group(3).lstrip(" ,")
    if rest and not rest[0].isalpha():
        rest = "l" + rest
    return f"M{match.group(1)} {match.group(2)} {rest}"


def glyph_path(lucide: str) -> Gsk.Path | None:
    """A Lucide glyph's geometry (24 × 24 units) as one `Gsk.Path`, or None if it was never imported."""
    if lucide in _glyphs:
        return _glyphs[lucide]
    from . import icons

    path = None
    for folder in icons.search_paths():
        svg = folder / f"{icons.icon_name(lucide)}.svg"
        if svg.is_file():
            text = svg.read_text()
            data = " ".join(_absolute_start(d) for d in re.findall(r'\sd="([^"]+)"', text))
            data += "".join(_shape_path(tag, attrs) for tag, attrs in re.findall(r"<(rect|circle|ellipse)\s([^>]*)>", text))
            if data.strip():
                path = Gsk.Path.parse(data)
            break
    _glyphs[lucide] = path
    return path


def draw_glyph(snapshot: Gtk.Snapshot, lucide: str, x: float, y: float, size: float, ink: Gdk.RGBA,
               *, filled: bool = False, stroke: float = 1.6) -> None:
    """Draw a Lucide glyph `size` px square at (x, y): stroked like the icon theme, or filled."""
    path = glyph_path(lucide)
    if path is None:
        return
    snapshot.save()
    snapshot.translate(Graphene.Point().init(x, y))
    snapshot.scale(size / 24.0, size / 24.0)
    if filled:
        snapshot.append_fill(path, Gsk.FillRule.WINDING, ink)
    line = Gsk.Stroke.new(stroke)
    line.set_line_cap(Gsk.LineCap.ROUND)
    line.set_line_join(Gsk.LineJoin.ROUND)
    snapshot.append_stroke(path, line, ink)
    snapshot.restore()


def text_layout(widget: Gtk.Widget, text: str, size: float, weight: int = 400, *, tabular: bool = False):
    """A Pango layout in the kit face at `size` px and `weight`, for parts that draw their own text."""
    gi.require_version("Pango", "1.0")
    from gi.repository import Pango

    layout = widget.create_pango_layout(text)
    description = widget.get_pango_context().get_font_description() or Pango.FontDescription()
    description = description.copy()
    description.set_absolute_size(size * Pango.SCALE)
    description.set_weight(weight)
    layout.set_font_description(description)
    if tabular:
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_font_features_new("tnum=1"))
        layout.set_attributes(attrs)
    return layout


def append_layout(snapshot: Gtk.Snapshot, layout, x: float, y: float, ink: Gdk.RGBA) -> None:
    snapshot.save()
    snapshot.translate(Graphene.Point().init(x, y))
    snapshot.append_layout(layout, ink)
    snapshot.restore()


def cover_rect(texture_width: float, texture_height: float, box: Graphene.Rect) -> Graphene.Rect:
    """Where a picture goes so it covers `box` (object-fit: cover), centred."""
    if texture_width <= 0 or texture_height <= 0:
        return box
    scale = max(box.get_width() / texture_width, box.get_height() / texture_height)
    width, height = texture_width * scale, texture_height * scale
    return rect(box.get_x() + (box.get_width() - width) / 2, box.get_y() + (box.get_height() - height) / 2,
                width, height)


def contain_rect(texture_width: float, texture_height: float, box: Graphene.Rect) -> Graphene.Rect:
    """Where a picture goes so all of it fits `box` (object-fit: contain), centred."""
    if texture_width <= 0 or texture_height <= 0:
        return box
    scale = min(box.get_width() / texture_width, box.get_height() / texture_height)
    width, height = texture_width * scale, texture_height * scale
    return rect(box.get_x() + (box.get_width() - width) / 2, box.get_y() + (box.get_height() - height) / 2,
                width, height)


def grayscale_matrix(amount: float) -> tuple[Graphene.Matrix, Graphene.Vec4]:
    """A colour matrix for CSS `grayscale(amount)`."""
    a = max(0.0, min(1.0, amount))
    r, g, b = 0.2126, 0.7152, 0.0722
    rows = [
        (r + (1 - r) * (1 - a), g - g * (1 - a), b - b * (1 - a)),
        (r - r * (1 - a), g + (1 - g) * (1 - a), b - b * (1 - a)),
        (r - r * (1 - a), g - g * (1 - a), b + (1 - b) * (1 - a)),
    ]
    # Graphene matrices multiply row vectors: column j of the CSS matrix is row j here.
    values = [rows[0][0], rows[1][0], rows[2][0], 0,
              rows[0][1], rows[1][1], rows[2][1], 0,
              rows[0][2], rows[1][2], rows[2][2], 0,
              0, 0, 0, 1]
    return Graphene.Matrix().init_from_float(values), Graphene.Vec4().init(0, 0, 0, 0)


def brightness_matrix(amount: float) -> tuple[Graphene.Matrix, Graphene.Vec4]:
    """A colour matrix for CSS `brightness(amount)`."""
    values = [amount, 0, 0, 0, 0, amount, 0, 0, 0, 0, amount, 0, 0, 0, 0, 1]
    return Graphene.Matrix().init_from_float(values), Graphene.Vec4().init(0, 0, 0, 0)


# ── Pictures, decoded off the main loop ────────────────────────────────────

class TextureRequest:
    """A cancellable subscription; cancelling never touches a GTK widget."""

    def __init__(self, loader: "TextureLoader", key: tuple, ident: int) -> None:
        self._loader = weakref.ref(loader)
        self._key, self._ident = key, ident

    def cancel(self) -> None:
        loader = self._loader()
        if loader is not None:
            loader._cancel(self._key, self._ident)


class TextureLoader:
    """Bounded asynchronous previews, coalesced by path and decode size.

    Output bounds apply to the *long* edge and pixel count, including panoramas.
    The LRU has a byte budget; the finite waiting queue does not contain decoded
    pixels. Only worker-count results can await main-loop delivery. Callers may
    cancel their subscription when a tile leaves the viewport.
    """

    _shared: "TextureLoader | None" = None
    MAX_SIDE = 1024
    MAX_PIXELS = 1024 * 1024
    MAX_SOURCE_PIXELS = 100_000_000

    def __init__(self, capacity: int = 600, workers: int | None = None, *,
                 byte_budget: int = 64 * 1024 * 1024, pending_limit: int = 64) -> None:
        if capacity < 1 or byte_budget < 1 or pending_limit < 1:
            raise ValueError("texture budgets must be positive")
        self._cache: OrderedDict[tuple, Gdk.Texture] = OrderedDict()
        self._costs: dict[tuple, int] = {}
        self._cache_bytes = 0
        self._capacity, self._byte_budget = capacity, byte_budget
        self._workers = max(1, workers or min(2, os.cpu_count() or 2))
        self._pending_limit = pending_limit
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=self._workers, thread_name_prefix="lumaui-media")
        self._pending: dict[tuple, dict[int, Callable]] = {}
        self._waiting: OrderedDict[tuple, None] = OrderedDict()
        self._active: set[tuple] = set()
        self._next_ident = 0
        self._closed = False
        self._available: dict[int, weakref.WeakMethod] = {}
        self._available_idle = 0

    @classmethod
    def shared(cls) -> "TextureLoader":
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    @staticmethod
    def bucket(side: float) -> int:
        """Round nearby sizes together without exceeding the preview ceiling."""
        side = min(TextureLoader.MAX_SIDE, max(32, int(side)))
        step = 64 if side <= 512 else 256
        return min(TextureLoader.MAX_SIDE, (side + step - 1) // step * step)

    def cached(self, source: str, side: float) -> Gdk.Texture | None:
        key = (source, self.bucket(side))
        with self._lock:
            texture = self._cache.get(key)
            if texture is not None:
                self._cache.move_to_end(key)
            return texture

    def request(self, source: str, side: float,
                callback: Callable[[Gdk.Texture | None], None]) -> TextureRequest | None:
        texture = self.cached(source, side)
        if texture is not None:
            callback(texture)
            return None
        key = (source, self.bucket(side))
        with self._lock:
            if (self._closed or len(self._pending.get(key, {})) >= self._pending_limit or
                    (key not in self._pending and len(self._pending) >= self._pending_limit)):
                rejected = True
            else:
                rejected = False
                self._next_ident += 1
                ident = self._next_ident
                if key not in self._pending:
                    self._pending[key] = {}
                    self._waiting[key] = None
                self._pending[key][ident] = callback
        if rejected:
            callback(None)
            return None
        self._pump()
        return TextureRequest(self, key, ident)

    def watch_available(self, callback: Callable) -> int | None:
        """Weakly observe freed admission capacity; never queue images or work.

        The callback must be a bound method. Viewport owners unregister as soon
        as a request is admitted or their widget leaves the viewport.
        """
        with self._lock:
            if self._closed:
                return None
            self._next_ident += 1
            ident = self._next_ident
            self._available[ident] = weakref.WeakMethod(callback)
            return ident

    def unwatch_available(self, ident: int | None) -> None:
        with self._lock:
            self._available.pop(ident, None)

    def _capacity_changed(self) -> None:
        if not self._available_idle and not self._closed:
            self._available_idle = GLib.idle_add(self._notify_available)

    def _notify_available(self) -> bool:
        self._available_idle = 0
        with self._lock:
            listeners = list(self._available.items())
        for ident, reference in listeners:
            callback = reference()
            if callback is None:
                self.unwatch_available(ident)
            else:
                callback()
        return False

    def _cancel(self, key: tuple, ident: int) -> None:
        freed = False
        with self._lock:
            subscribers = self._pending.get(key)
            if subscribers is not None:
                subscribers.pop(ident, None)
                if not subscribers:
                    self._pending.pop(key, None)
                    self._waiting.pop(key, None)
                    freed = True
        if freed:
            self._capacity_changed()

    def _pump(self) -> None:
        with self._lock:
            ready = []
            while not self._closed and self._waiting and len(self._active) < self._workers:
                key, _ = self._waiting.popitem(last=False)
                # A new subscriber may reuse a cancelled but still running key.
                if key in self._active:
                    continue
                self._active.add(key)
                ready.append(key)
        for key in ready:
            self._pool.submit(self._decode, key)

    def _decode(self, key: tuple) -> None:
        source, side = key
        texture = None
        try:
            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf

            _format, width, height = GdkPixbuf.Pixbuf.get_file_info(source)
            if not width or not height or width * height > self.MAX_SOURCE_PIXELS:
                raise ValueError("missing or oversized image dimensions")
            scale = min(1.0, side / min(width, height), self.MAX_SIDE / max(width, height),
                        (self.MAX_PIXELS / (width * height)) ** 0.5)
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                source, max(1, int(width * scale)), max(1, int(height * scale)), True)
            pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
            texture = Gdk.MemoryTexture.new(
                pixbuf.get_width(), pixbuf.get_height(),
                Gdk.MemoryFormat.R8G8B8A8 if pixbuf.get_has_alpha() else Gdk.MemoryFormat.R8G8B8,
                pixbuf.read_pixel_bytes(), pixbuf.get_rowstride())
        except Exception:  # noqa: BLE001 - unreadable pictures show the empty tile
            texture = None
        GLib.idle_add(self._finish, key, texture)

    def _finish(self, key: tuple, texture: Gdk.Texture | None) -> bool:
        with self._lock:
            self._active.discard(key)
            callbacks = self._pending.pop(key, {})
            self._waiting.pop(key, None)
            if texture is not None and callbacks and not self._closed:
                # MemoryTexture retains rowstride-sized bytes, not compressed file size.
                cost = texture.get_width() * texture.get_height() * 4
                self._cache_bytes -= self._costs.pop(key, 0)
                self._cache[key] = texture
                self._costs[key] = cost
                self._cache_bytes += cost
                while self._cache and (len(self._cache) > self._capacity or
                                       self._cache_bytes > self._byte_budget):
                    old, _ = self._cache.popitem(last=False)
                    self._cache_bytes -= self._costs.pop(old)
        try:
            for callback in callbacks.values():
                callback(texture)
        finally:
            self._pump()
            self._capacity_changed()
        return False

    def close(self) -> None:
        """Stop admitting work and discard queued subscriptions and cached pixels."""
        with self._lock:
            self._closed = True
            self._pending.clear()
            self._waiting.clear()
            self._cache.clear()
            self._costs.clear()
            self._cache_bytes = 0
            self._available.clear()
        if self._available_idle:
            GLib.source_remove(self._available_idle)
            self._available_idle = 0
        self._pool.shutdown(wait=False, cancel_futures=True)


class _WidgetTexture:
    """Internal path-preview lifetime owned by a native mapped widget.

    Observe native scroller adjustments, allocation/snapshot and map events;
    there is no polling or unbounded retry/decode queue. Saturated widgets hold
    only weak capacity observers until an actual request completes. Consumers
    refresh from their existing snapshot/allocate hook.
    """

    def __init__(self, widget: Gtk.Widget, callback: Callable) -> None:
        self._widget = weakref.ref(widget)
        self._callback = weakref.WeakMethod(callback)
        self._source = None
        self._side = 0
        self._wanted = None
        self._request = None
        self._watch = None
        self._loader = None
        self._idle = 0
        self._scrollers = []
        self._handlers = []
        self._delivered = False
        reference = weakref.ref(self)

        def event(*_args):
            owner = reference()
            if owner is not None:
                owner._changed()

        widget.connect('map', event)
        widget.connect('unmap', event)
        widget.connect('notify::root', event)

    def set_source(self, source: str | None, side: float) -> None:
        if source != self._source:
            self._release()
            self._source = source
        self._side = side
        self.refresh()

    def _changed(self) -> None:
        widget = self._widget()
        if widget is None or not widget.get_mapped():
            self._disconnect()
            self._release()
        elif not self._idle:
            reference = weakref.ref(self)

            def update():
                owner = reference()
                if owner is not None:
                    owner._idle = 0
                    owner.refresh()
                return False

            self._idle = GLib.idle_add(update)

    def _disconnect(self) -> None:
        for adjustment, ident in self._handlers:
            adjustment.disconnect(ident)
        self._handlers.clear()
        self._scrollers.clear()
        if self._idle:
            GLib.source_remove(self._idle)
            self._idle = 0

    def _observe(self, widget: Gtk.Widget) -> None:
        scrollers = []
        parent = widget.get_parent()
        while parent is not None:
            if isinstance(parent, Gtk.ScrolledWindow):
                scrollers.append(parent)
            parent = parent.get_parent()
        if scrollers == self._scrollers:
            return
        self._disconnect()
        self._scrollers = scrollers
        reference = weakref.ref(self)

        def changed(*_args):
            owner = reference()
            if owner is not None:
                owner._changed()

        for scroller in scrollers:
            for adjustment in (scroller.get_hadjustment(), scroller.get_vadjustment()):
                for signal in ('changed', 'value-changed'):
                    self._handlers.append((adjustment, adjustment.connect(signal, changed)))

    def _visible(self, widget: Gtk.Widget) -> bool:
        if not widget.get_mapped() or widget.get_width() <= 0 or widget.get_height() <= 0:
            return False
        for scroller in self._scrollers:
            content = scroller.get_child()
            if isinstance(content, Gtk.Viewport):
                content = content.get_child()
            if content is None:
                return False
            success, bounds = widget.compute_bounds(content)
            horizontal = scroller.get_hadjustment()
            vertical = scroller.get_vadjustment()
            # Adjustments change before GtkViewport updates its transform.
            # Read content coordinates and the current scroll position so a
            # newly exposed cover can load without waiting for a snapshot.
            x = bounds.get_x() - horizontal.get_value()
            y = bounds.get_y() - vertical.get_value()
            width = horizontal.get_page_size() or scroller.get_width()
            height = vertical.get_page_size() or scroller.get_height()
            if not success or (x >= width or y >= height or
                               x + bounds.get_width() <= 0 or
                               y + bounds.get_height() <= 0):
                return False
        return True

    def _unwatch(self) -> None:
        if self._loader is not None and self._watch is not None:
            self._loader.unwatch_available(self._watch)
        self._watch = None

    def _release(self) -> None:
        self._unwatch()
        if self._request is not None:
            self._request.cancel()
            self._request = None
        self._wanted = None
        if self._delivered:
            self._delivered = False
            callback = self._callback()
            if callback is not None:
                callback(None)

    def close(self) -> None:
        self._disconnect()
        self._release()
        self._source = None

    def refresh(self) -> None:
        widget = self._widget()
        if widget is None:
            self.close()
            return
        if not widget.get_mapped():
            self._disconnect()
            self._release()
            return
        self._observe(widget)
        if not self._source or not self._visible(widget):
            self._release()
            return
        loader = TextureLoader.shared()
        wanted = (self._source, loader.bucket(self._side))
        if self._wanted == wanted:
            return
        if self._request is not None:
            self._request.cancel()
            self._request = None
        self._unwatch()
        self._loader = loader
        self._wanted = wanted
        reference = weakref.ref(self)
        synchronous = True
        rejected = False

        def arrived(texture):
            nonlocal rejected
            owner = reference()
            if owner is None or owner._wanted != wanted:
                return
            if synchronous and texture is None:
                rejected = True
                return
            owner._request = None
            current = owner._widget()
            if current is None or not owner._visible(current):
                owner._release()
                return
            owner._delivered = texture is not None
            callback = owner._callback()
            if callback is not None:
                callback(texture)

        request = loader.request(*wanted, arrived)
        synchronous = False
        if rejected:
            self._wanted = None
            self._watch = loader.watch_available(self.refresh)
            # A closed loader cannot free capacity; do not repeatedly retry it.
            if self._watch is None:
                self._wanted = wanted
        else:
            self._request = request
