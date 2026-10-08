"""Stable transaction card shared by package and other document workflows.

The island, window and colour treatment remain App Kit owned.
Changing phase changes content/opacity, never the card's widget hierarchy.
"""
from __future__ import annotations

import math
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from .widgets import Island, motion_duration, install_appkit
from .content_controls import TextButton


class TransactionCard(Island):
    def __init__(self):
        install_appkit()
        super().__init__()
        self.add_css_class("luma-transaction-card")
        self.set_hexpand(True)
        self.fraction = 0.0
        self._shown_fraction = 0.0
        self._tick = 0
        self._indeterminate = False
        self._angle = 0.0
        self.phase = "ready"
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                               valign=Gtk.Align.CENTER, halign=Gtk.Align.FILL, vexpand=True)
        for setter in (self.content.set_margin_top, self.content.set_margin_bottom,
                       self.content.set_margin_start, self.content.set_margin_end):
            setter(24)
        self.append(self.content)
        self.stage = Gtk.Overlay(halign=Gtk.Align.CENTER)
        self.stage.set_size_request(180, 180)
        self.ring = Gtk.DrawingArea(content_width=180, content_height=180)
        self.ring.set_draw_func(self._draw_ring)
        self.ring.set_can_target(False)
        self.stage.set_child(self.ring)
        self.icon = Gtk.Picture(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                can_shrink=True)
        self.icon.set_size_request(116, 116)
        self.stage.add_overlay(self.icon)
        self.badge = Gtk.Image.new_from_icon_name("object-select-symbolic")
        self.badge.add_css_class("luma-transaction-done")
        self.badge.set_size_request(32, 32)
        self.badge.set_halign(Gtk.Align.END); self.badge.set_valign(Gtk.Align.END)
        self.badge.set_margin_end(8); self.badge.set_margin_bottom(8)
        self.badge.set_can_target(False)
        self.stage.add_overlay(self.badge)
        self.content.append(self.stage)
        self.name = self._label("", "luma-transaction-name", 36)
        self.sub = self._label("", "luma-transaction-sub", 20)
        self.phase_label = self._label("", "luma-transaction-phase", 20)
        self.actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                               halign=Gtk.Align.CENTER)
        self.actions.set_size_request(230, -1)
        self.primary = TextButton("", style="raised")
        self.primary.add_css_class("luma-transaction-primary")
        self.primary.set_size_request(230, 44)
        self.primary_overlay = Gtk.Overlay()
        self.fill = Gtk.ProgressBar()
        self.fill.add_css_class("luma-transaction-fill")
        self.fill.set_valign(Gtk.Align.FILL)
        self.primary_overlay.set_child(self.fill)
        self.primary_label = Gtk.Label(label="Install", ellipsize=Pango.EllipsizeMode.END)
        self.primary_overlay.add_overlay(self.primary_label)
        self.primary.set_child(self.primary_overlay)
        self.actions.append(self.primary)
        self.secondary_slot = Gtk.Overlay()
        self.secondary_slot.set_size_request(230, 60)
        self.secondary_slot.set_child(Gtk.Box())
        self.secondary = TextButton("Cancel")
        self.secondary.set_halign(Gtk.Align.CENTER)
        self.secondary.set_valign(Gtk.Align.START)
        self.secondary.add_css_class("flat")
        self.secondary_slot.add_overlay(self.secondary)
        self.keep = Gtk.CheckButton(label="Keep settings and data", active=True,
                                   halign=Gtk.Align.FILL, valign=Gtk.Align.START)
        self.keep.add_css_class("luma-transaction-keep")
        self.keep.set_size_request(230, 40)
        self.secondary_slot.add_overlay(self.keep)
        self.actions.append(self.secondary_slot)
        self.content.append(self.actions)
        self.foot = Gtk.Box(spacing=8)
        self.foot.add_css_class("luma-transaction-foot")
        self.foot.set_size_request(-1, 34)
        self.file_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE)
        self.foot.append(self.file_label)
        self.advanced = Gtk.ToggleButton(label="Advanced ⌄")
        self.advanced.add_css_class("flat")
        self.advanced.set_size_request(-1, 26)
        self.foot.append(self.advanced)
        self.append(self.foot)
        self.set_icon("application-x-executable")
        self.set_phase("ready", "Install")

    def _label(self, text, css, height):
        label = Gtk.Label(label=text, ellipsize=Pango.EllipsizeMode.END,
                          single_line_mode=True, halign=Gtk.Align.FILL)
        label.add_css_class(css)
        label.set_size_request(-1, height)
        self.content.append(label)
        return label

    def set_icon(self, icon):
        theme = Gtk.IconTheme.get_for_display(self.get_display())
        # Desktop entries may carry a theme name, absolute file path, or a
        # serialized GIcon. Let GIO retain that distinction; a file path is
        # not a theme name (notably for Android and private application icons).
        if isinstance(icon, str):
            try:
                icon = Gio.Icon.new_for_string(icon)
            except GLib.Error:
                icon = None
        if icon is None:
            icon = Gio.ThemedIcon.new("application-x-executable")
        paintable = theme.lookup_by_gicon(icon, 116, 1, Gtk.TextDirection.NONE, 0)
        # Package artwork already includes its own tile or silhouette.
        # Do not add a second frame around a complete application icon.
        self.icon.set_paintable(paintable)

    def set_phase(self, phase, action, text="", fraction=0.0, *, removing=False,
                  cancellable=False):
        self.phase = phase
        self.primary_label.set_label(action)
        self.primary.update_property([Gtk.AccessibleProperty.LABEL], [action])
        self.phase_label.set_label(text)
        self.phase_label.set_tooltip_text(text or None)
        self.primary.set_sensitive(phase != "working")
        self.primary.remove_css_class("destructive-action")
        self.primary.remove_css_class("suggested-action")
        self.primary.remove_css_class("raised")
        self.primary.remove_css_class("danger-solid")
        self.primary.add_css_class("danger-solid" if removing and phase == "ready" else "raised")
        if removing and phase == "ready":
            self.primary.add_css_class("destructive-action")
        self.badge.set_opacity(1 if phase == "done" else 0)
        self.ring.set_opacity(0 if phase == "ready" else 1)
        self.fill.set_opacity(1 if phase == "working" else 0)
        self.secondary.set_visible(phase == "working" or (phase == "done" and not removing))
        self.secondary.set_label("Cancel" if phase == "working" else "Uninstall")
        self.secondary.set_sensitive(cancellable if phase == "working" else True)
        self.secondary.set_tooltip_text("This step must finish before it can be reversed."
                                       if phase == "working" and not cancellable else None)
        self.keep.set_visible(removing and phase == "ready")
        # Opacity preserves foot allocation, including the hidden toggle.
        self.advanced.set_opacity(1 if phase == "ready" else 0)
        self.advanced.set_sensitive(phase == "ready")
        self.advanced.set_can_focus(phase == "ready")
        self.set_fraction(fraction)

    def set_fraction(self, value):
        self._indeterminate = value is None
        if self._tick:
            self.ring.remove_tick_callback(self._tick); self._tick = 0
        if self._indeterminate:
            self.fill.set_opacity(0)
            self._angle = 0.0
            self.ring.queue_draw()
            if motion_duration(1000):
                def spin(widget, clock):
                    self._angle = (clock.get_frame_time() / 1500000) * 2 * math.pi
                    widget.queue_draw()
                    return self._indeterminate and self.phase == "working"
                self._tick = self.ring.add_tick_callback(spin)
            return
        self.fraction = max(0.0, min(1.0, value))
        self.fill.set_fraction(self.fraction)
        if self._tick:
            self.ring.remove_tick_callback(self._tick); self._tick = 0
        duration = motion_duration(450)
        if not duration or not self.get_mapped():
            self._shown_fraction = self.fraction; self.ring.queue_draw(); return
        start = self._shown_fraction
        began = None
        def animate(widget, clock):
            nonlocal began
            now = clock.get_frame_time()
            if began is None: began = now
            t = min(1.0, (now - began) / (duration * 1000))
            self._shown_fraction = start + (self.fraction - start) * (1 - (1-t)**3)
            widget.queue_draw()
            if t == 1: self._tick = 0
            return t < 1
        self._tick = self.ring.add_tick_callback(animate)

    def _draw_ring(self, _area, cr, width, height):
        colour = self.get_style_context().get_color()
        cr.set_line_width(3); cr.set_line_cap(1)
        cr.set_source_rgba(colour.red, colour.green, colour.blue, .10)
        cr.arc(width / 2, height / 2, 86, 0, 2 * math.pi); cr.stroke()
        found, tint = self.get_style_context().lookup_color("luma_accent")
        if not found: tint = colour
        cr.set_source_rgba(tint.red, tint.green, tint.blue, 1)
        if self._indeterminate:
            cr.arc(width / 2, height / 2, 86, self._angle, self._angle + math.pi / 2)
            cr.stroke()
        elif self._shown_fraction > 0:
            cr.arc(width / 2, height / 2, 86, -math.pi / 2,
                   -math.pi / 2 + 2 * math.pi * self._shown_fraction); cr.stroke()
