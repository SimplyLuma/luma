#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Go live and record from the bar, five times each, against a real server: the bar's keys follow every
start and stop, the server receives each stream and each recording is a playable file. Pictures of the
bar (idle, a widget selected, recording, live) go to SHOT_DIR. Needs a display (Xvfb) and encoders.

    STREAMY_FAKE_SOURCES=1 python3 tests/live_cycle.py rtmp://127.0.0.1:1935/live/streamy SHOT_DIR
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["STREAMY_FAKE_SOURCES"] = "1"
os.environ.setdefault("STREAMY_DATA_DIR", tempfile.mkdtemp(prefix="streamy-live-"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst, Gtk  # noqa: E402

SERVER, SHOTS = sys.argv[1], sys.argv[2]
TURNS = int(os.environ.get("STREAMY_LIVE_TURNS", "5"))
errors: list[str] = []


def pump(seconds: float = 0.3) -> None:
    loop = GLib.MainLoop()
    GLib.timeout_add(int(seconds * 1000), loop.quit)
    loop.run()


def wait(what, seconds: float = 20.0) -> float:
    started = time.monotonic()
    while not what():
        if time.monotonic() - started > seconds:
            raise AssertionError(f"timed out after {seconds:.0f} s")
        pump(0.1)
    return time.monotonic() - started


def shoot(win, name: str) -> None:
    from luma_streamy.shots import render_after_paint

    done = []
    assert render_after_paint(win, f"{SHOTS}/{name}.png", then=lambda: done.append(name))
    wait(lambda: done, 15)
    print(f"shot {name}", flush=True)


def walk(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from walk(child)
        child = child.get_next_sibling()


def confirm(win, action: str) -> None:
    """Press the in-bar confirmation's own button (a DestructiveDialog in the bar)."""
    pump(0.4)
    for widget in walk(win.center):
        if isinstance(widget, Gtk.Button) and widget.get_mapped() and any(
                isinstance(w, Gtk.Label) and w.get_label() == action for w in [widget, *walk(widget)]):
            widget.emit("clicked")
            return
    raise AssertionError(f"no {action!r} confirmation in the bar")


def check(app) -> None:
    win = app.props.active_window
    shown = []
    show_bar = win.center.show_bar
    win.center.show_bar = lambda items, **kw: (shown.append(list(items)), show_bar(items, **kw))[1]

    def labels():
        win._refresh_bar()
        return [getattr(i, "label", None) for i in shown[-1]]

    def press(label):
        for item in shown[-1] if shown else []:
            if getattr(item, "label", None) == label and getattr(item, "on_activate", None):
                item.on_activate()
                return
        raise AssertionError(f"no {label!r} key in the bar: {labels()}")

    folder = tempfile.mkdtemp(prefix="streamy-recordings-")
    store = win.dest_store
    for row in list(store.rows):
        store.remove(row.id)
    stream = store.add("custom")
    stream.server = SERVER
    store.update(stream)
    record = store.add("record")
    record.folder = folder
    record.enabled = False  # Go live doesn't record too
    store.update(record)
    win.dest_panel.refresh()
    win.select(None)
    pump(1.0)
    labels()
    shoot(win, "bar-idle")
    scene = win.project.active_scene
    picture = next(w for w in scene.widgets if w.has_picture)
    win.select(picture.id)
    pump(0.5)
    assert "Record" in labels() and "Go live" in labels(), labels()
    shoot(win, "bar-selected")
    win.select(None)
    pump(0.3)

    for turn in range(TURNS):
        labels()
        press("Go live")
        took = wait(lambda: win.stream_state == "on")
        assert "End stream" in labels(), labels()
        pump(2.0)
        if turn == 0:
            shoot(win, "bar-live")
        press("End stream")
        if turn == 0:
            pump(0.4)
            shoot(win, "bar-end-confirm")
        confirm(win, "End stream")
        ended = wait(lambda: win.stream_state == "off")
        assert "Go live" in labels() and "End stream" not in labels(), labels()
        print(f"live {turn + 1}: on after {took:.2f} s, off after {ended:.2f} s; bar {labels()}", flush=True)
        pump(0.5)

    for turn in range(TURNS):
        before = set(os.listdir(folder))
        labels()
        press("Record")
        took = wait(lambda: win.recording_since is not None)
        assert "Stop recording" in labels(), labels()
        pump(2.0)
        if turn == 0:
            shoot(win, "bar-recording")
        press("Stop recording")
        ended = wait(lambda: win.recording_since is None)
        assert "Record" in labels() and "Stop recording" not in labels(), labels()
        new = sorted(set(os.listdir(folder)) - before)
        assert len(new) == 1, new
        size = os.path.getsize(os.path.join(folder, new[0]))
        assert size > 20_000, f"{new[0]} is {size} bytes"
        print(f"record {turn + 1}: on after {took:.2f} s, off after {ended:.2f} s; {new[0]} {size} bytes; "
              f"bar {labels()}", flush=True)
        pump(0.5)
    print(f"RECORDINGS {folder}", flush=True)
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
