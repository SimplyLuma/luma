# SPDX-License-Identifier: Apache-2.0
"""Native surface preview with pointer/touch orbit and keyboard controls."""
import math
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gtk
from luma_appkit import apply_type
from .mesh import project


class MeshPreview(Gtk.Box):
    def __init__(self, mesh):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True, vexpand=True)
        self.mesh = mesh
        self.yaw, self.pitch, self.zoom = -.5, .3, 1.
        self._origin = (self.yaw, self.pitch)
        self.faces = project(mesh, self.yaw, self.pitch)
        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True, focusable=True)
        self.area.set_name('vw-model-surface')
        self.area.update_property([Gtk.AccessibleProperty.LABEL], ['3D model. Drag to rotate; arrow keys rotate; plus and minus zoom; Home resets.'])
        self.area.set_draw_func(self._draw)
        drag = Gtk.GestureDrag()
        drag.connect('drag-begin', self._begin)
        drag.connect('drag-update', self._drag)
        self.area.add_controller(drag)
        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect('scroll', self._scroll)
        self.area.add_controller(scroll)
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self._key)
        self.area.add_controller(keys)
        self.append(self.area)
        self.append(apply_type(Gtk.Label(label='Drag to rotate · Scroll to zoom', wrap=True), 'caption'))

    def _begin(self, gesture, x, y):
        self.area.grab_focus()
        self._origin = self.yaw, self.pitch

    def _drag(self, gesture, dx, dy):
        self.yaw = self._origin[0] + dx / 160
        self.pitch = max(-math.pi / 2, min(math.pi / 2, self._origin[1] + dy / 160))
        self._rotate()

    def _rotate(self):
        self.faces = project(self.mesh, self.yaw, self.pitch)
        self.area.queue_draw()

    def set_zoom(self, zoom):
        self.zoom = max(.2, min(5., zoom))
        self.area.queue_draw()

    def _scroll(self, controller, dx, dy):
        self.set_zoom(self.zoom * (1.1 if dy < 0 else 1 / 1.1))
        return True

    def _key(self, controller, key, code, state):
        if key in (Gdk.KEY_Left, Gdk.KEY_Right):
            self.yaw += -.15 if key == Gdk.KEY_Left else .15
        elif key in (Gdk.KEY_Up, Gdk.KEY_Down):
            self.pitch = max(-math.pi / 2, min(math.pi / 2, self.pitch + (-.15 if key == Gdk.KEY_Up else .15)))
        elif key in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_minus):
            self.set_zoom(self.zoom * (1 / 1.1 if key == Gdk.KEY_minus else 1.1))
            return True
        elif key == Gdk.KEY_Home:
            self.yaw, self.pitch, self.zoom = -.5, .3, 1.
        else:
            return False
        self._rotate()
        return True

    def _draw(self, area, cr, width, height):
        scale = min(width, height) * .8 * self.zoom
        cr.save()
        cr.translate(width / 2, height / 2)
        cr.scale(scale, -scale)
        cr.set_line_width(.5 / max(scale, 1))
        for depth, points, light in self.faces:
            cr.move_to(*points[0][:2])
            cr.line_to(*points[1][:2])
            cr.line_to(*points[2][:2])
            cr.close_path()
            cr.set_source_rgb(.42 * light, .55 * light, .62 * light)
            cr.fill_preserve()
            cr.set_source_rgba(.12, .16, .2, .45)
            cr.stroke()
        cr.restore()

    def clear(self):
        self.faces = []
