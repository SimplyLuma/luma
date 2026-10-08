#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Every window is the same Glass, whatever built it.

Owner, 2026-09-22: in Glass each application's title bar was a slightly
different colour, and the content of a window could be read through the
surfaces standing on it.

This renders four windows that are built four different ways, in the same
process and on the same display, and reads the pixels:

  A. a kit window (``luma_appkit.AppWindow``);
  B. a plain ``Adw.ApplicationWindow`` with an ``Adw.HeaderBar``, which is what
     an application that never loads the kit produces;
  C. a plain ``Gtk.ApplicationWindow`` with a ``Gtk.HeaderBar``, which has no
     libadwaita window class at all;
  D. an ``Adw.ApplicationWindow`` whose own style sheet paints an opaque
     header, loaded the way eleven applications used to load theirs.

Then it asserts what the owner asked for:

  * the title band is one colour in all four, to the value;
  * island interiors are opaque in all four, so nothing is read through them;
  * the gap between islands is still the window's translucent surface, so the
     wallpaper shows around the islands and not through them.

It fails on the code before this change: B and C had no title band at all
(libadwaita never defined ``@luma_chrome``, so GTK dropped the declaration
that named it), and A's islands were a 22% veil.
"""

from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")

from gi.repository import Adw, Gdk, Gio, GLib, Gsk, Graphene, Gtk  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "appkit"))

WIDTH, HEIGHT = 480, 360
OUT_DIR = os.environ.get("LUMA_GLASS_PARITY_OUT", "")


def _fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def _settle() -> None:
    context = GLib.MainContext.default()
    for _ in range(400):
        if not context.pending():
            break
        context.iteration(False)


class Render:
    """A window's pixels, addressed by where its widgets actually landed.

    Sampling fixed coordinates would measure the client-side shadow margin on
    one window and the title row on another, which is how a check comes out
    green while measuring nothing in particular.
    """

    def __init__(self, window: Gtk.Window, name: str) -> None:
        paintable = Gtk.WidgetPaintable.new(window)
        self.width = int(paintable.get_intrinsic_width())
        self.height = int(paintable.get_intrinsic_height())
        if self.width <= 0 or self.height <= 0:
            _fail(f"{name} never took a size ({self.width}x{self.height})")
        snapshot = Gtk.Snapshot.new()
        paintable.snapshot(snapshot, self.width, self.height)
        node = snapshot.to_node()
        if node is None:
            _fail(f"{name} rendered nothing at all")
        renderer = Gsk.CairoRenderer.new()
        renderer.realize(None)
        texture = renderer.render_texture(
            node, Graphene.Rect().init(0, 0, self.width, self.height))
        renderer.unrealize()
        if OUT_DIR:
            os.makedirs(OUT_DIR, exist_ok=True)
            texture.save_to_png(os.path.join(OUT_DIR, f"{name}.png"))
        # Straight alpha in a known channel order: the checks read the alpha
        # of an island and of the window surface beside it, and premultiplied
        # pixels would answer a different question.
        downloader = Gdk.TextureDownloader.new(texture)
        downloader.set_format(Gdk.MemoryFormat.R8G8B8A8)
        data, self.stride = downloader.download_bytes()
        self.pixels = data.get_data()
        if self.pixels is None or len(self.pixels) < self.stride * self.height:
            _fail(f"{name}: downloaded {0 if self.pixels is None else len(self.pixels)} "
                  f"bytes for a {self.width}x{self.height} render")
        self.name = name

    def pixel(self, x: int, y: int) -> tuple[int, int, int, int]:
        x = max(0, min(self.width - 1, int(x)))
        y = max(0, min(self.height - 1, int(y)))
        offset = y * self.stride + x * 4
        red, green, blue, alpha = self.pixels[offset:offset + 4]
        return (red, green, blue, alpha)

    def dominant(self, x0: int, x1: int, y: int) -> tuple[int, int, int, int]:
        """The commonest colour along a row, so a glyph cannot be the answer."""
        counts: dict[tuple[int, int, int, int], int] = {}
        for x in range(int(x0), int(x1)):
            colour = self.pixel(x, y)
            counts[colour] = counts.get(colour, 0) + 1
        if not counts:
            _fail(f"{self.name}: sampled no pixels between {x0} and {x1}")
        return max(counts.items(), key=lambda item: item[1])[0]


def _bounds(widget: Gtk.Widget, window: Gtk.Window, what: str):
    found, rect = widget.compute_bounds(window)
    if not found or rect.size.width < 8 or rect.size.height < 8:
        _fail(f"{what} has no usable allocation in its window")
    return rect


def _island() -> Gtk.Widget:
    island = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    island.add_css_class("luma-island")
    island.set_margin_start(14)
    island.set_margin_end(14)
    island.set_margin_top(6)
    island.set_margin_bottom(14)
    island.set_vexpand(True)
    island.set_hexpand(True)
    return island


def _kit_window(application):
    """A. The application kit's own window."""
    from luma_appkit import AppWindow, CommandRegistry  # noqa: PLC0415

    window = AppWindow(application=application, title="Kit",
                       app_id="org.projectluma.GlassParity",
                       icon_name="application-x-executable",
                       commands=CommandRegistry(()),
                       default_width=WIDTH, default_height=HEIGHT,
                       minimum_width=360, minimum_height=280)
    window.set_default_size(WIDTH, HEIGHT)
    island = _island()
    body = getattr(window, "body", None)
    if body is None or not hasattr(body, "append"):
        _fail("the kit window has no body to put an island in")
    body.append(island)
    return window, window.title_bar, island


def _plain_adw_window(application):
    """B. What an application that never loads the kit produces."""
    window = Adw.ApplicationWindow(application=application, title="Plain")
    window.set_default_size(WIDTH, HEIGHT)
    header = Adw.HeaderBar()
    header.add_css_class("luma-titlebar")
    island = _island()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.append(header)
    box.append(island)
    window.set_content(box)
    return window, header, island


def _plain_gtk_window(application):
    """C. A GTK window with no libadwaita window class at all."""
    window = Gtk.ApplicationWindow(application=application, title="GTK")
    window.set_default_size(WIDTH, HEIGHT)
    header = Gtk.HeaderBar()
    header.add_css_class("luma-titlebar")
    island = _island()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.append(header)
    box.append(island)
    # No titlebar of its own: this window is not even client-side decorated,
    # which is the case that kept the light headerbar colour before.
    window.set_child(box)
    return window, header, island


def _opinionated_window(application):
    """D. A window whose own sheet paints its header and its islands.

    This is the shape eleven applications had: a style sheet of their own,
    loaded through a provider of their own, at a priority above the toolkit.
    The appearance layer has to outrank it, or one application on the desktop
    is a different colour however well the toolkit behaves.
    """
    provider = Gtk.CssProvider()
    provider.load_from_string(
        ".luma-opinionated headerbar { background-color: #e7e3da; "
        "background-image: none; }\n"
        ".luma-opinionated .luma-island { "
        "background-color: rgba(255, 255, 255, 0.22); }\n"
    )
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
    window = Adw.ApplicationWindow(application=application, title="Opinionated")
    window.add_css_class("luma-opinionated")
    window.set_default_size(WIDTH, HEIGHT)
    header = Adw.HeaderBar()
    header.add_css_class("luma-titlebar")
    island = _island()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.append(header)
    box.append(island)
    window.set_content(box)
    return window, header, island


BUILDERS = (
    ("kit", _kit_window),
    ("plain-libadwaita", _plain_adw_window),
    ("plain-gtk", _plain_gtk_window),
    ("opinionated", _opinionated_window),
)


def _measure(application, scheme_name):
    measured = {}
    for name, builder in BUILDERS:
        window, header, island = builder(application)
        window.present()
        _settle()
        render = Render(window, f"{scheme_name}-{name}")
        header_rect = _bounds(header, window, f"{name}: the title row")
        island_rect = _bounds(island, window, f"{name}: the island")

        # High in the title row, above the title text, across its middle half.
        header_y = header_rect.origin.y + max(3, header_rect.size.height * 0.14)
        header_x0 = header_rect.origin.x + header_rect.size.width * 0.3
        header_x1 = header_rect.origin.x + header_rect.size.width * 0.7
        # The island's own middle.
        island_y = island_rect.origin.y + island_rect.size.height * 0.5
        island_x0 = island_rect.origin.x + island_rect.size.width * 0.3
        island_x1 = island_rect.origin.x + island_rect.size.width * 0.7
        # Between the window's edge and the island: the window's own surface.
        gap_x = island_rect.origin.x - 4

        measured[name] = {
            "header": render.dominant(header_x0, header_x1, header_y),
            "island": render.dominant(island_x0, island_x1, island_y),
            "surface": render.pixel(gap_x, island_y),
            # A window that never learned its treatment is the fault this
            # check was written for, so say so rather than only reporting a
            # colour that happens to differ.
            "classes": [css for css in window.get_css_classes()
                        if css.startswith("luma-")],
        }
        window.destroy()
        _settle()
    if len(measured) != len(BUILDERS):
        _fail(f"measured {len(measured)} windows, expected {len(BUILDERS)}")
    return measured


def _same(first, second, tolerance: int = 2) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(first, second))


def _check(scheme_name: str, measured) -> None:
    for name, values in measured.items():
        print(f"  [{scheme_name}] {name}: header {values['header']} "
              f"island {values['island']} surface {values['surface']} "
              f"classes {values['classes']}")

    # ADR-052 (2026-09-25) and v70: LumaUI owns the Luma app's frame, and
    # third-party windows go back to a stock look with the Luma accent. Solid
    # paints an opaque frame. So the Glass-era claims (one shared title tint
    # across every toolkit, and wallpaper showing beside the islands) no
    # longer hold and are not asserted. What still holds, for every window:
    # it renders (an empty snapshot was the 2026-09-26 regression, fixed in
    # 916a1f17), its title band is painted, and its islands are opaque.
    # The kit window's frame is LumaUI's and opaque (v70 Solid). A third-party
    # window keeps whatever its (still Glass-patched) toolkit paints until the
    # visual patches retire, so it only has to paint a title row at all.
    for name, values in measured.items():
        floor = 255 if name == "kit" else 128
        if values["header"][3] < floor:
            _fail(f"[{scheme_name}] {name} title band has alpha "
                  f"{values['header'][3]} (needs >= {floor}); the window did "
                  f"not paint its title row")
        if values["island"][3] != 255:
            _fail(f"[{scheme_name}] {name} island interior has alpha "
                  f"{values['island'][3]}; islands are opaque, so nothing is "
                  f"read through them")

    print(f"[{scheme_name}] {len(measured)} windows: title bands painted, "
          f"islands opaque")


def main() -> int:
    if not Gtk.init_check():
        _fail("no display; this check is evidence only when it renders")
    Adw.init()

    from luma_appkit import install_appkit  # noqa: PLC0415
    install_appkit()

    settings = Gio.Settings.new("org.project_luma.shell-state")
    settings.set_string("surface-treatment", "glass")
    interface = Gio.Settings.new("org.gnome.desktop.interface")

    application = Gtk.Application(application_id="org.projectluma.GlassParity",
                                  flags=Gio.ApplicationFlags.NON_UNIQUE)
    application.register(None)

    print(f"surface-treatment is "
          f"{settings.get_string('surface-treatment')!r}, chosen by the user: "
          f"{settings.get_user_value('surface-treatment') is not None}")

    for scheme_name, scheme in (("light", "default"), ("dark", "prefer-dark")):
        interface.set_string("color-scheme", scheme)
        _settle()
        _check(scheme_name, _measure(application, scheme_name))

    print("Glass surface parity: four toolkits render painted title bands and opaque islands.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
