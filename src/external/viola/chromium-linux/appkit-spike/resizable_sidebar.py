# SPDX-License-Identifier: GPL-3.0-only
"""Native split layout with a single retained sidebar and temporary hover reveal."""
from gi.repository import Gdk, GLib, Gtk
from luma_appkit import Island


class ResizableSidebar:
    MIN_WIDTH = 170

    def __init__(self, host, composition, fixed, breakpoint_bin):
        self.host, self.composition, self.fixed, self.bin = host, composition, fixed, breakpoint_bin
        self.saved_width = 252
        self.collapsed = self.peeking = self.dragging = False
        self.last_window_width = 0
        self.leave_source = 0
        self.pointer_inside = False
        self.overlay = Gtk.Overlay(child=composition)
        # Keep AppKit Island's own shadow visible outside its rounded content.
        self.overlay.set_overflow(Gtk.Overflow.VISIBLE)
        composition.set_overflow(Gtk.Overflow.VISIBLE)
        self.handle = Gtk.Box(width_request=9, focusable=True)
        self.handle.set_cursor_from_name('col-resize')
        self.handle.set_tooltip_text('Drag to resize sidebar; drag left to collapse')
        self.handle.update_property([Gtk.AccessibleProperty.LABEL], ['Resize sidebar'])
        composition.insert_child_after(self.handle, fixed)
        drag = Gtk.GestureDrag(button=1)
        drag.connect('drag-begin', self.begin)
        drag.connect('drag-update', self.update)
        drag.connect('drag-end', self.end)
        self.handle.add_controller(drag)
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self.key)
        self.handle.add_controller(keys)
        self.edge = Gtk.Box(width_request=8, halign=Gtk.Align.START, vexpand=True)
        self.edge.set_visible(False)
        self.overlay.add_overlay(self.edge)
        self.reveal = Island()
        self.reveal.set_halign(Gtk.Align.START)
        self.reveal.set_visible(False)
        self.overlay.add_overlay(self.reveal)
        for widget in (self.edge, self.reveal):
            motion = Gtk.EventControllerMotion()
            motion.connect('enter', self.enter)
            motion.connect('leave', self.leave)
            widget.add_controller(motion)
        self.overlay.add_tick_callback(self.allocated)

    def width(self, requested):
        maximum = max(self.MIN_WIDTH, min(520, self.host.get_width() - 370))
        width = max(self.MIN_WIDTH, min(round(requested), maximum))
        for widget in (self.fixed, self.bin, self.host.sidebar):
            widget.set_size_request(width, 1 if widget is not self.host.sidebar else -1)
        return width

    def allocated(self, *_):
        if self.host.phone:
            return True
        width = self.host.get_width()
        if width != self.last_window_width:
            self.last_window_width = width
            self.width(self.saved_width)
        return True

    def begin(self, _gesture, x, _y):
        self.dragging = True
        self.start_width = self.fixed.get_width()
        valid, bounds = self.handle.compute_bounds(self.host)
        self.drag_origin = bounds.get_x() + x

    def requested(self, gesture, dx):
        valid, x, _y = gesture.get_point(None)
        placed, bounds = self.handle.compute_bounds(self.host)
        if valid and placed:
            return self.start_width + bounds.get_x() + x - self.drag_origin
        return self.start_width + dx

    def update(self, _gesture, dx, _dy):
        self.width(self.requested(_gesture, dx))

    def end(self, _gesture, dx, _dy):
        self.dragging = False
        requested = self.requested(_gesture, dx)
        if requested < self.MIN_WIDTH:
            self.collapse()
        else:
            self.saved_width = self.width(requested)
            self.host.send('sidebar:setWidth', {'width': self.saved_width, 'final': True})

    def key(self, _controller, key, _code, _state):
        if key in (Gdk.KEY_Left, Gdk.KEY_Right):
            requested = self.fixed.get_width() + (-20 if key == Gdk.KEY_Left else 20)
            if requested < self.MIN_WIDTH:
                self.collapse()
            else:
                self.saved_width = self.width(requested)
                self.host.send('sidebar:setWidth', {'width': self.saved_width, 'final': True})
            return True
        return False

    def toggle(self):
        if self.collapsed:
            self.hide_peek()
            self.collapsed = False
            self.edge.set_visible(False)
            self.fixed.set_visible(True)
            self.host.sidebar.set_visible(True)
            self.handle.set_visible(True)
            self.width(self.saved_width)
            self.host.toggle.remove_css_class('active')
        else:
            self.collapse()

    def collapse(self):
        self.hide_peek()
        self.collapsed = True
        self.fixed.set_visible(False)
        self.host.sidebar.set_visible(False)
        self.handle.set_visible(False)
        self.edge.set_visible(True)
        self.host.toggle.add_css_class('active')

    def enter(self, *_):
        if self.host.phone:
            return
        self.pointer_inside = True
        if self.leave_source:
            GLib.source_remove(self.leave_source)
            self.leave_source = 0
        if self.collapsed and not self.peeking:
            self.composition.remove(self.fixed)
            self.reveal.append(self.fixed)
            self.fixed.set_visible(True)
            self.host.sidebar.set_visible(True)
            self.width(self.saved_width)
            self.reveal.set_visible(True)
            self.peeking = True

    def leave(self, *_):
        self.pointer_inside = False
        if not self.leave_source:
            self.leave_source = GLib.timeout_add(220, self.dismiss_if_outside)

    def dismiss_if_outside(self):
        if self.host.open_popover or self.dragging:
            return True
        self.leave_source = 0
        if not self.pointer_inside:
            self.hide_peek()
        return False

    def hide_peek(self):
        if self.peeking:
            self.reveal.remove(self.fixed)
            self.composition.prepend(self.fixed)
            self.reveal.set_visible(False)
            self.fixed.set_visible(not self.collapsed)
            self.host.sidebar.set_visible(not self.collapsed)
            self.peeking = False
