# SPDX-License-Identifier: GPL-3.0-only
"""Native GTK page surface for the non-shipping integration probe.

Consumes imported GPU textures. A complete accessibility/input bridge is
still required before this can replace a browser WebContents presentation.
"""
import time
from collections import deque
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Gtk, Graphene


def buffer_fits_viewport(buffer_size, viewport_size):
    if not viewport_size or min(*buffer_size, *viewport_size) <= 0:
        return False
    width, height = buffer_size
    wanted_width, wanted_height = viewport_size
    # Wayland can apply a fractional output scale independently of emulation.
    # Accept uniform density changes with at most one physical pixel rounding.
    return abs(width * wanted_height - height * wanted_width) <= max(width, height)


def frame_content(frame):
    x, y = frame.get('content_x', 0), frame.get('content_y', 0)
    width = frame.get('content_width', frame['width'])
    height = frame.get('content_height', frame['height'])
    if (min(x, y) < 0 or min(width, height) <= 0 or
            x + width > frame['width'] or y + height > frame['height']):
        return None
    return x, y, width, height


class GpuPage(Gtk.Widget):
    __gtype_name__ = 'ViolaNativeGpuPageProbe'

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True, focusable=True)
        self.texture = None
        self.displayed_frame = None
        self.displayed_size = None
        self.frame = None
        self.expected_buffer_size = None
        self.on_frame = None
        self.on_resize = None
        self.cursors = {}
        self.rejected_sizes = 0
        self.frame_size_diagnostic = {}
        self.snapshot_count = 0
        self.presentation_samples = deque(maxlen=600)
        self.received_samples = deque(maxlen=600)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.update_property([Gtk.AccessibleProperty.LABEL], [
            'Browser page integration probe; page accessibility is not connected'])

    def set_frame(self, texture, metadata):
        self.received_samples.append((time.monotonic(), metadata['id']))
        self.frame = metadata
        self.set_cursor_from_name(self.cursors.get(metadata['target_id'], 'default'))
        # Notify input even for a transitional buffer: attachment establishes
        # the viewport. Never stretch a pre-resize frame into the new allocation.
        expected = self.expected_buffer_size
        self.frame_size_diagnostic = dict(expected=expected,
            received=(metadata['width'], metadata['height']))
        content = frame_content(metadata)
        self.frame_size_diagnostic['content'] = content
        matches = bool(content and buffer_fits_viewport(content[2:], expected))
        if matches:
            self.texture = texture
            self.displayed_frame = metadata
            # The coded DMA-BUF can include alignment/letterbox padding. Its
            # content rectangle, not its storage extent, defines the page.
            self.displayed_size = (self.get_width(), self.get_height())
        elif self.displayed_frame and self.displayed_frame['target_id'] != metadata['target_id']:
            # Never retain another tab's content while its first frame attaches.
            self.texture = None
            self.displayed_frame = None
            self.displayed_size = None
        if not matches:
            self.rejected_sizes += 1
        if self.on_frame:
            self.on_frame(metadata)
        self.queue_draw()

    def cursor_changed(self, metadata):
        from native_cursor import CURSORS
        name = CURSORS.get(metadata['cursor'], 'default')
        self.cursors = {metadata['target_id']: name}
        if self.frame and self.frame['target_id'] == metadata['target_id']:
            self.set_cursor_from_name(name)
        return False

    def clear(self):
        self.texture = None
        self.displayed_frame = None
        self.displayed_size = None
        self.frame = None
        self.set_cursor_from_name('default')
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        # The page must never impose a texture-sized minimum on a tiled
        # window. Native layout owns its allocation, regardless of the old
        # buffer's dimensions during a resize.
        return (1, 800 if orientation == Gtk.Orientation.HORIZONTAL else 600, -1, -1)

    def do_snapshot(self, snapshot):
        self.snapshot_count += 1
        if self.texture is None or not self.get_width() or not self.get_height():
            return
        # Keep the last accepted frame at its original logical size while the
        # engine catches up. HIDDEN overflow clips shrinking allocations without
        # blanking the page or stretching its content during a sidebar drag.
        bounds = Graphene.Rect()
        width, height = self.displayed_size
        x, y, content_width, content_height = frame_content(self.displayed_frame)
        scale_x, scale_y = width / content_width, height / content_height
        padded = (x != 0 or y != 0 or content_width != self.displayed_frame['width']
                  or content_height != self.displayed_frame['height'])
        if padded:
            snapshot.push_clip(Graphene.Rect().init(0, 0, width, height))
        bounds.init(-x * scale_x, -y * scale_y,
                    self.displayed_frame['width'] * scale_x,
                    self.displayed_frame['height'] * scale_y)
        snapshot.append_texture(self.texture, bounds)
        if padded:
            snapshot.pop()
        self.presentation_samples.append((time.monotonic(), self.displayed_frame['id']))

    def do_size_allocate(self, width, height, baseline):
        if self.on_resize and width > 0 and height > 0:
            native = self.get_native()
            surface = native.get_surface() if native else None
            scale = surface.get_scale() if surface and hasattr(surface, 'get_scale') else self.get_scale_factor()
            self.expected_buffer_size = (round(width * scale), round(height * scale))
            self.on_resize(width, height, scale)
