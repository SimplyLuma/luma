# SPDX-License-Identifier: Apache-2.0
"""Quiet pen hover feedback without overriding application brush cursors."""
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, GLib, Gtk

_dot = None


def pen_dot():
    global _dot
    if _dot is None:
        # Static, high-contrast ink point: a white centre and dark outline.
        # Both remain visible over light paper and dark drawing surfaces.
        pixels = bytearray(16 * 16 * 4)
        for y in range(16):
            for x in range(16):
                distance = (x - 8) ** 2 + (y - 8) ** 2
                if distance <= 9:
                    ink = 255 if distance <= 2 else 24
                    offset = (y * 16 + x) * 4
                    pixels[offset:offset + 4] = bytes((ink, ink, ink, 255))
        texture = Gdk.MemoryTexture.new(16, 16,
            Gdk.MemoryFormat.R8G8B8A8_PREMULTIPLIED,
            GLib.Bytes.new(bytes(pixels)), 16 * 4)
        _dot = Gdk.Cursor.new_from_texture(texture, 8, 8, None)
    return _dot


class PenCursor:
    def __init__(self, widget):
        self.widget = widget
        self.override = False
        self.cursor = None
        controller = Gtk.EventControllerMotion(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        controller.connect('enter', self.motion)
        controller.connect('motion', self.motion)
        controller.connect('leave', self.leave)
        widget.add_controller(controller)

    def motion(self, controller, *_):
        event = controller.get_current_event()
        tool = event.get_device_tool() if event else None
        self.update(tool is not None and tool.get_tool_type() != Gdk.DeviceToolType.MOUSE)

    def update(self, pen):
        current = self.widget.get_cursor()
        if self.override and current is not self.cursor:
            # The application selected its own brush/resize cursor meanwhile.
            self.override = False
        if pen and current is None:
            self.cursor = pen_dot()
            self.widget.set_cursor(self.cursor)
            self.override = True
        elif not pen:
            self.leave()

    def leave(self, *_):
        if self.override and self.widget.get_cursor() is self.cursor:
            self.widget.set_cursor(None)
        self.override = False


def install_pen_cursor(widget):
    """Use a dot for inherited pen cursors; retain explicit app/tool cursors."""
    return PenCursor(widget)
