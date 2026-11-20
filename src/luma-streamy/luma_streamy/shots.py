# SPDX-License-Identifier: Apache-2.0
"""Check runs: STREAMY_SHOT=out.png [STREAMY_SHOT_WIDTH=1280] [STREAMY_SHOT_SELECT=1] renders the window."""
from __future__ import annotations

import os

from gi.repository import GLib, Graphene, Gtk


def render(window: Gtk.Window, path: str) -> None:
    width, height = window.get_width(), window.get_height()
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(window).snapshot(snapshot, width, height)
    node = snapshot.to_node()
    texture = window.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, width, height))
    texture.save_to_png(path)


def schedule(window, path: str) -> None:
    width = int(os.environ.get("STREAMY_SHOT_WIDTH", "1280"))
    height = int(os.environ.get("STREAMY_SHOT_HEIGHT", "800" if width > 560 else "780"))
    window.set_default_size(width, height)

    def select() -> bool:
        if os.environ.get("STREAMY_SHOT_SELECT"):
            scene = window.project.active_scene
            pictures = [w for w in scene.widgets if w.has_picture] if scene else []
            if pictures:
                window.select(pictures[-1].id)
                window._open_inspector()
        return False

    def shoot() -> bool:
        render(window, path)
        window.close()
        return False

    GLib.timeout_add(2500, select)
    GLib.timeout_add(int(os.environ.get("STREAMY_SHOT_DELAY", "5000")), shoot)
