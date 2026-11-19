#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""A private libinput context that loads Lua plugins from one directory.

The stock libinput 1.31 tools only look in /etc/libinput/plugins and
$libdir/libinput/plugins, which the compositor reads too, so a plugin under
test could not run there without changing the desktop. This opens its own
context through libinput.so, adds only the given plugin directory, mirrors
the touchpad settings and prints events and libinput's log in
`libinput debug-events` form. The recorder runs it as the "p" stream with
--shadow-plugins; nothing it decides reaches the desktop.

    shadow.py --plugins DIR [--flag ...] --device /dev/input/eventN ...

Standard library only (ctypes). Root, like libinput's own tools. Key events
are never printed.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import select
import signal
import sys

lib = ctypes.CDLL("libinput.so.10", use_errno=True)
libc = ctypes.CDLL(None, use_errno=True)

OPEN = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
CLOSE = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_void_p)
LOG = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p)


class Interface(ctypes.Structure):
    _fields_ = [("open_restricted", OPEN), ("close_restricted", CLOSE)]


P = ctypes.c_void_p
for name, res, args in (
    ("libinput_path_create_context", P, [ctypes.POINTER(Interface), P]),
    ("libinput_path_add_device", P, [P, ctypes.c_char_p]),
    ("libinput_log_set_priority", None, [P, ctypes.c_int]),
    ("libinput_log_set_handler", None, [P, LOG]),
    ("libinput_plugin_system_append_path", None, [P, ctypes.c_char_p]),
    ("libinput_plugin_system_load_plugins", ctypes.c_int, [P, ctypes.c_int]),
    ("libinput_get_fd", ctypes.c_int, [P]),
    ("libinput_dispatch", ctypes.c_int, [P]),
    ("libinput_get_event", P, [P]),
    ("libinput_event_get_type", ctypes.c_int, [P]),
    ("libinput_event_get_device", P, [P]),
    ("libinput_event_destroy", None, [P]),
    ("libinput_device_get_sysname", ctypes.c_char_p, [P]),
    ("libinput_device_get_name", ctypes.c_char_p, [P]),
    ("libinput_event_get_pointer_event", P, [P]),
    ("libinput_event_get_gesture_event", P, [P]),
    ("libinput_event_pointer_get_time_usec", ctypes.c_uint64, [P]),
    ("libinput_event_pointer_get_dx", ctypes.c_double, [P]),
    ("libinput_event_pointer_get_dy", ctypes.c_double, [P]),
    ("libinput_event_pointer_get_button", ctypes.c_uint32, [P]),
    ("libinput_event_pointer_get_button_state", ctypes.c_int, [P]),
    ("libinput_event_pointer_has_axis", ctypes.c_int, [P, ctypes.c_int]),
    ("libinput_event_pointer_get_scroll_value", ctypes.c_double, [P, ctypes.c_int]),
    ("libinput_event_gesture_get_time_usec", ctypes.c_uint64, [P]),
    ("libinput_event_gesture_get_finger_count", ctypes.c_int, [P]),
    ("libinput_event_gesture_get_cancelled", ctypes.c_int, [P]),
    ("libinput_device_config_tap_set_enabled", ctypes.c_int, [P, ctypes.c_int]),
    ("libinput_device_config_tap_set_drag_enabled", ctypes.c_int, [P, ctypes.c_int]),
    ("libinput_device_config_tap_set_drag_lock_enabled", ctypes.c_int, [P, ctypes.c_int]),
    ("libinput_device_config_tap_set_button_map", ctypes.c_int, [P, ctypes.c_int]),
    ("libinput_device_config_scroll_set_natural_scroll_enabled", ctypes.c_int, [P, ctypes.c_int]),
    ("libinput_device_config_scroll_set_method", ctypes.c_int, [P, ctypes.c_int]),
    ("libinput_device_config_dwt_set_enabled", ctypes.c_int, [P, ctypes.c_int]),
    ("libinput_device_config_click_set_method", ctypes.c_int, [P, ctypes.c_int]),
    ("libinput_device_config_middle_emulation_set_enabled", ctypes.c_int, [P, ctypes.c_int]),
    ("libinput_device_config_accel_set_speed", ctypes.c_int, [P, ctypes.c_double]),
):
    fn = getattr(lib, name)
    fn.restype, fn.argtypes = res, args
libc.vsnprintf.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_void_p]

NAMES = {1: "DEVICE_ADDED", 2: "DEVICE_REMOVED", 400: "POINTER_MOTION", 402: "POINTER_BUTTON",
         405: "POINTER_SCROLL_FINGER", 800: "GESTURE_SWIPE_BEGIN", 801: "GESTURE_SWIPE_UPDATE",
         802: "GESTURE_SWIPE_END", 803: "GESTURE_PINCH_BEGIN", 804: "GESTURE_PINCH_UPDATE",
         805: "GESTURE_PINCH_END", 806: "GESTURE_HOLD_BEGIN", 807: "GESTURE_HOLD_END"}
BUTTONS = {0x110: "BTN_LEFT", 0x111: "BTN_RIGHT", 0x112: "BTN_MIDDLE"}
_buf = ctypes.create_string_buffer(4096)


def _open(path, flags, _data):
    try:
        return os.open(path, flags | os.O_CLOEXEC)
    except OSError as exc:
        return -exc.errno


def _close(fd, _data):
    os.close(fd)


def _log(_li, _prio, fmt, va):
    libc.vsnprintf(_buf, len(_buf), fmt, va)
    sys.stdout.write(_buf.value.decode("utf-8", "replace"))
    sys.stdout.flush()


_keep = [OPEN(_open), CLOSE(_close), LOG(_log)]   # the C side holds these pointers


def configure(dev, flags: list[str]) -> None:
    set_ = {
        "--enable-tap": (lib.libinput_device_config_tap_set_enabled, 1),
        "--disable-tap": (lib.libinput_device_config_tap_set_enabled, 0),
        "--enable-drag": (lib.libinput_device_config_tap_set_drag_enabled, 1),
        "--disable-drag": (lib.libinput_device_config_tap_set_drag_enabled, 0),
        "--enable-drag-lock": (lib.libinput_device_config_tap_set_drag_lock_enabled, 1),
        "--disable-drag-lock": (lib.libinput_device_config_tap_set_drag_lock_enabled, 0),
        "--enable-natural-scrolling": (lib.libinput_device_config_scroll_set_natural_scroll_enabled, 1),
        "--disable-natural-scrolling": (lib.libinput_device_config_scroll_set_natural_scroll_enabled, 0),
        "--enable-dwt": (lib.libinput_device_config_dwt_set_enabled, 1),
        "--disable-dwt": (lib.libinput_device_config_dwt_set_enabled, 0),
        "--enable-middlebutton": (lib.libinput_device_config_middle_emulation_set_enabled, 1),
        "--disable-middlebutton": (lib.libinput_device_config_middle_emulation_set_enabled, 0),
        "--set-click-method=clickfinger": (lib.libinput_device_config_click_set_method, 2),
        "--set-click-method=buttonareas": (lib.libinput_device_config_click_set_method, 1),
        "--set-click-method=none": (lib.libinput_device_config_click_set_method, 0),
        "--set-scroll-method=twofinger": (lib.libinput_device_config_scroll_set_method, 1),
        "--set-scroll-method=edge": (lib.libinput_device_config_scroll_set_method, 2),
        "--set-scroll-method=none": (lib.libinput_device_config_scroll_set_method, 0),
        "--set-tap-map=lrm": (lib.libinput_device_config_tap_set_button_map, 0),
        "--set-tap-map=lmr": (lib.libinput_device_config_tap_set_button_map, 1),
    }
    for flag in flags:
        if flag in set_:
            fn, value = set_[flag]
            fn(dev, value)
        elif flag.startswith("--set-speed="):
            lib.libinput_device_config_accel_set_speed(dev, float(flag.split("=", 1)[1]))


def describe(ev, kind: int, sysname: str, t0: list) -> str | None:
    name = NAMES.get(kind)
    if name is None:
        return None                                  # keys, tablets, switches: not printed
    if kind in (1, 2):
        dev = lib.libinput_event_get_device(ev)
        return f"-{sysname:<8} {name:<28} {lib.libinput_device_get_name(dev).decode('utf-8', 'replace')}"
    if kind < 800:
        p = lib.libinput_event_get_pointer_event(ev)
        t = lib.libinput_event_pointer_get_time_usec(p)
        if kind == 400:
            text = f"{lib.libinput_event_pointer_get_dx(p):6.2f}/{lib.libinput_event_pointer_get_dy(p):6.2f}"
        elif kind == 402:
            b = lib.libinput_event_pointer_get_button(p)
            state = "pressed" if lib.libinput_event_pointer_get_button_state(p) else "released"
            text = f"{BUTTONS.get(b, 'BTN_' + str(b))} ({b}) {state}"
        else:
            v = lib.libinput_event_pointer_get_scroll_value(p, 0) if lib.libinput_event_pointer_has_axis(p, 0) else 0
            h = lib.libinput_event_pointer_get_scroll_value(p, 1) if lib.libinput_event_pointer_has_axis(p, 1) else 0
            text = f"vert {v:.2f}/0.0 horiz {h:.2f}/0.0 (finger)"
    else:
        g = lib.libinput_event_get_gesture_event(ev)
        t = lib.libinput_event_gesture_get_time_usec(g)
        text = str(lib.libinput_event_gesture_get_finger_count(g))
        if kind in (802, 805, 807) and lib.libinput_event_gesture_get_cancelled(g):
            text += " cancelled"
    if not t0:
        t0.append(t)
    return f" {sysname:<8} {name:<28} {(t - t0[0]) / 1e6:+.3f}s\t{text}"


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--plugins", required=True)
    p.add_argument("--device", action="append", default=[])
    p.add_argument("--flag", action="append", default=[])
    args = p.parse_args(argv)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    iface = Interface(_keep[0], _keep[1])
    li = lib.libinput_path_create_context(ctypes.byref(iface), None)
    lib.libinput_log_set_handler(li, _keep[2])
    lib.libinput_log_set_priority(li, 10)                 # LIBINPUT_LOG_PRIORITY_DEBUG
    lib.libinput_plugin_system_append_path(li, os.path.realpath(args.plugins).encode())
    lib.libinput_plugin_system_load_plugins(li, 0)
    print(f"shadow: plugins from {os.path.realpath(args.plugins)}", flush=True)
    for path in args.device:
        if not lib.libinput_path_add_device(li, path.encode()):
            print(f"Failed to initialize device {path}", flush=True)
    fd = lib.libinput_get_fd(li)
    t0: list = []
    while True:
        select.select([fd], [], [], 1.0)
        lib.libinput_dispatch(li)
        while True:
            ev = lib.libinput_get_event(li)
            if not ev:
                break
            kind = lib.libinput_event_get_type(ev)
            dev = lib.libinput_event_get_device(ev)
            if kind == 1:
                configure(dev, args.flag)
            line = describe(ev, kind, lib.libinput_device_get_sysname(dev).decode(), t0)
            lib.libinput_event_destroy(ev)
            if line:
                print(line, flush=True)


if __name__ == "__main__":
    sys.exit(main())
