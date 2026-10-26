# SPDX-License-Identifier: Apache-2.0
"""Public GVnc framebuffer bridge and display-independent PPM capture."""
import ctypes
import ctypes.util
import sys
from pathlib import Path


def native_library(name, linux_soname):
    """Resolve bundled macOS libraries before consulting the host linker cache."""
    if sys.platform == 'darwin':
        soname = f'lib{name}.0.dylib'
        bundled = Path(sys.executable).parent.parent / 'Frameworks' / soname
        if bundled.is_file():
            return str(bundled)
        return ctypes.util.find_library(name) or soname
    return ctypes.util.find_library(name) or linux_soname


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
        gvnc = ctypes.CDLL(native_library('gvnc-1.0', 'libgvnc-1.0.so.0'))
        objects = ctypes.CDLL(native_library('gobject-2.0', 'libgobject-2.0.so.0'))
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


def capture(socket_path, destination, timeout=15):
    """Request one complete framebuffer over QEMU's private Unix VNC socket.

    Accelerated QEMU scanouts do not always expose a QMP screendump surface.
    GVnc works without a GTK display, preserving CLI screenshot support.
    """
    import gi
    gi.require_version('GVnc', '1.0')
    from gi.repository import Gio, GLib, GVnc
    connection = GVnc.Connection.new()
    connection.set_shared(True)
    loop = GLib.MainLoop()
    framebuffer = None
    coverage = None
    covered = 0
    errors = []
    complete = False

    def fail(message):
        errors.append(RuntimeError(message))
        loop.quit()

    def auth(_connection, types):
        available = [types.get_nth(index) for index in range(types.n_values)]
        if any((v.get_uint() if hasattr(v, 'get_uint') else v) == GVnc.ConnectionAuth.NONE for v in available):
            connection.set_auth_type(GVnc.ConnectionAuth.NONE)
        else:
            fail('Unexpected authentication on the private display socket.')

    def initialized(_connection):
        nonlocal framebuffer, coverage
        try:
            connection.set_encodings([GVnc.ConnectionEncoding.RAW])
            framebuffer = Framebuffer(connection, connection.get_width(), connection.get_height())
            coverage = bytearray(framebuffer.width * framebuffer.height)
            connection.framebuffer_update_request(False, 0, 0, framebuffer.width, framebuffer.height)
        except Exception as exc:
            fail(str(exc))

    def updated(_connection, x, y, width, height):
        nonlocal covered, complete
        if not framebuffer or complete:
            return
        stride = framebuffer.width
        if x < 0 or y < 0 or x + width > stride or y + height > framebuffer.height:
            fail('Invalid framebuffer rectangle.')
            return
        for row in range(y, y + height):
            start = row * stride + x
            covered += coverage[start:start + width].count(0)
            coverage[start:start + width] = b'\1' * width
        if covered == len(coverage):
            complete = True
            loop.quit()

    def expired():
        fail('Timed out capturing the accelerated guest display.')
        return False

    connection.connect('vnc-auth-choose-type', auth)
    connection.connect('vnc-initialized', initialized)
    connection.connect('vnc-framebuffer-update', updated)
    connection.connect('vnc-error', lambda _c, message: fail(message))
    connection.connect('vnc-disconnected', lambda _c: None if complete else fail('Display disconnected during capture.'))
    deadline = GLib.timeout_add_seconds(timeout, expired)
    try:
        if not connection.open_addr(Gio.UnixSocketAddress.new(str(socket_path)), None):
            raise RuntimeError('Could not open the private guest display.')
        loop.run()
        if errors:
            raise errors[0]
        assert complete
        raw = framebuffer.buffer.raw
        pixels = bytearray(framebuffer.width * framebuffer.height * 3)
        for channel in range(3):
            pixels[channel::3] = raw[channel::4]
        # Match QMP's default, binary PPM, without a GUI or image encoder.
        with Path(destination).open('xb') as stream:
            stream.write(f'P6\n{framebuffer.width} {framebuffer.height}\n255\n'.encode())
            stream.write(pixels)
    finally:
        source = GLib.MainContext.default().find_source_by_id(deadline)
        if source:
            source.destroy()
        connection.shutdown()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Capture a private QEMU display as PPM.')
    parser.add_argument('socket', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    capture(args.socket, args.destination)
