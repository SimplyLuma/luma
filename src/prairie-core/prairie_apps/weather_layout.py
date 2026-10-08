# SPDX-License-Identifier: Apache-2.0
"""v70's 1.1fr/1fr forecast grid; width changes are breakpoint setters."""
from gi.repository import GObject, Graphene, Gsk, Gtk


class ForecastManager(Gtk.LayoutManager):
    def do_get_request_mode(self, widget):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    @staticmethod
    def children(widget):
        left = widget.get_first_child()
        return left, left.get_next_sibling() if left else None

    def do_measure(self, widget, orientation, for_size):
        left, right = self.children(widget)
        if left is None or right is None:
            return 0, 0, -1, -1
        if orientation == Gtk.Orientation.HORIZONTAL:
            a, b = left.measure(orientation, -1), right.measure(orientation, -1)
            if widget.stacked:
                return max(a[0], b[0]), max(a[1], b[1]), -1, -1
            return a[0] + b[0] + 12, a[1] + b[1] + 12, -1, -1
        widths = (for_size, for_size) if widget.stacked else self.widths(for_size)
        a, b = left.measure(orientation, widths[0]), right.measure(orientation, widths[1])
        if widget.stacked:
            return a[0] + b[0] + 12, a[1] + b[1] + 12, -1, -1
        return max(a[0], b[0]), max(a[1], b[1]), -1, -1

    @staticmethod
    def widths(width):
        if width < 0:
            return -1, -1
        left = round(max(0, width - 12) * 1.1 / 2.1)
        return left, max(0, width - 12 - left)

    def do_allocate(self, widget, width, height, baseline):
        left, right = self.children(widget)
        if left is None or right is None:
            return
        if widget.stacked:
            first_height = left.measure(Gtk.Orientation.VERTICAL, width)[1]
            left.allocate(width, first_height, -1, None)
            transform = Gsk.Transform().translate(Graphene.Point().init(0, first_height + 12))
            right.allocate(width, max(0, height - first_height - 12), -1, transform)
        else:
            first_width, second_width = self.widths(width)
            left.allocate(first_width, height, -1, None)
            transform = Gsk.Transform().translate(Graphene.Point().init(first_width + 12, 0))
            right.allocate(second_width, height, -1, transform)


class ForecastLayout(Gtk.Box):
    stacked = GObject.Property(type=bool, default=False)

    def __init__(self):
        super().__init__(hexpand=True)
        self.set_layout_manager(ForecastManager())
        self.connect('notify::stacked', lambda *_: self.queue_resize())
