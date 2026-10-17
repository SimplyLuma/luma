# SPDX-License-Identifier: Apache-2.0
"""Live GTK4 guest display over a private QEMU Unix VNC socket.

GVnc owns protocol decoding and damage tracking. GTK4 draws immutable copies of
its framebuffer; no screenshot polling, web view, or GTK3 viewer is involved.
"""
from __future__ import annotations
import ctypes
import ctypes.util
import sys
from pathlib import Path

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GVnc', '1.0')
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, GVnc


class PixelFormat(ctypes.Structure):
    # Public VncPixelFormat layout, from gvnc's installed C API.
    _fields_ = [('bits_per_pixel', ctypes.c_uint8), ('depth', ctypes.c_uint8),
                ('byte_order', ctypes.c_uint16), ('true_color_flag', ctypes.c_uint8),
                ('red_max', ctypes.c_uint16), ('green_max', ctypes.c_uint16),
                ('blue_max', ctypes.c_uint16), ('red_shift', ctypes.c_uint8),
                ('green_shift', ctypes.c_uint8), ('blue_shift', ctypes.c_uint8)]


class Framebuffer:
    """Own the mutable buffer explicitly: GI cannot set a gpointer property.

    The bridge calls only public GVnc/GObject functions. Keeping the Python-owned
    buffer alive until Connection replaces its framebuffer avoids temporary GI
    array storage escaping a constructor call. GTK textures receive copies.
    """
    def __init__(self, connection, width, height):
        if not 1 <= width <= 4096 or not 1 <= height <= 4096:
            raise ValueError('Guest display exceeds supported 4096×4096 dimensions.')
        self.width, self.height = width, height
        self.buffer = ctypes.create_string_buffer(width * height * 4)
        gvnc = ctypes.CDLL(ctypes.util.find_library('gvnc-1.0') or 'libgvnc-1.0.so.0')
        objects = ctypes.CDLL(ctypes.util.find_library('gobject-2.0') or 'libgobject-2.0.so.0')
        pointer = ctypes.pythonapi.PyCapsule_GetPointer
        pointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
        pointer.restype = ctypes.c_void_p
        native = pointer(connection.__gpointer__, None)
        gvnc.vnc_connection_get_pixel_format.argtypes = [ctypes.c_void_p]
        gvnc.vnc_connection_get_pixel_format.restype = ctypes.c_void_p
        gvnc.vnc_base_framebuffer_new.argtypes = [ctypes.c_void_p, ctypes.c_uint16,
            ctypes.c_uint16, ctypes.c_int, ctypes.POINTER(PixelFormat), ctypes.c_void_p]
        gvnc.vnc_base_framebuffer_new.restype = ctypes.c_void_p
        gvnc.vnc_connection_set_framebuffer.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gvnc.vnc_connection_set_framebuffer.restype = ctypes.c_int
        objects.g_object_unref.argtypes = [ctypes.c_void_p]
        objects.g_object_unref.restype = None
        # RGBX byte order, independent of host byte order.
        little = sys.byteorder == 'little'
        format = PixelFormat(32, 24, 1234 if little else 4321, 1, 255, 255, 255,
                             0 if little else 24, 8 if little else 16, 16 if little else 8)
        framebuffer = gvnc.vnc_base_framebuffer_new(ctypes.addressof(self.buffer), width,
            height, width * 4, ctypes.byref(format), gvnc.vnc_connection_get_pixel_format(native))
        if not framebuffer:
            raise RuntimeError('GVnc could not allocate the guest framebuffer.')
        try:
            if not gvnc.vnc_connection_set_framebuffer(native, framebuffer):
                raise RuntimeError('GVnc rejected the guest framebuffer.')
        finally:
            # Connection retains its own reference; Python retains the backing bytes.
            objects.g_object_unref(framebuffer)

    def texture(self):
        return Gdk.MemoryTexture.new(self.width, self.height, Gdk.MemoryFormat.R8G8B8X8,
                                     GLib.Bytes.new(self.buffer.raw), self.width * 4)


class GuestDisplay(Gtk.Picture):
    __gtype_name__ = 'LumaTesterGuestDisplay'
    __gsignals__ = {
        'connected': (GObject.SignalFlags.RUN_LAST, None, ()),
        'disconnected': (GObject.SignalFlags.RUN_LAST, None, ()),
        'display-error': (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    def __init__(self, socket_path):
        super().__init__(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN,
                         hexpand=True, vexpand=True, focusable=True)
        self.socket_path = Path(socket_path)
        self.connection = GVnc.Connection.new()
        self.framebuffer = None
        self.ready = False
        self.frames = 0
        self.pending_frame = 0
        self.button_mask = 0
        self.last_pointer = (0, 0)
        self.pressed_keys = set()
        self.scroll_x = self.scroll_y = 0.0
        self.connection.set_shared(True)
        self.connection.connect('vnc-auth-choose-type', self.auth)
        self.connection.connect('vnc-initialized', self.initialized)
        self.connection.connect('vnc-desktop-resize', self.resized)
        self.connection.connect('vnc-pixel-format-changed', self.format_changed)
        self.connection.connect('vnc-framebuffer-update', self.updated)
        self.connection.connect('vnc-disconnected', self.disconnected)
        self.connection.connect('vnc-error', lambda _c, message: self.emit('display-error', message))
        self.connection.connect('vnc-auth-failure', lambda _c, message: self.emit('display-error', message))
        motion = Gtk.EventControllerMotion()
        motion.connect('motion', self.motion)
        self.add_controller(motion)
        click = Gtk.GestureClick(button=0)
        click.connect('pressed', self.pressed)
        click.connect('released', self.released)
        self.add_controller(click)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed', self.key_pressed)
        keys.connect('key-released', self.key_released)
        self.add_controller(keys)
        focus = Gtk.EventControllerFocus()
        focus.connect('leave', lambda _c: self.release_input())
        self.add_controller(focus)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect('scroll', self.scroll)
        self.add_controller(scroll)
        self.update_property([Gtk.AccessibleProperty.LABEL], ['Luma guest display. Click to use the guest. Control Alt returns to launcher controls.'])

    def open(self):
        if not self.socket_path.is_socket():
            return False
        return self.connection.open_addr(Gio.UnixSocketAddress.new(str(self.socket_path)), None)

    def auth(self, connection, types):
        available = [types.get_nth(index) for index in range(types.n_values)]
        available = [value.get_uint() if hasattr(value, 'get_uint') else value for value in available]
        if GVnc.ConnectionAuth.NONE in available:
            connection.set_auth_type(GVnc.ConnectionAuth.NONE)
        else:
            self.emit('display-error', 'The private guest display requested unexpected authentication.')
            connection.shutdown()

    def initialized(self, connection):
        connection.set_encodings([GVnc.ConnectionEncoding.COPY_RECT, GVnc.ConnectionEncoding.RAW,
                                  GVnc.ConnectionEncoding.HEXTILE, GVnc.ConnectionEncoding.ZRLE,
                                  GVnc.ConnectionEncoding.DESKTOP_RESIZE])
        if self.resize_buffer(connection.get_width(), connection.get_height()):
            self.ready = True
            self.emit('connected')

    def resize_buffer(self, width, height):
        try:
            # Keep the old buffer alive until Connection releases the old framebuffer.
            replacement = Framebuffer(self.connection, width, height)
            self.framebuffer = replacement
            self.connection.framebuffer_update_request(False, 0, 0, width, height)
            return True
        except Exception as exc:
            self.emit('display-error', str(exc))
            self.connection.shutdown()
            return False

    def resized(self, _connection, width, height):
        if self.ready:
            self.resize_buffer(width, height)

    def format_changed(self, _connection, _format):
        if self.ready:
            self.resize_buffer(self.connection.get_width(), self.connection.get_height())

    def updated(self, _connection, _x, _y, _width, _height):
        if self.framebuffer and not self.pending_frame:
            self.pending_frame = self.add_tick_callback(self.paint_frame)

    def paint_frame(self, _widget, _clock):
        self.pending_frame = 0
        if not self.ready or not self.framebuffer:
            return False
        self.set_paintable(self.framebuffer.texture())
        self.frames += 1
        self.connection.framebuffer_update_request(True, 0, 0,
                                                   self.framebuffer.width, self.framebuffer.height)
        return False

    def coordinates(self, x, y):
        if not self.ready or not self.framebuffer:
            return None
        width, height = self.framebuffer.width, self.framebuffer.height
        scale = min(self.get_width() / width, self.get_height() / height)
        if scale <= 0:
            return None
        x = (x - (self.get_width() - width * scale) / 2) / scale
        y = (y - (self.get_height() - height * scale) / 2) / scale
        if 0 <= x < width and 0 <= y < height:
            return int(x), int(y)
        return None

    def motion(self, _controller, x, y):
        point = self.coordinates(x, y)
        if point:
            self.last_pointer = point
            self.connection.pointer_event(self.button_mask, *point)

    def pressed(self, gesture, _count, x, y):
        point = self.coordinates(x, y)
        if point:
            self.grab_focus()
            self.last_pointer = point
            self.button_mask |= {1: 1, 2: 2, 3: 4}.get(gesture.get_current_button(), 0)
            self.connection.pointer_event(self.button_mask, *point)

    def released(self, gesture, _count, x, y):
        self.button_mask &= ~{1: 1, 2: 2, 3: 4}.get(gesture.get_current_button(), 0)
        if self.ready:
            self.last_pointer = self.coordinates(x, y) or self.last_pointer
            self.connection.pointer_event(self.button_mask, *self.last_pointer)

    def key_pressed(self, _controller, key, _code, state):
        if not self.ready:
            return False
        if key in (Gdk.KEY_Control_L, Gdk.KEY_Control_R):
            state |= Gdk.ModifierType.CONTROL_MASK
        if key in (Gdk.KEY_Alt_L, Gdk.KEY_Alt_R):
            state |= Gdk.ModifierType.ALT_MASK
        if state & Gdk.ModifierType.CONTROL_MASK and state & Gdk.ModifierType.ALT_MASK:
            self.release_input()
            self.get_root().child_focus(Gtk.DirectionType.TAB_FORWARD)
            return True
        self.pressed_keys.add(key)
        self.connection.key_event(True, key, 0)
        return True

    def key_released(self, _controller, key, _code, _state):
        if key in self.pressed_keys:
            self.pressed_keys.discard(key)
            if self.ready:
                self.connection.key_event(False, key, 0)

    def release_input(self):
        if self.ready:
            for key in self.pressed_keys:
                self.connection.key_event(False, key, 0)
            self.connection.pointer_event(0, *self.last_pointer)
        self.pressed_keys.clear()
        self.button_mask = 0

    def scroll(self, _controller, dx, dy):
        if not self.ready:
            return False
        self.scroll_x += dx
        self.scroll_y += dy
        for attribute, negative, positive in [('scroll_y', 8, 16), ('scroll_x', 32, 64)]:
            delta = getattr(self, attribute)
            count = min(int(abs(delta)), 20)
            mask = positive if delta > 0 else negative
            for _ in range(count):
                self.connection.pointer_event(self.button_mask | mask, *self.last_pointer)
                self.connection.pointer_event(self.button_mask, *self.last_pointer)
            setattr(self, attribute, delta - count * (1 if delta > 0 else -1))
        return True

    def disconnected(self, _connection):
        self.ready = False
        self.emit('disconnected')

    def close(self):
        self.release_input()
        if self.pending_frame:
            self.remove_tick_callback(self.pending_frame)
            self.pending_frame = 0
        self.connection.shutdown()
