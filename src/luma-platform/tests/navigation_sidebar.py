#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""The regular navigation sidebar, loaded the way an application loads the kit.

The kit's sheets reach GTK as separate providers at one priority, and GTK takes
each property from the last provider that sets it, whatever the specificity in
another. A bare `.luma-navigation-list row { background: transparent }` in
luma-appkit.css therefore beat the base sheet's selected and hover fills: no
regular sidebar showed which view was open. This renders a real sidebar through
install_appkit() in light and dark and measures it against v70 (?app=contacts):

- the sidebar stands on the frame: no island class and no fill of its own;
- a row is 50px tall and the selected one carries a visible, neutral fill;
- a heading's hairline runs beside its label, 1px, to the sidebar's inset;
- an app bar (a pane's title row) ends in a 1px rule.

    LUMA_APPKIT_STYLE_PATH=appkit/luma-appkit.css LUMA_APPKIT_BASE_PATH=appkit/luma-appkit-base.css \
    LUMA_APPKIT_TOKENS_PATH=appkit/luma-appkit-tokens.css xvfb-run -a python3 tests/navigation_sidebar.py
"""
from __future__ import annotations

import os
import sys
import time

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402


def settle(seconds: float = 0.6) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.005)


def pixels(window: Gtk.Widget, widget: Gtk.Widget):
    """Render `widget` as the window draws it; return (width, height, rgba bytes)."""
    width, height = widget.get_width(), widget.get_height()
    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, width, height)
    node = snapshot.to_node()
    texture = window.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, width, height))
    downloader = Gdk.TextureDownloader.new(texture)
    downloader.set_format(Gdk.MemoryFormat.R8G8B8A8)
    data, stride = downloader.download_bytes()
    return width, height, data.get_data(), stride


def colour_at(image, x: int, y: int) -> tuple[int, int, int, int]:
    _width, _height, data, stride = image
    offset = y * stride + x * 4
    return tuple(data[offset:offset + 4])


def grey(image, x: int, y: int) -> tuple[int, int, int]:
    """The pixel (straight RGBA) laid over mid grey."""
    r, g, b, a = colour_at(image, x, y)
    return tuple(round(128 + (c - 128) * a / 255) for c in (r, g, b))


def bounds(widget: Gtk.Widget, relative: Gtk.Widget) -> Graphene.Rect:
    ok, rect = widget.compute_bounds(relative)
    assert ok, f"{widget} is not laid out"
    return rect


def check(dark: bool, failures: list[str]) -> None:
    from luma_appkit import NavigationRow, NavigationSidebar

    mode = "dark" if dark else "light"
    sidebar = NavigationSidebar()
    sidebar.set_size_request(sidebar.get_size_request()[0], 360)
    sidebar.append_section("Library")
    rows = [NavigationRow(title, icon_name="folder-symbolic", trailing=str(n))
            for n, title in enumerate(("All books", "Reading now", "New"))]
    for row in rows:
        sidebar.append_row(row)
    sidebar.append_section("Where")
    sidebar.append_row(NavigationRow("This device", icon_name="drive-harddisk-symbolic", trailing="6"))
    sidebar.list.select_row(rows[0])
    bar = Gtk.Box()
    bar.add_css_class("luma-appbar")
    bar.append(Gtk.Label(label="All books"))
    bar.set_size_request(240, -1)
    box = Gtk.Box(spacing=9, margin_start=9, margin_top=9, margin_bottom=9, margin_end=9)
    box.append(sidebar)
    box.append(bar)
    bar.set_valign(Gtk.Align.START)
    window = Adw.ApplicationWindow(application=app, default_width=560, default_height=400)
    window.add_css_class("luma-app-window")
    window.set_content(box)
    window.present()
    settle()

    # Each widget is rendered alone and every pixel is laid over mid grey, so
    # the result does not depend on the surface behind it: under a stock
    # toolkit the island is transparent, under Luma's it is opaque.
    image = pixels(window, sidebar)
    if os.environ.get("LUMA_TEST_SNAPSHOTS"):
        texture = Gdk.MemoryTexture.new(image[0], image[1], Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(image[2]), image[3])
        texture.save_to_png(os.path.join(os.environ["LUMA_TEST_SNAPSHOTS"], f"navigation-sidebar-{mode}.png"))
    at = lambda widget: bounds(widget, sidebar)  # noqa: E731
    from luma_appkit.lumaui_tokens import SIDEBAR
    if sidebar.has_css_class("luma-island"):
        failures.append(f"{mode}: the sidebar is a boxed island, not the frame's surface")
    corner = colour_at(image, 0, image[1] - 1)
    if corner[3] != 0:
        failures.append(f"{mode}: the sidebar paints its own surface ({corner}) instead of standing on the frame")
    selected, plain = at(rows[0]), at(rows[1])
    for row in (selected, plain):
        if abs(row.get_height() - SIDEBAR["row_height"]) > 1:
            failures.append(f"{mode}: a row is {row.get_height()}px tall, not {SIDEBAR['row_height']}")
    # A point clear of text: 3px below the row's top edge, 60% across.
    probe = lambda r: grey(image, int(r.get_x() + r.get_width() * 0.6), int(r.get_y() + 3))  # noqa: E731
    on, off = probe(selected), probe(plain)
    delta = [on[i] - off[i] for i in range(3)]
    if max(abs(d) for d in delta) < 6:
        failures.append(f"{mode}: the selected row has no visible fill ({on} against {off})")
    elif max(delta) - min(delta) > 8:
        failures.append(f"{mode}: the selected row's fill is tinted, not neutral ({on} against {off})")

    sections = [child for child in _children(sidebar.list) if child.has_css_class("luma-navigation-section")]
    for section in sections:
        label = _find(section, "luma-navigation-heading")
        rule = _find(section, "luma-navigation-heading-rule")
        if label is None or rule is None:
            failures.append(f"{mode}: a heading has no hairline beside its label")
            continue
        text, line = at(label), at(rule)
        gap = line.get_x() - (text.get_x() + text.get_width())
        if round(line.get_height()) != 1 or abs(gap - SIDEBAR["heading_gap"]) > 0.5:
            failures.append(f"{mode}: the hairline is {line.get_width()}x{line.get_height()}, {gap}px from its label")
        right = selected.get_x() + selected.get_width() - SIDEBAR["heading_inset"]
        if abs(line.get_x() + line.get_width() - right) > 0.5:
            failures.append(f"{mode}: the hairline ends at {line.get_x() + line.get_width()}, not {right}")
        x, y = int(line.get_x() + line.get_width() / 2), int(line.get_y())
        if all(grey(image, x, y + d) == grey(image, x, y - 4) for d in (0, 1)):
            failures.append(f"{mode}: the hairline is not drawn at {line.get_x()},{line.get_y()}")
    heading = at(sections[0])
    if abs(selected.get_y() - (heading.get_y() + heading.get_height() + SIDEBAR["heading_bottom"])) > 0.5:
        failures.append(f"{mode}: the rows do not start {SIDEBAR['heading_bottom']}px under their heading")

    strip = pixels(window, bar)
    middle, bottom = strip[0] // 2, strip[1]
    if grey(strip, middle, bottom - 1) == grey(strip, middle, bottom - 4):
        failures.append(f"{mode}: the app bar has no rule under it")
    window.close()
    settle(0.2)


def _children(widget: Gtk.Widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def _find(widget: Gtk.Widget, css_class: str):
    for child in _children(widget):
        if child.has_css_class(css_class):
            return child
        found = _find(child, css_class)
        if found is not None:
            return found
    return None


app = Adw.Application(application_id="org.projectluma.NavigationSidebarTest", flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
assert Gdk.Display.get_default() is not None, "needs a display"
failures: list[str] = []
tokens = os.environ.get("LUMA_APPKIT_TOKENS_PATH", "")
style = os.environ.get("LUMA_APPKIT_STYLE_PATH", "")
for dark in (False, True):
    # The kit picks its sheets from the desktop's treatment; here the paths say which.
    if tokens:
        os.environ["LUMA_APPKIT_TOKENS_PATH"] = tokens.replace(
            "luma-appkit-tokens.css", "luma-appkit-dark-tokens.css" if dark else "luma-appkit-tokens.css")
    if style:
        os.environ["LUMA_APPKIT_STYLE_PATH"] = style.replace(
            "luma-appkit.css", "luma-appkit-dark.css" if dark else "luma-appkit.css")
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
    import luma_appkit.widgets as widgets
    widgets._appkit_providers.pop(Gdk.Display.get_default(), None)
    widgets.install_appkit()
    check(dark, failures)
if failures:
    raise SystemExit("FAIL: " + "; ".join(failures))
print("ok: the sidebar stands on the frame, 50px rows show their selection, headings carry a hairline, and the app bar ends in a rule")
sys.exit(0)
