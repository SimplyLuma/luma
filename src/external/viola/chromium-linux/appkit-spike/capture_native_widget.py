# SPDX-License-Identifier: GPL-3.0-only
"""Diagnostic GTK rendering only; not an outside-compositor shadow capture."""
import gi
gi.require_version('Gsk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Gsk, Graphene, Gtk


def capture(window, path, width=1180, height=820, *, paintable=None):
    if not window.get_mapped():
        raise RuntimeError('GTK capture requires the isolated mapped QA display')
    width, height = window.get_width(), window.get_height()
    snapshot = Gtk.Snapshot()
    (paintable or Gtk.WidgetPaintable.new(window)).snapshot(snapshot, width, height)
    node = snapshot.to_node()
    if node is None:
        raise RuntimeError('Unpresented GTK window produced no render node')
    renderer = Gsk.Renderer.new_for_surface(window.get_surface())
    bounds = Graphene.Rect()
    bounds.init(0, 0, width, height)
    try:
        texture = renderer.render_texture(node, bounds)
        if not texture.save_to_png(str(path)):
            raise RuntimeError('Could not save GTK widget diagnostic')
    finally:
        renderer.unrealize()
