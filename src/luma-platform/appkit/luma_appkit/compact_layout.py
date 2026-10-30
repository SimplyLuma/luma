# SPDX-License-Identifier: Apache-2.0
"""Shared policy for compact physical screens, independent of app identity."""

COMPACT_SCREEN_LIMIT = 600


def bottom_attached(width: int, height: int, mobile: bool) -> bool:
    return mobile and min(width, height) > 0 and max(width, height) < COMPACT_SCREEN_LIMIT


def minimum_size(width: int, height: int, screen_width: int, screen_height: int,
                 fullscreen: bool) -> tuple[int, int]:
    if fullscreen and min(screen_width, screen_height) > 0:
        return min(width, screen_width), min(height, screen_height)
    return width, height


def compact_bar(widget) -> bool:
    from .lumaui import mobile_form_factor
    if not mobile_form_factor():
        return False
    display = widget.get_display()
    native = widget.get_native()
    surface = native.get_surface() if native is not None else None
    monitor = display.get_monitor_at_surface(surface) if surface is not None else None
    if monitor is None:
        monitors = display.get_monitors()
        monitor = monitors.get_item(0) if monitors.get_n_items() else None
    if monitor is None:
        return False
    geometry = monitor.get_geometry()
    return bottom_attached(geometry.width, geometry.height, True)
