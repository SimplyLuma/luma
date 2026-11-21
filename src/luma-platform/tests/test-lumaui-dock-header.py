#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""The dock header band (contrast in every treatment, geometry), DockTile.set_status, ModeSwitch(icon_only)."""
import re
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

APPKIT = Path(__file__).resolve().parents[1] / "appkit"
TREATMENTS = {"light": "luma-appkit-tokens.css", "dark": "luma-appkit-dark-tokens.css",
              "frost": "luma-appkit-frost-tokens.css", "glass": "luma-appkit-glass-tokens.css"}
DEFINE = re.compile(r"^@define-color luma_([a-z0-9_]+) ([^;]+);", re.M)


def colours(sheet: str) -> dict:
    found = {}
    for name, value in DEFINE.findall((APPKIT / sheet).read_text()):
        found[name] = value.strip()  # the last definition wins, as in GTK
    return found


def rgba(value: str) -> tuple[float, float, float, float]:
    value = value.replace(" ", "")
    if value.startswith("#"):
        digits = value[1:]
        return tuple(int(digits[i:i + 2], 16) / 255 for i in (0, 2, 4)) + (1.0,)
    match = re.fullmatch(r"rgba?\(([\d.]+),([\d.]+),([\d.]+)(?:,([\d.]+))?\)", value)
    assert match, f"not a plain colour: {value}"
    r, g, b, a = match.groups()
    return float(r) / 255, float(g) / 255, float(b) / 255, float(a or 1)


def over(top, bottom):
    r, g, b, a = top
    return tuple(c * a + d * (1 - a) for c, d in zip((r, g, b), bottom[:3])) + (1.0,)


def luminance(colour) -> float:
    def channel(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in colour[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def band_contrast() -> None:
    """Over a black and a white wallpaper: window, island, well, band; the header's words and glyphs read."""
    for treatment, sheet in TREATMENTS.items():
        c = colours(sheet)
        for wallpaper in ((0, 0, 0, 1), (1, 1, 1, 1)):
            ground = wallpaper
            for layer in ("window", "island", "well"):
                ground = over(rgba(c[layer]), ground)
            band = over(rgba(c["dock_header"]), ground)
            assert band != ground, f"{treatment}: the band is a step from the well"
            for ink, least in (("ink", 4.5), ("muted", 4.5), ("danger_ink", 4.5), ("record", 3.0)):
                ratio = contrast(over(rgba(c[ink]), band), band)
                assert ratio >= least, f"{treatment}: {ink} on the header band is {ratio:.2f}:1 (< {least})"


def settle(seconds: float = 0.3) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.005)


def header_and_status() -> None:
    from luma_appkit import Dock, DockTile, lumaui_tokens

    dock_tokens = lumaui_tokens.DOCK
    tile = DockTile("preview", "Preview", "monitor", Gtk.Label(label="stage"), main=True)
    other = DockTile("audio", "Audio", "volume-2", Gtk.Label(label="mixer"), closable=True)
    dock = Dock([tile, other], layout=Dock.split("h", 0.6, "preview", "audio"))
    window = Gtk.Window(child=dock, default_width=800, default_height=500)
    window.present()
    settle()
    # Ring, padding and hairline included, the band is header_height tall and the tile's full width, 1px in.
    ok, box = tile.header.compute_bounds(tile)
    assert ok and box.get_y() == 1 and box.get_x() == 1, (box.get_x(), box.get_y())
    assert box.get_y() + box.get_height() == dock_tokens["header_height"], box.get_height()
    assert box.get_width() == tile.get_width() - 2, (box.get_width(), tile.get_width())
    # set_status: dot and word after the title, More still at the end; the accessible label carries it.
    assert not tile.status_label.get_visible() and not tile.status_dot.get_visible()
    tile.set_status("Live", tone="live")
    settle(0.1)
    assert tile.status_label.get_visible() and tile.status_dot.get_visible() and tile.status.has_css_class("live")
    children = []
    child = tile.header.get_first_child()
    while child is not None:
        children.append(child)
        child = child.get_next_sibling()
    assert children.index(tile.status) == children.index(tile.title_label) + 1 and children[-1] is tile.more_button
    _, title_at = tile.title_label.compute_point(tile.header, _point(tile.title_label.get_width(), 0))
    _, status_at = tile.status.compute_point(tile.header, _point(0, 0))
    assert status_at.x >= title_at.x and status_at.x - title_at.x <= dock_tokens["header_gap"] + 1
    tile.set_status("Paused")
    assert not tile.status.has_css_class("live") and tile.status_label.get_label() == "Paused"
    tile.set_status(None)
    assert not tile.status_label.get_visible()
    try:
        tile.set_status("Live", tone="loud")
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown tone is refused")
    # A tab group dissolving puts actions and More back after the status, not between title and status.
    dock.move_tile("audio", "preview", "tabs")
    settle(0.1)
    dock.move_tile("audio", "preview", "right")
    settle(0.1)
    order = []
    child = tile.header.get_first_child()
    while child is not None:
        order.append(child)
        child = child.get_next_sibling()
    assert order.index(tile.actions) > order.index(tile.status) and order[-1] is tile.more_button
    window.destroy()


def _point(x, y):
    gi.require_version("Graphene", "1.0")
    from gi.repository import Graphene

    return Graphene.Point().init(x, y)


def icon_only_modes() -> None:
    from luma_appkit import ModeSwitch

    modes = ModeSwitch([("landscape", "Landscape", "rectangle-horizontal"), ("vertical", "Vertical", "smartphone")],
                       icon_only=True, label="Canvas")
    window = Gtk.Window(child=modes, default_width=1200, default_height=200)
    window.present()
    settle()
    assert modes.has_css_class("icon-only")
    for key, button in modes.buttons.items():
        assert not button.label_widget.get_visible(), key
        assert button.get_tooltip_text() == button.label_widget.get_label()
    modes.set_current("vertical")
    assert not any(b.label_widget.get_visible() for b in modes.buttons.values())
    for bad in (lambda: ModeSwitch([("a", "A"), ("b", "B")], icon_only=True),
                lambda: ModeSwitch([("a", "A", None), ("b", "B", "square")], icon_only=True)):
        try:
            bad()
        except ValueError:
            pass
        else:
            raise AssertionError("icon_only needs every mode's icon")
    window.destroy()


if __name__ == "__main__":
    band_contrast()
    Gtk.init()
    from luma_appkit import install_appkit

    install_appkit()
    header_and_status()
    icon_only_modes()
    print("ok")
