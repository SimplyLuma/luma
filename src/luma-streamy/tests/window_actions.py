#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Every key in every editor and menu reaches a method that exists, and switching scenes never moves a
panel. Runs the real window on drawn stand-in sources (needs a display: Xvfb).

    STREAMY_FAKE_SOURCES=1 python3 tests/window_actions.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["STREAMY_FAKE_SOURCES"] = "1"
os.environ.setdefault("STREAMY_DATA_DIR", tempfile.mkdtemp(prefix="streamy-actions-"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst, Gtk  # noqa: E402

errors: list[str] = []
sys.excepthook = lambda kind, value, tb: errors.append(f"{kind.__name__}: {value}")


def walk(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from walk(child)
        child = child.get_next_sibling()


def pump(seconds: float = 0.3) -> None:
    loop = GLib.MainLoop()
    GLib.timeout_add(int(seconds * 1000), loop.quit)
    loop.run()


def check(app) -> None:
    from luma_appkit.bar_panel import PanelRow

    win = app.props.active_window
    win._choose_image = lambda *_a, **_k: None  # no file dialog in a check run
    pressed = 0
    scene = win.project.active_scene
    for w in list(scene.widgets):
        for editor in ("source", "look", "arrange"):
            win.select(w.id)
            win._grow(editor)
            pump(0.2)
            rows = [r for r in walk(win.center) if isinstance(r, PanelRow)]
            for row in rows:
                if row.get_sensitive() and getattr(row, "on_activate", None) is not None:
                    row.on_activate()
                    pressed += 1
                    pump(0.05)
            win.center.fold()
    # The widget menu's commands.
    for w in list(win.project.active_scene.widgets):
        win._widget_menu(w.id, win.arrange)
        pump(0.1)
    for other in win.project.scenes:
        # As a right click does: anchored to the scene's row, which a switch rebuilds.
        win._scene_menu(other.id, win._scene_rows[other.id])
        pump(0.1)
    print(f"pressed {pressed} panel rows; errors: {errors}")
    assert pressed > 5 and not errors, errors

    # Panels belong to the app, not the scene: switching scenes moves nothing.
    def boxes():
        return {k: (t.get_width(), t.get_height()) for k, t in win.dock.tiles.items()}

    pump(0.5)
    before = boxes()
    for other in win.project.scenes:
        win.switch_scene(other.id)
        pump(0.5)
        assert boxes() == before, (other.name, boxes(), before)
    print("scene switches kept the panels", before)
    # A person's seam size holds through a relayout and scene switches.
    outer = next(p for p, node in win.dock._panes if node["split"] == "v")
    outer.held = True
    outer.set_position(outer.get_position() - 60)
    pump(0.3)
    outer.held = False
    pump(0.5)
    sized = boxes()
    win.dock.refit()
    win.queue_resize()
    for other in win.project.scenes:
        win.switch_scene(other.id)
        pump(0.5)
        assert boxes() == sized, ("the seam moved back", other.name, boxes(), sized)
    assert sized["preview"][1] < before["preview"][1], (sized, before)
    print("a dragged seam holds", sized)
    app.quit()


def main() -> int:
    Gst.init(None)
    from luma_streamy.application import StreamyApplication

    app = StreamyApplication()
    def run_checks(a) -> bool:
        try:
            check(a)
        except Exception as error:  # noqa: BLE001 - a check run reports and stops
            import traceback

            errors.append("".join(traceback.format_exception(error)))
            a.quit()
        return False

    app.connect("activate", lambda a: GLib.timeout_add(2500, run_checks, a))
    status = app.run([])
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else status


if __name__ == "__main__":
    sys.exit(main())
