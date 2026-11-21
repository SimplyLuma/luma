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
    pump(1.0)  # the rows rebuilt by the menu rows above are settled
    # The field is in the row at once; used before its focus timer, so a window without focus (Xvfb)
    # never finishes it first.
    win.rename_scene(scene.id)
    field = field_in(win._scene_rows[scene.id])
    field.set_text("Renamed here")
    field.emit("activate")
    pump(0.2)
    assert win.project.active_scene.name == "Renamed here", win.project.active_scene.name
    pump(0.3)
    win.rename_scene(scene.id)
    field = field_in(win._scene_rows[scene.id])
    field.set_text("Never")
    field._finished = True  # as Esc does
    field._cancel()
    pump(0.2)
    assert win.project.active_scene.name == "Renamed here"
    w = win.project.active_scene.widgets[0]
    win.rename_widget(w)
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
    outputs(win)
    inspector(win)
    several(win)
    win.close()  # the engine stops with the window, before the interpreter goes
    pump(1.0)
    app.quit()


def several(win) -> None:
    """Several widgets: the list and the canvas agree, Mixed where their Look differs, every command acts on
    all of them as one undo step."""
    from luma_appkit import TypeLabel

    scene = next(sc for sc in win.project.scenes if sum(w.has_picture for w in sc.widgets) >= 2)
    win.switch_scene(scene.id)
    pump(0.2)
    a, b = [w for w in scene.widgets if w.has_picture][:2]
    win.project.set_effect(a, "corner_radius", 0)
    win.project.set_effect(b, "corner_radius", 30)
    # Two rows chosen in the list (as Ctrl+click does): the selection follows.
    win.widget_list.unselect_all()
    win.widget_list.select_row(win._widget_rows[a.id])
    win.widget_list.select_row(win._widget_rows[b.id])
    pump(0.2)
    assert set(win.selection) == {a.id, b.id}, win.selection
    assert win.inspector_tile.title == "2 widgets", win.inspector_tile.title
    words = [w.label.get_label() for w in walk(win.inspector) if isinstance(w, TypeLabel)]
    assert "Mixed" in words, words
    steps = len(win.history.undos)

    def now():  # undo restores a copy of the project: look them up again
        sc = win.project.scene(scene.id)
        return sc, sc.widget(a.id), sc.widget(b.id)

    win.apply_look_all("card")
    sc, wa, wb = now()
    assert win._look_of(wa) == win._look_of(wb) == "card"
    win.toggle_hidden()
    sc, wa, wb = now()
    assert not wa.visible and not wb.visible
    win.toggle_lock()
    sc, wa, wb = now()
    assert wa.locked and wb.locked
    win.restack(1)
    sc, wa, wb = now()
    assert [w.id for w in sc.widgets[-2:]] == [a.id, b.id], [w.name for w in sc.widgets]
    assert len(win.history.undos) == steps + 4, (len(win.history.undos), steps)
    for _ in range(3):
        win.undo()
    pump(0.2)
    sc, wa, wb = now()
    assert wa.visible and wb.visible and not wa.locked
    win.set_selection([a.id, b.id])
    count = len(sc.widgets)
    win.duplicate_selected()
    pump(0.2)
    sc, wa, wb = now()
    assert len(sc.widgets) == count + 2 and len(win.selection) == 2, (len(sc.widgets), win.selection)
    win.delete_selected()
    pump(0.2)
    assert len(now()[0].widgets) == count
    win.undo()
    pump(0.2)
    assert len(now()[0].widgets) == count + 2
    win.undo()
    pump(0.2)
    assert len(now()[0].widgets) == count
    win.set_selection([a.id, b.id])
    win._widget_menu(a.id, win.arrange)
    pump(0.2)
    assert any(getattr(r, "label", "") == "Delete 2 widgets" for r in win._last_menu if r is not None)
    win.select(None)
    print("several: list and canvas agree, Mixed, one undo step per command")


def inspector(win) -> None:
    """Closed, the Inspector stays closed through selections and scene switches (and is saved so);
    Source/Look/Arrange or Ctrl+I open it."""
    win.toggle_inspector()
    pump(0.1)
    closed = win.dock.layout()
    assert not win._inspector_shown() and "inspector" in closed.get("closed", {}), closed
    scene = next(sc for sc in win.project.scenes if any(w.has_picture for w in sc.widgets))
    win.switch_scene(scene.id)
    for w in scene.widgets:
        win.select(w.id)
        pump(0.05)
    for other in win.project.scenes:
        win.switch_scene(other.id)
        pump(0.1)
    win.switch_scene(scene.id)
    pump(0.1)
    assert not win._inspector_shown(), "the Inspector reopened by itself"
    picture = next(w for w in win.project.active_scene.widgets if w.has_picture)
    win.select(picture.id)
    win._grow("look")
    pump(0.1)
    assert win._inspector_shown() and "inspector" not in win.dock.layout().get("closed", {})
    win.toggle_inspector()
    pump(0.1)
    assert not win._inspector_shown()
    win.toggle_inspector()
    pump(0.1)
    assert win._inspector_shown()
    win.select(None)
    print("inspector: closes, stays closed, opens when asked")


def outputs(win) -> None:
    """The bar's own keys, pressed: Record shows Stop recording at once and Stop ends it (three times);
    Go live with no stream destination is "Add a destination" and opens the Destinations + menu."""
    import time

    shown = []
    show_bar = win.center.show_bar
    win.center.show_bar = lambda items, **kw: (shown.append(list(items)), show_bar(items, **kw))[1]

    def key(label):
        win._refresh_bar()
        for item in shown[-1]:
            if getattr(item, "label", None) == label and getattr(item, "on_activate", None):
                return item
        raise AssertionError(f"no {label!r} key in the bar: {[getattr(i, 'label', i) for i in shown[-1]]}")

    def labels():
        return [getattr(i, "label", None) for i in shown[-1]]

    win.select(None)
    pump(0.2)
    for turn in range(3):
        key("Record").on_activate()
        pump(0.3)
        if win.recording_since is None:
            # No encoder in this container: the bar still says why, and Record stays Record.
            assert "Record" in labels(), labels()
            print("record: no encoder here; Record stays", labels())
            break
        assert "Stop recording" in labels() and "Go live" in labels() or "Add a destination" in labels(), labels()
        pump(1.0)
        key("Stop recording").on_activate()
        started = time.monotonic()
        while win.recording_since is not None and time.monotonic() - started < 15:
            pump(0.2)
        assert win.recording_since is None, f"recording {turn} did not stop"
        assert "Record" in labels(), labels()
    else:
        print("record: started and stopped 3 times; the bar followed each")
    # A selection never takes the outputs away.
    scene = next(sc for sc in win.project.scenes if any(w.has_picture for w in sc.widgets))
    win.switch_scene(scene.id)
    picture = next(w for w in scene.widgets if w.has_picture)
    win.select(picture.id)
    pump(0.1)
    assert "Record" in labels() and ("Go live" in labels() or "Add a destination" in labels()), labels()
    win.select(None)
    if not win._streams_ready():
        grown = []
        grow = win.center.grow
        win.center.grow = lambda name=None, *a, **kw: (grown.append(name), grow(name, *a, **kw))[1]
        key("Add a destination").on_activate()
        pump(0.2)
        win.center.grow = grow
        assert "add-destination" in grown, grown
        win.center.fold()
        print("go live with no destination: Add a destination opens the + menu")
    win.center.show_bar = show_bar


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
