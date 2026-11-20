# SPDX-License-Identifier: Apache-2.0
"""Check runs: STREAMY_SHOT=out.png [STREAMY_SHOT_WIDTH=1280] [STREAMY_SHOT_SELECT=1] renders the window.

Next to the picture, out.json holds the measured boxes the conform check (tests/conform.py) reads.
"""
from __future__ import annotations

import os

from gi.repository import GLib, Graphene, Gtk


def render(window: Gtk.Window, path: str) -> bool:
    width, height = window.get_width(), window.get_height()
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(window).snapshot(snapshot, width, height)
    node = snapshot.to_node()
    if node is None or window.get_renderer() is None:
        return False  # not drawn yet: try again in a moment
    texture = window.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, width, height))
    texture.save_to_png(path)
    return True


def _box(widget: Gtk.Widget, root: Gtk.Widget) -> list[float] | None:
    if widget is None or not widget.get_mapped():
        return None
    ok, rect = widget.compute_bounds(root)
    if not ok:
        return None
    return [round(rect.get_x(), 1), round(rect.get_y(), 1), round(rect.get_width(), 1), round(rect.get_height(), 1)]


def _find(widget: Gtk.Widget, css: str) -> list[Gtk.Widget]:
    found = []
    child = widget.get_first_child()
    while child is not None:
        if child.has_css_class(css):
            found.append(child)
        found += _find(child, css)
        child = child.get_next_sibling()
    return found


def _children(widget: Gtk.Widget) -> list[Gtk.Widget]:
    out, child = [], widget.get_first_child()
    while child is not None:
        out.append(child)
        child = child.get_next_sibling()
    return out


def measure(window) -> dict:
    """The boxes that LumaUI's rules are about, in window coordinates."""
    from luma_appkit import SidebarRow

    def rows(listbox):
        out = []
        child = listbox.get_first_child()
        while child is not None:
            if isinstance(child, SidebarRow) and child.get_mapped():
                lead = child.lead
                trail = getattr(child, "trail_widget", None)
                out.append({"title": child.title_label.get_label(), "row": _box(child, window),
                            "lead": _box(lead, window) if lead is not None else None,
                            "label": _box(child.title_label, window),
                            "trail": _box(trail, window) if trail is not None else None,
                            "trail_icon": trail.get_child().get_icon_name() if trail is not None and
                            hasattr(trail, "get_child") and hasattr(trail.get_child(), "get_icon_name") else None,
                            "hidden": child.has_css_class("muted")})
            child = child.get_next_sibling()
        return out

    tiles = {}
    for key, tile in window.dock.tiles.items():
        tiles[key] = {"box": _box(tile, window), "header": _box(tile.header, window),
                      "glyph": _box(tile.header.get_first_child(), window),
                      "more": _box(tile.more_button, window),
                      "more_glyph": _box(tile.more_button.get_child(), window),
                      "body": _box(tile.body, window)}
    audio_content = window.audio_box
    x0, y0 = window.arrange.canvas_to_view(0, 0)
    x1, y1 = window.arrange.canvas_to_view(*window.project.canvas)
    stage = _box(window.arrange, window)
    # The subject chip's name: a plain chip's label, or a subject chip's.
    chip_labels = [child for chip in _find(window.center, "lumaui-bar-chip")
                   for child in _children(chip) if isinstance(child, Gtk.Label)]
    chip_labels += _find(window.center, "lumaui-bar-subject-label")
    return {
        "window": [window.get_width(), window.get_height()],
        "island": _box(window.island, window),
        "sidebar": _box(window.sidebar, window) if window.sidebar.get_mapped() else None,
        "sections": [_box(w, window) for w in _find(window.sidebar, "luma-navigation-heading")],
        "scenes": rows(window.sidebar.list),
        "widgets": rows(window.widget_list),
        "tiles": tiles,
        "audio_content": _box(audio_content, window),
        "strips": [_box(w, window) for w in _find(window.audio_box, "streamy-strip")],
        "video": [stage[0] + x0, stage[1] + y0, x1 - x0, y1 - y0] if stage else None,
        "bar": _box(window.center, window),
        "chip": [{"text": lab.get_label(), "box": _box(lab, window),
                  "ellipsized": lab.get_layout().is_ellipsized()} for lab in chip_labels if lab.get_mapped()],
        "grown": window.center.grown,
    }


def schedule(window, path: str) -> None:
    width = int(os.environ.get("STREAMY_SHOT_WIDTH", "1280"))
    height = int(os.environ.get("STREAMY_SHOT_HEIGHT", "800" if width > 560 else "760"))
    window.set_default_size(width, height)

    def select() -> bool:
        if os.environ.get("STREAMY_SHOT_EDITOR"):
            window.edit_destination(then_go=True)
            return False
        if os.environ.get("STREAMY_SHOT_DRAWER"):
            window.dock.open_drawer(os.environ["STREAMY_SHOT_DRAWER"])
            return False
        if os.environ.get("STREAMY_SHOT_RECORDING"):
            window.toggle_record()
            return False
        if os.environ.get("STREAMY_SHOT_STATES"):
            # One hidden picture and one muted sound, for the rows' state keys.
            scene = window.project.active_scene
            pictures = [w for w in scene.widgets if w.has_picture]
            sounds = [w for w in scene.widgets if w.has_sound]
            if pictures:
                window._set_visible(pictures[0], False)
            if sounds:
                window._toggle_mute(sounds[0])
        if os.environ.get("STREAMY_SHOT_PROBLEM"):
            # The display widget as a failed or cancelled share leaves it, selected, its Source open.
            scene = window.project.active_scene
            display = next((w for w in scene.widgets if w.kind == "display"), None)
            if display is not None:
                window._widget_failed(None, display.id, os.environ["STREAMY_SHOT_PROBLEM"])
                window.select(display.id)
                window._grow("source")
            return False
        if os.environ.get("STREAMY_SHOT_SELECT"):
            scene = window.project.active_scene
            pictures = [w for w in scene.widgets if w.has_picture] if scene else []
            if pictures:
                window.select(pictures[-1].id)
                if os.environ.get("STREAMY_SHOT_GROW"):
                    window._grow(os.environ["STREAMY_SHOT_GROW"])
        return False

    tries = [0]

    def shoot() -> bool:
        tries[0] += 1
        if tries[0] == 1:
            import json

            try:
                with open(path.rsplit(".", 1)[0] + ".json", "w") as out:
                    json.dump(measure(window), out, indent=1)
            except Exception as error:  # a check run: say what broke, keep the picture
                print(f"measure failed: {error!r}")
        if render(window, path) or tries[0] > 10:
            window.close()
            return False
        return True

    GLib.timeout_add(2500, select)
    GLib.timeout_add(int(os.environ.get("STREAMY_SHOT_DELAY", "5000")),
                     lambda: GLib.timeout_add(500, shoot) and False)
