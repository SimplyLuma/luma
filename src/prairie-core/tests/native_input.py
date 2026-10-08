# SPDX-License-Identifier: Apache-2.0
"""Private Xvfb pointer input shared by creator interaction gates."""
import ctypes

def outside_click(window, x, y):
    import gi
    gi.require_version('GdkX11', '4.0')
    from gi.repository import GdkX11
    surface = window.get_surface()
    assert isinstance(surface, GdkX11.X11Surface), 'This native input gate requires Xvfb'
    x11 = ctypes.CDLL('libX11.so.6')
    xtst = ctypes.CDLL('libXtst.so.6')
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    display = x11.XOpenDisplay(None)
    assert display
    try:
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XTranslateCoordinates.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong)]
        rx, ry, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
        assert x11.XTranslateCoordinates(display, surface.get_xid(), x11.XDefaultRootWindow(display),
            x, y, ctypes.byref(rx), ctypes.byref(ry), ctypes.byref(child))
        xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeMotionEvent(display, -1, rx.value, ry.value, 0)
        xtst.XTestFakeButtonEvent(display, 1, 1, 0)
        xtst.XTestFakeButtonEvent(display, 1, 0, 0)
        x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XSync(display, 0)
    finally:
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay(display)
