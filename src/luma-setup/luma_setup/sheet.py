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
import logging
import os
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gsk, Gtk, Pango  # noqa: E402

from . import catalog, system  # noqa: E402
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


def softened_wallpaper(light: bool) -> Gdk.Texture | None:
    """The wallpaper as it is behind the sheet: blurred once, small, then drawn full screen.

    A 4K picture blurred every frame is too much for a Pi; one small copy,
    blurred once here (three box passes, close to a gaussian) and scaled up
    smoothly, is the same softness for nothing.
    """
    path = (os.environ.get("LUMA_SETUP_WALLPAPER_LIGHT" if light else "LUMA_SETUP_WALLPAPER")
            or _wallpaper_path(light))
    if not path:
        return None
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 640, -1, True)
    except GLib.Error as error:
        log.info("no wallpaper: %s", error.message)
        return None
    width = 160
    height = max(1, round(width * pixbuf.get_height() / pixbuf.get_width()))
    small = pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.HYPER)
    saturated = small.copy()
    small.saturate_and_pixelate(saturated, 1.05 if light else 1.15, False)
    blurred = _blur(saturated, radius=4, passes=3)
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
                out[base + c] = max(0, min(255, round(planes[c][y][x])))
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


# -- the window -------------------------------------------------------------------
class SetupWindow(Gtk.ApplicationWindow):
    """The room and the sheet (desktop and phone); sheet_tv.SetupTV is the TV's."""

    __gtype_name__ = "LumaSetupWindow"

    def __init__(self, application: Gtk.Application, setup: Setup, form: Posture,
                 on_signed_in: Callable[[], None] | None = None) -> None:
        super().__init__(application=application, title=HEAD, decorated=False)
        self.add_css_class("su-window")
        self.setup = setup
        self.form = form
        self.phone = form == Posture.PHONE
        self.light = prefers_light()
        self._on_signed_in = on_signed_in
        self.network = system.Network(self._network_changed)
        if not setup.network:
            setup.network = "wired" if self.network.wired() else self.network.connected_name()
        self.pages = setup.pages(wired=self.network.wired(), wifi=self.network.has_wifi())
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
        texture = softened_wallpaper(self.light)
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

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("notify::default-height", lambda *_a: self._fit())
        self.connect("realize", lambda *_a: GLib.idle_add(lambda: (self._fit(), False)[1]))
        self.draw()

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
        for i, _page in enumerate(self.pages):
            dot = Gtk.Box(valign=Gtk.Align.CENTER)
            dot.add_css_class("su-dot")
            if i == self.index:
                dot.add_css_class("su-on")
            elif i < self.index:
                dot.add_css_class("su-past")
            self.dots.append(dot)
        self.dots.update_property([Gtk.AccessibleProperty.LABEL],
                                  [f"Step {self.index + 1} of {len(self.pages)}"])

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
        build = {"hello": self._hello, "wifi": self._wifi, "you": self._you, "device": self._device}[self.page]
        title, lede, body, extra = build()
        pane = self._pane(title, lede, body, extra)
        old = self.stack.get_visible_child()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_child(pane)
        self.stack.set_visible_child(pane)
        if old is not None:
            GLib.timeout_add(self.stack.get_transition_duration() + 40, lambda: (self._drop(old), False)[1])
        self._dots()
        self._foot()
        if self.page == "wifi":
            self.network.start_scanning()
        else:
            self.network.stop_scanning()
        if self.page == "device" and self.setup.online and not self._zone_asked:
            self._ask_zone()
        GLib.idle_add(lambda: (self._first_focus(), False)[1])

    def redraw(self) -> None:
        """The same page again (a choice changed), keeping the scroll."""
        adjustment = self.body.get_vadjustment()
        value = adjustment.get_value()
        self.page_parts = {}
        build = {"hello": self._hello, "wifi": self._wifi, "you": self._you, "device": self._device}[self.page]
        title, lede, body, extra = build()
        pane = self._pane(title, lede, body, extra)
        old = self.stack.get_visible_child()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.add_child(pane)
        self.stack.set_visible_child(pane)
        if old is not None:
            self.stack.remove(old)
        self._foot()
        GLib.idle_add(lambda: (adjustment.set_value(value), False)[1])

    def _drop(self, child: Gtk.Widget) -> None:
        if child.get_parent() is self.stack and child is not self.stack.get_visible_child():
            self.stack.remove(child)

    def _pane(self, title: str, lede: str, body: Gtk.Widget, extra: str = "") -> Gtk.Widget:
        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pane.add_css_class("su-pane")
        pane.add_css_class(f"su-k-{self.page}")
        pane.add_css_class("su-body")
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        heading.add_css_class("su-hg")
        if extra:
            heading.add_css_class(extra)
        heading.set_name("su-hg")
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

    def _first_focus(self) -> None:
        target = self.page_parts.get("focus")
        if target is not None and target.get_mapped():
            target.grab_focus()

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
        self.setup.set_name(value)
        self.page_parts["user"].set_value(self.setup.username)
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
                if self.page == "device" and not self._finishing:
                    self.redraw()
        system.detect_timezone(found)

    # -- the foot ------------------------------------------------------------------------
    def _foot(self) -> None:
        while (child := self.foot.get_first_child()) is not None:
            self.foot.remove(child)
        if self._finishing:
            return  # the foot stays, empty, under the steps
        page = self.page
        back = button("Back", "su-bt", "su-fill", on_click=lambda: self.go(-1)) if self.index > 0 else None
        skip = None
        if page == "wifi" and not self.setup.online:
            skip = button("Skip", "su-bt", "su-quiet", on_click=lambda: self.go(1))
        key_text = "Finish" if page == "device" else "Continue"
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
        if self.page == "device":
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
        if keyval == Gdk.KEY_Escape:
            return True  # a system sheet waits for an answer
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not self._finishing:
            focus = self.get_focus()
            # Return in a network's password joins it (the field's own activate); on a row it picks the row.
            if isinstance(focus, Gtk.Text) and self.page == "wifi":
                return False
            if isinstance(focus, Gtk.Button) and focus is not getattr(self, "key", None):
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
        self._dots()
        self._foot()
        rows = (("account", "Creating your account", f"@{s.username}"),
                ("language", "Language and keyboard", _keyboard_name(s)),
                ("timezone", "Time zone", catalog.zone(s.timezone).city),
                ("name", "Naming this Luma", s.device_name))
        steps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER)
        steps.add_css_class("su-steps")
        steps.set_name("su-steps")
        steps.set_size_request(300, -1)
        self._step_rows: dict[str, Gtk.Box] = {}
        for key, text, detail in rows:
            spinner = Gtk.Spinner(spinning=True)
            spinner.add_css_class("su-spin")
            mark = Gtk.Box(valign=Gtk.Align.CENTER)
            mark.set_size_request(20, 20)
            mark.append(spinner)
            row = box(mark, label(text), label(detail, "su-sub", xalign=1.0, hexpand=True, ellipsize=Pango.EllipsizeMode.END),
                      spacing=12, css=("su-step",))
            row.mark = mark
            steps.append(row)
            self._step_rows[key] = row
        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pane.add_css_class("su-body")
        heading = box(avatar(s.first[:1].upper(), "su-mid", s.face),
                      label(f"Welcome, {s.first}.", "su-title", xalign=0.5),
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
        payload = s.payload()
        s.password = s.again = ""  # the sheet keeps no copy once the helper has it
        password = payload["password"]
        system.Applier(self._step_done, lambda ok, why: self._applied(ok, why, payload["username"], password)
                       ).run(payload)

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
        GLib.timeout_add(650, lambda: (self._sign_in(username, password), False)[1])

    def _sign_in(self, username: str, password: str) -> None:
        self.root.add_css_class("su-in")
        system.sign_in(username, password, self._sign_in_problem)
        if self._on_signed_in is not None:
            self._on_signed_in()

    def _sign_in_problem(self, message: str) -> None:
        log.warning("sign-in: %s", message)


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
