# SPDX-License-Identifier: Apache-2.0
"""First-boot setup: the "Set up this Luma" sheet over the softened wallpaper.

One window, full screen and undecorated, that draws the room itself (the
wallpaper, softened) and one flat sheet on it: the head (the Luma mark, "Set
up this Luma", accessibility, the page dots), the page, and the foot (Back,
Skip where a page may be skipped, and the key). Desktop: a centred sheet 720
wide. Phone: the same sheet as a drawer from the bottom edge, full width,
lists in one column, a full-width 50 px key. TV: sheet_tv, from LumaUI's TV
layer, driven by the remote (posture()).

Nothing here can be left for good: the device cannot run without an
account. The only skip is on Get online. Esc does nothing; Return presses
the key, except where it joins a network.
"""
from __future__ import annotations

import enum
import functools
import logging
import os
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from . import catalog, system  # noqa: E402
from .connect import ConnectFlow  # noqa: E402
from .model import Setup, mismatch, strength, username_problem  # noqa: E402

__all__ = ("SetupWindow", "Posture", "posture", "Field", "icon", "load_style")

log = logging.getLogger("luma-setup")

HEAD = "Set up this Luma"
OWNER = "You’re this Luma’s owner. Signing in also unlocks your saved passwords."
FACES = Path(os.environ.get("LUMA_SETUP_FACES", "/usr/share/pixmaps/faces"))
WALLPAPERS = Path("/usr/share/backgrounds/luma")


class Posture(enum.Enum):
    DESKTOP = "desktop"
    PHONE = "phone"
    TV = "tv"


def posture() -> Posture:
    """LUMA_SETUP_POSTURE, else TV where LumaUI's TV layer says so (and has its parts), phone on a phone."""
    forced = os.environ.get("LUMA_SETUP_POSTURE", "").strip().lower()
    if forced in {p.value for p in Posture}:
        return Posture(forced)
    try:
        from luma_appkit import lumaui
    except ImportError:
        return Posture.DESKTOP
    tv = getattr(lumaui, "tv_posture", None)
    if tv is not None and tv():
        try:
            from luma_appkit.tv_parts import TvField  # noqa: F401 - the TV pages need it
            return Posture.TV
        except ImportError:
            pass
    if lumaui.mobile_form_factor():
        return Posture.PHONE
    return Posture.DESKTOP


def trace(*parts) -> None:
    """LUMA_SETUP_TRACE=<file>: focus and key events, one line each (device test runs only)."""
    path = os.environ.get("LUMA_SETUP_TRACE", "")
    if path:
        import time
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{time.monotonic():.3f} " + " ".join(str(p) for p in parts) + "\n")


def describe(widget: Gtk.Widget | None) -> str:
    if widget is None:
        return "-"
    owner = widget
    while owner is not None and not owner.get_name().startswith("su-") and not isinstance(owner, Gtk.Button):
        owner = owner.get_parent()
    owner = owner or widget
    text = owner.get_label() if isinstance(owner, Gtk.Button) else ""
    return f"{type(widget).__name__}<{type(owner).__name__}#{owner.get_name()}.{'.'.join(owner.get_css_classes())} {text!r}>"


# -- small parts ------------------------------------------------------------------
def icon(name: str, size: int = 16, *classes: str) -> Gtk.Image:
    """A Lucide glyph (LumaUI's lumaui-<name>-symbolic)."""
    image = Gtk.Image(icon_name=f"lumaui-{name}-symbolic", pixel_size=size)
    for css in classes:
        image.add_css_class(css)
    return image


def label(text: str, *classes: str, xalign: float = 0.0, wrap: bool = False, **props) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=xalign, wrap=wrap, **props)
    for css in classes:
        widget.add_css_class(css)
    return widget


def box(*children: Gtk.Widget, vertical: bool = False, spacing: int = 0, css: tuple = (), **props) -> Gtk.Box:
    widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                     spacing=spacing, **props)
    for name in css:
        widget.add_css_class(name)
    for child in children:
        if child is not None:
            widget.append(child)
    return widget


def button(text: str, *classes: str, on_click: Callable[[], None] | None = None, **props) -> Gtk.Button:
    widget = Gtk.Button(label=text, **props)
    for css in classes:
        widget.add_css_class(css)
    if on_click is not None:
        widget.connect("clicked", lambda _b: on_click())
    return widget


class Field(Gtk.Box):
    """The design's field: a recessed well with an optional lead, the text, and a trailing glyph."""

    __gtype_name__ = "LumaSetupField"

    def __init__(self, placeholder: str = "", *, value: str = "", prefix: str = "", lead: str = "",
                 trail: str = "", password: bool = False, purpose: Gtk.InputPurpose = Gtk.InputPurpose.FREE_FORM,
                 on_changed: Callable[[str], None] | None = None, on_activate: Callable[[], None] | None = None,
                 name: str = "") -> None:
        super().__init__(spacing=6)
        self.add_css_class("su-field")
        if lead:
            self.append(icon(lead, 15, "su-lead"))
        if prefix:
            self.append(label(prefix, "su-pre"))
        self.text = Gtk.Text(text=value, placeholder_text=placeholder, hexpand=True, visibility=not password,
                             input_purpose=Gtk.InputPurpose.PASSWORD if password else purpose)
        self.text.update_property([Gtk.AccessibleProperty.LABEL], [placeholder or prefix])
        if name:
            self.text.set_name(name)
        self.append(self.text)
        self.trail = icon(trail, 16, "su-ok" if trail == "check" else "su-pen") if trail else None
        if self.trail is not None:
            self.trail.set_visible(trail != "check")
            self.append(self.trail)
        self._quiet = False
        # Leaving a field leaves no selection drawn in it.
        leave = Gtk.EventControllerFocus()
        leave.connect("leave", lambda _c: self.text.select_region(self.text.get_position(), self.text.get_position()))
        self.text.add_controller(leave)
        if on_changed is not None:
            self.text.connect("changed", lambda t: None if self._quiet else on_changed(t.get_text()))
        if on_activate is not None:
            self.text.connect("activate", lambda _t: on_activate())

    @property
    def value(self) -> str:
        return self.text.get_text()

    def set_value(self, value: str) -> None:
        """Set without telling on_changed (the username following the name)."""
        if value != self.text.get_text():
            self._quiet = True
            position = len(value)
            self.text.set_text(value)
            self.text.set_position(position)
            self._quiet = False

    def set_mark(self, good: bool = False, bad: bool = False) -> None:
        (self.add_css_class if bad else self.remove_css_class)("su-bad")
        if self.trail is not None and self.trail.has_css_class("su-ok"):
            self.trail.set_visible(good)

    def do_grab_focus(self) -> bool:
        return self.text.grab_focus_without_selecting()


class ChoiceList(Gtk.Box):
    """A recessed list of rows; the chosen one is a raised chip with a tick (su-list, su-li)."""

    __gtype_name__ = "LumaSetupChoiceList"

    def __init__(self, label_text: str, *, max_height: int = 0, name: str = "") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("su-list")
        if name:
            self.set_name(name)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.rows.update_property([Gtk.AccessibleProperty.LABEL], [label_text])
        if max_height:
            self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                             max_content_height=max_height - 8, child=self.rows)
            self.append(self.scroll)
        else:
            self.scroll = None
            self.append(self.rows)
        self.buttons: dict[str, Gtk.Button] = {}

    def add(self, key: str, text: str, *, lead: str = "", sub: str = "", chosen: bool = False,
            on_pick: Callable[[str], None] | None = None) -> Gtk.Button:
        inner = box(spacing=10)
        words = box(spacing=9, hexpand=True)
        if lead:
            words.append(icon(lead, 16, "su-lead"))
        words.append(label(text, ellipsize=Pango.EllipsizeMode.END))
        if sub:
            sub_label = label(sub, "su-sub", xalign=1.0, hexpand=True)
            words.append(sub_label)
        inner.append(words)
        inner.append(icon("check", 16, "su-tick"))
        row = Gtk.Button(child=inner)
        row.fkey = f"li:{self.get_name()}:{key}"   # the same row after a redraw keeps the focus
        row.add_css_class("su-li")
        row.update_property([Gtk.AccessibleProperty.LABEL], [text])
        if chosen:
            row.add_css_class("su-on")
        if on_pick is not None:
            row.connect("clicked", lambda _b: on_pick(key))
        self.rows.append(row)
        self.buttons[key] = row
        return row

    def choose(self, key: str) -> None:
        for name, row in self.buttons.items():
            (row.add_css_class if name == key else row.remove_css_class)("su-on")

    def clear(self) -> None:
        for row in list(self.buttons.values()):
            self.rows.remove(row)
        self.buttons.clear()

    def reveal(self, key: str) -> None:
        """Scroll the chosen row into view (the list opens at the language already picked)."""
        row = self.buttons.get(key)
        if row is None or self.scroll is None:
            return

        def scroll() -> bool:
            ok, bounds = row.compute_bounds(self.rows)
            if ok:
                adjustment = self.scroll.get_vadjustment()
                top = bounds.get_y() - (adjustment.get_page_size() - bounds.get_height()) / 2
                adjustment.set_value(max(0, top))
            return False
        GLib.idle_add(scroll)


class Turn(Gtk.Widget):
    """A spinner whose turn is known (process-working, one turn a second in twelve steps).

    Held, it is drawn at its first angle: the setup holds the step still for the
    frame it hands to the first session, which draws it held and turns it on from
    there, so the hand-over frame is the same in both and the step keeps moving.
    """

    __gtype_name__ = "LumaSetupTurn"

    def __init__(self, size: int = 16) -> None:
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.set_size_request(size, size)
        self.add_css_class("su-spin")
        self.angle = 0.0
        self._tick = 0
        self._start = 0
        self._size = size
        self._icon = None

    def turn(self) -> None:
        if not self._tick:
            self._start = 0
            self._tick = self.add_tick_callback(self._step)

    def hold(self) -> None:
        if self._tick:
            self.remove_tick_callback(self._tick)
            self._tick = 0
        self.angle = 0.0
        self.queue_draw()

    def _step(self, _widget, clock) -> bool:
        now = clock.get_frame_time()
        if not self._start:
            self._start = now
        # Twelve steps a turn, one turn a second: a 4K sheet is redrawn twelve times a
        # second, not sixty, while the first session starts beside it.
        angle = float(int((now - self._start) * 12 / 1_000_000) % 12 * 30)
        if angle != self.angle:
            self.angle = angle
            self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def do_snapshot(self, snapshot) -> None:
        if self._icon is None:
            theme = Gtk.IconTheme.get_for_display(self.get_display())
            self._icon = theme.lookup_icon("process-working-symbolic", None, self._size, 1,
                                           Gtk.TextDirection.NONE, 0)
        width, height = self.get_width(), self.get_height()
        snapshot.save()
        snapshot.translate(Graphene.Point().init(width / 2, height / 2))
        snapshot.rotate(self.angle)
        snapshot.translate(Graphene.Point().init(-width / 2, -height / 2))
        self._icon.snapshot_symbolic(snapshot, width, height, [self.get_color()])
        snapshot.restore()


def avatar(initial: str, size: str, face: str = "") -> Gtk.Widget:
    """Their initial on the accent, or a picture: su-av."""
    if face:
        picture = Gtk.Picture.new_for_filename(face)
        picture.set_content_fit(Gtk.ContentFit.COVER)
        frame = Gtk.Frame(child=picture)
        frame.set_overflow(Gtk.Overflow.HIDDEN)
        widget = frame
        widget.add_css_class("su-photo")
    else:
        widget = Gtk.Label(label=initial) if initial else icon("user", 36 if size == "su-big" else 20)
    holder = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, overflow=Gtk.Overflow.HIDDEN)
    holder.add_css_class("su-av")
    holder.add_css_class(size)
    if face:
        holder.add_css_class("su-photo")
    widget.set_hexpand(True)
    widget.set_halign(Gtk.Align.FILL if face else Gtk.Align.CENTER)
    widget.set_valign(Gtk.Align.FILL if face else Gtk.Align.CENTER)
    holder.append(widget)
    return holder


def _logo(light: bool) -> Gtk.Widget:
    """The Luma mark (luma-logos' wordmark), or the word itself where the logos are not installed."""
    name = "luma-logo-text" if light else "luma-logo-text-dark"
    if Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).has_icon(name):
        logo = Gtk.Image(icon_name=name, pixel_size=38)
        logo.set_size_request(38, 12)
    else:
        logo = Gtk.Label(use_markup=True, label="luma<span foreground=\"#e8673a\">.</span>")
        logo.add_css_class("su-wordmark")
    logo.update_property([Gtk.AccessibleProperty.LABEL], ["Luma"])
    logo.add_css_class("su-logo")
    return logo


def load_style(display: Gdk.Display | None = None) -> None:
    display = display or Gdk.Display.get_default()
    provider = Gtk.CssProvider()
    provider.load_from_path(str(Path(__file__).with_name("setup.css")))
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


def prefers_light() -> bool:
    forced = os.environ.get("LUMA_SETUP_THEME", "")
    if forced:
        return forced == "light"
    source = Gio.SettingsSchemaSource.get_default()
    if source is not None and source.lookup("org.gnome.desktop.interface", True) is not None:
        return Gio.Settings.new("org.gnome.desktop.interface").get_string("color-scheme") == "prefer-light"
    return False


# The softened room: one definition for the setup's sheet and for the first session's
# cover (luma-setup --cover, gnome-shell 0321), so the two are the same picture.
SOFTEN = {"read": 640, "width": 160, "radius": 4, "passes": 3, "saturation": {"dark": 1.15, "light": 1.05}}


def wallpaper_for(light: bool) -> str | None:
    """The picture the room is made from: LUMA_SETUP_WALLPAPER(_LIGHT), else the session's wallpaper."""
    return (os.environ.get("LUMA_SETUP_WALLPAPER_LIGHT" if light else "LUMA_SETUP_WALLPAPER")
            or _wallpaper_path(light))


@functools.lru_cache(maxsize=4)
def softened_wallpaper(light: bool, path: str | None = None) -> Gdk.Texture | None:
    """The wallpaper as it is behind the sheet: blurred once, small, then drawn full screen.

    A 4K picture blurred every frame is too much for a Pi; one small copy,
    blurred once here (three box passes, close to a gaussian) and scaled up
    smoothly, is the same softness for nothing. Pure integer work on the same
    file gives the same texture in any session (SOFTEN is the whole recipe).
    """
    path = path or wallpaper_for(light)
    if not path:
        return None
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, SOFTEN["read"], -1, True)
    except GLib.Error as error:
        log.info("no wallpaper: %s", error.message)
        return None
    width = SOFTEN["width"]
    height = max(1, (width * pixbuf.get_height() + pixbuf.get_width() // 2) // pixbuf.get_width())
    small = pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.HYPER)
    saturated = small.copy()
    small.saturate_and_pixelate(saturated, SOFTEN["saturation"]["light" if light else "dark"], False)
    blurred = _blur(saturated, radius=SOFTEN["radius"], passes=SOFTEN["passes"])
    return Gdk.MemoryTexture.new(width, height, Gdk.MemoryFormat.R8G8B8, GLib.Bytes.new(blurred), width * 3)


def _blur(pixbuf: GdkPixbuf.Pixbuf, radius: int, passes: int) -> bytes:
    """A separable box blur, `passes` times, over a small RGB picture; edges clamp."""
    width, height, stride = pixbuf.get_width(), pixbuf.get_height(), pixbuf.get_rowstride()
    channels = pixbuf.get_n_channels()
    data = pixbuf.get_pixels()
    planes = [[[data[y * stride + x * channels + c] for x in range(width)] for y in range(height)] for c in range(3)]

    def run(line: list, n: int) -> list:
        span = 2 * radius + 1
        total = sum(line[min(max(i, 0), n - 1)] for i in range(-radius, radius + 1))
        out = [0.0] * n
        for i in range(n):
            out[i] = total / span
            total += line[min(i + radius + 1, n - 1)] - line[max(i - radius, 0)]
        return out

    for plane in planes:
        for _ in range(passes):
            for y in range(height):
                plane[y] = run(plane[y], width)
            for x in range(width):
                column = run([plane[y][x] for y in range(height)], height)
                for y in range(height):
                    plane[y][x] = column[y]
    out = bytearray(width * height * 3)
    for y in range(height):
        for x in range(width):
            base = (y * width + x) * 3
            for c in range(3):
                out[base + c] = max(0, min(255, int(planes[c][y][x] + 0.5)))
    return bytes(out)


def _wallpaper_path(light: bool) -> str | None:
    source = Gio.SettingsSchemaSource.get_default()
    if source is not None and source.lookup("org.gnome.desktop.background", True) is not None:
        settings = Gio.Settings.new("org.gnome.desktop.background")
        uri = settings.get_string("picture-uri" if light else "picture-uri-dark") or settings.get_string("picture-uri")
        try:
            path = GLib.filename_from_uri(uri)[0] if uri else None
        except GLib.Error:
            path = None
        if path and os.path.exists(path):
            return path
    fallback = WALLPAPERS / ("meadow.png" if light else "prism.png")
    return str(fallback) if fallback.exists() else None


class ScaleBin(Gtk.Widget):
    """Draws its child larger on a big screen at scale 1 (a 4K TV's login screen), from the first frame.

    The child is laid out at the window size divided by the factor and drawn
    through a scale transform, which GTK also applies to input. The factor
    keeps the layout near 1600 x 1000 logical pixels; at a normal size it is 1.
    """

    __gtype_name__ = "LumaSetupScaleBin"

    def __init__(self, child: Gtk.Widget) -> None:
        super().__init__(hexpand=True, vexpand=True)
        self.child = child
        child.set_parent(self)
        self.factor = 1.0
        self.size = (0, 0)
        self.on_resize = None

    @staticmethod
    def factor_for(width: int, height: int) -> float:
        forced = os.environ.get("LUMA_SETUP_SCALE", "")
        if forced:
            return max(1.0, float(forced))
        raw = min(width / 1600, height / 1000)
        return max(1.0, int(raw * 4) / 4)

    def do_dispose(self) -> None:
        if self.child is not None:
            self.child.unparent()
            self.child = None
        Gtk.Widget.do_dispose(self)

    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        self.factor = self.factor_for(width, height)
        if (width, height) != self.size:
            self.size = (width, height)
            if self.on_resize is not None:
                GLib.idle_add(lambda: (self.on_resize(), False)[1])
        transform = Gsk.Transform.new().scale(self.factor, self.factor)
        self.child.allocate(round(width / self.factor), round(height / self.factor), -1, transform)


def snapshot(window: Gtk.Window, name: str) -> None:
    """LUMA_SETUP_SNAPSHOTS=<dir>: a PNG of the window as each page settles (device test runs only)."""
    folder = os.environ.get("LUMA_SETUP_SNAPSHOTS", "")
    if not folder:
        return

    def save() -> bool:
        width, height = window.get_width(), window.get_height()
        renderer = window.get_renderer()
        if not width or not height or renderer is None:
            return False
        paintable = Gtk.WidgetPaintable.new(window)
        snap = Gtk.Snapshot()
        paintable.snapshot(snap, width, height)
        node = snap.to_node()
        if node is not None:
            os.makedirs(folder, exist_ok=True)
            renderer.render_texture(node, None).save_to_png(os.path.join(folder, f"{name}.png"))
        return False
    GLib.timeout_add(1200, save)


# -- the window -------------------------------------------------------------------
class SetupWindow(Gtk.ApplicationWindow):
    """The room and the sheet (desktop and phone); sheet_tv.SetupTV is the TV's."""

    __gtype_name__ = "LumaSetupWindow"

    def __init__(self, application: Gtk.Application, setup: Setup, form: Posture,
                 on_signed_in: Callable[[], None] | None = None, *, frame: dict | None = None) -> None:
        super().__init__(application=application, title=HEAD, decorated=False)
        self.add_css_class("su-window")
        self.setup = setup
        self.form = form
        self.phone = form == Posture.PHONE
        # frame: the last frame of a finished setup, drawn again (luma-setup --cover)
        self.frame = frame
        self.light = bool(frame["light"]) if frame else prefers_light()
        self.wallpaper = (frame.get("wallpaper") or None) if frame else wallpaper_for(self.light)
        self._on_signed_in = on_signed_in
        if frame is None:
            self.network = system.Network(self._network_changed)
            if not setup.network:
                setup.network = "wired" if self.network.wired() else self.network.connected_name()
            self.pages = setup.pages(wired=self.network.wired(), wifi=self.network.has_wifi())
            # One sheet for everything: the desk, Luma Connect and apps follow in this
            # session, and the person is signed in once, at the very end.
            from . import welcome as later
            self.later_mod = later
            self.groups = later.collections()
            self.pages += later.page_names(self.phone, self.groups)
            self.tiling = later.tiling_on()
            self.connect_flow = ConnectFlow(self._connect_changed)
        else:
            self.network = None
            self.pages = ["hello"]
        self.later = 0
        self.dash = "luma"
        self.chosen: set[str] = set()
        self.index = 0
        self.open_network = ""
        self.joining = ""
        self.join_problem = ""
        self.zone_open = False
        self.zone_query = ""
        self.faces_open = False
        self._zone_asked = False
        self._finishing = False
        self.page_parts: dict[str, Gtk.Widget] = {}

        root = Gtk.Overlay()
        root.add_css_class("su")
        root.set_name("su")
        if self.light:
            root.add_css_class("su-light")
        if self.phone:
            root.add_css_class("su-ph")
        self.root = root
        cover = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.COVER, hexpand=True, vexpand=True)
        texture = softened_wallpaper(self.light, self.wallpaper)
        if texture is not None:
            cover.set_paintable(texture)
        cover.set_name("su-cover")
        root.set_child(cover)
        dim = Gtk.Box(hexpand=True, vexpand=True, can_target=False)
        dim.add_css_class("su-cover-dim")
        root.add_overlay(dim)
        scrim = Gtk.Box(hexpand=True, vexpand=True)
        scrim.add_css_class("su-scrim")
        root.add_overlay(scrim)

        self.sheet = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, overflow=Gtk.Overflow.HIDDEN)
        self.sheet.add_css_class("su-sheet")
        self.sheet.set_name("su-sheet")
        if self.phone:
            self.sheet.set_halign(Gtk.Align.FILL)
            self.sheet.set_valign(Gtk.Align.END)
            grab = Gtk.Box(halign=Gtk.Align.CENTER)
            grab.add_css_class("su-grab")
            self.sheet.append(grab)
        else:
            self.sheet.set_halign(Gtk.Align.CENTER)
            self.sheet.set_valign(Gtk.Align.CENTER)
            self.sheet.set_size_request(720, -1)
        self.sheet.append(self._head())
        self.body = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
        self.body.set_name("su-body")
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=220,
                               vhomogeneous=False, interpolate_size=True)
        self.body.set_child(self.stack)
        self.sheet.append(self.body)
        self.foot = Gtk.Box(spacing=8)
        self.foot.add_css_class("su-foot")
        self.foot.set_name("su-foot")
        self.sheet.append(self.foot)
        root.add_overlay(self.sheet)
        self.scaler = ScaleBin(root)
        self.scaler.on_resize = self._fit
        self.set_child(self.scaler)

        self.connect("notify::focus-widget", lambda *_a: trace("focus", self.page if self.pages else "-",
                                                               describe(self.get_focus())))
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("notify::default-height", lambda *_a: self._fit())
        self.connect("realize", lambda *_a: GLib.idle_add(lambda: (self._fit(), False)[1]))
        if frame is not None:
            self._finishing = True
            self._dots()
            self._foot()
            self._finished_pane(frame["first"], frame.get("face", ""), frame["rows"], frame.get("marks") or {})
            return
        self.draw()
        if os.environ.get("LUMA_SETUP_SNAPSHOTS"):
            import signal
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, lambda: (self._snap("-now"), True)[1])

    # -- head ----------------------------------------------------------------------
    def _head(self) -> Gtk.Widget:
        head = Gtk.Box(spacing=10)
        head.add_css_class("su-head")
        head.set_name("su-head")
        logo = _logo(self.light)
        sep = Gtk.Box(valign=Gtk.Align.CENTER)
        sep.add_css_class("su-sep")
        self.head_label = label(HEAD, "su-hl")
        spacer = Gtk.Box(hexpand=True)
        self.a11y = Gtk.MenuButton(child=icon("accessibility", 16), valign=Gtk.Align.CENTER,
                                   tooltip_text="Accessibility")
        self.a11y.add_css_class("su-a11y")
        self.a11y.update_property([Gtk.AccessibleProperty.LABEL], ["Accessibility"])
        self.a11y.set_popover(self._a11y_popover())
        self.dots = Gtk.Box(spacing=5, valign=Gtk.Align.CENTER)
        self.dots.add_css_class("su-dots")
        self.dots.set_name("su-dots")
        for part in (logo, sep, self.head_label, spacer, self.a11y, self.dots):
            head.append(part)
        return head

    def _a11y_popover(self) -> Gtk.Popover:
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for key, text in (("screen-reader", "Screen reader"), ("large-text", "Larger text"), ("zoom", "Zoom")):
            switch = Gtk.Switch(active=system.accessibility(key), valign=Gtk.Align.CENTER)
            switch.update_property([Gtk.AccessibleProperty.LABEL], [text])

            def toggled(widget, _param, name=key) -> None:
                system.set_accessibility(name, widget.get_active())
                self.setup.accessibility[name] = widget.get_active()
            switch.connect("notify::active", toggled)
            rows.append(box(label(text, hexpand=True), switch, spacing=16, css=("su-row",)))
        popover = Gtk.Popover(child=rows)
        popover.add_css_class("su-pop")
        return popover

    def _dots(self) -> None:
        while (child := self.dots.get_first_child()) is not None:
            self.dots.remove(child)
        if self._finishing:
            return
        for i in range(len(self.pages) + self.later):
            dot = Gtk.Box(valign=Gtk.Align.CENTER)
            dot.add_css_class("su-dot")
            if i == self.index:
                dot.add_css_class("su-on")
            elif i < self.index:
                dot.add_css_class("su-past")
            self.dots.append(dot)
        self.dots.update_property([Gtk.AccessibleProperty.LABEL],
                                  [f"Step {self.index + 1} of {len(self.pages) + self.later}"])

    # -- size --------------------------------------------------------------------------
    def _fit(self) -> None:
        """The sheet is never taller than the screen less 40 (phone: 48); the page scrolls inside."""
        factor = getattr(self, "scaler", None).factor if getattr(self, "scaler", None) else 1.0
        height = (self.get_height() or self.get_default_size()[1]) / factor
        if height <= 0:
            return
        room = height - (48 if self.phone else 40) - 60 - 72
        self.body.set_max_content_height(max(200, room))
        if not self.phone:
            width = self.get_width() / factor
            self.sheet.set_size_request(min(720, max(320, width - 32)) if width else 720, -1)

    # -- pages -------------------------------------------------------------------------
    @property
    def page(self) -> str:
        return self.pages[self.index]

    def draw(self, forward: bool = True) -> None:
        self.page_parts = {}
        build = {"hello": self._hello, "wifi": self._wifi, "you": self._you, "device": self._device,
                 "desk": self._desk, "connect": self._connect_page, "apps": self._apps}[self.page]
        title, lede, body, extra = build()
        pane = self._pane(title, lede, body, extra)
        old = self.stack.get_visible_child()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_child(pane)
        self.stack.set_visible_child(pane)
        if old is not None:
            GLib.timeout_add(self.stack.get_transition_duration() + 40, lambda: (self._drop(old), False)[1])
        self._dots()
        self.set_focus(None)
        self._build_foot()
        if self.page == "wifi":
            self.network.start_scanning()
        else:
            self.network.stop_scanning()
        if self.page == "device" and self.setup.online and not self._zone_asked:
            self._ask_zone()
        # The new page's first place, now: a key pressed in the crossfade lands there, never on
        # whatever GTK would pick when the focused button went (Back). Again once laid out.
        self._first_focus()
        GLib.idle_add(lambda: (self._first_focus(only_if_lost=True), False)[1])
        self._snap()

    def _snap(self, suffix: str = "") -> None:
        self._shots = getattr(self, "_shots", 0) + 1
        snapshot(self, f"{self._shots:02d}-{self.page}{suffix}")

    def redraw(self) -> None:
        """The same page again (a choice changed), keeping the scroll and the focus."""
        kept = self._focus_key()
        adjustment = self.body.get_vadjustment()
        value = adjustment.get_value()
        self.page_parts = {}
        build = {"hello": self._hello, "wifi": self._wifi, "you": self._you, "device": self._device,
                 "desk": self._desk, "connect": self._connect_page, "apps": self._apps}[self.page]
        title, lede, body, extra = build()
        pane = self._pane(title, lede, body, extra)
        old = self.stack.get_visible_child()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.add_child(pane)
        self.stack.set_visible_child(pane)
        if old is not None:
            self.stack.remove(old)
        self._build_foot()
        self._restore_focus(kept)
        GLib.idle_add(lambda: (adjustment.set_value(value), False)[1])
        self._snap("-change")

    def _drop(self, child: Gtk.Widget) -> None:
        if child.get_parent() is self.stack and child is not self.stack.get_visible_child():
            self.stack.remove(child)

    def _pane(self, title: str, lede: str, body: Gtk.Widget, extra: str = "") -> Gtk.Widget:
        app_icon = ""
        if extra.startswith("icon:"):
            app_icon, extra = extra[5:], ""
        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pane.add_css_class("su-pane")
        pane.add_css_class(f"su-k-{self.page}")
        pane.add_css_class("su-body")
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        heading.add_css_class("su-hg")
        if extra:
            heading.add_css_class(extra)
        heading.set_name("su-hg")
        if app_icon:
            picture = Gtk.Image(icon_name=app_icon, pixel_size=52 if self.phone else 60)
            picture.add_css_class("su-app")
            heading.append(picture)
        title_label = label(title, "su-title", xalign=0.5, wrap=True)
        heading.append(title_label)
        heading.append(label(lede, "su-lede", xalign=0.5, wrap=True))
        pane.append(heading)
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main.add_css_class("su-main")
        main.append(body)
        pane.append(main)
        self.page_parts["title"] = title_label
        return pane

    def _first_focus(self, only_if_lost: bool = False) -> None:
        """The page's first place: its field, else the key (Return goes on), else its first control."""
        if only_if_lost and self._focus_in(self.stack.get_visible_child()) or only_if_lost and self._focus_in(self.foot):
            return
        target = self.page_parts.get("focus") or getattr(self, "key", None)   # nothing to type: Return goes on
        if target is not None and not target.get_sensitive():
            target = self._first_focusable(self.stack.get_visible_child())
        trace("first-focus", self.page, describe(target), target is not None and target.get_mapped(),
              target is not None and target.get_sensitive())
        if target is None:
            return
        if isinstance(target, Field):
            target.text.grab_focus_without_selecting()
        else:
            target.grab_focus()

    def _focus_in(self, ancestor: Gtk.Widget | None) -> bool:
        focus = self.get_focus()
        return ancestor is not None and focus is not None and (focus is ancestor or focus.is_ancestor(ancestor))

    def _focus_key(self) -> str | None:
        """What has the focus, in words that survive a rebuild: a row's or option's key, a field's name, a button's label."""
        widget = self.get_focus()
        while widget is not None and widget is not self:
            if getattr(widget, "fkey", None):
                return widget.fkey
            if isinstance(widget, (Gtk.Text, Gtk.Button)) and widget.get_name().startswith("su-"):
                return "name:" + widget.get_name()
            if isinstance(widget, Gtk.Button) and widget.get_label():
                return "label:" + widget.get_label()
            widget = widget.get_parent()
        return None

    def _restore_focus(self, kept: str | None) -> None:
        found = self._find_focus(kept) if kept else None
        if found is None:
            self._first_focus()
        elif isinstance(found, Gtk.Text):
            found.grab_focus_without_selecting()
        else:
            found.grab_focus()

    def _find_focus(self, kept: str) -> Gtk.Widget | None:
        kind, _, value = kept.partition(":")
        todo = [self.stack.get_visible_child(), self.foot]
        while todo:
            widget = todo.pop()
            if widget is None:
                continue
            if widget.get_sensitive() and (
                    getattr(widget, "fkey", None) == kept
                    or kind == "name" and isinstance(widget, (Gtk.Text, Gtk.Button)) and widget.get_name() == value
                    or kind == "label" and isinstance(widget, Gtk.Button) and widget.get_label() == value):
                return widget
            child = widget.get_first_child()
            while child is not None:
                todo.append(child)
                child = child.get_next_sibling()
        return None

    @staticmethod
    def _first_focusable(root: Gtk.Widget | None) -> Gtk.Widget | None:
        todo = [root]
        while todo:
            widget = todo.pop(0)
            if widget is None:
                continue
            if widget.get_focusable() and widget.get_sensitive() and widget.get_visible() and \
                    isinstance(widget, (Gtk.Text, Gtk.Button)):
                return widget
            child = widget.get_first_child()
            while child is not None:
                todo.append(child)
                child = child.get_next_sibling()
        return None

    # Hello -----------------------------------------------------------------------------
    def _hello(self):
        s = self.setup
        languages = ChoiceList("Language", max_height=196 if self.phone else 286, name="su-languages")
        for lang in catalog.languages():
            languages.add(lang.locale, lang.name, chosen=lang.locale == s.locale, on_pick=self._pick_language)
        languages.reveal(s.locale)
        keyboards = ChoiceList("Keyboard", name="su-keyboards")
        for kb in catalog.keyboards(s.locale):
            keyboards.add(kb.layout, kb.name, lead="keyboard", chosen=kb.layout == s.layout,
                          on_pick=self._pick_keyboard)
        trial = Field("Type here to try it", name="su-try")
        trial.add_css_class("su-try")
        column = box(label("Keyboard", "su-lab"), keyboards, trial, vertical=True)
        column.set_hexpand(True)
        if self.phone:
            body = box(languages, column, vertical=True, spacing=16)
        else:
            languages.set_size_request(336, -1)
            languages.set_hexpand(False)
            languages.set_valign(Gtk.Align.START)
            column.set_valign(Gtk.Align.START)
            body = box(languages, column, spacing=16)
        body.add_css_class("su-lk")
        self.page_parts.update(languages=languages, keyboards=keyboards,
                               focus=languages.buttons.get(s.locale))
        return catalog.greeting(s.locale), "Choose your language and keyboard.", body, "su-greet"

    def _pick_language(self, locale: str) -> None:
        self.setup.locale = locale
        self.setup.layout = catalog.keyboards(locale)[0].layout
        system.set_live_layout(self.setup.layout)
        self.redraw()
        button = self.page_parts["languages"].buttons.get(locale)
        if button is not None:
            button.grab_focus()

    def _pick_keyboard(self, layout: str) -> None:
        self.setup.layout = layout
        system.set_live_layout(layout)
        self.page_parts["keyboards"].choose(layout)

    # Get online ------------------------------------------------------------------------
    def _wifi(self):
        nets = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, halign=Gtk.Align.CENTER)
        nets.add_css_class("su-nets")
        nets.set_name("su-nets")
        if not self.phone:
            nets.set_size_request(440, -1)
        else:
            nets.set_halign(Gtk.Align.FILL)
        first = None
        for net in self.network.networks():
            row = self._net_row(net.ssid, net.bars, net.secured, net.connected)
            nets.append(row)
            first = first or row
        nets.append(self._other_row())
        note = label("You can do this later in Settings. The time zone is set for you once you’re online.",
                     "su-note", xalign=0.5, wrap=True)
        note.set_margin_top(14)
        self.page_parts["focus"] = self.page_parts.get("open-field") or (first and first.get_first_child())
        return "Get online", "No cable found. Pick a Wi-Fi network.", box(nets, note, vertical=True), ""

    def _net_row(self, ssid: str, bars: int, secured: bool, connected: bool) -> Gtk.Widget:
        is_open = self.open_network == ssid and not connected
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        row.add_css_class("su-net")
        if is_open:
            row.add_css_class("su-open")
        if connected:
            row.add_css_class("su-on")
        inner = box(label(ssid, hexpand=True, ellipsize=Pango.EllipsizeMode.END), spacing=10)
        if connected:
            inner.append(label("Connected", "su-con"))
        if secured:
            inner.append(icon("lock", 13, "su-lock"))
        inner.append(self._signal(bars))
        head = Gtk.Button(child=inner)
        head.add_css_class("su-nh")
        head.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"{ssid}, {'secured' if secured else 'open'}{', connected' if connected else ''}"])
        head.connect("clicked", lambda _b: self._tap_network(ssid, secured, connected))
        row.append(head)
        if is_open:
            join = lambda: self._join(ssid)  # noqa: E731
            field = Field(f"Password for {ssid}", password=True, on_activate=join, name="su-wpw")
            field.set_hexpand(True)
            self.page_parts["wpw"] = field
            self.page_parts["open-field"] = field
            key = self._join_button(join)
            row.append(box(field, key, spacing=8, css=("su-nf",)))
            if self.join_problem:
                row.append(label(self.join_problem, "su-why", wrap=True))
        return row

    def _other_row(self) -> Gtk.Widget:
        is_open = self.open_network == "*"
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        row.add_css_class("su-net")
        if is_open:
            row.add_css_class("su-open")
        head = Gtk.Button(child=box(icon("plus", 15), label("Other network"), spacing=8, css=("su-other",)))
        head.add_css_class("su-nh")
        head.connect("clicked", lambda _b: self._tap_network("*", True, False))
        row.append(head)
        if is_open:
            join = lambda: self._join("*")  # noqa: E731
            name = Field("Network name", name="su-hid", on_activate=lambda: self.page_parts["hidpw"].grab_focus())
            password = Field("Password (leave empty if open)", password=True, on_activate=join)
            self.page_parts.update(hid=name, hidpw=password, **{"open-field": name})
            column = box(name, password, self._join_button(join), vertical=True, spacing=8, css=("su-nf",))
            row.append(column)
            if self.join_problem:
                row.append(label(self.join_problem, "su-why", wrap=True))
        return row

    def _join_button(self, join: Callable[[], None]) -> Gtk.Button:
        key = Gtk.Button()
        key.add_css_class("su-bt")
        key.add_css_class("su-key")
        if self.joining:
            spinner = Gtk.Spinner(spinning=True)
            spinner.add_css_class("su-spin")
            key.set_child(spinner)
            key.update_property([Gtk.AccessibleProperty.LABEL], ["Joining"])
        else:
            key.set_label("Join")
            key.connect("clicked", lambda _b: join())
        return key

    @staticmethod
    def _signal(bars: int) -> Gtk.Widget:
        sig = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        sig.add_css_class("su-sig")
        for i, height in enumerate((5, 9, 13)):
            bar = Gtk.Box(valign=Gtk.Align.END)
            bar.set_size_request(3, height)
            bar.add_css_class("su-bar")
            if i < bars:
                bar.add_css_class("su-lit")
            sig.append(bar)
        sig.set_size_request(-1, 13)
        return sig

    def _tap_network(self, ssid: str, secured: bool, connected: bool) -> None:
        if connected or self.joining:
            return
        self.join_problem = ""
        if not secured and ssid != "*":
            self._join(ssid)
            return
        self.open_network = "" if self.open_network == ssid else ssid
        self.redraw()
        GLib.idle_add(lambda: (self._first_focus(), False)[1])

    def _join(self, ssid: str) -> None:
        hidden = ssid == "*"
        if hidden:
            name = self.page_parts["hid"].value.strip()
            password = self.page_parts["hidpw"].value
            if not name:
                self.page_parts["hid"].grab_focus()
                return
        else:
            name = ssid
            field = self.page_parts.get("wpw") if self.open_network == ssid else None
            password = field.value if field is not None else ""
        self.joining = name
        self.join_problem = ""
        self.redraw()

        def done(ok: bool, why: str) -> None:
            self.joining = ""
            if ok:
                self.setup.network = name
                self.open_network = ""
                self._ask_zone()
            else:
                self.join_problem = why
            if self.page == "wifi":
                self.redraw()
        self.network.join(name, password, done, hidden=hidden)

    def _network_changed(self) -> None:
        if self.network.wired():
            self.setup.network = self.setup.network or "wired"
        elif not self.joining:
            self.setup.network = self.network.connected_name()
        if self.page == "wifi" and not self.joining and not self.open_network:
            self.redraw()
        else:
            self._foot()

    # About you -------------------------------------------------------------------------
    def _you(self):
        s = self.setup
        face = Gtk.Button()
        face.add_css_class("su-avb")
        face.update_property([Gtk.AccessibleProperty.LABEL], ["Choose a photo"])
        face_overlay = Gtk.Overlay(child=avatar(s.first[:1].upper(), "su-big", s.face))
        self.page_parts["face-overlay"] = face_overlay
        cam = Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.END)
        cam.add_css_class("su-cam")
        glyph = icon("x" if self.faces_open else "camera", 14)
        glyph.set_hexpand(True)
        cam.append(glyph)
        face_overlay.add_overlay(cam)
        face.set_child(face_overlay)
        face.set_halign(Gtk.Align.CENTER)
        face.connect("clicked", lambda _b: self._toggle_faces())
        face_column = box(face, vertical=True, spacing=10, css=("su-face",))
        if self.faces_open:
            picker = Gtk.Popover(child=self._faces(), has_arrow=False, position=Gtk.PositionType.BOTTOM,
                                 autohide=True)
            picker.add_css_class("su-faces-pop")
            picker.set_parent(face)
            face.connect("destroy", lambda _w, p=picker: p.unparent())
            picker.connect("closed", lambda _p: self._faces_closed())
            GLib.idle_add(lambda: (picker.popup(), False)[1])
            self.page_parts["picker"] = picker

        name = Field("Full name", value=s.name, purpose=Gtk.InputPurpose.NAME, name="su-name",
                     on_changed=self._typed_name)
        user = Field("nick", value=s.username, prefix="Username", trail="pencil", name="su-uname",
                     on_changed=self._typed_username)
        user.add_css_class("su-un")
        password = Field("Password", value=s.password, password=True, name="su-pw",
                         on_changed=lambda v: self._typed_password("password", v))
        again = Field("Password again", value=s.again, password=True, trail="check", name="su-pw2",
                      on_changed=lambda v: self._typed_password("again", v))
        bars = Gtk.Box(spacing=3, valign=Gtk.Align.CENTER)
        for _i in range(4):
            bar = Gtk.Box()
            bar.add_css_class("su-bar")
            bars.append(bar)
        hint = label("", hexpand=True, wrap=True)
        meter = box(bars, hint, spacing=10, css=("su-str",))
        meter.set_name("su-str")
        form = box(name, user, password, again, meter, vertical=True, spacing=8, css=("su-form",))
        form.set_hexpand(True)
        if self.phone:
            you = box(face_column, form, vertical=True, spacing=24)
        else:
            face_column.set_size_request(112, -1)
            you = box(face_column, form, spacing=24, halign=Gtk.Align.CENTER)
            you.set_size_request(520, -1)
        you.add_css_class("su-you")
        owner = box(icon("key-round", 14), label(OWNER, wrap=True, hexpand=self.phone), spacing=8,
                    css=("su-owner",), halign=Gtk.Align.FILL if self.phone else Gtk.Align.CENTER)
        self.page_parts.update(name=name, user=user, password=password, again=again, meter=meter, bars=bars,
                               hint=hint, face=face, focus=name if not s.name else password)
        self._update_you()
        return "About you", "You’ll sign in to this Luma with these.", box(you, owner, vertical=True), ""

    def _faces(self) -> Gtk.Widget:
        grid = Gtk.FlowBox(max_children_per_line=3, min_children_per_line=3, selection_mode=Gtk.SelectionMode.NONE,
                           column_spacing=6, row_spacing=6, halign=Gtk.Align.CENTER)
        grid.add_css_class("su-faces")
        choices = [""] + [str(p) for p in sorted(FACES.glob("*.jpg"))[:4]] if FACES.is_dir() else [""]
        for face in choices:
            pick = Gtk.Button(child=avatar((self.setup.first[:1] or "A").upper(), "su-small", face))
            pick.update_property([Gtk.AccessibleProperty.LABEL],
                                 ["Your initial" if not face else Path(face).stem.replace("-", " ")])
            if face == self.setup.face:
                pick.add_css_class("su-on")
            pick.connect("clicked", lambda _b, f=face: self._pick_face(f))
            grid.append(pick)
        return grid

    def _toggle_faces(self) -> None:
        self.faces_open = not self.faces_open
        self.redraw()

    def _faces_closed(self) -> None:
        if self.faces_open:
            self.faces_open = False
            GLib.idle_add(lambda: (self.redraw(), False)[1])

    def _pick_face(self, face: str) -> None:
        self.setup.face = face
        self.faces_open = False
        picker = self.page_parts.get("picker")
        if picker is not None:
            picker.popdown()
        self.redraw()

    def _typed_name(self, value: str) -> None:
        had = self.setup.first[:1].upper()
        self.setup.set_name(value)
        self.page_parts["user"].set_value(self.setup.username)
        initial = self.setup.first[:1].upper()
        overlay = self.page_parts.get("face-overlay")
        if overlay is not None and not self.setup.face and initial != had:
            overlay.set_child(avatar(initial, "su-big"))   # the initial on the photo follows the name
        self._update_you()

    def _typed_username(self, value: str) -> None:
        cleaned = self.setup.set_username(value)
        if cleaned != value:
            self.page_parts["user"].set_value(cleaned)
        self._update_you()

    def _typed_password(self, which: str, value: str) -> None:
        setattr(self.setup, which, value)
        self._update_you()

    def _update_you(self) -> None:
        s, parts = self.setup, self.page_parts
        score, hint = strength(s.password)
        miss = mismatch(s.password, s.again)
        problem = username_problem(s.username, _taken) if s.username else ""
        meter = parts["meter"]
        for i in range(5):
            meter.remove_css_class(f"s{i}")
        meter.add_css_class(f"s{score}")
        bar = parts["bars"].get_first_child()
        i = 0
        while bar is not None:
            (bar.add_css_class if i < score else bar.remove_css_class)("su-lit")
            bar, i = bar.get_next_sibling(), i + 1
        parts["hint"].set_label(problem or ("The passwords don’t match." if miss else hint or "Pick something only you know."))
        parts["user"].set_mark(bad=bool(problem))
        parts["again"].set_mark(good=bool(s.again) and s.password == s.again, bad=miss)
        # the initial on the photo follows the name
        self._foot()

    # Name this Luma ------------------------------------------------------------------
    def _device(self):
        s = self.setup
        name = Field("Luma", value=s.device_name, name="su-dev", on_changed=self._typed_device)
        host = label(f"{_host(s.device_name)}.local", "su-note")
        host.set_margin_top(4)
        zone = catalog.zone(s.timezone)
        automatic = s.online and s.timezone_automatic and not self.zone_open
        line = f"{zone.name} · {zone.city}" + (" · set automatically" if automatic else "")
        card = box(spacing=12, css=("su-tz",))
        glyph = Gtk.Box(valign=Gtk.Align.CENTER)
        glyph.add_css_class("su-tzi")
        globe = icon("globe", 16)
        globe.set_hexpand(True)
        glyph.append(globe)
        card.append(glyph)
        card.append(box(label("Time zone", "su-tzt"), label(line, "su-tzs", ellipsize=Pango.EllipsizeMode.END), vertical=True,
                        spacing=1, hexpand=True, valign=Gtk.Align.CENTER))
        picking = self.zone_open or not s.online
        if s.online:
            change = button("Done" if self.zone_open else "Change", "su-all", on_click=self._toggle_zones,
                            valign=Gtk.Align.CENTER)
            card.append(change)
        column = box(name, host, card, vertical=True, css=("su-narrow",), halign=Gtk.Align.CENTER)
        if not self.phone:
            column.set_size_request(400, -1)
        else:
            column.set_halign(Gtk.Align.FILL)
        if picking:
            column.append(self._zone_picker())
        self.page_parts.update(dev=name, host=host, focus=name)
        return "Name this Luma", "Your other devices and your network will see it by this name.", column, ""

    def _zone_picker(self) -> Gtk.Widget:
        search = Field("Search for a city", value=self.zone_query, lead="search", name="su-tzq",
                       on_changed=self._typed_zone)
        search.add_css_class("su-search")
        listing = ChoiceList("Time zone", max_height=196, name="su-zones")
        self.page_parts.update(tzq=search, zones=listing)
        self._fill_zones(listing)
        return box(search, listing, vertical=True, spacing=8, css=("su-tzpick",))

    def _fill_zones(self, listing: ChoiceList) -> None:
        listing.clear()
        found = catalog.search_zones(self.zone_query)
        for z in found:
            listing.add(z.id, z.city, sub=z.name, chosen=z.id == self.setup.timezone, on_pick=self._pick_zone)
        empty = self.page_parts.get("zones-empty")
        if empty is not None and empty.get_parent() is not None:
            empty.get_parent().remove(empty)
        if not found:
            note = label("No city by that name.", "su-note", xalign=0.5)
            listing.rows.append(note)
            self.page_parts["zones-empty"] = note

    def _typed_zone(self, value: str) -> None:
        self.zone_query = value
        self._fill_zones(self.page_parts["zones"])

    def _toggle_zones(self) -> None:
        self.zone_open = not self.zone_open
        self.zone_query = ""
        self.redraw()

    def _pick_zone(self, zone_id: str) -> None:
        self.setup.timezone = zone_id
        self.setup.timezone_automatic = False
        self.zone_open = False
        self.zone_query = ""
        self.redraw()

    def _typed_device(self, value: str) -> None:
        self.setup.set_device(value)
        self.page_parts["host"].set_label(f"{_host(value)}.local")
        self._foot()

    def _ask_zone(self) -> None:
        self._zone_asked = True

        def found(zone: str | None) -> None:
            if zone and not self.zone_open:
                self.setup.timezone = zone
                self.setup.timezone_automatic = True
            elif not zone:
                self.zone_open = True   # online, but nowhere found: pick it, as offline
            if self.page == "device" and not self._finishing:
                self.redraw()
        system.detect_timezone(found)

    # Make it yours -------------------------------------------------------------------
    def _option(self, group: str, key: str, name: str, line: str, on: bool) -> Gtk.Button:
        glyph = Gtk.Box(valign=Gtk.Align.CENTER)
        glyph.add_css_class("su-gl")
        glyph.add_css_class(f"g-{key}")
        for _i in range(3 if key == "luma" else 2 if key in ("stack", "tile") else 1):
            glyph.append(Gtk.Box())
        words = box(label(name, "su-optn"), label(line, "su-optl"), vertical=True, valign=Gtk.Align.CENTER)
        option = Gtk.Button(child=box(glyph, words, spacing=11), hexpand=True)
        option.fkey = f"opt:{group}:{key}"
        option.add_css_class("su-opt")
        if on:
            option.add_css_class("su-on")
        option.update_property([Gtk.AccessibleProperty.LABEL], [f"{name}, {line}"])
        option.connect("clicked", lambda _b: self._choose(group, key))
        return option

    def _choose(self, group: str, key: str) -> None:
        if group == "win":
            self.tiling = key == "tile"
        else:
            self.dash = key
        self.redraw()
        GLib.idle_add(lambda: (self.key.grab_focus(), False)[1])  # chosen; Return goes on

    def _desk(self):
        """A live miniature of their desk; the choices apply as the session starts, before the reveal."""
        preview = self.later_mod.MiniDesktop(self.tiling, self.dash, self.light)
        preview.set_halign(Gtk.Align.CENTER)
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        rows.set_margin_top(26)
        for title, group, options in (
                ("Windows", "win", (("stack", "Stacking", "Overlap freely", not self.tiling),
                                    ("tile", "Tiling", "Side by side", self.tiling))),
                ("Dash", "dash", (("luma", "Luma", "Islands", self.dash == "luma"),
                                  ("cupertino", "Cupertino", "One dock", self.dash == "cupertino"),
                                  ("redmond", "Redmond", "Taskbar", self.dash == "redmond")))):
            seg = Gtk.Box(spacing=3, homogeneous=True, hexpand=True)
            seg.add_css_class("su-seg")
            for key, name, line, on in options:
                seg.append(self._option(group, key, name, line, on))
            heading = label(title, "su-rowb")
            heading.set_size_request(76, -1)
            rows.append(box(heading, seg, spacing=12, css=("su-row",)))
        return "Make it yours.", "How should Luma feel?", box(preview, rows, vertical=True), ""

    # Luma Connect ---------------------------------------------------------------------
    def _connect_page(self):
        flow = self.connect_flow
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER)
        card.add_css_class("su-card")
        if not self.phone:
            card.set_size_request(360, -1)
        if flow.done:
            card.append(label(f"@{flow.account}" if flow.account else "Signed in", "su-meb", xalign=0.5))
            card.append(label("Your Luma Connect account", "su-mee", xalign=0.5))
        elif flow.code:
            card.append(label("On your phone or another device, open", "su-note", "su-c", xalign=0.5, wrap=True))
            card.append(label(flow.url_short, "su-meb", xalign=0.5, selectable=True))
            card.append(label("and enter this code", "su-note", "su-c", xalign=0.5))
            code = label(flow.code, "su-code", xalign=0.5, selectable=True)
            code.update_property([Gtk.AccessibleProperty.LABEL], [" ".join(flow.code)])
            card.append(code)
            if flow.qr is not None:
                qr = Gtk.Picture.new_for_paintable(flow.qr)
                qr.set_size_request(132, 132)
                qr.set_halign(Gtk.Align.CENTER)
                qr.add_css_class("su-qr")
                card.append(qr)
            spinner = Gtk.Spinner(spinning=True)
            spinner.add_css_class("su-spin")
            card.append(box(spinner, label("Waiting for you to approve it", "su-note"), spacing=8,
                            halign=Gtk.Align.CENTER))
        else:
            card.append(label(flow.why or "Sign in to Luma Connect from your desktop once Luma starts.",
                              "su-note", "su-c", xalign=0.5, wrap=True))
            if flow.available:
                card.append(button("Sign in with a code", "su-link", on_click=flow.start, halign=Gtk.Align.CENTER))
        if not flow.started and flow.available and self.setup.online and not flow.why:
            GLib.idle_add(lambda: (flow.start(), False)[1])
        return ("Luma Connect", "Your files, photos and messages, on every device.", card,
                "icon:org.projectluma.Connect")

    def _skip_connect(self) -> None:
        self.connect_flow.later = True
        self.connect_flow.stop()
        self.go(1)

    def _connect_changed(self) -> None:
        if self.page == "connect" and not self._finishing:
            self.redraw()

    # Add apps ------------------------------------------------------------------------
    def _apps(self):
        groups = Gtk.Box(spacing=12)
        if self.phone:
            groups.set_orientation(Gtk.Orientation.VERTICAL)
        groups.add_css_class("su-groups")
        for group in self.groups:
            ids = [a["id"] for a in group["apps"]]
            every = all(i in self.chosen for i in ids)
            head = box(box(label(group["name"], "su-gn"),
                           label(f"{len(ids)} app{'s' if len(ids) != 1 else ''}", "su-ge"), vertical=True,
                           hexpand=True),
                       button("Clear" if every else "Add all", "su-all", valign=Gtk.Align.CENTER,
                              on_click=lambda ids=ids, every=every: self._all_apps(ids, every)),
                       spacing=10, css=("su-ghead",))
            tiles = Gtk.Box(spacing=4, homogeneous=True)
            tiles.add_css_class("su-tiles")
            for app in group["apps"]:
                tile = Gtk.Button(child=box(Gtk.Image(icon_name=app["icon"], pixel_size=48),
                                            label(app["name"], "su-tn", xalign=0.5), vertical=True, spacing=3))
                tile.add_css_class("su-tile")
                if app["id"] in self.chosen:
                    tile.add_css_class("su-on")
                tile.update_property([Gtk.AccessibleProperty.LABEL], [app["name"]])
                tile.connect("clicked", lambda _b, i=app["id"]: self._toggle_app(i))
                tiles.append(tile)
            groups.append(box(head, tiles, vertical=True, css=("su-group",), hexpand=True))
        count = len(self.chosen)
        summary = label(f"{count} app{'s' if count != 1 else ''}, installed once Luma starts" if count else "",
                        "su-sum", xalign=0.5)
        summary.set_visible(bool(count))
        return "Add apps", "Free tools for your next project.", box(groups, summary, vertical=True), \
            "icon:org.projectluma.Depot"

    def _toggle_app(self, app_id: str) -> None:
        self.chosen.symmetric_difference_update({app_id})
        self.redraw()

    def _all_apps(self, ids: list[str], every: bool) -> None:
        (self.chosen.difference_update if every else self.chosen.update)(ids)
        self.redraw()

    # -- the foot ------------------------------------------------------------------------
    def _foot(self) -> None:
        kept = self._focus_key() if self._focus_in(self.foot) else None
        self._build_foot()
        if kept is not None:
            self._restore_focus(kept)

    def _build_foot(self) -> None:
        while (child := self.foot.get_first_child()) is not None:
            self.foot.remove(child)
        if self._finishing:
            return  # the foot stays, empty, under the steps
        page = self.page
        back = button("Back", "su-bt", "su-fill", on_click=lambda: self.go(-1)) if self.index > 0 else None
        skip = None
        if page == "wifi" and not self.setup.online:
            skip = button("Skip", "su-bt", "su-quiet", on_click=lambda: self.go(1))
        last = self.index == len(self.pages) - 1
        key_text = "Start using Luma" if last else "Continue"
        if page == "connect" and not self.connect_flow.done:
            skip = button("Skip", "su-bt", "su-quiet", on_click=self._skip_connect)
        key = button(key_text, "su-bt", "su-key", on_click=self._press_key)
        key.set_name("su-key")
        key.set_sensitive(self.setup.ready(page, _taken))
        self.key = key
        if self.phone:
            self.foot.set_orientation(Gtk.Orientation.VERTICAL)
            self.foot.set_spacing(4)
            self.foot.append(key)
            if back or skip:
                pair = box(back, skip, spacing=0, homogeneous=True)
                for child in (back, skip):
                    if child is not None:
                        child.set_hexpand(True)
                self.foot.append(pair)
        else:
            self.foot.set_orientation(Gtk.Orientation.HORIZONTAL)
            self.foot.append(Gtk.Box(hexpand=True))
            for part in (back, skip, key):
                if part is not None:
                    self.foot.append(part)

    def _press_key(self) -> None:
        if not self.key.get_sensitive():
            return
        if self.index == len(self.pages) - 1:
            self.finish()
        else:
            self.go(1)

    def go(self, step: int) -> None:
        self.index = max(0, min(len(self.pages) - 1, self.index + step))
        self.open_network = ""
        self.faces_open = False
        self.zone_open = False
        self.draw(forward=step > 0)

    def _key(self, _controller, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        trace("key", Gdk.keyval_name(keyval), self.page, describe(self.get_focus()))
        if keyval == Gdk.KEY_Escape:
            return True  # a system sheet waits for an answer
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not self._finishing:
            focus = self.get_focus()
            # Return in a network's password joins it (the field's own activate); on a row it picks the row.
            if isinstance(focus, Gtk.Text) and self.page == "wifi":
                return False
            # On a row already chosen, Return goes on; on any other button it presses that button.
            chosen_row = (isinstance(focus, Gtk.Button) and focus.has_css_class("su-on")
                          and (focus.has_css_class("su-li") or focus.has_css_class("su-opt")))
            if isinstance(focus, Gtk.Button) and focus is not getattr(self, "key", None) and not chosen_row:
                return False
            self._press_key()
            return True
        return False

    # -- Finish ----------------------------------------------------------------------------
    def finish(self) -> None:
        """The account is made and everything applied; then they are signed straight in."""
        s = self.setup
        self._finishing = True
        self.network.stop_scanning()
        # Nothing keeps the focus (no ring in the last frame): the first session draws this frame again.
        self.set_focus(None)
        self._dots()
        self._foot()
        rows = [["account", "Creating your account", f"@{s.username}"],
                ["language", "Language and keyboard", _keyboard_name(s)],
                ["timezone", "Time zone", catalog.zone(s.timezone).city],
                ["name", "Naming this Luma", s.device_name],
                ["signin", "Signing you in", ""]]   # turns until the desktop is up (the first session's cover)
        self._finished_pane(s.first, s.face, rows, {})
        self._shots = getattr(self, "_shots", 0) + 1
        snapshot(self, f"{self._shots:02d}-finish")
        payload = s.payload()
        payload["first_session"] = {
            "desk": {"tiling": self.tiling, "dash": self.dash} if "desk" in self.pages else None,
            "apps": sorted(self.chosen),
            "connect_later": not self.connect_flow.done,
        }
        payload["connect_token"] = self.connect_flow.token   # sealed for the account by the helper, never stored plain
        # What the last frame is made of (not a picture of it): the first session draws it again.
        payload["last_frame"] = last_frame(self.form, self.light, self.wallpaper, s.first, s.face, rows)
        self.connect_flow.stop()
        s.password = s.again = ""  # the sheet keeps no copy once the helper has it
        password = payload["password"]
        system.Applier(self._step_done, lambda ok, why: self._applied(ok, why, payload["username"], password)
                       ).run(payload)

    def _finished_pane(self, first: str, face: str, rows: list, marks: dict) -> None:
        """Welcome, the photo and the four steps; marks says which have ticked (else a spinner)."""
        steps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER)
        steps.add_css_class("su-steps")
        steps.set_name("su-steps")
        steps.set_size_request(300, -1)
        self._step_rows: dict[str, Gtk.Box] = {}
        for key, text, detail in rows:
            mark = Gtk.Box(valign=Gtk.Align.CENTER)
            mark.set_size_request(20, 20)
            row = box(mark, label(text), label(detail, "su-sub", xalign=1.0, hexpand=True, ellipsize=Pango.EllipsizeMode.END),
                      spacing=12, css=("su-step",))
            row.mark = mark
            steps.append(row)
            self._step_rows[key] = row
            if key in marks:
                self._step_done(key, bool(marks[key]))
            else:
                spinner = Turn()
                mark.append(spinner)
                if not self.frame:
                    spinner.turn()   # the first session's copy turns once it is on screen
        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pane.add_css_class("su-body")
        heading = box(avatar(first[:1].upper(), "su-mid", face),
                      label(f"Welcome, {first}.", "su-title", xalign=0.5),
                      label("Getting Luma ready for you.", "su-lede", xalign=0.5),
                      vertical=True, css=("su-hg",))
        heading.get_first_child().set_margin_bottom(14)
        steps.set_margin_top(22)
        pane.append(heading)
        pane.append(steps)
        old = self.stack.get_visible_child()
        self.stack.add_child(pane)
        self.stack.set_visible_child(pane)
        if old is not None:
            GLib.timeout_add(300, lambda: (self._drop(old), False)[1])

    def _step_done(self, key: str, ok: bool) -> None:
        # The keyring is enrolled with the account; its row is the account's.
        row = self._step_rows.get(key)
        if row is None:
            return
        while (child := row.mark.get_first_child()) is not None:
            row.mark.remove(child)
        row.mark.append(icon("check" if ok else "x", 16, "su-done" if ok else "su-lead"))
        row.add_css_class("su-past")

    def _applied(self, ok: bool, why: str, username: str, password: str) -> None:
        if not ok:
            # Back to About you with the reason: nothing was made.
            self._finishing = False
            self.index = self.pages.index("you") if "you" in self.pages else 0
            self.draw(forward=False)
            self.page_parts["hint"].set_label(why)
            return
        # Signed in once the last tick is on screen (two frames drawn after it, so the
        # compositor has shown the first): the first session draws this same frame again.
        after_frames(self, 2, lambda: GLib.timeout_add(
            650, lambda: (hand_over(self, self, lambda: self._sign_in(username, password)), False)[1]))

    def _turns(self) -> list:
        return [row.mark.get_first_child() for row in getattr(self, "_step_rows", {}).values()
                if isinstance(row.mark.get_first_child(), Turn)]

    def hold_steps(self) -> None:
        for turn in self._turns():
            turn.hold()

    def turn_steps(self) -> None:
        for turn in self._turns():
            turn.turn()

    def _sign_in(self, username: str, password: str) -> None:
        """Signed in with the sheet still up: the first session opens on this same frame (--cover)."""
        system.sign_in(username, password, self._sign_in_problem)
        if self._on_signed_in is not None:
            self._on_signed_in()

    def _sign_in_problem(self, message: str) -> None:
        log.warning("sign-in: %s", message)


def hand_over(widget: Gtk.Widget, sheet, sign_in: Callable[[], None]) -> None:
    """Sign in and give the screen to the first session, whose first frame is this one.

    With Luma's gdm the setup keeps the screen, its last step turning, until the
    session's cover has drawn; then it holds the step at its first angle, draws
    that, and lets gdm switch (handover.py). Without it, the step is held before
    signing in, as gdm switches at once.
    """
    from . import handover
    if not handover.supported():
        sheet.hold_steps()
        after_frames(widget, 2, sign_in)
        return
    sign_in()
    started = GLib.get_monotonic_time()

    def check() -> bool:
        late = GLib.get_monotonic_time() - started > handover.WAIT_S * 1_000_000
        if not handover.waiting():
            return False   # gdm has switched already (its own time limit)
        if handover.cover_is_ready() or late:
            sheet.hold_steps()
            after_frames(widget, 2, handover.go)
            return False
        return True
    GLib.timeout_add(50, check)


def after_frames(widget: Gtk.Widget, count: int, then: Callable[[], None]) -> None:
    """then() once the widget's window has drawn count more frames (3 s at most)."""
    clock = widget.get_frame_clock()
    state = {"left": count, "done": False}

    def finish() -> None:
        if not state["done"]:
            state["done"] = True
            if clock is not None and state.get("handler"):
                clock.disconnect(state["handler"])
            then()

    def painted(_clock) -> None:
        state["left"] -= 1
        if state["left"] <= 0:
            GLib.idle_add(lambda: (finish(), False)[1])
        else:
            widget.queue_draw()
    if clock is None:
        finish()
        return
    state["handler"] = clock.connect("after-paint", painted)
    widget.queue_draw()
    GLib.timeout_add(3000, lambda: (finish(), False)[1])


def last_frame(form: Posture, light: bool, wallpaper: str | None, first: str, face: str, rows: list) -> dict:
    """The setup's last frame as data: the first session draws it again from this (luma-setup --cover).

    The text settings go with it, so the same words are drawn the same way in
    the person's own session, whatever their own settings are.
    """
    settings = Gtk.Settings.get_default()
    text = {}
    for name in ("gtk-font-name", "gtk-xft-dpi", "gtk-xft-antialias", "gtk-xft-hinting", "gtk-xft-hintstyle",
                 "gtk-xft-rgba", "gtk-hint-font-metrics", "gtk-icon-theme-name", "gtk-theme-name"):
        try:
            value = settings.get_property(name)
        except TypeError:
            continue
        if isinstance(value, (bool, int, str)):
            text[name] = value
    try:
        text["gtk-font-rendering"] = int(settings.get_property("gtk-font-rendering"))
    except TypeError:
        pass
    return {"posture": form.value, "light": bool(light), "wallpaper": wallpaper or "", "first": first,
            "face": face or "", "rows": [list(row) for row in rows], "settings": text,
            "scale": os.environ.get("LUMA_SETUP_SCALE", "")}


def _later_pages(phone: bool) -> int:
    try:
        from .welcome import page_names
        return len(page_names(phone))
    except Exception as error:  # noqa: BLE001 - no welcome to count: the dots are first boot's own
        log.info("welcome pages unknown: %s", error)
        return 0


def _taken(name: str) -> bool:
    import pwd

    data = system.fixture()
    if data is not None:
        return name in data.get("taken", ())
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


def _host(text: str) -> str:
    from .model import host_name

    return host_name(text)


def _keyboard_name(setup: Setup) -> str:
    for kb in catalog.keyboards(setup.locale):
        if kb.layout == setup.layout:
            return kb.name
    return setup.layout
