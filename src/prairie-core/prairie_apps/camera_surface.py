# SPDX-License-Identifier: Apache-2.0
"""Camera's photographic canvas and shutter silhouette (v70/v71 app-only parts).

The frame, menus, text roles and feedback are LumaUI's. Geometry below is
measured from cmRender/cmShape (desktop) and cmRenderPhone (a phone, v71) in
the simulator. Colours come from kit media tokens.
There is no device access or persistent state in this composition module.

On a phone (a window under the kit's phone width) Camera is laid out like the
phone cameras people know: black, the quick toggles along the top, the
picture upright, zoom pills over its foot, the modes as a strip of words, then
last shot, the shutter and switch camera. The same controls move between the
desktop bar and the phone rows, so each keeps one name and one behaviour.
"""
from __future__ import annotations

import math
import os
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
import cairo
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk
from luma_appkit import (ActionCenter, BarWidget, TextButton, Island, Toast, ToastHost, BarThumbnail,
                        ModeSwitch, RichMenuItem, apply_type, icons)
from luma_appkit import lumaui_tokens
from luma_appkit.action_bubble import FloatingMenu, MenuItem, float_at, rect_in
from luma_appkit.bar_frame import BarFrame
from luma_appkit.action_center import BarAction, make_control
from luma_appkit.media_style import TextureLoader
from luma_appkit.structure_adapt import WidthWatch
from luma_appkit.structure_layers import LayerHost
from luma_appkit.lumaui import media_context, mobile_form_factor, set_css_class
from .camera_backend import image_rotation_from_mount
from .camera_state import ASPECT_RATIOS, aspect_frame, portrait

#: v71 cmRenderPhone: the words in the mode strip, and its swipe (lGest: past 50, mostly sideways).
SWIPE_DISTANCE, SWIPE_SLOPE = 50, 1.5
GLYPHS = {"photo": "camera", "portrait": "user", "video": "video", "time": "timer", "scan": "scan-text"}


class CameraMenuItem(RichMenuItem):
    """Name each rendered row, including the row rebuilt for a phone drawer."""

    def menu_widget(self, close):
        widget = super().menu_widget(close)
        widget.set_name(self.camera_name)
        return widget


def colour(widget, name, alpha=1):
    found, value = widget.get_style_context().lookup_color(name)
    if not found:
        raise RuntimeError(f"Missing LumaUI media token: {name}")
    return value.red, value.green, value.blue, value.alpha * alpha


class FixedViewport(Gtk.Widget):
    """Keep photographic content inside the v70 overlay's fixed geometry."""
    __gtype_name__ = "LumaCameraFixedViewport"

    def __init__(self, child, width, height, *, radius=0):
        super().__init__(overflow=Gtk.Overflow.HIDDEN)
        self.dimensions = width, height
        self.radius = radius
        child.set_parent(self)

    def do_measure(self, orientation, for_size):
        value = self.dimensions[0 if orientation == Gtk.Orientation.HORIZONTAL else 1]
        return value, value, -1, -1

    def do_size_allocate(self, width, height, baseline):
        self.get_first_child().allocate(width, height, baseline, None)

    def do_snapshot(self, snapshot):
        child = self.get_first_child()
        if child:
            if self.radius:
                corner = Graphene.Size().init(self.radius, self.radius)
                rounded = Gsk.RoundedRect()
                rounded.init(Graphene.Rect().init(0, 0, self.get_width(), self.get_height()),
                             corner, corner, corner, corner)
                snapshot.push_rounded_clip(rounded)
            self.snapshot_child(child, snapshot)
            if self.radius:
                snapshot.pop()

    def clear(self):
        child = self.get_first_child()
        if child:
            child.unparent()

    def do_dispose(self):
        self.clear()


class CameraPicture(Gtk.Picture):
    """The crop/zoom/mirror in the viewfinder; saved captures are independent."""
    __gtype_name__ = "LumaCameraPicture"

    def __init__(self, window):
        super().__init__(can_shrink=True, content_fit=Gtk.ContentFit.COVER,
                         hexpand=True, vexpand=True)
        self.window = window
        self.set_size_request(1, 1)
        self.set_name("cm-frame")

    def do_snapshot(self, snapshot):
        canvas_width, canvas_height = self.get_width(), self.get_height()
        background = Gdk.RGBA()
        background.red, background.green, background.blue, background.alpha = colour(self, "luma_media_shadow")
        snapshot.append_color(background, Graphene.Rect().init(0, 0, canvas_width, canvas_height))
        surface = self.window._surface
        left, top, width, height = aspect_frame(canvas_width, canvas_height,
                                                surface.frame_aspect(canvas_width, canvas_height))
        snapshot.save()
        snapshot.translate(Graphene.Point().init(left, top))
        # v71 .cmvf2 .cmframe: square on a phone, where the picture meets the black.
        radius = 0  # The application frame owns the outer corners.
        corner = Graphene.Size().init(radius, radius)
        rounded = Gsk.RoundedRect()
        rounded.init(Graphene.Rect().init(0, 0, width, height), corner, corner, corner, corner)
        snapshot.push_rounded_clip(rounded)
        paintable = self.get_paintable()
        # The live pipeline already crops the preview for the selected zoom.
        # The sample image is drawn as the simulator draws it: 104% about the centre on a desk
        # (.cmframe img, inset -2%); on a phone at 100%, left at that -2% offset (.cmvf2 .cmframe > img).
        fixture = self.window._fixture
        zoom = max(1, self.window.state.zoom) * (1 if surface.phone else 1.04) if fixture else 1
        shift = (-.02 * width, -.02 * height) if fixture and surface.phone else (0, 0)
        mirror = bool(fixture and fixture.camera.mirror)
        def picture():
            if not paintable:
                return
            iw, ih = paintable.get_intrinsic_width(), paintable.get_intrinsic_height()
            scale = max(width / max(1, iw), height / max(1, ih))
            pw, ph = iw * scale, ih * scale
            snapshot.save()
            snapshot.translate(Graphene.Point().init(width / 2 + shift[0], height / 2 + shift[1]))
            snapshot.scale(-zoom if mirror else zoom, zoom)
            snapshot.translate(Graphene.Point().init(-width / 2, -height / 2))
            snapshot.translate(Graphene.Point().init((width - pw) / 2, (height - ph) / 2))
            paintable.snapshot(snapshot, pw, ph)
            snapshot.restore()
        exposure = 1 + self.window._surface.values.get("ev", 0) * .25
        matrix = Graphene.Matrix().init_from_float([
            exposure, 0, 0, 0, 0, exposure, 0, 0,
            0, 0, exposure, 0, 0, 0, 0, 1])
        snapshot.push_color_matrix(matrix, Graphene.Vec4().init(0, 0, 0, 0))
        picture()
        if self.window.state.mode == "portrait":
            snapshot.push_mask(Gsk.MaskMode.ALPHA)
            cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, width, height))
            cr.translate(width * .58, height * .52)
            cr.scale(width * .26, height * .42)
            gradient = cairo.RadialGradient(0, 0, .55, 0, 0, .90)
            gradient.add_color_stop_rgba(0, 1, 1, 1, 0)
            gradient.add_color_stop_rgba(1, 1, 1, 1, 1)
            cr.set_source(gradient)
            cr.paint()
            del cr
            snapshot.pop()
            snapshot.push_blur(7)
            picture()
            snapshot.pop()
            snapshot.pop()
        snapshot.pop()
        snapshot.pop()
        snapshot.restore()


class FinderMarks(Gtk.DrawingArea):
    """Thirds, focus reticle and document outline, without capturing them."""
    __gtype_name__ = "LumaCameraFinderMarks"

    def __init__(self, window):
        super().__init__(can_target=False, hexpand=True, vexpand=True)
        self.window = window
        self.set_name("cm-grid-overlay")
        self.set_draw_func(self.draw)

    def draw(self, area, context, width, height):
        window = self.window
        left, top, width, height = aspect_frame(width, height, window._surface.frame_aspect(width, height))
        context.save()
        context.rectangle(left, top, width, height)
        context.clip()
        context.translate(left, top)
        if window.state.grid:
            context.set_source_rgba(*colour(self, "luma_on_media", .3))
            context.set_line_width(1)
            for n in (1, 2):
                context.move_to(width * n / 3, 0)
                context.line_to(width * n / 3, height)
                context.move_to(0, height * n / 3)
                context.line_to(width, height * n / 3)
            context.stroke()
        if window.state.mode == "scan":
            context.set_source_rgba(*colour(self, "luma_on_media", .1))
            for n, (x, y) in enumerate(((.28, .22), (.71, .18), (.76, .80), (.24, .84))):
                (context.move_to if n == 0 else context.line_to)(width * x, height * y)
            context.close_path()
            context.fill_preserve()
            context.set_source_rgba(*colour(self, "luma_on_media"))
            context.set_line_width(2)
            context.stroke()
        context.restore()


class Shutter(Gtk.Button):
    __gtype_name__ = "LumaCameraShutter"

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.set_name("cm-shutter")
        self.add_css_class("camera-shutter")
        self.set_size_request(60, 60)
        self.disc = Gtk.DrawingArea(content_width=60, content_height=60, can_target=False)
        self.disc.set_draw_func(self.draw)
        self.set_child(self.disc)
        self.connect("clicked", lambda *_: window._on_capture())

    @property
    def phone(self):
        surface = getattr(self.window, "_surface", None)
        return bool(surface and surface.phone)

    def set_phone(self, phone):
        size = 80 if phone else 60
        self.set_size_request(size, size)
        self.disc.set_content_width(size)
        self.disc.set_content_height(size)
        self.disc.queue_draw()

    def do_snapshot(self, snapshot):
        # v70 .cmshutter:active scales the whole shutter around its centre;
        # on a phone only the disc gives (v71 .cmshutter2:active i).
        snapshot.save()
        if self.get_state_flags() & Gtk.StateFlags.ACTIVE and not self.phone:
            cx, cy = self.get_width() / 2, self.get_height() / 2
            snapshot.translate(Graphene.Point().init(cx, cy))
            snapshot.scale(.94, .94)
            snapshot.translate(Graphene.Point().init(-cx, -cy))
        Gtk.Button.do_snapshot(self, snapshot)
        snapshot.restore()

    def draw(self, area, cr, width, height):
        if self.phone:
            self.draw_phone(cr, width, height)
            return
        cx, cy = width / 2, height / 2
        cr.set_source_rgba(*colour(self, "luma_on_media", .92))
        cr.set_line_width(2.5)
        cr.arc(cx, cy, 28.75, 0, math.tau)
        cr.stroke()
        video = self.window.state.mode in ("video", "time")
        extent = 11 if self.window.state.recording else 24
        gradient = cairo.LinearGradient(0, cy - extent, 0, cy + extent)
        gradient.add_color_stop_rgba(0, *colour(self, "luma_danger_fill" if video else "luma_media_key"))
        gradient.add_color_stop_rgba(1, *colour(self, "luma_danger_fill_end" if video else "luma_media_key_end"))
        cr.set_source(gradient)
        if self.window.state.recording:
            for x, y, start in ((cx + 5, cy - 5, -math.pi / 2),
                                (cx + 5, cy + 5, 0),
                                (cx - 5, cy + 5, math.pi / 2),
                                (cx - 5, cy - 5, math.pi)):
                cr.arc(x, y, 6, start, start + math.pi / 2)
            cr.close_path()
        else:
            cr.arc(cx, cy, 24, 0, math.tau)
        cr.fill()

    def draw_phone(self, cr, width, height):
        """v71 .cmshutter2: an 80 white ring (4 px), a 64 disc, white for photos and red
        for video; recording, the disc becomes a 30 square with 8 px corners. Luma's
        corners are superellipses (v71 `corner-shape: superellipse(1.6)`), so the ring
        and disc are too."""
        cx, cy = width / 2, height / 2
        cr.set_source_rgba(*colour(self, "luma_on_media"))
        cr.set_line_width(4)
        squircle(cr, cx, cy, 38, 38, 38)
        cr.stroke()
        video = self.window.state.mode in ("video", "time")
        cr.set_source_rgba(*colour(self, "luma_record" if video else "luma_on_media"))
        pressed = .9 if self.get_state_flags() & Gtk.StateFlags.ACTIVE else 1
        if self.window.state.recording:
            squircle(cr, cx, cy, 15 * pressed, 15 * pressed, 8 * pressed)
        else:
            squircle(cr, cx, cy, 32 * pressed, 32 * pressed, 32 * pressed)
        cr.fill()


#: CSS corner-shape: superellipse(1.6) is the curve |x|^n + |y|^n = 1 with n = 2^1.6.
SUPERELLIPSE = 2 ** 1.6


def squircle(cr, cx, cy, half_width, half_height, radius, steps=24):
    """A rectangle centred on (cx, cy) whose corners of `radius` are Luma's superellipse."""
    radius = min(radius, half_width, half_height)
    exponent = 2 / SUPERELLIPSE
    corners = ((half_width - radius, -(half_height - radius), -math.pi / 2),
               (half_width - radius, half_height - radius, 0),
               (-(half_width - radius), half_height - radius, math.pi / 2),
               (-(half_width - radius), -(half_height - radius), math.pi))
    cr.new_path()
    for ox, oy, start in corners:
        for step in range(steps + 1):
            angle = start + math.pi / 2 * step / steps
            c, s = math.cos(angle), math.sin(angle)
            x = math.copysign(abs(c) ** exponent, c) * radius
            y = math.copysign(abs(s) ** exponent, s) * radius
            cr.line_to(cx + ox + x, cy + oy + y)
    cr.close_path()


class PhoneFrame(Gtk.Widget):
    """v71 .cmvf2 .cmframe: the picture as wide as the phone, as tall as its shape asks,
    and never taller than the room between the rows (then it simply fills that room).
    Square-cornered; a shorter frame sits low in the room, near the controls."""
    __gtype_name__ = "LumaCameraPhoneFrame"

    def __init__(self, ratio):
        super().__init__(hexpand=True, vexpand=True)
        self.ratio = ratio
        self.child = None

    def set_ratio(self, ratio):
        if ratio != self.ratio:
            self.ratio = ratio
            self.queue_allocate()

    def set_child(self, child):
        if self.child is not None:
            self.child.unparent()
        self.child = child
        if child is not None:
            child.set_parent(self)

    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1

    def do_size_allocate(self, width, height, baseline):
        if self.child is None:
            return
        frame_height = min(height, round(width / self.ratio))
        # As v71 lays it out (the frame is the last of three grid rows that share the
        # spare room, centred in its own): five sixths of the spare room above it.
        top = round((height - frame_height) * 5 / 6)
        self.child.allocate(width, frame_height, -1,
                            Gsk.Transform().translate(Graphene.Point().init(0, top)))

    def do_dispose(self):
        self.set_child(None)


class ModeStrip(Gtk.Widget):
    """v71 .cmmodes2: the modes as a strip of words, the current one lit and centred.

    The words are laid out side by side 22 apart and the strip is slid so the
    current word sits on the centre line, as the simulator scrolls it; the
    words that do not fit run off either edge.
    """
    __gtype_name__ = "LumaCameraModeStrip"
    GAP = 22

    def __init__(self, on_choose):
        super().__init__(overflow=Gtk.Overflow.HIDDEN, hexpand=True,
                         accessible_role=Gtk.AccessibleRole.TAB_LIST)
        self.set_name("cm-modes")
        self.add_css_class("camera-modes")
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Mode"])
        self.on_choose = on_choose
        self.buttons = {}
        self.current = None

    def set_modes(self, modes, current, sensitive):
        if [key for key, _label in modes] != list(self.buttons):
            for button in self.buttons.values():
                button.unparent()
            self.buttons = {}
            for key, label in modes:
                button = Gtk.Button(accessible_role=Gtk.AccessibleRole.TAB)
                button.set_name("cm-mode-" + key)
                button.add_css_class("camera-mode-word")
                button.set_child(Gtk.Label(label=label.upper()))
                button.update_property([Gtk.AccessibleProperty.LABEL], [label])
                button.connect("clicked", lambda _b, k=key: self.on_choose(k))
                button.set_parent(self)
                self.buttons[key] = button
        self.current = current
        for key, button in self.buttons.items():
            set_css_class(button, "on", key == current)
            button.update_state([Gtk.AccessibleState.SELECTED], [key == current])
            button.set_sensitive(sensitive)
        self.queue_allocate()

    def do_measure(self, orientation, for_size):
        sizes = [button.measure(orientation, -1) for button in self.buttons.values()]
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 0, sum(s[1] for s in sizes) + self.GAP * max(0, len(sizes) - 1), -1, -1
        natural = max((s[1] for s in sizes), default=0)
        return natural, natural, -1, -1

    def do_size_allocate(self, width, height, baseline):
        widths = [button.measure(Gtk.Orientation.HORIZONTAL, -1)[1] for button in self.buttons.values()]
        x, centre, places = 0, None, []
        for (key, button), button_width in zip(self.buttons.items(), widths):
            places.append((button, x, button_width))
            if key == self.current:
                centre = x + button_width / 2
            x += button_width + self.GAP
        offset = width / 2 - (centre if centre is not None else (x - self.GAP) / 2)
        for button, left, button_width in places:
            button_height = button.measure(Gtk.Orientation.VERTICAL, button_width)[1]
            transform = Gsk.Transform().translate(Graphene.Point().init(round(offset + left),
                                                                        round((height - button_height) / 2)))
            button.allocate(button_width, button_height, -1, transform)

    def do_dispose(self):
        for button in self.buttons.values():
            button.unparent()
        self.buttons = {}


class CameraSurface:
    def __init__(self, window):
        self.window = w = window
        self.menu = None
        self.thumbnail_cache = {}
        self.thumbnail_generation = 0
        self.focus_timeout = 0
        self.phone = False
        self.last_size = None
        self.values = w._fixture.values if w._fixture else {
            "flash": "off", "fmt": "jpeg", "aspect": "4:3", "pro": False,
            "iso": "Auto", "ss": "Auto", "wb": "Auto", "mf": "Auto",
            "sw": {"stab": False, "mirror": w.state.mirror, "loc": False}, "focus": None,
        }
        self.island = w._finder_island = Island()
        self.island.set_name("cm-island")
        self.island.add_css_class("camera-island")  # v71 .cmwin: the camera body is black in either appearance
        self.island.set_vexpand(True)
        self.overlay = w._finder_overlay = Gtk.Overlay(hexpand=True, vexpand=True)
        self.overlay.set_name("cm-viewfinder")
        self.overlay.add_css_class("camera-viewfinder")
        w._picture = CameraPicture(w)
        self.overlay.set_child(w._picture)
        self.island.append(self.overlay)
        self.host = ToastHost(self.island)
        self.host.set_name("cm-body")
        marks = FinderMarks(w)
        w._grid_overlay = marks
        self.overlay.add_overlay(marks)
        # Pipeline status is distinct from the mode/recording tag.
        w._status = apply_type(Gtk.Label(label="Starting camera…", halign=Gtk.Align.CENTER,
                                         valign=Gtk.Align.CENTER), "body")
        w._status.add_css_class("camera-status")
        w._status.set_name("cm-status")
        self.overlay.add_overlay(w._status)
        self.countdown = apply_type(Gtk.Label(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                               visible=False, can_target=False), "camera_countdown")
        self.countdown.set_name("cm-countdown")
        self.countdown.add_css_class("camera-countdown")
        self.overlay.add_overlay(self.countdown)
        self.capture_flash = Gtk.DrawingArea(hexpand=True, vexpand=True, can_target=False, visible=False)
        self.capture_flash.set_name("cm-capture-flash")
        self.capture_flash.set_draw_func(lambda area, cr, width, height: self.draw_flash(area, cr, width, height))
        self.overlay.add_overlay(self.capture_flash)
        self.tag = apply_type(Gtk.Label(halign=Gtk.Align.CENTER, valign=Gtk.Align.START,
                                        margin_top=14), "body")
        self.tag.add_css_class("camera-status")
        self.tag.set_name("cm-tag")
        self.tag.add_css_class("camera-tag-label")
        self.tag.set_size_request(-1, 30)
        self.tag_box = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, valign=Gtk.Align.START, margin_top=14)
        self.tag_box.add_css_class("camera-status")
        self.tag_box.add_css_class("camera-tag")
        self.tag.set_margin_top(0)
        self.tag.remove_css_class("camera-status")
        self.record_dot = Gtk.DrawingArea(content_width=8, content_height=8, valign=Gtk.Align.CENTER)
        self.record_dot.set_draw_func(self.draw_record_dot)
        self.tag_box.append(self.record_dot)
        self.tag_box.append(self.tag)
        self.overlay.add_overlay(self.tag_box)
        w._focus_reticle = Gtk.Box(visible=False, can_target=False)
        w._focus_reticle.add_css_class("camera-focus-reticle")
        w._focus_reticle.set_name("cm-focus")
        w._focus_reticle.set_size_request(72, 72)
        w._focus_reticle.set_halign(Gtk.Align.START)
        w._focus_reticle.set_valign(Gtk.Align.START)
        self.overlay.add_overlay(w._focus_reticle)
        self.ev = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.START,
                          valign=Gtk.Align.START, width_request=96, height_request=124, visible=False)
        self.ev.set_name("cm-exposure")
        self.ev.add_css_class("camera-exposure")
        self.ev.append(icons.image("sun", pixel_size=15))
        self.ev_scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, -2, 2, .1)
        self.ev_scale.set_inverted(True)
        self.ev_scale.set_draw_value(False)
        self.ev_scale.set_halign(Gtk.Align.CENTER)
        self.ev_scale.set_vexpand(True)
        self.ev_scale.set_value(self.values.get("ev", 0))
        self.ev_scale.update_property([Gtk.AccessibleProperty.LABEL], ["Exposure"])
        self.ev_scale.connect("value-changed", lambda scale: self.set_value("ev", scale.get_value()))
        self.ev.append(self.ev_scale)
        self.overlay.add_overlay(self.ev)
        gesture = Gtk.GestureClick(button=1)
        gesture.connect("released", self.focus)
        w._picture.add_controller(gesture)
        # This session at the upper left; source at the upper right.
        w._last_shot_button = TextButton("", icon="image", style="raised", on_click=self.session)
        w._last_shot_button.set_name("cm-session")
        w._last_shot_button.set_tooltip_text("This session")
        w._last_shot_button.set_size_request(44, 44)
        w._last_shot_button.add_css_class("camera-corner")
        w._last_shot_button.add_css_class("camera-session")
        w._last_shot_button.set_sensitive(False)
        w._last_shot_button.set_halign(Gtk.Align.START)
        w._last_shot_button.set_valign(Gtk.Align.START)
        w._last_shot_button.set_margin_start(16)
        w._last_shot_button.set_margin_top(16)
        self.overlay.add_overlay(w._last_shot_button)
        w._last_shot_picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.COVER,
                                           width_request=38, height_request=38)
        w._last_shot_picture.set_size_request(1, 1)
        last_overlay = Gtk.Overlay(child=w._last_shot_picture, hexpand=True, vexpand=True)
        self.session_placeholder = icons.image("image", pixel_size=17)
        self.session_placeholder.set_halign(Gtk.Align.CENTER)
        self.session_placeholder.set_valign(Gtk.Align.CENTER)
        self.session_placeholder.add_css_class("camera-muted")
        last_overlay.add_overlay(self.session_placeholder)
        self.session_viewport = FixedViewport(last_overlay, 38, 38, radius=9)
        w._last_shot_button.set_child(self.session_viewport)
        w._source_picker = TextButton("", style="raised", on_click=self.cameras)
        w._source_picker.set_name("cm-cameras")
        w._source_picker.set_tooltip_text("Camera")
        w._source_picker.set_size_request(1, 44)
        w._source_picker.add_css_class("camera-corner")
        w._source_picker.add_css_class("camera-source")
        w._source_picker.set_halign(Gtk.Align.END)
        w._source_picker.set_valign(Gtk.Align.START)
        w._source_picker.set_margin_end(16)
        w._source_picker.set_margin_top(16)
        self.source_line = Gtk.Box(spacing=8, margin_start=14, margin_end=12)
        self.source_icon = icons.image("camera")
        self.source_icon.add_css_class("camera-muted")
        self.source_line.append(self.source_icon)
        w._source_label = apply_type(Gtk.Label(label=w._source_description(w._devices[w.state.source])), "body")
        self.source_line.append(w._source_label)
        chevron = icons.image("chevron-down", pixel_size=16)
        chevron.add_css_class("camera-muted")
        self.source_line.append(chevron)
        w._source_picker.set_child(self.source_line)
        self.overlay.add_overlay(w._source_picker)
        self.desktop_center = ActionCenter().attach(self.overlay)
        self.bar = w._capture_controls = self.desktop_center.bar
        self.bar.set_name("cm-capture-center")
        self.fixed = self.desktop_center.bar_row
        w._mode_picker = self.control("cm-mode", "Mode", self.modes, text="Photo", width=1)
        w._mode_picker.add_css_class("camera-mode")
        self.zoom_box = Gtk.Box(spacing=2)
        self.zoom_box.set_name("cm-zoom")
        self.zoom_box.update_property([Gtk.AccessibleProperty.LABEL], ["Zoom"])
        self.zoom_buttons = {}
        w._zoom_picker = self.zoom_box
        self.shape = self.control("cm-aspect", "Aspect Ratio", self.aspects, text="4:3", width=40, height=28)
        self.shape.add_css_class("camera-shape")
        self.flash = self.action("cm-flash", "Flash: auto", self.cycle_flash, "zap")
        w._timer_button = self.action("cm-timer", "Timer", self.timer, "timer")
        w._grid_button = self.action("cm-grid", "Grid", self.grid, "grid-2x2")
        w._settings_button = self.action("cm-settings", "Format, shape and settings", self.settings, "ellipsis")
        w._capture_button = Shutter(w)
        # TODO(kit-request camera-02-viewfinder.md): IM1 ValueSlider for supported manual adjustments.
        self.manual = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.END,
                              valign=Gtk.Align.CENTER, margin_end=16, accessible_role=Gtk.AccessibleRole.GROUP)
        self.manual.add_css_class("camera-manual")
        self.manual.set_name("cm-manual")
        self.manual.update_property([Gtk.AccessibleProperty.LABEL], ["Manual controls"])
        for key, label in (("iso", "ISO"), ("ss", "Shutter"), ("wb", "WB"), ("mf", "Focus"), ("all", "All")):
            if key == "all":
                separator = Gtk.Box(can_target=False, accessible_role=Gtk.AccessibleRole.PRESENTATION)
                separator.add_css_class("camera-manual-separator")
                self.manual.append(separator)
            button = self.control("cm-dial-" + key, "Everything back to automatic" if key == "all" else label, lambda k=key: self.dial(k), width=56, height=52)
            words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
            caption = apply_type(Gtk.Label(label=label.upper()), "label")
            caption.add_css_class("camera-dial-caption")
            words.append(caption)
            value = apply_type(Gtk.Label(label="Auto" if key == "all" else "A"), "body")
            value.set_name("cm-value-" + key)
            value.add_css_class("camera-dial-value")
            words.append(value)
            button.set_child(words)
            self.manual.append(button)
        self.overlay.add_overlay(self.manual)
        self.strip = Gtk.Box(spacing=4, halign=Gtk.Align.START, valign=Gtk.Align.START,
                             margin_start=68, margin_top=16, visible=False)
        self.strip.set_name("cm-strip")
        self.strip.set_size_request(-1, 44)
        self.strip.add_css_class("camera-strip")
        self.strip.connect("notify::visible", lambda strip, _property:
                           (w._last_shot_button.add_css_class if strip.get_visible()
                            else w._last_shot_button.remove_css_class)("on"))
        self.overlay.add_overlay(self.strip)
        # Backend-only controls kept out of the visible surface.
        w._camera_label, w._format_label, w._storage_label = Gtk.Label(), Gtk.Label(), Gtk.Label()
        # Switch camera: on a phone the round button beside the shutter (v71 .cmflip2);
        # on a desk the camera chip at the upper right picks one by name instead.
        self.flip = w._switch_button = self.control("cm-flip", "Switch camera", self.flip_camera,
                                                    icon="refresh-ccw", width=52, height=52)
        self.flip.add_css_class("camera-flip")
        self.flip.get_child().set_pixel_size(24)
        w._lens_buttons, w._source_checks = {}, {}
        w._video_controls = Gtk.Box(visible=False)
        w._record_time = self.tag
        w._pause_button, w._video_still_button = Gtk.Button(), Gtk.Button()
        w._audio_meter = Gtk.LevelBar()
        w._top_hud = w._options_hud = Gtk.Box()
        w._last_shot_frame = Gtk.Box()
        w._session_tray = self.strip
        w._roll_box = Gtk.Box()
        w._roll_count = Gtk.Label()
        self.build_phone()
        self._show_desktop_controls()
        # v71: Camera watches its own size and redraws in the other shape when the
        # window crosses the phone width (the kit's tier), in either direction.
        self.width_watch = WidthWatch(self.host, lambda width: self.adapt(width <= lumaui_tokens.PHONE_MAX_WIDTH),
                                      threshold=lumaui_tokens.PHONE_MAX_WIDTH)
        swipe = Gtk.GestureDrag(button=1)
        swipe.connect("drag-end", self.swiped)
        w._picture.add_controller(swipe)
        self.swipe_time = 0.0
        self.overlay.add_tick_callback(self.resize)
        self.refresh()
        if self.values["focus"] is not None:
            self.start_focus_fade()

    def action(self, name, label, callback, icon, **semantics):
        button = self.control(name, label, callback, icon=icon, width=36, height=36)
        button.add_css_class("camera-tool")  # v70/v71 .cmbar4 .cmt: the quiet toggles, ink-2
        self.set_action(button, label, icon, **semantics)
        return button

    def set_action(self, button, label, icon, *, note=None, active=False, sensitive=True):
        """One quick toggle: its glyph, the note in its corner (A, 3s), lit when on.

        The button stays the same widget whichever row it stands in, so its
        name and behaviour never fork between the desk and the phone."""
        glyph = icons.image(icon, pixel_size=20 if self.phone else 16)
        if note:
            child = Gtk.Overlay(child=glyph)
            caption = Gtk.Label(label=note, halign=Gtk.Align.END, valign=Gtk.Align.END)
            caption.add_css_class("camera-control-note")
            child.add_overlay(caption)
        else:
            child = glyph
        button.set_child(child)
        button.set_tooltip_text(label)
        button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        set_css_class(button, "on", active)
        button.set_sensitive(sensitive)

    def frame_aspect(self, width, height):
        """The photo shape drawn inside the picture's canvas, or None to fill it.

        On a phone the frame itself is the shape (the aspect frame around the
        picture), so the picture fills it. On a desk the 4:3 (or chosen) photo
        is drawn inside the viewfinder, except while a window edge is being
        dragged, when the whole viewfinder fills until the matching size lands."""
        w = self.window
        if self.phone or w.state.mode in ("video", "time"):
            return None
        if w._desktop and (w.is_maximized() or w.is_fullscreen()):
            return None
        aspect = self.values["aspect"]
        if w._desktop and height > 0 and abs(width / height - ASPECT_RATIOS[aspect]) > .005:
            return None
        return aspect

    def capture_aspect(self, device):
        """The still's crop, in the sensor's own orientation, so the saved photo is the
        shape the viewfinder shows: upright on a phone (the mount's rotation turns a
        sensor-wide crop upright), the chosen shape on a desk."""
        if not self.phone:
            return self.values["aspect"]
        ratio = portrait(self.values["aspect"])
        rotation = 0 if device.desktop_uvc else image_rotation_from_mount(
            device.rotation, front_facing=device.location == "front")
        return 1 / ratio if rotation in (90, 270) else ratio

    def phone_ratio(self):
        """v71 cmRenderPhone: 9:16 for video, otherwise the chosen shape held upright."""
        return 9 / 16 if self.window.state.mode in ("video", "time") else portrait(self.values["aspect"])

    def build_phone(self):
        """The phone rows (v71 cmRenderPhone). Built once; adapt() moves the controls in."""
        w = self.window
        self.phone_device = mobile_form_factor() or not w._desktop
        self.phone_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.phone_box.set_name("cm-phone")
        self.phone_box.add_css_class("camera-phone-body")
        # The status bar above and the gesture chin below are the phone's (v71 .cmphone 44 / 34);
        # the black runs under both.
        set_css_class(self.phone_box, "device", self.phone_device)
        self.top_row = Gtk.Box(height_request=56)
        self.top_row.set_name("cm-top-row")
        self.top_row.add_css_class("camera-top-row")
        self.spacers = [Gtk.Box(hexpand=True) for _ in range(3)]
        self.record_pill = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER, visible=False)
        self.record_pill.set_name("cm-record-pill")
        self.record_pill.add_css_class("camera-record-pill")
        self.record_pill.set_size_request(-1, 32)
        pill_dot = Gtk.DrawingArea(content_width=8, content_height=8, valign=Gtk.Align.CENTER)
        pill_dot.set_draw_func(self.draw_pill_dot)
        self.record_pill.append(pill_dot)
        self.record_pill_time = Gtk.Label(label="00:00")
        self.record_pill_time.add_css_class("camera-record-time")
        self.record_pill.append(self.record_pill_time)
        self.phone_frame = PhoneFrame(self.phone_ratio())
        self.phone_frame.set_name("cm-vf")
        self.mode_strip = ModeStrip(self.set_mode)
        self.mode_strip.set_size_request(-1, 44)
        self.bottom_row = Gtk.CenterBox(height_request=112)
        self.bottom_row.set_name("cm-bottom-row")
        self.bottom_row.add_css_class("camera-bottom-row")
        self.flip.set_valign(Gtk.Align.CENTER)
        self.bottom_row.set_end_widget(self.flip)
        self.phone_controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.phone_controls.append(self.mode_strip)
        self.phone_controls.append(self.bottom_row)
        for part in (self.top_row, self.phone_frame, self.phone_controls):
            self.phone_box.append(part)
        # Camera has no action bar: on a phone its panels (Settings, Shape) rise in the kit's
        # bar frame (16 gutter, 34 up, 26 corners, as v71's phone shell draws .cmpop), opaque so
        # the shutter never shows through, and its toasts hang in the black band under the top
        # row (v71 top 96), never on the shutter.
        # (BarFrame.configure needs the window, so adapt() applies it once Camera is in one.)

    def _show_desktop_controls(self):
        w = self.window
        controls = (w._mode_picker, self.zoom_box, w._capture_button, self.shape,
                    self.flash, w._timer_button, w._grid_button, w._settings_button)
        for control in controls:
            if control.get_parent() is self.fixed:
                self.fixed.remove(control)
        self.desktop_center.show_bar([BarWidget(control) for control in controls])

    def adapt(self, phone):
        """Redraw in the other shape when the window crosses the phone width."""
        if phone == self.phone:
            return
        w = self.window
        if self.menu:
            self.menu.close()
        self.phone = phone
        media_context(self.overlay, phone)
        moved = (self.flash, w._timer_button, w._settings_button, self.shape)
        if phone:
            self.desktop_center.hide_bar()
            for control in (*moved, w._capture_button):
                if control.get_parent() is self.fixed:
                    self.fixed.remove(control)
            row = (self.flash, self.spacers[0], self.shape, self.record_pill, self.spacers[1],
                   w._timer_button, self.spacers[2], w._settings_button)
            for part in row:
                self.top_row.append(part)
            self.overlay.remove_overlay(w._last_shot_button)
            self.bottom_row.set_start_widget(w._last_shot_button)
            self.bottom_row.set_center_widget(w._capture_button)
            self.island.remove(self.overlay)
            self.phone_frame.set_child(self.overlay)
            self.island.append(self.phone_box)
        else:
            for part in list(self.descendants_of(self.top_row)):
                self.top_row.remove(part)
            self.bottom_row.set_start_widget(None)
            self.bottom_row.set_center_widget(None)
            self.overlay.add_overlay(w._last_shot_button)
            self.phone_frame.set_child(None)
            self.island.remove(self.phone_box)
            self.island.append(self.overlay)
            for control in (*moved, w._capture_button):
                self.fixed.append(control)
        for control in (*moved, w._last_shot_button, w._capture_button, self.tag_box):
            set_css_class(control, "phone", phone)
        # The island's own inner edge is not drawn over a full-black camera.
        set_css_class(self.island, "compact", phone)
        if phone:
            BarFrame.configure(self.host, opaque=True)
            self.host.set_edge("top", 96 if self.phone_device else 52)
        else:
            self.host.set_edge("bottom")
        w._capture_button.set_phone(phone)
        w._capture_button.set_valign(Gtk.Align.CENTER if phone else Gtk.Align.FILL)
        # v71 .cmlast2 is "Last shot" (it opens Photos); the desk's corner is the session.
        session_label = "Last shot" if phone else "This session"
        w._last_shot_button.set_tooltip_text(session_label)
        w._last_shot_button.update_property([Gtk.AccessibleProperty.LABEL], [session_label])
        # One control size on a phone's top row (v71 polish: the shape is 44 tall like Flash).
        for control in (self.flash, w._timer_button, w._settings_button):
            control.set_size_request(*((44, 44) if phone else (36, 36)))
        self.shape.set_size_request(*((60, 44) if phone else (40, 28)))
        for control in moved:
            control.set_valign(Gtk.Align.CENTER if phone else Gtk.Align.FILL)
        # The room between the rows stands for v71's #cm-vf on a phone; the picture's own
        # overlay is the frame inside it.
        self.overlay.set_name("cm-frame-box" if phone else "cm-viewfinder")
        self.phone_frame.set_name("cm-viewfinder" if phone else "cm-vf")
        size = 52 if phone else 44
        w._last_shot_button.set_size_request(size, size)
        w._last_shot_button.set_sensitive(bool(w.state.roll) or phone)
        w._last_shot_button.set_halign(Gtk.Align.CENTER if phone else Gtk.Align.START)
        w._last_shot_button.set_valign(Gtk.Align.CENTER if phone else Gtk.Align.START)
        for margin in ("start", "top"):
            getattr(w._last_shot_button, "set_margin_" + margin)(0 if phone else 16)
        self.session_viewport.dimensions = (52, 52) if phone else (38, 38)
        self.session_viewport.radius = 14 if phone else 9
        self.session_viewport.queue_resize()
        if phone and self.zoom_box.get_parent() is self.fixed:
            self.fixed.remove(self.zoom_box)
            self.overlay.add_overlay(self.zoom_box)
        elif not phone and self.zoom_box.get_parent() is self.overlay:
            self.overlay.remove_overlay(self.zoom_box)
            self.fixed.append(self.zoom_box)
        self.zoom_box.set_halign(Gtk.Align.CENTER if phone else Gtk.Align.FILL)
        self.zoom_box.set_valign(Gtk.Align.END if phone else Gtk.Align.FILL)
        self.zoom_box.set_margin_bottom(14 if phone else 0)
        if not phone:
            self._show_desktop_controls()
        for part in (self.bar, w._source_picker):
            part.set_visible(not phone)
        if phone:
            self.strip.set_visible(False)
        self.refresh()
        self.last_size = None

    @staticmethod
    def descendants_of(box):
        child = box.get_first_child()
        while child:
            yield child
            child = child.get_next_sibling()

    def flip_camera(self):
        """v71 .cmflip2: the next camera, round the list."""
        w = self.window
        if len(w._devices) > 1 and not w.state.recording:
            self.select_source((w.state.source + 1) % len(w._devices))

    def swiped(self, gesture, dx, dy):
        """v71 lGest: on a phone, slide the picture sideways to change mode."""
        w = self.window
        if not self.phone or w.state.recording:
            return
        if abs(dx) <= SWIPE_DISTANCE or abs(dx) <= abs(dy) * SWIPE_SLOPE:
            return
        # A swipe that acts never also counts as a tap (no focus mark).
        self.swipe_time = time.monotonic()
        modes = [key for key, _label in self.available_modes()]
        if w.state.mode not in modes:
            return
        index = modes.index(w.state.mode) - (1 if dx > 0 else -1)
        if 0 <= index < len(modes):
            self.set_mode(modes[index])

    def show_record_time(self):
        """The running time: the frame's tag on a desk, the red pill in the top row on a phone."""
        text = self.record_time()
        self.record_pill_time.set_label(text)
        if self.window.state.recording and not self.phone:
            self.tag.set_label(text)

    def control(self, name, label, callback, *, text=None, icon=None, width=36, height=36):
        button = Gtk.Button()
        button.set_name(name)
        button.add_css_class("camera-control")
        button.set_size_request(width, height)
        button.set_tooltip_text(label)
        button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        if icon:
            button.set_child(icons.image(icon, pixel_size=16))
        elif text:
            button.set_child(apply_type(Gtk.Label(label=text), "body"))
        button.connect("clicked", lambda *_: callback())
        return button

    def popup(self, anchor, title, choices):
        rows = [title]
        for key, label, icon, description, selected, callback in choices:
            row = CameraMenuItem(label, icon=icon, subtitle=description or None,
                           selected=selected, checked=selected, on_activate=callback)
            row.camera_name = key.replace("camera-", "cm-", 1)
            rows.append(row)
        self.popup_rows(anchor, title, rows)

    def popup_rows(self, anchor, title, rows):
        if self.menu:
            self.menu.close()
        # On a phone FloatingMenu opens in the bar frame BarFrame.configure placed above the controls.
        menu = self.menu = FloatingMenu(rows, label=title)
        menu.set_name("cm-menu")
        for button, row in zip(menu.buttons, [row for row in rows if isinstance(row, MenuItem)]):
            button.set_name(row.camera_name)
        menu.popup(anchor, align="end" if anchor.get_parent() is self.manual else "center")
        if self.phone and anchor is self.window._settings_button:
            self.light_while_open(anchor, menu)
        if not self.phone:
            host = LayerHost.window_host(anchor)
            rectangle = rect_in(host, anchor)
            frame = rect_in(host, self.overlay)
            prefer = "above" if anchor is not self.window._source_picker else "below"
            if anchor.get_parent() is self.manual:
                # Camera-only geometry: the menu stands beside the dial column.
                column = rect_in(host, self.manual)
                measure_width = max(menu.measure(Gtk.Orientation.HORIZONTAL, -1)[0],
                                    250 + menu.get_margin_start() + menu.get_margin_end())
                height = (menu.measure(Gtk.Orientation.VERTICAL, measure_width)[1]
                          - menu.get_margin_top() - menu.get_margin_bottom())
                top = max(frame.y + 12, min(frame.y + frame.height - height - 12,
                                           rectangle.y + rectangle.height / 2 - height / 2))
                rectangle.x = column.x - 10
                rectangle.y = round(top - 10)
                rectangle.width = rectangle.height = 0
                prefer = "below"
            float_at(host, menu, rectangle, prefer=prefer,
                     align="end" if anchor.get_parent() is self.manual else "center",
                     width=250, offset=10, edge=12, inset=frame.x + 12)

    def light_while_open(self, button, menu):
        """v71 polish: settings open is an on toggle (the yellow glyph on a brighter disc)."""
        button.add_css_class("on")

        def check():
            if menu.is_open and self.menu is menu and self.phone:
                return GLib.SOURCE_CONTINUE
            button.remove_css_class("on")
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(150, check)

    @staticmethod
    def descendants(widget):
        child = widget.get_first_child()
        while child:
            yield child
            yield from CameraSurface.descendants(child)
            child = child.get_next_sibling()

    def cameras(self):
        w = self.window
        fixture = w._fixture
        choices = []
        for index, device in enumerate(w._devices):
            camera = fixture.cameras[index] if fixture else None
            description = camera.subtitle + (" · RAW" if camera.raw else "") if camera else f"{device.preview_width} × {device.preview_height}"
            key = camera.id if camera else str(index)
            choices.append(("camera-source-" + key, w._source_description(device), "smartphone" if camera and camera.phone else "camera",
                            description, index == w.state.source, lambda i=index: self.select_source(i)))
        self.popup(w._source_picker, "Camera", choices)

    def select_source(self, index):
        self.window._switch_to_camera(index)
        self.refresh()
        if self.window._fixture:
            Toast.show(self.host, "Using " + self.window._fixture.camera.name)

    def available_modes(self):
        w = self.window
        if w._fixture:
            return list(w._fixture.modes)
        modes = [("photo", "Photo")]
        if w._devices and w._devices[w.state.source].desktop_uvc:
            from .camera_video import VideoRecording
            if VideoRecording.available():
                modes.append(("video", "Video"))
        return modes

    def modes(self):
        w = self.window
        self.popup(w._mode_picker, "Mode", [("camera-mode-" + key, label, GLYPHS[key], "", w.state.mode == key,
                   lambda k=key: self.set_mode(k)) for key, label in self.available_modes()])

    def set_mode(self, mode):
        w = self.window
        if w.state.recording:
            return
        w.state.mode = mode
        if w._fixture:
            w._fixture.values["mode"] = mode
        self.values["focus"] = None
        self.refresh()
        self.last_size = None

    def aspects(self):
        self.popup(self.shape, "Shape", [("camera-aspect-" + key.replace(":", "-"), key, None, "", self.values["aspect"] == key,
                   lambda k=key: self.choose_aspect(k)) for key in ("4:3", "3:2", "16:9", "1:1")])

    def choose_aspect(self, aspect):
        self.set_value("aspect", aspect)
        self.fit_window_to_aspect(aspect)
        self.aspects()

    def active_frame_ratio(self):
        """Ratio of the image actually drawn inside the viewfinder."""
        if self.window.state.mode not in ("video", "time"):
            return ASPECT_RATIOS[self.values["aspect"]]
        paintable = self.window._picture.get_paintable()
        if paintable and paintable.get_intrinsic_width() > 0 and paintable.get_intrinsic_height() > 0:
            return paintable.get_intrinsic_width() / paintable.get_intrinsic_height()
        device = self.window._devices[self.window.state.source]
        return device.viewfinder_width / max(1, device.viewfinder_height)

    def fit_window_to_aspect(self, aspect, *, from_resize=False):
        """Size the desktop window around the visible photo or video frame.

        The photo keeps its chosen crop, so sizing the canvas to that ratio
        removes the black letterbox on a normal, resizable desktop window.
        Maximized/fullscreen windows remain under the compositor's control.
        """
        w = self.window
        if not w._desktop or self.phone or w.is_maximized() or w.is_fullscreen():
            return
        frame_width, frame_height = w._picture.get_width(), w._picture.get_height()
        surface = w.get_surface()
        if not surface or frame_width < 2 or frame_height < 2:
            return
        monitor = surface.get_display().get_monitor_at_surface(surface)
        if monitor is None:
            return
        geometry = monitor.get_geometry()
        chrome_width = max(0, w.get_width() - frame_width)
        chrome_height = max(0, w.get_height() - frame_height)
        if not hasattr(self, "_fit_frame_bounds"):
            self._fit_frame_bounds = frame_width, frame_height
        reference_width, reference_height = self._fit_frame_bounds
        max_width = min(reference_width, max(360, round(geometry.width * .90) - chrome_width))
        max_height = min(reference_height, max(480, round(geometry.height * .90) - chrome_height))
        ratio = ASPECT_RATIOS[aspect] if isinstance(aspect, str) else aspect
        # GTK's client-side frame can round each axis by a few pixels while
        # menus and decorations settle. Correct visible drift, not those
        # small allocation changes, or the window will walk on its own.
        if abs(frame_width / frame_height - ratio) < .02:
            self._last_fitted_frame = frame_width, frame_height
            return
        if from_resize and hasattr(self, "_last_fitted_frame"):
            previous_width, previous_height = self._last_fitted_frame
            width_change = abs(frame_width - previous_width) / max(1, previous_width)
            height_change = abs(frame_height - previous_height) / max(1, previous_height)
            # A side drag leads with that dimension; a corner drag follows
            # whichever dimension moved further relative to its old size.
            next_width = frame_width if width_change >= height_change else frame_height * ratio
            next_width = min(next_width, max(360, round(geometry.width * .90) - chrome_width),
                             (round(geometry.height * .90) - chrome_height) * ratio)
            # A minimum window height must not turn a user's width resize into
            # a wider window. At constrained widths, keep the height minimum
            # and let the preview letterbox instead.
            minimum_width = w._geometry_minimum[0] - chrome_width
            next_width = max(minimum_width, next_width)
            next_height = next_width / ratio
            self._fit_frame_bounds = next_width, next_height
        else:
            next_width = min(max_width, max_height * ratio)
            # Changing the crop must not accidentally switch the desktop
            # window into its phone anatomy. A user drag still crosses the
            # tier normally; only this automatic fit keeps the current tier.
            desktop_width = (lumaui_tokens.PHONE_MAX_WIDTH + 1
                             + w.get_width() - self.host.get_width())
            next_width = max(next_width, desktop_width - chrome_width)
            next_height = next_width / ratio
        next_height = max(next_height, w._geometry_minimum[1] - chrome_height)
        self._last_fitted_frame = next_width, next_height
        w.set_default_size(round(next_width + chrome_width), round(next_height + chrome_height))

    def _finish_resize_fit(self):
        self._fit_timeout = 0
        if not self.window._closed and self.window.get_mapped():
            self.fit_window_to_aspect(self.active_frame_ratio(), from_resize=True)
        return GLib.SOURCE_REMOVE

    def choose_format(self, format):
        self.set_value("fmt", format)
        self.window.state.keep_raw = format == "raw"
        self.settings()

    def toggle_manual(self, enabled):
        self.set_value("pro", enabled)
        if self.menu:
            self.menu.close()

    def set_value(self, key, value):
        self.values[key] = value
        self.refresh()

    def cycle_flash(self):
        if not self.window._fixture:
            Toast.show(self.host, "Flash is unavailable for this camera", kind="warning")
            return
        self.values["flash"] = {"auto": "on", "on": "off", "off": "auto"}[self.values["flash"]]
        self.refresh()
        Toast.show(self.host, "Flash " + ("automatic" if self.values["flash"] == "auto" else self.values["flash"]))

    def timer(self):
        w = self.window
        w.state.timer = {0: 3, 3: 10, 10: 0}[w.state.timer]
        self.refresh()

    def grid(self):
        self.window.state.grid = not self.window.state.grid
        self.refresh()

    def zoom(self, value):
        w = self.window
        if w.state.recording:
            return
        w.state.zoom = value
        if w._fixture:
            w._fixture.values["zoom"] = value
        elif w._crop:
            device = w._devices[w.state.source]
            w._configure_crop(w._crop, device.preview_width, device.preview_height)
        self.refresh()

    def settings(self):
        w = self.window
        formats = w._fixture.formats if w._fixture else {"jpeg": ("JPEG", "")}
        raw = w._fixture and w._fixture.camera.raw
        video = w.state.mode in ("video", "time")
        rows = []
        def add(key, label, **kwargs):
            row = CameraMenuItem(label, **kwargs)
            row.camera_name = "cm-" + key
            rows.append(row)
        if not video:
            rows.append("Photo format")
            for key, (label, description) in formats.items():
                if key != "raw" or raw:
                    add("format-" + key, label, subtitle=description or None,
                        selected=self.values["fmt"] == key, checked=self.values["fmt"] == key,
                        on_activate=lambda k=key: self.choose_format(k))
            rows.append(None)
        if raw and w.state.mode not in ("video", "time"):
            add("manual", "Manual controls", icon="aperture", subtitle="ISO, shutter, focus, white balance",
                toggle=self.values["pro"], on_toggle=self.toggle_manual)
        for key, label, icon in (("stab", "Stabilization", "vibrate"), ("mirror", "Mirror the front camera", "flip-horizontal-2"),
                                 ("loc", "Save where photos are taken", "map-pin")):
            add("setting-" + key, label, icon=icon, toggle=self.values["sw"][key],
                on_toggle=lambda value, k=key: self.toggle_setting(k))
        device = w._devices[w.state.source]
        mp = device.still_width * device.still_height / 1_000_000
        rows.append(None)
        for key, label, icon, value in (("size", "Photo size", "image", f"{mp:g} MP"),
                                      ("video-format", "Video", "video", "4K · 30 fps" if w._fixture else f"{device.preview_width} × {device.preview_height}"),
                                      ("save-to", "Save to", "folder", "Pictures › Camera")):
            add(key, label, icon=icon, value=value,
                on_activate=lambda n=label, v=value: Toast.show(self.host, n + ": " + v))
        self.popup_rows(w._settings_button, "Settings" if video else "Photo format", rows)

    def toggle_setting(self, key):
        if not self.window._fixture and key in ("loc", "stab"):
            Toast.show(self.host, "Unavailable for this camera", kind="warning")
            return
        self.values["sw"][key] = not self.values["sw"][key]
        if key == "mirror":
            self.window._set_mirror(self.values["sw"][key])
        self.refresh()

    def dial(self, key):
        if key == "all":
            for dial in ("iso", "ss", "wb", "mf"):
                self.values[dial] = "Auto"
            self.refresh()
            Toast.show(self.host, "Everything is automatic again")
            return
        fixture = self.window._fixture
        if not fixture:
            return
        _key, label, choices = next(row for row in fixture.dials if row[0] == key)
        anchor = next(c for c in self.descendants(self.manual) if c.get_name() == "cm-dial-" + key)
        self.popup(anchor, label, [("camera-dial-value-" + str(index), value, None, "", self.values[key] == value,
                   lambda v=value: self.set_value(key, v)) for index, value in enumerate(choices)])

    def focus(self, gesture, presses, x, y):
        w = self.window
        if time.monotonic() - self.swipe_time < .4:
            return  # the end of a swipe that changed mode, not a tap
        if not w._fixture:
            w._request_refocus(x, y)
            return
        self.values["focus"] = [x / max(1, w._picture.get_width()) * 100, y / max(1, w._picture.get_height()) * 100]
        self.values["ev"] = 0
        self.ev_scale.set_value(0)
        self.start_focus_fade()
        self.refresh()

    def start_focus_fade(self):
        w = self.window
        w._focus_reticle.set_opacity(1)
        self.ev.set_opacity(1)
        if self.focus_timeout:
            GLib.source_remove(self.focus_timeout)
        def dim():
            self.focus_timeout = 0
            w._focus_reticle.set_opacity(.45)
            self.ev.set_opacity(.45)
            return GLib.SOURCE_REMOVE
        self.focus_timeout = GLib.timeout_add(2500, dim)

    def session(self):
        if self.phone:
            # v71 .cmlast2: on a phone the last shot opens it in Photos.
            if self.window.state.roll:
                self.window.get_application().activate_action("open-photos", None)
            return
        if self.window.state.roll:
            self.strip.set_visible(not self.strip.get_visible())

    def update_session(self):
        w = self.window
        self.thumbnail_generation += 1
        while child := self.strip.get_first_child():
            if isinstance(child, FixedViewport):
                child.clear()
            self.strip.remove(child)
        # v71 cmStrip: the session's last six shots, then Open in Photos (the key) and ✕.
        seen_paths = set()
        for index, shot in enumerate(w.state.roll[:6]):
            if shot.path in seen_paths:
                continue
            seen_paths.add(shot.path)
            from .camera_fixture import format_time
            label = (w._fixture.shots[index]["label"] if w._fixture else
                     "Video, " + format_time(shot.duration) if shot.duration is not None else "JPEG")
            tile = make_control(BarThumbnail(None, duration=format_time(shot.duration) if shot.duration is not None else None,
                                             label=label, size="compact"))
            picture = next(child for child in self.descendants(tile) if isinstance(child, Gtk.Picture))
            self.load_thumbnail(picture, shot.path)
            self.strip.append(FixedViewport(tile, 36, 36, radius=10))
        separator = Gtk.Box(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.PRESENTATION)
        separator.add_css_class("camera-strip-separator")
        self.strip.append(separator)
        if os.environ.get("LUMA_CAMERA_PRIVATE_PREVIEW") != "1":
            # In the fixture the app's open-photos action says the samples stay in the preview.
            open_button = make_control(BarAction("", "Open in Photos", primary=True,
                                                 on_activate=lambda: w.get_application().activate_action("open-photos", None)))
            open_button.set_name("cm-open-photos")
            self.strip.append(open_button)
        close = self.control("cm-strip-close", "Close session", lambda: self.strip.set_visible(False),
                             icon="x", width=36, height=36)
        close.set_name("cm-strip-close")
        self.strip.append(close)
        if w.state.roll:
            self.session_placeholder.set_visible(False)
            self.load_thumbnail(w._last_shot_picture, w.state.roll[0].path)
        else:
            self.session_placeholder.set_visible(True)
            w._last_shot_picture.set_paintable(None)
        w._last_shot_button.set_sensitive(bool(w.state.roll) or self.phone)
        self.strip.set_visible(False)

    def load_thumbnail(self, picture, path):
        w = self.window
        if path in self.thumbnail_cache:
            picture.set_paintable(self.thumbnail_cache[path])
            return
        if w._fixture:
            texture = Gdk.Texture.new_from_filename(str(path))
            self.thumbnail_cache[path] = texture
            picture.set_paintable(texture)
            return
        if path.suffix == ".webm":
            return
        generation = self.thumbnail_generation
        def loaded(texture):
            if texture and not w._closed and generation == self.thumbnail_generation:
                self.thumbnail_cache[path] = texture
                picture.set_paintable(texture)
        TextureLoader.shared().request(str(path), 64, loaded)

    def refresh(self):
        w = self.window
        fixture = w._fixture
        names = dict(fixture.modes) if fixture else {"photo": "Photo", "video": "Video"}
        mode_name = names.get(w.state.mode, w.state.mode)
        mode_line = Gtk.Box(spacing=5, halign=Gtk.Align.CENTER)
        mode_line.append(apply_type(Gtk.Label(label=mode_name), "body"))
        arrow = icons.image("chevron-down", pixel_size=12)
        arrow.add_css_class("camera-muted")
        mode_line.append(arrow)
        w._mode_picker.set_child(mode_line)
        w._mode_picker.update_property([Gtk.AccessibleProperty.LABEL], [mode_name])
        w._mode_picker.set_tooltip_text(mode_name)
        video = w.state.mode in ("video", "time")
        recording = w.state.recording
        # The shape: "4:3" (tap to change), or the video format; v71 writes it "4K · 30" on a phone.
        shape_text = ("4K · 30" if self.phone else "4K") if video and fixture else self.values["aspect"]
        self.shape.set_child(apply_type(Gtk.Label(label=shape_text), "body"))
        shape_label = "Video format" if video else "Aspect Ratio"
        self.shape.update_property([Gtk.AccessibleProperty.LABEL], [shape_label])
        self.shape.set_tooltip_text(shape_label)
        self.shape.set_sensitive(not video)
        # Recording on a phone: the red timecode pill takes the shape's place in the top row.
        self.shape.set_visible(not (self.phone and recording))
        self.record_pill.set_visible(self.phone and recording)
        self.set_action(self.flash, "Flash: " + self.values["flash"],
            "zap-off" if self.values["flash"] == "off" else "zap",
            note="A" if self.values["flash"] == "auto" else None,
            active=self.values["flash"] == "on", sensitive=not video and bool(fixture))
        self.set_action(w._timer_button, "Timer", "timer",
            note=f"{w.state.timer}s" if w.state.timer else None, active=bool(w.state.timer), sensitive=not video)
        self.set_action(w._settings_button, "Settings" if self.phone else "Format, shape and settings",
                        "settings-2" if self.phone else "ellipsis", sensitive=w._settings_button.get_sensitive())
        (w._grid_button.add_css_class if w.state.grid else w._grid_button.remove_css_class)("on")
        w._grid_button.update_state([Gtk.AccessibleState.PRESSED], [int(Gtk.AccessibleTristate.TRUE if w.state.grid
                                                                      else Gtk.AccessibleTristate.FALSE)])
        self.manual.set_visible(bool(self.values["pro"] and not video and fixture and fixture.camera.raw
                                     and not self.phone))
        mode_tag = "Portrait · f/2.0" if w.state.mode == "portrait" else "Document found" if w.state.mode == "scan" else ""
        self.tag.set_label(self.record_time() if recording and not self.phone else mode_tag)
        self.record_pill_time.set_label(self.record_time())
        self.tag.set_visible(bool(self.tag.get_label()))
        self.tag_box.set_visible(bool(self.tag.get_label()))
        self.record_dot.set_visible(recording and not self.phone)
        set_css_class(self.tag, "recording", recording and not self.phone)
        w._source_picker.set_sensitive(not w.state.recording)
        self.source_icon.set_from_icon_name("lumaui-smartphone-symbolic" if fixture and fixture.camera.phone else "lumaui-camera-symbolic")
        self.source_icon.set_pixel_size(16)
        w._mode_picker.set_sensitive(not w.state.recording)
        # While recording v71 leaves the words, last shot and switch camera unchanged (not dimmed);
        # set_mode and flip_camera ignore them until the take ends.
        self.mode_strip.set_modes(self.available_modes(), w.state.mode, True)
        self.phone_frame.set_ratio(self.phone_ratio())
        busy = getattr(w, "_capture_pipeline", None) is not None or getattr(w, "_stopping_pipeline", None) is not None
        self.flip.set_sensitive(len(w._devices) > 1 and not busy)
        label = "Stop" if w.state.recording else "Record" if video else "Take photo"
        w._capture_button.set_tooltip_text(label)
        w._capture_button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        w._capture_button.get_child().queue_draw()
        w._picture.queue_draw()
        w._grid_overlay.set_visible(True)
        w._grid_overlay.queue_draw()
        zooms = fixture.camera.zooms if fixture else (1, 2, 4)
        while child := self.zoom_box.get_first_child():
            self.zoom_box.remove(child)
        self.zoom_buttons = {}
        set_css_class(self.zoom_box, "camera-zoom-pills", self.phone)
        self.zoom_box.set_spacing(4 if self.phone else 2)
        if self.phone:
            # v71 .cmzoom2: every stop the same 38, the current one lit with ×, on the over-media bar glass.
            for zoom in zooms:
                current = abs(zoom - w.state.zoom) < 1e-6
                text = (".5" if zoom == .5 else f"{zoom:g}") + ("×" if current else "")
                button = self.control(f"cm-zoom-{zoom:g}", f"{zoom:g}×", lambda z=zoom: self.zoom(z),
                                      text=text, width=38, height=38)
                button.add_css_class("camera-zoom-pill")
                set_css_class(button, "on", current)
                self.zoom_box.append(button)
                self.zoom_buttons[zoom] = button
        elif len(zooms) > 1:
            modes = ModeSwitch([(f"{zoom:g}", f"{zoom:g}×") for zoom in zooms],
                               current=f"{w.state.zoom:g}", label="Zoom", stop_width=34,
                               on_change=lambda key: self.zoom(float(key)))
            modes.add_css_class("camera-zoom-modes")
            self.zoom_box.append(modes)
            for zoom in zooms:
                button = modes.buttons[f"{zoom:g}"]
                button.set_name(f"cm-zoom-{zoom:g}")
                self.zoom_buttons[zoom] = button
        else:
            # TODO(kit-request camera-01-capture-center.md): a disabled one-stop ModeSwitch.
            zoom = zooms[0]
            button = self.control(f"cm-zoom-{zoom:g}", f"{zoom:g}×", lambda z=zoom: self.zoom(z), text=f"{zoom:g}×", width=44 if self.phone else 34, height=30)
            button.add_css_class("on")
            button.add_css_class("camera-zoom-choice")
            button.set_sensitive(False)
            self.zoom_box.append(button)
            self.zoom_buttons[zoom] = button
        self.zoom_box.set_opacity(.35 if len(zooms) == 1 and not self.phone else 1)
        # Gtk.Fixed allocates a child's minimum width. Reserve the kit's
        # natural width so its ellipsizable labels have room for their text.
        self.zoom_box.set_size_request(-1, -1)
        if not self.phone:
            self.zoom_box.set_size_request(self.zoom_box.measure(Gtk.Orientation.HORIZONTAL, -1)[1], -1)
        focus = self.values["focus"]
        w._focus_reticle.set_visible(focus is not None)
        self.ev.set_visible(focus is not None)
        if focus:
            self.position_focus(focus)
        self.last_size = None
        for key in ("iso", "ss", "wb", "mf"):
            value = self.values[key]
            short = "A" if value == "Auto" else value.replace(" K", "K").replace("Daylight", "Day").replace("Tungsten", "Tung")
            label = next(c for c in self.descendants(self.manual) if c.get_name() == "cm-value-" + key)
            label.set_label(short)
            button = next(c for c in self.descendants(self.manual) if c.get_name() == "cm-dial-" + key)
            (button.add_css_class if value != "Auto" else button.remove_css_class)("camera-dial-set")

    def record_time(self):
        from .camera_fixture import format_time
        return format_time(self.window.state.elapsed)

    def draw_record_dot(self, area, cr, width, height):
        cr.set_source_rgba(*colour(area, "luma_danger_ink"))
        cr.arc(width / 2, height / 2, 4, 0, math.tau)
        cr.fill()

    def draw_pill_dot(self, area, cr, width, height):
        cr.set_source_rgba(*colour(area, "luma_on_media"))
        cr.arc(width / 2, height / 2, 4, 0, math.tau)
        cr.fill()

    def draw_flash(self, area, cr, width, height):
        cr.set_source_rgba(*colour(area, "luma_on_media"))
        cr.paint()

    def flash_capture(self):
        self.capture_flash.set_visible(True)
        self.capture_flash.set_opacity(.95)
        start = GLib.get_monotonic_time()
        def fade(widget, clock):
            elapsed = (GLib.get_monotonic_time() - start) / 1_000_000
            widget.set_opacity(max(0, .95 * (1 - elapsed / .38)))
            if elapsed >= .38:
                widget.set_visible(False)
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE
        self.capture_flash.add_tick_callback(fade)

    def capture_fixture(self):
        w = self.window
        if w._timer_source:
            return
        if w.state.mode not in ("video", "time") and w.state.timer:
            w._timer_remaining = w.state.timer
            self.countdown.set_label(str(w._timer_remaining))
            self.countdown.set_visible(True)
            def tick():
                w._timer_remaining -= 1
                if w._timer_remaining:
                    self.countdown.set_label(str(w._timer_remaining))
                    return GLib.SOURCE_CONTINUE
                self.countdown.set_visible(False)
                w._timer_source = 0
                self.finish_fixture_capture()
                return GLib.SOURCE_REMOVE
            w._timer_source = GLib.timeout_add(1000, tick)
        else:
            self.finish_fixture_capture()

    def finish_fixture_capture(self):
        w = self.window
        shot = w._fixture.capture()
        if shot:
            w._last_capture_path = shot.path
            self.update_session()
            if w.state.mode in ("photo", "portrait", "scan"):
                self.flash_capture()
            if w.state.mode == "scan":
                Toast.show(self.host, "Saved as a PDF in Documents")
            elif shot.duration is not None:
                Toast.show(self.host, "Saved to Pictures › Camera")
        elif w.state.recording:
            if w._video_clock_source:
                GLib.source_remove(w._video_clock_source)
            def tick():
                if not w.state.recording or w._closed:
                    w._video_clock_source = 0
                    return GLib.SOURCE_REMOVE
                w.state.elapsed += 1
                self.show_record_time()
                return GLib.SOURCE_CONTINUE
            w._video_clock_source = GLib.timeout_add(1000, tick)
        self.refresh()

    def dispose(self):
        if self.focus_timeout:
            GLib.source_remove(self.focus_timeout)
            self.focus_timeout = 0
        if self.menu:
            self.menu.close()
        self.session_viewport.clear()
        child = self.strip.get_first_child()
        while child:
            if isinstance(child, FixedViewport):
                child.clear()
            child = child.get_next_sibling()

    def resize(self, widget, clock):
        ratio = self.active_frame_ratio()
        size = (widget.get_width(), widget.get_height(), len(self.zoom_buttons), self.phone, ratio)
        if size == self.last_size:
            return GLib.SOURCE_CONTINUE
        previous_size = self.last_size
        self.last_size = size
        width, height, stops, phone, _ratio = size
        if phone:
            # The phone rows lay themselves out; only the focus mark follows the frame.
            focus = self.values["focus"]
            if focus:
                self.position_focus(focus)
            return GLib.SOURCE_CONTINUE
        # The shared action bar measures/fits its actual controls; no fixed
        # positions or photographic outline participates in desktop geometry.
        focus = self.values["focus"]
        if focus:
            self.position_focus(focus)
        if width > 1 and height > 1:
            if previous_size is None or previous_size[4] != ratio:
                if getattr(self, "_fit_timeout", 0):
                    GLib.source_remove(self._fit_timeout)
                    self._fit_timeout = 0
                self.fit_window_to_aspect(ratio)
            elif self.window._desktop and not self.phone:
                if getattr(self, "_fit_timeout", 0):
                    GLib.source_remove(self._fit_timeout)
                self._fit_timeout = GLib.timeout_add(140, self._finish_resize_fit)
        return GLib.SOURCE_CONTINUE

    def position_focus(self, focus):
        w = self.window
        x, y = w._picture.get_width() * focus[0] / 100, w._picture.get_height() * focus[1] / 100
        w._focus_reticle.set_margin_start(max(0, round(x - 36)))
        w._focus_reticle.set_margin_top(max(0, round(y - 36)))
        self.ev.set_margin_start(max(0, round(x + 48)))
        self.ev.set_margin_top(max(0, round(y - 62)))
