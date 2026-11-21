#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""ActionEditor's first field and empty sections, SyncKey and OverrideDot, and contained row thumbnails."""
import time

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

Gtk.init()

from luma_appkit import (ActionEditor, BarAction, DetailsPane, OverrideDot, RowLead, SyncKey,  # noqa: E402
                         Sparkline, ToggleChip, install_appkit)

install_appkit()


def settle(seconds: float = 0.2) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.005)


# ActionEditor: the first field takes focus with its text selected; a hidden body takes no room.
name = Gtk.Entry(text="Camera")
body = Gtk.Box(visible=False)
editor = ActionEditor("Rename", "pencil", fields=[("Name", name), ("Note", "Optional")], body=body,
                      primary=BarAction("check", label="Save"))
window = Gtk.Window(child=editor)
window.present()
settle()
assert editor.has_css_class("bodyless") and not editor.scroller.get_visible()
assert editor.hint.get_label() == "Esc to cancel · Ctrl Return", editor.hint.get_label()
editor.focus_content()
settle()
assert window.get_focus() is not None and window.get_focus().is_ancestor(name) or window.get_focus() is name
bounds = name.get_selection_bounds()  # (start, end) when something is selected
assert tuple(bounds)[-2:] == (0, 6), bounds
body.set_visible(True)
assert not editor.has_css_class("bodyless") and editor.scroller.get_visible()
window.destroy()

# SyncKey: synced (a button that unsyncs), own (Sync with …), and the other side's information.
calls = []
key = SyncKey("Landscape", "Vertical", on_unsync=lambda: calls.append("unsync"), on_sync=lambda: calls.append("sync"))
assert key.words.get_label() == "In sync with Landscape" and key.has_css_class("synced")
assert key.row.get_can_target() and not key.action.get_visible()
key.row.emit("clicked")
assert calls == ["unsync"]
key.set_synced(False, changes=3)
assert key.words.get_label() == "Vertical has its own look" and key.action.get_visible()
assert not key.row.get_can_target()
key.row.emit("clicked")
key.action.emit("clicked")
assert calls == ["unsync", "sync"], calls
info = SyncKey("Landscape", "Vertical", primary=True)
assert info.words.get_label() == "Vertical follows this look" and not info.action.get_visible()
info.set_synced(False, changes=1)
assert info.words.get_label() == "Vertical has its own look (1 change)"
info.set_synced(False, changes=3)
assert info.words.get_label() == "Vertical has its own look (3 changes)"
dot = OverrideDot(tooltip="Differs from Landscape")
assert not dot.has_css_class("active")
dot.set_active(True)
assert dot.has_css_class("active") and dot.get_tooltip_text() == "Differs from Landscape"

# A contained thumbnail: the whole 16:9 or 9:16 picture in its own shape, never cropped.
wide = RowLead.thumbnail(Gdk.Paintable.new_empty(160, 90), fit="contain")
tall = RowLead.thumbnail(Gdk.Paintable.new_empty(90, 160), fit="contain")
for lead, ratio in ((wide, 16 / 9), (tall, 9 / 16)):
    shape = lead.get_center_widget()
    assert isinstance(shape, Gtk.AspectFrame) and abs(shape.get_ratio() - ratio) < 1e-3, shape
    assert shape.get_child().get_content_fit() == Gtk.ContentFit.CONTAIN and lead.has_css_class("contain")
forced = RowLead.thumbnail(Gdk.Paintable.new_empty(10, 10), fit="contain", aspect=16 / 9)
assert abs(forced.get_center_widget().get_ratio() - 16 / 9) < 1e-3
assert RowLead.thumbnail(Gdk.Paintable.new_empty(160, 90)).get_center_widget().get_content_fit() == \
    Gtk.ContentFit.COVER
for bad in (lambda: RowLead.thumbnail(None, fit="fill"), lambda: RowLead.thumbnail(None, fit="contain", aspect=0)):
    try:
        bad()
    except ValueError:
        pass
    else:
        raise AssertionError("refused")
# ToggleChip: its own toggle; a refusal keeps it; the live dot; set_active is the app's state.
asked = []
refuse = {"value": False}
chip = ToggleChip("Vertical", active=True, on_toggle=lambda on: (asked.append(on), not refuse["value"])[1])
other = ToggleChip("Landscape", active=True)
assert chip.get_active() and other.get_active() and not chip.live_dot.get_visible()
refuse["value"] = True
chip.set_active(True)
chip.emit("clicked")
assert chip.get_active() and asked == [False], "refused: the chip stays on"
refuse["value"] = False
chip.emit("clicked")
assert not chip.get_active() and asked == [False, False]
chip.set_active(True)
assert chip.get_active() and asked == [False, False], "the app's own state calls nothing"
chip.set_live(True)
assert chip.live and chip.live_dot.get_visible() and chip.has_css_class("live")

# DetailsPane(raised=True): the sheet carries .raised (a raised island beside the main one).
pane = DetailsPane("Inspector", main=True, raised=True)
assert pane.sheet.has_css_class("raised")
pane.set_raised(False)
assert not pane.sheet.has_css_class("raised")
# Sparkline: a rolling window, tones, marks, a description, and it draws.
spark = Sparkline([1, 2, 3], capacity=4, label="Bitrate")
for v in (4, 5):
    spark.push(v)
assert spark.values == [2.0, 3.0, 4.0, 5.0]
spark.mark()
spark.set_tone("danger")
assert spark.has_css_class("danger") and not spark.has_css_class("accent")
for bad in (lambda: spark.set_tone("red"), lambda: Sparkline(capacity=1)):
    try:
        bad()
    except ValueError:
        pass
    else:
        raise AssertionError("refused")
holder = Gtk.Window(default_width=360, default_height=60)
holder.set_child(spark)
holder.present()
settle()
assert spark.get_height() == 24 and spark.get_width() > 100
paint = Gtk.WidgetPaintable.new(spark)
snap = Gtk.Snapshot()
paint.snapshot(snap, spark.get_width(), spark.get_height())
assert snap.to_node() is not None, "it draws"
holder.destroy()
print("ok")
