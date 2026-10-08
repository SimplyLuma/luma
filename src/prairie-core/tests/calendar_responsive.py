#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Current Calendar routes on a private bus and Xvfb, using only v70 fixtures.

LUMA_CALENDAR_PRIVATE_BUS=1 must accompany dbus-run-session. Arguments:
output-directory width. Captures are test-display images, never Nick's desktop.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

from calendar_preview_identity import require_private_bus, require_test_display


def capture_window(window, path):
    """Render this fixture window through GTK; no desktop capture tool."""
    import gi
    gi.require_version("Graphene", "1.0")
    from gi.repository import Graphene, Gtk
    width, height = window.get_width(), window.get_height()
    paintable = Gtk.WidgetPaintable.new(window).get_current_image()
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, width, height)
    node = snapshot.to_node()
    assert node is not None, "Fixture window produced no render node"
    bounds = Graphene.Rect()
    bounds.init(0, 0, width, height)
    texture = window.get_native().get_renderer().render_texture(node, bounds)
    assert (texture.get_width(), texture.get_height()) == (width, height)
    assert texture.save_to_png(str(path)), "Fixture image could not be saved"


def main():
    require_private_bus()
    require_test_display()
    root = Path(__file__).resolve().parents[3]
    os.environ["LUMA_CALENDAR_FIXTURE"] = str(root / "tests/fixtures/calendar-v70.json")
    os.environ["LUMA_CALENDAR_STYLE_PATH"] = str(root / "src/prairie-core/style/calendar.css")
    from prairie_apps import calendar as app
    from gi.repository import GLib, Gtk

    app.APP_ID += ".ResponsiveTest"
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    width = int(sys.argv[2])
    application = app.CalendarApplication()
    report = {"expected_width": width, "stages": [], "failures": []}
    window = None
    selected = None
    saved_title = "Edited fixture event"

    def resize(content_width):
        # Xvfb's solid-CSD fallback reserves5px on each side. Keep the
        # assertion on actual content width; request the enclosing width.
        border = 10 if window.has_css_class("solid-csd") else 0
        window.set_default_size(content_width + border, 810)

    def descendants(widget):
        yield widget
        child = widget.get_first_child()
        while child:
            yield from descendants(child)
            child = child.get_next_sibling()

    def capture(name, expected_width=None):
        expected = width if expected_width is None else expected_width
        actual = window.get_width()
        assert actual == expected, (name, actual, expected)
        if actual <= 720:
            heading_ok, heading_bounds = window.heading.compute_bounds(window)
            corner_ok, corner_bounds = window.corner.compute_bounds(window)
            assert heading_ok and corner_ok, name
            assert heading_bounds.get_y() >= corner_bounds.get_y() + corner_bounds.get_height() + 4, (
                name, "date heading overlaps controls", heading_bounds.get_y(),
                corner_bounds.get_y(), corner_bounds.get_height(), window.heading.get_margin_top())
            if window.view in {"month", "week"}:
                body = window.surface if window.view == "month" else window.surface.week_header
                body_ok, body_bounds = body.compute_bounds(window)
                assert body_ok and body_bounds.get_y() >= heading_bounds.get_y() + heading_bounds.get_height() + 4, (
                    name, "calendar body overlaps date heading", body_bounds.get_y(),
                    heading_bounds.get_y(), heading_bounds.get_height())
        assert window.panel_control.get_active() == window.pane.shown, name
        if window.pane.shown:
            assert window.pane.sheet.get_mapped(), (name, "Open pane is not mapped")
            assert window.pane.close_button.get_mapped(), (name, "Pane Close is not mapped")
            if actual > 639:
                host_ok, host_bounds = window.host.compute_bounds(window)
                pane_ok, pane_bounds = window.pane.sheet.compute_bounds(window)
                assert host_ok and pane_ok, name
                gap = pane_bounds.get_x() - host_bounds.get_x() - host_bounds.get_width()
                assert gap >= 8, (name, "details pane has no island gutter", gap)
        controls = []
        for widget in descendants(window):
            if isinstance(widget, (Gtk.Button, Gtk.MenuButton)) and widget.get_mapped():
                ok, rect = widget.compute_bounds(window)
                if ok:
                    assert rect.get_x() >= -1, (name, widget.get_name(), rect.get_x())
                    assert rect.get_x() + rect.get_width() <= actual + 1, (
                        name, widget.get_name(), rect.get_x(), rect.get_width())
                    controls.append([widget.get_name(), rect.get_x(), rect.get_width()])
        report["stages"].append(dict(name=name, width=actual, view=window.view,
                                    details=window.pane.shown, drawer=window.pane.is_drawer,
                                    controls=controls))
        capture_window(window, output / f"{name}.png")

    def month():
        nonlocal selected
        assert window.fixture and window.loaded and len(window.fixture.events) == 42
        assert window.view == "month"
        selected = next(e for e in window.events if e.editable and e.start.date() == window.today)
        capture("month")
        uid = window.sources[0].uid
        window.toggle_calendar(uid)
        assert uid in window.hidden_sources
        assert len(window.calendar_marks.items) == min(4, len(window.sources) - 1)
        assert all(item[0].get_tooltip_text() != window.sources[0].name for item in window.calendar_marks.items)
        window.toggle_calendar(uid)
        assert uid not in window.hidden_sources
        assert len(window.calendar_marks.items) == min(4, len(window.sources))
        window.select_day(window.today)

    def day():
        assert window.pane.shown and window.pane.subject == window.today
        assert window.pane.is_drawer == (width <= 639)
        capture("day")
        window.pane.close_button.emit("clicked")

    def closed():
        assert not window.pane.shown
        capture("panel-closed")
        window.panel_control.emit("clicked")

    def reopened():
        assert window.pane.shown
        assert window.pane.close_button.get_mapped()
        window.pane.close_button.emit("clicked")

    def reclosed():
        assert not window.pane.shown
        window.open_event(selected)

    def event():
        assert window.pane.shown and window.selected_event == selected
        assert window.pane.is_drawer == (width <= 639)
        capture("event-details")
        window.edit_event(selected)

    def editor():
        assert window.draft.title == selected.summary
        assert window.title_field.get_mapped()
        capture("edit-event")
        window.title_field.set_text(saved_title)
        assert window.draft.title == saved_title
        window.save_draft()

    def saved():
        assert window.draft is None and not window._saving
        assert next(e for e in window.fixture.events if e.uid == selected.uid).summary == saved_title
        assert len(window.fixture.events) == 42
        window.pane.close()
        window.set_view("week")

    def week():
        assert window.view == "week"
        # The highlighted day heading spans its grid track; inset its text,
        # not the heading box, so the phone column is not narrowed by 8 px.
        head = window.surface.week_header_canvas.items[2][0]
        track = window.surface.get_first_child().get_child_at(3, 1)
        head_ok, head_box = head.compute_bounds(window)
        track_ok, track_box = track.compute_bounds(window)
        assert head_ok and track_ok
        assert abs(head_box.get_x() - track_box.get_x()) <= 1, (head_box, track_box)
        assert abs(head_box.get_width() - track_box.get_width()) <= 1, (head_box, track_box)
        for day_head, *_ in window.surface.week_header_canvas.items:
            day_name = day_head.get_first_child()
            assert not day_name.get_layout().is_ellipsized(), (width, day_name.get_label())
        capture("week")
        window.set_view("flow")

    def flow():
        assert window.view == "flow"
        capture("flow")
        window.select_day(window.today)

    def escape():
        assert window.pane.shown
        assert window.pane.close_button.grab_focus()
        assert window.get_focus() == window.pane.close_button
        subprocess.run(["xdotool", "search", "--onlyvisible", "--name", "^Calendar$",
                        "windowfocus", "key", "Escape"], check=True)

    def escaped():
        assert not window.pane.shown, "Escape did not dismiss the pane/drawer"
        capture("escape-dismissed")
        window.new_event(text="Lunch with Priya Fri 1pm")

    def parsed():
        assert window.draft.title == "Lunch with Priya" and window.draft.people
        capture("parsed-editor")
        draft = window.draft
        window.discard_draft()
        assert window.draft is None
        window.restore_draft(draft)
        assert window.draft == draft
        window.discard_draft()
        window.set_view("month")
        resize(1024)

    def wide():
        window.select_day(window.today)

    def wide_day():
        assert window.pane.shown and not window.pane.is_drawer
        capture("wide-1024", 1024)
        window.pane.close()
        resize(width)

    def returned():
        assert window.view == "month" and not window.pane.shown
        capture("returned")

    steps = iter((month, day, closed, reopened, reclosed, event, editor, saved, week, flow, escape, escaped, parsed,
                  wide, wide_day, returned))

    def finish():
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        if window is not None:
            window.close()
        application.quit()

    def tick():
        try:
            next(steps)()
        except StopIteration:
            finish()
            print(json.dumps(report))
            return GLib.SOURCE_REMOVE
        except Exception as error:
            import traceback
            traceback.print_exc()
            report["failures"].append(repr(error))
            finish()
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(600, tick)
        return GLib.SOURCE_REMOVE

    def activated(_application):
        nonlocal window
        window = application.get_active_window()
        assert window.get_application().get_application_id() == app.APP_ID
        resize(width)
        GLib.timeout_add(2000, tick)

    def timeout():
        report["failures"].append("Calendar responsive test exceeded30seconds")
        finish()
        return GLib.SOURCE_REMOVE

    GLib.timeout_add_seconds(30, timeout)
    application.connect_after("activate", activated)
    application.run([])
    return 1 if report["failures"] or len(report["stages"]) != 11 else 0


if __name__ == "__main__":
    raise SystemExit(main())
