# SPDX-License-Identifier: Apache-2.0
"""Live GTK4 guest display over a private QEMU Unix VNC socket.

GVnc owns input. On Unix sockets a second shared connection streams whole
framebuffer updates on a worker thread, so GTK4 only ever draws complete frames;
GVnc writes rectangles as they arrive and a frame-clock copy of its buffer can
catch an update half applied. Windows (TCP + VNC auth) keeps the GVnc framebuffer.
"""
from __future__ import annotations
from pathlib import Path
import socket
import struct
import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GVnc', '1.0')
gi.require_version('Gsk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, GVnc, Gsk, Graphene


from .rfb import Framebuffer as BaseFramebuffer
from . import host


class Framebuffer(BaseFramebuffer):
    def texture(self):
        return Gdk.MemoryTexture.new(self.width, self.height, Gdk.MemoryFormat.R8G8B8X8,
                                     GLib.Bytes.new(self.buffer.raw), self.width * 4)


class FrameStream(threading.Thread):
    """Shared RFB client: RAW + DesktopSize, publishes only completed updates."""

    def __init__(self, path, published):
        super().__init__(daemon=True, name='luma-guest-frames')
        self.path, self.published = str(path), published
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.lock = threading.Lock()
        self.frame = None
        self.closed = False

    def read(self, view):
        while len(view):
            count = self.sock.recv_into(view)
            if not count:
                raise EOFError
            view = view[count:]

    def take(self, count):
        data = bytearray(count)
        self.read(memoryview(data))
        return data

    def allocate(self, width, height):
        if not 1 <= width <= 4096 or not 1 <= height <= 4096:
            raise ValueError('Guest display exceeds supported 4096×4096 dimensions.')
        self.width, self.height = width, height
        self.back = bytearray(width * height * 4)

    def handshake(self):
        self.sock.connect(self.path)
        if not self.take(12).startswith(b'RFB 003.'):
            raise RuntimeError('The private guest display is not an RFB server.')
        self.sock.sendall(b'RFB 003.008\n')
        if 1 not in self.take(self.take(1)[0]):
            raise RuntimeError('The private guest display requested unexpected authentication.')
        self.sock.sendall(b'\x01')
        if struct.unpack('>I', self.take(4))[0]:
            raise RuntimeError('The private guest display refused the frame stream.')
        self.sock.sendall(b'\x01')  # shared: GVnc stays connected for input
        width, height = struct.unpack('>HH', self.take(4))
        bpp, _depth, big, true_colour, rmax, gmax, bmax, rshift, gshift, bshift = \
            struct.unpack('>BBBBHHHBBB3x', self.take(16))
        self.take(struct.unpack('>I', self.take(4))[0])
        self.allocate(width, height)
        if (bpp, big, true_colour, rmax, gmax, bmax, rshift, gshift, bshift) == \
                (32, 0, 1, 255, 255, 255, 16, 8, 0):
            self.format = Gdk.MemoryFormat.B8G8R8X8  # QEMU's native surface: no conversion
        else:
            self.sock.sendall(struct.pack('>B3xBBBBHHHBBB3x', 0, 32, 24, 0, 1,
                                          255, 255, 255, 0, 8, 16))
            self.format = Gdk.MemoryFormat.R8G8B8X8
        self.sock.sendall(struct.pack('>BxHii', 2, 2, 0, -223))  # RAW, DesktopSize

    def request(self, incremental):
        self.sock.sendall(struct.pack('>BBHHHH', 3, incremental, 0, 0, self.width, self.height))

    def run(self):
        try:
            self.handshake()
            self.request(0)
            while True:
                kind = self.take(1)[0]
                if kind == 0:
                    self.update()
                    self.request(1)
                elif kind == 1:
                    self.take(3)
                    self.take(6 * struct.unpack('>H', self.take(2))[0])
                elif kind == 2:
                    pass
                elif kind == 3:
                    self.take(3)
                    self.take(struct.unpack('>I', self.take(4))[0])
                else:
                    raise RuntimeError(f'Unexpected guest display message {kind}.')
        except (OSError, EOFError, ValueError, RuntimeError, struct.error) as exc:
            if not self.closed:
                GLib.idle_add(self.published, None, str(exc))

    def update(self):
        self.take(1)
        resized = False
        for _ in range(struct.unpack('>H', self.take(2))[0]):
            x, y, width, height, encoding = struct.unpack('>HHHHi', self.take(12))
            if encoding == -223:
                self.allocate(width, height)
                resized = True
            elif encoding == 0:
                if x + width > self.width or y + height > self.height:
                    raise ValueError('Guest display update exceeds the framebuffer.')
                view, stride, span = memoryview(self.back), self.width * 4, width * 4
                if x == 0 and width == self.width:
                    self.read(view[y * stride:(y + height) * stride])
                else:
                    for row in range(y, y + height):
                        start = row * stride + x * 4
                        self.read(view[start:start + span])
            else:
                raise RuntimeError(f'Unexpected guest display encoding {encoding}.')
        frame = (self.width, self.height, self.format, bytes(self.back))
        with self.lock:
            pending, self.frame = self.frame is not None, frame
        if not pending:
            GLib.idle_add(self.published, resized, None)
        if resized:
            self.request(0)

    def latest(self):
        with self.lock:
            frame, self.frame = self.frame, None
        return frame

    def close(self):
        self.closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


class StreamFramebuffer:
    def __init__(self, width, height, format, data):
        self.width, self.height, self.format, self.data = width, height, format, data

    def texture(self):
        return Gdk.MemoryTexture.new(self.width, self.height, self.format,
                                     GLib.Bytes.new(self.data), self.width * 4)


class GuestDisplay(Gtk.Picture):
    __gtype_name__ = 'LumaTesterGuestDisplay'
    __gsignals__ = {
        'connected': (GObject.SignalFlags.RUN_LAST, None, ()),
        'disconnected': (GObject.SignalFlags.RUN_LAST, None, ()),
        'display-error': (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    def __init__(self, socket_path, touch=None):
        super().__init__(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN,
                         hexpand=True, vexpand=True, focusable=True)
        self.touch = touch
        self.device = None
        self.touch_active = False
        self.touch_point = (0, 0)
        self.scroll_contact = False
        self.scroll_source = 0
        self.socket_path = Path(socket_path)
        self.connection = GVnc.Connection.new()
        self.framebuffer = None
        self.ready = False
        self.frames = 0
        self.pending_frame = 0
        self.pending_tick = 0
        self.connect('map', self.mapped)
        self.connect('unmap', self.unmapped)
        self.button_mask = 0
        self.last_pointer = (0, 0)
        self.pressed_keys = set()
        self.scroll_x = self.scroll_y = 0.0
        self.connection.set_shared(True)
        self.stream = None
        self.connection.connect('vnc-auth-choose-type', self.auth)
        if host.windows():
            self.connection.connect('vnc-auth-credential', lambda c, values:
                host.display_credentials(c, values, self.socket_path, GVnc))
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

    def set_device(self, description):
        self.release_input()
        self.device=description
        self.queue_draw()

    def hinge(self):
        if not self.device or not self.framebuffer:return None
        expected=self.device.get('pixels',{})
        if (expected.get('width'),expected.get('height'))!=(self.framebuffer.width,self.framebuffer.height):return None
        return self.device.get('hinge')

    def do_snapshot(self, snapshot):
        if self.framebuffer and self.get_paintable():
            inset = 28
            scale = min(max(1, self.get_width() - inset * 2) / self.framebuffer.width,
                        max(1, self.get_height() - inset * 2) / self.framebuffer.height)
            width, height = self.framebuffer.width * scale, self.framebuffer.height * scale
            rect = Graphene.Rect().init((self.get_width() - width) / 2,
                (self.get_height() - height) / 2, width, height)
            outline = Gsk.RoundedRect()
            outline.init_from_rect(rect, 4)
            # Draw only the screen silhouette: the changing framebuffer itself
            # is never blurred. The shared colours also handle high contrast.
            for token, offset, blur in [('luma_float_shadow', 3, 16),
                                        ('luma_float_shadow_near', 1, 4)]:
                found, colour = self.get_style_context().lookup_color(token)
                if found:
                    colour.alpha *= 0.65
                    snapshot.append_outset_shadow(outline, colour, 0, offset, 0, blur)
            snapshot.save()
            snapshot.translate(Graphene.Point().init(rect.get_x(), rect.get_y()))
            self.get_paintable().snapshot(snapshot, width, height)
            hinge=self.hinge()
            if hinge:
                colour=Gdk.RGBA();colour.parse('#111214')
                gap=Graphene.Rect().init(hinge['x']*scale,hinge['y']*scale,hinge['width']*scale,hinge['height']*scale)
                snapshot.append_color(colour,gap)
            snapshot.restore()

    def open(self):
        if not (self.socket_path.is_file() if host.windows() else self.socket_path.is_socket()):
            return False
        return self.connection.open_addr(host.display_address(self.socket_path), None)

    def auth(self, connection, types):
        if not host.display_auth(connection, types, self.socket_path, GVnc):
            self.emit('display-error', 'The private guest display requested unexpected authentication.')
            connection.shutdown()

    def initialized(self, connection):
        connection.set_encodings([GVnc.ConnectionEncoding.COPY_RECT, GVnc.ConnectionEncoding.RAW,
                                  GVnc.ConnectionEncoding.HEXTILE, GVnc.ConnectionEncoding.ZRLE,
                                  GVnc.ConnectionEncoding.DESKTOP_RESIZE])
        if host.windows():
            if self.resize_buffer(connection.get_width(), connection.get_height()):
                self.ready = True
                self.emit('connected')
            return
        # GVnc keeps a framebuffer but never requests updates: it is input only.
        try:
            self.input_framebuffer = Framebuffer(connection, connection.get_width(), connection.get_height())
        except Exception as exc:
            self.emit('display-error', str(exc))
            connection.shutdown()
            return
        self.stream = FrameStream(self.socket_path, self.streamed)
        self.stream.start()

    def streamed(self, resized, error):
        if error:
            if self.stream and not self.stream.closed:
                self.emit('display-error', error)
            return False
        frame = self.stream.latest() if self.stream else None
        if not frame:
            return False
        self.framebuffer = StreamFramebuffer(*frame)
        if not self.ready:
            self.ready = True
            self.emit('connected')
        if not self.pending_frame and not self.pending_tick:
            self.schedule_frame()
        return False

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
        if self.ready and not self.stream:
            self.resize_buffer(width, height)

    def format_changed(self, _connection, _format):
        if self.ready and not self.stream:
            self.resize_buffer(self.connection.get_width(), self.connection.get_height())

    def updated(self, _connection, _x, _y, _width, _height):
        if not self.stream and self.framebuffer and not self.pending_frame and not self.pending_tick:
            self.schedule_frame()

    def schedule_frame(self):
        # GVnc emits damage per rectangle, not per display frame. Copying the
        # entire buffer for every chunk can enqueue thousands of textures/sec.
        if self.get_mapped():
            self.pending_tick = self.add_tick_callback(self.paint_tick)
        else:
            # The loading page keeps the viewer unmapped. A frame clock alone
            # would stop its first frame and the next VNC request indefinitely.
            self.pending_frame = GLib.timeout_add(16, self.paint_frame)

    def paint_tick(self, _widget, _clock):
        self.pending_tick = 0
        return self.paint_frame()

    def mapped(self, _widget):
        if self.pending_frame:
            GLib.source_remove(self.pending_frame)
            self.pending_frame = 0
        if self.ready and self.framebuffer and not self.pending_tick:
            self.schedule_frame()

    def unmapped(self, _widget):
        if self.pending_tick:
            self.remove_tick_callback(self.pending_tick)
            self.pending_tick = 0
        if self.ready and self.framebuffer and not self.pending_frame:
            self.pending_frame = GLib.timeout_add(16, self.paint_frame)

    def paint_frame(self):
        self.pending_frame = 0
        if not self.ready or not self.framebuffer:
            return False
        if self.stream:
            frame = self.stream.latest()
            if frame:
                self.framebuffer = StreamFramebuffer(*frame)
        self.set_paintable(self.framebuffer.texture())
        self.frames += 1
        if not self.stream:
            self.connection.framebuffer_update_request(True, 0, 0,
                                                       self.framebuffer.width, self.framebuffer.height)
        return False

    def coordinates(self, x, y):
        if not self.ready or not self.framebuffer:
            return None
        width, height = self.framebuffer.width, self.framebuffer.height
        scale = min(max(1, self.get_width() - 56) / width, max(1, self.get_height() - 56) / height)
        if scale <= 0:
            return None
        x = (x - (self.get_width() - width * scale) / 2) / scale
        y = (y - (self.get_height() - height * scale) / 2) / scale
        hinge=self.hinge()
        if hinge and hinge['x']<=x<hinge['x']+hinge['width'] and hinge['y']<=y<hinge['y']+hinge['height']:
            return None
        if 0 <= x < width and 0 <= y < height:
            return int(x), int(y)
        return None

    def motion(self, _controller, x, y):
        point = self.coordinates(x, y)
        if point:
            self.last_pointer = point
            if self.touch:
                if self.touch_active and not self.scroll_contact:
                    self.send_touch('update', point)
            else:
                self.connection.pointer_event(self.button_mask, *point)

    def pressed(self, gesture, _count, x, y):
        point = self.coordinates(x, y)
        if point:
            if self.touch and gesture.get_current_button() not in (0, 1):
                return
            self.grab_focus()
            self.last_pointer = point
            if self.touch:
                self.release_touch()
                self.touch_active = True
                self.send_touch('begin', point)
                return
            self.button_mask |= {1: 1, 2: 2, 3: 4}.get(gesture.get_current_button(), 0)
            self.connection.pointer_event(self.button_mask, *point)

    def released(self, gesture, _count, x, y):
        if self.touch:
            if self.touch_active and not self.scroll_contact:
                self.send_touch('end', self.coordinates(x, y) or self.touch_point)
                self.touch_active = False
            return
        self.button_mask &= ~{1: 1, 2: 2, 3: 4}.get(gesture.get_current_button(), 0)
        if self.ready:
            self.last_pointer = self.coordinates(x, y) or self.last_pointer
            self.connection.pointer_event(self.button_mask, *self.last_pointer)

    def send_touch(self, phase, point):
        if not self.touch or not self.ready or not self.framebuffer:
            return
        self.touch_point = point
        try:
            self.touch(phase, *point, self.framebuffer.width, self.framebuffer.height)
        except Exception as exc:
            self.touch_active = False
            self.emit('display-error', 'Guest touch input: ' + str(exc))

    def release_touch(self):
        if self.scroll_source:
            GLib.source_remove(self.scroll_source)
            self.scroll_source = 0
        if self.touch_active:
            self.send_touch('cancel', self.touch_point)
        self.touch_active = self.scroll_contact = False

    def finish_touch_scroll(self):
        self.scroll_source = 0
        if self.scroll_contact:
            self.send_touch('end', self.touch_point)
            self.touch_active = self.scroll_contact = False
        return False

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
        self.release_touch()
        if self.ready:
            for key in self.pressed_keys:
                self.connection.key_event(False, key, 0)
            self.connection.pointer_event(0, *self.last_pointer)
        self.pressed_keys.clear()
        self.button_mask = 0

    def scroll(self, _controller, dx, dy):
        if not self.ready:
            return False
        if self.touch:
            if self.touch_active and not self.scroll_contact:
                return True
            width, height = self.framebuffer.width, self.framebuffer.height
            if not self.scroll_contact:
                point = self.last_pointer if self.last_pointer != (0, 0) else (width / 2, height / 2)
                self.touch_active = self.scroll_contact = True
                self.send_touch('begin', point)
            unit = 1 if _controller and _controller.get_unit() == Gdk.ScrollUnit.SURFACE else 40
            x, y = self.touch_point
            if not 1 <= x - dx * unit <= width - 2 or not 1 <= y - dy * unit <= height - 2:
                self.send_touch('end', (x, y))
                x = width / 2 if dx else x
                y = height / 2 if dy else y
                self.send_touch('begin', (x, y))
            point = (max(1, min(width - 2, x - dx * unit)),
                     max(1, min(height - 2, y - dy * unit)))
            self.send_touch('update', point)
            if self.scroll_source:
                GLib.source_remove(self.scroll_source)
            self.scroll_source = GLib.timeout_add(120, self.finish_touch_scroll)
            return True
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
        if self.stream:
            self.stream.close()
        self.release_touch()
        self.ready = False
        self.emit('disconnected')

    def close(self):
        self.release_input()
        self.ready = False
        if self.pending_tick:
            self.remove_tick_callback(self.pending_tick)
            self.pending_tick = 0
        if self.pending_frame:
            GLib.source_remove(self.pending_frame)
            self.pending_frame = 0
        if self.stream:
            self.stream.close()
        self.connection.shutdown()
