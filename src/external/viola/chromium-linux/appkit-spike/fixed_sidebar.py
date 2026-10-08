# SPDX-License-Identifier: GPL-3.0-only
"""Constrain the native sidebar's natural request, including live artwork."""
import gi
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk


class FixedSidebar(Adw.Bin):
    def __init__(self, child, width=252):
        super().__init__(child=child, width_request=width, hexpand=False, vexpand=True)
        # AdwBin retains child ownership/disposal. Its ordinary bin layout
        # propagates artwork's natural size; this fixed sidebar must not.
        self.set_layout_manager(None)

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            width = max(1, self.get_size_request()[0])
            return width, width, -1, -1
        child = self.get_child()
        return child.measure(orientation, for_size) if child else (0, 0, -1, -1)

    def do_size_allocate(self, width, height, baseline):
        child = self.get_child()
        if child:
            child.allocate(width, height, baseline, None)

    def do_snapshot(self, snapshot):
        child = self.get_child()
        if child:
            self.snapshot_child(child, snapshot)
