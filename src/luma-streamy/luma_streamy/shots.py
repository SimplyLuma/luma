# SPDX-License-Identifier: Apache-2.0
"""Check runs: STREAMY_SHOT=out.png [STREAMY_SHOT_WIDTH=1280] [STREAMY_SHOT_SELECT=1] renders the window.

Next to the picture, out.json holds the measured boxes the conform check (tests/conform.py) reads.
"""
from __future__ import annotations

import os

from gi.repository import GLib, Graphene, Gtk


def render(window: Gtk.Window, path: str, quiet: bool = False) -> bool:
    width, height = window.get_width(), window.get_height()
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(window).snapshot(snapshot, width, height)
    node = snapshot.to_node()
    if node is None or window.get_renderer() is None:
        if not quiet:
            print(f"render: node {node} renderer {window.get_renderer()} mapped {window.get_mapped()} {width}x{height}", flush=True)
        return False  # not drawn yet: try again in a moment
    texture = window.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, width, height))
    texture.save_to_png(path)
    return True


def render_after_paint(window: Gtk.Window, path: str, then=None, frames: int = 600) -> bool:
    """Render the window into ``path`` right after its next paint: its render node exists only between
    a paint and the next queued draw, and a live preview queues one per frame. ``then()`` follows."""
    clock = window.get_frame_clock()
    if clock is None:
        return False
    left = [frames]

    def painted(clock_) -> None:
        left[0] -= 1
        if render(window, path, quiet=left[0] > 0) or left[0] <= 0:
            clock_.disconnect(handler)
            if then is not None:
                then()

    handler = clock.connect("after-paint", painted)
    window.queue_draw()
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


def _token(name: str):
    from luma_appkit.lumaui_tokens import DOCK

    return DOCK.get(name)


def corner_radius(path: str, box, inset_color_at=None) -> float | None:
    """A rounded box's corner radius, read from the picture: how far along the top-left diagonal the
    box's own colour starts (r·(1 − 1/√2) for a circular corner)."""
    try:
        from gi.repository import GdkPixbuf

        pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
    except Exception:
        return None
    x0, y0 = int(round(box[0])), int(round(box[1]))
    n, rs, px = pixbuf.get_n_channels(), pixbuf.get_rowstride(), pixbuf.get_pixels()

    def at(x, y):
        i = y * rs + x * n
        return px[i], px[i + 1], px[i + 2]

    inside = at(x0 + 24, y0 + 24) if inset_color_at is None else at(*inset_color_at)
    for d in range(0, 40):
        c = at(x0 + d, y0 + d)
        if sum(abs(a - b) for a, b in zip(c, inside)) < 12:
            return round(d / (1 - 2 ** -0.5), 1)
    return None


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
    audio_content = window.mixer_row
    x0, y0 = window.arrange.canvas_to_view(0, 0)
    x1, y1 = window.arrange.canvas_to_view(*window.project.canvas)
    stage = _box(window.arrange, window)
    # The subject chip's name: a plain chip's label, or a subject chip's.
    chip_labels = [child for chip in _find(window.center, "lumaui-bar-chip")
                   for child in _children(chip) if isinstance(child, Gtk.Label)]
    chip_labels += _find(window.center, "lumaui-bar-subject-label")
    # The sidebar's heading text and its rows' leads share one left edge (NavigationSidebar).
    headings = [_box(w, window) for w in _find(window.sidebar, "luma-navigation-heading")]
    # Every row of an open editor: one height, one pair of edges.
    editor_rows = [_box(w, window) for w in _find(window.center, "streamy-row") if w.get_mapped()]
    # Anything of a hidden sidebar still drawn or hit-testable at the window's edge.
    leak = []
    if not window.sidebar.get_mapped() or window.sidebar.get_width() < 2:
        def walk(widget):
            child = widget.get_first_child()
            while child is not None:
                box = _box(child, window)
                if box and box[2] > 1 and box[3] > 1 and child.get_child_visible() and box[0] + box[2] > 0:
                    leak.append([type(child).__name__, box])
                walk(child)
                child = child.get_next_sibling()
        if window.sidebar.get_mapped():
            walk(window.sidebar)
    return {
        "window": [window.get_width(), window.get_height()],
        "requested_width": int(os.environ.get("STREAMY_SHOT_WIDTH", "1280")),
        "headings": headings,
        "editor_rows": editor_rows,
        "sidebar_leak": leak,
        "tile_radius_token": _token("tile_radius"),
        "dock_gap": _token("gap"),
        "island": _box(window.island, window),
        "sidebar": _box(window.sidebar, window) if window.sidebar.get_mapped() else None,
        "sections": [_box(w, window) for w in _find(window.sidebar, "luma-navigation-heading")],
        "scenes": rows(window.sidebar.list),
        "widgets": rows(window.widget_list),
        "tiles": tiles,
        "audio_content": _box(audio_content, window),
        "strips": [_box(w, window) for w in window._strips.values()],
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

    def exact() -> bool:
        # A LumaUI window picks its opening size at present (ADR-042): ask for the check run's again,
        # so a 360 run is 360 pixels wide, not the shape rule's.
        w, h = window.get_width(), window.get_height()
        if (w, h) != (width, height) and w and h:
            # The default size counts the frame; the widget's own size is what the picture is.
            dw, dh = window.get_default_size()
            window.set_default_size(dw + width - w, dh + height - h)
        return False

    for when in (300, 1200, 2200):
        GLib.timeout_add(when, exact)

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
            # A widget (the display by default) failed, waiting or never shared: selected, its Source open.
            scene = window.project.active_scene
            kind = os.environ.get("STREAMY_SHOT_PROBLEM_KIND", "display")
            target = next((w for w in scene.widgets if w.kind == kind), None)
            if target is not None:
                window.engine._remove_branch(target.id)
                if os.environ["STREAMY_SHOT_PROBLEM"] == "waiting":
                    target.source["fake-silent"] = True  # its stream opens and sends nothing
                    window._show_scene()
                    window._widget_waiting(None, target.id)
                else:
                    window._widget_failed(None, target.id, os.environ["STREAMY_SHOT_PROBLEM"])
                window.select(target.id)
                window._grow("source")
            return False
        if os.environ.get("STREAMY_SHOT_SIDEBAR") == "hidden":
            window.sidebar_toggle.set_active(False)
        if os.environ.get("STREAMY_SHOT_KIND"):
            # Select the first widget of a kind (a camera, a microphone) and grow an editor.
            scene = window.project.active_scene
            target = next((w for w in scene.widgets if w.kind == os.environ["STREAMY_SHOT_KIND"]), None)
            if target is not None:
                window.select(target.id)
                if os.environ.get("STREAMY_SHOT_GROW"):
                    window._grow(os.environ["STREAMY_SHOT_GROW"])
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
    painted = [None]
    done = [False]

    def finish() -> None:
        import json

        json_path = path.rsplit(".", 1)[0] + ".json"
        try:
            data = json.load(open(json_path))
            data["radii"] = {key: corner_radius(path, t["box"]) for key, t in data["tiles"].items()
                             if t["box"] and key not in ("preview", "landscape", "vertical")}
            json.dump(data, open(json_path, "w"), indent=1)
        except Exception as error:
            print(f"radius read failed: {error!r}")
        window.close()

    def after_paint(clock) -> None:
        # The window's render node exists only between its paint and the next queued draw; a live
        # preview queues one per frame, so a timer almost never lands in that gap. Right after the
        # paint it always holds the frame just drawn.
        if render(window, path, quiet=True):
            clock.disconnect(painted[0])
            painted[0] = None
            done[0] = True
            GLib.idle_add(lambda: finish() and False)

    def shoot() -> bool:
        if done[0]:
            return False
        tries[0] += 1
        if tries[0] == 1:
            import json

            try:
                with open(path.rsplit(".", 1)[0] + ".json", "w") as out:
                    json.dump(measure(window), out, indent=1)
            except Exception as error:  # a check run: say what broke, keep the picture
                print(f"measure failed: {error!r}")
        clock = window.get_frame_clock()
        if painted[0] is None and clock is not None:
            painted[0] = clock.connect("after-paint", after_paint)
        window.queue_draw()
        # A slow software GL (an aarch64 builder) can take many seconds to draw the first frame.
        if tries[0] > int(os.environ.get("STREAMY_SHOT_TRIES", "60")):
            print(f"render: no frame painted after {tries[0]} tries", flush=True)
            if painted[0] is not None:
                window.get_frame_clock().disconnect(painted[0])
            window.close()
            return False
        return True

    if os.environ.get("STREAMY_SHOT_FRAMES"):
        frames = [0, None]

        def count() -> bool:
            clock = window.get_frame_clock()  # None until the window is realized
            if frames[1] is None and clock is not None:
                frames[1] = clock.connect("after-paint", lambda *_a: frames.__setitem__(0, frames[0] + 1))
            print(f"frames painted {frames[0]}", flush=True)
            return True

        GLib.timeout_add(1000, count)
    if os.environ.get("STREAMY_SHOT_LAYOUT"):  # a dock layout (JSON) to shoot, e.g. Vertical docked small
        import json

        window.dock.set_layout(json.loads(os.environ["STREAMY_SHOT_LAYOUT"]))
    GLib.timeout_add(2500, select)
    GLib.timeout_add(int(os.environ.get("STREAMY_SHOT_DELAY", "5000")),
                     lambda: GLib.timeout_add(500, shoot) and False)
