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
        # The Inspector's rows for the selection (Source, Look, Arrange), each from a fresh Inspector.
        win.select(w.id)
        pump(0.2)
        count = len([r for r in walk(win.inspector) if isinstance(r, PanelRow)])
        for index in range(count):
            win.select(None)
            win.select(w.id)
            pump(0.05)
            rows = [r for r in walk(win.inspector) if isinstance(r, PanelRow)]
            if index < len(rows) and rows[index].get_sensitive() and getattr(rows[index], "on_activate", None):
                rows[index].on_activate()
                pressed += 1
                pump(0.05)
            win.center.fold()
    win.select(None)
    pump(0.1)
    for row in [r for r in walk(win.inspector) if isinstance(r, PanelRow)]:  # the scene's
        if row.get_sensitive() and getattr(row, "on_activate", None) is not None and "Add" not in row.get_name():
            pressed += 1
    from luma_appkit.action_bubble import MenuItem

    def leaves(rows):
        for row in rows:
            if isinstance(row, MenuItem):
                yield from leaves(row.submenu) if row.submenu is not None else [row]

    def close_menus():
        for top in Gtk.Window.list_toplevels():
            if top is not win and top.get_visible() and top.get_transient_for() is win:
                top.close()
        win.center.fold()

    # Every row of every widget's menu (submenus too) and of every scene's menu, at a point, each from a
    # fresh menu and undone after, so each acts on the scene as it was.
    activated = 0
    for kind in ("widget", "scene"):
        targets = [w.id for w in win.project.active_scene.widgets] if kind == "widget" else \
            [s.id for s in win.project.scenes]
        for target in targets:
            open_menu = (lambda t=target: win._widget_menu(t, win.arrange, 40, 40)) if kind == "widget" else \
                (lambda t=target: win._scene_menu(t, win._scene_rows[t], 10, 10))
            open_menu()
            pump(0.1)
            count = len(list(leaves(win._last_menu)))
            close_menus()
            for index in range(count):
                exists = target in ({w.id for w in win.project.active_scene.widgets} if kind == "widget"
                                    else {s.id for s in win.project.scenes})
                if not exists:
                    break
                steps = len(win.history.undos)
                open_menu()
                pump(0.05)
                items = list(leaves(win._last_menu))
                if index >= len(items):
                    break
                items[index].on_activate()
                activated += 1
                pump(0.1)
                close_menus()
                if len(win.history.undos) > steps:
                    win.undo()
                    pump(0.1)
    print(f"pressed {pressed} panel rows, {activated} menu rows; errors: {errors}")
    assert pressed > 5 and activated > 20 and not errors, errors

    # Inline rename: the row's name is edited in place; Enter keeps it, Esc cancels.
    from luma_appkit import InlineRenameField

    def field_in(row):
        return next(c for c in walk(row) if isinstance(c, InlineRenameField))

    scene = win.project.active_scene
    win.rename_scene(scene.id)
    pump(0.3)
    field = field_in(win._scene_rows[scene.id])
    field.set_text("Renamed here")
    field.emit("activate")
    pump(0.2)
    assert win.project.active_scene.name == "Renamed here", win.project.active_scene.name
    win.rename_scene(scene.id)
    pump(0.3)
    field = field_in(win._scene_rows[scene.id])
    field.set_text("Never")
    field._finished = True  # as Esc does
    field._cancel()
    pump(0.2)
    assert win.project.active_scene.name == "Renamed here"
    w = win.project.active_scene.widgets[0]
    win.rename_widget(w)
    pump(0.3)
    field = field_in(win._widget_rows[w.id])
    field.set_text("Cam")
    field.emit("activate")
    pump(0.2)
    assert win.project.active_scene.widget(w.id).name == "Cam"
    print("inline rename keeps and cancels")

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
    assert sized["landscape"][1] < before["landscape"][1], (sized, before)
    print("a dragged seam holds", sized)
    win.close()  # the engine stops with the window, before the interpreter goes
    pump(1.0)
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
