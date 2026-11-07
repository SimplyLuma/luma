# SPDX-License-Identifier: Apache-2.0
"""LumaUI: opening a website, on every posture.

`open_link(window, uri)` opens the page in the browser, as `Gtk.UriLauncher` does. On a TV,
where reading a website from the sofa is rarely what you want, it first offers a QR code to open
the page on your phone, with "Open in Viola" as the fallback (docs/developer-platform/tv-mode.md).
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

__all__ = ["open_link", "qr_matrix"]


def qr_matrix(text: str):
    """The QR modules for `text` (rows of booleans with the quiet zone), or None without an encoder."""
    for module in ("prairie_apps.qr_code", "luma_continuity.qr"):
        try:
            matrix = __import__(module, fromlist=["matrix"]).matrix
        except (ImportError, AttributeError):
            continue
        try:
            return matrix(text)
        except Exception:  # noqa: BLE001 - an encoder that fails draws no code
            return None
    return None


def _qr(uri: str, size: int) -> Gtk.Widget | None:
    matrix = qr_matrix(uri)
    if not matrix:
        return None
    area = Gtk.DrawingArea(content_width=size, content_height=size, halign=Gtk.Align.CENTER,
                           accessible_role=Gtk.AccessibleRole.IMG)
    area.add_css_class("lumaui-qr")
    area.update_property([Gtk.AccessibleProperty.LABEL], ["Code to scan with your phone"])

    def draw(_area, cr, width, height):
        cells = len(matrix)
        scale = max(1, min(width, height) // cells)
        left, top = (width - cells * scale) // 2, (height - cells * scale) // 2
        cr.set_source_rgb(1, 1, 1)  # scanners need dark modules on light, in every theme
        cr.rectangle(left, top, cells * scale, cells * scale)
        cr.fill()
        cr.set_source_rgb(0, 0, 0)
        for y, row in enumerate(matrix):
            for x, dark in enumerate(row):
                if dark:
                    cr.rectangle(left + x * scale, top + y * scale, scale, scale)
        cr.fill()
    area.set_draw_func(draw)
    return area


def open_link(window: Gtk.Window, uri: str) -> None:
    """Open a website: in the browser, or on a TV first as a code for your phone."""
    if not getattr(window, "tv_posture", False):
        Gtk.UriLauncher.new(uri).launch(window, None, None, None)
        return
    from .structure_layers import LayerHost
    from .tv_frame import set_part
    host = LayerHost.window_host(window)
    card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=48)
    card.update_property([Gtk.AccessibleProperty.LABEL], ["Open on Your Phone"])
    from gi.repository import LumaUI  # noqa: F401 - the unit for the code's size
    unit = LumaUI.ui_tv_unit(window.get_width(), window.get_height()) or 1.0
    code = _qr(uri, int(264 * unit))
    if code is not None:
        card.append(code)
    words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER, hexpand=True)
    title = Gtk.Label(label="Open on Your Phone", xalign=0, wrap=True)
    set_part(title, "title")
    words.append(title)
    place = uri.split("://", 1)[-1].rstrip("/")
    words.append(Gtk.Label(label=(f"Scan with your phone, or go to {place}" if code is not None
                                  else f"On your phone, go to {place}"), xalign=0, wrap=True))
    buttons = Gtk.Box(spacing=14, margin_top=20)
    set_part(buttons, "buttons")
    viola = Gtk.Button(label="Open in Viola")
    close = Gtk.Button(label="Close")
    close.add_css_class("lumaui-dialog-cancel")
    buttons.append(close)
    buttons.append(viola)
    words.append(buttons)
    card.append(words)
    handle = host.present_modal(card, initial_focus=close)
    close.connect("clicked", lambda *_a: handle.close())
    viola.connect("clicked", lambda *_a: (handle.close(), Gtk.UriLauncher.new(uri).launch(window, None, None, None)))
