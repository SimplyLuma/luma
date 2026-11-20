#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Light dismiss: a grown action bar folds on a primary press outside it, or Esc.

The press is watched on the window in the capture phase and claimed, so it
never reaches what is under it (an ArrangeView canvas does not select or drag).
A press inside the bar, or on another layer over the island, is not outside.
A field in the bar with an edit commits first: it loses focus.
"""
import time

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

Gtk.init()

from luma_appkit import ActionCenter, ActionEditor, BarAction, BarPrompt  # noqa: E402
from luma_appkit.structure_layers import LayerHost  # noqa: E402


def settle(seconds: float = 0.3) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.005)


def controllers(widget: Gtk.Widget, kind: type) -> list:
    found = []
    model = widget.observe_controllers()
    for i in range(model.get_n_items()):
        controller = model.get_item(i)
        if isinstance(controller, kind) and controller.get_name() == "lumaui-light-dismiss":
            found.append(controller)
    return found


def centre(widget: Gtk.Widget, root: Gtk.Widget) -> tuple[float, float]:
    ok, rect = widget.compute_bounds(root)
    assert ok
    return rect.get_x() + rect.get_width() / 2, rect.get_y() + rect.get_height() / 2


window = Gtk.Window(default_width=900, default_height=640)
body = Gtk.Box(hexpand=True, vexpand=True)
canvas = Gtk.Box(hexpand=True, vexpand=True)  # stands for an ArrangeView
body.append(canvas)
window.set_child(body)
center = ActionCenter()
center.attach(body)
commits = []
field = Gtk.Entry(text="12")
leave = Gtk.EventControllerFocus()
leave.connect("leave", lambda *_a: commits.append(field.get_text()))
field.add_controller(leave)
panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
panel.append(field)
center.show_bar([BarAction("sparkles", "Style", key="style"), BarAction("trash-2", tooltip="Delete")])
window.present()
settle()

presses = controllers(window, Gtk.GestureClick)
keys = controllers(window, Gtk.EventControllerKey)
assert len(presses) == 1 and len(keys) == 1, (presses, keys)
press = presses[0]
assert press.get_propagation_phase() == Gtk.PropagationPhase.CAPTURE  # before anything under the bar
assert press.get_button() == Gdk.BUTTON_PRIMARY
assert keys[0].get_propagation_phase() == Gtk.PropagationPhase.CAPTURE

# At rest nothing is dismissed and the press goes on to the canvas.
x, y = centre(canvas, window)
press.emit("pressed", 1, x, y)
assert center.state == "bar" and center.grown is None

# A grown panel: a press inside the bar keeps it; a press on the canvas folds it, the field's edit committed.
center.grow("style", panel)
settle()
assert center.grown == "style"
field.grab_focus()
settle()
field.set_text("24")
bx, by = centre(center.bar, window)
press.emit("pressed", 1, bx, by)
assert center.grown == "style", "a press inside the grown bar is the bar's"
press.emit("pressed", 1, x, y)
settle()
assert center.grown is None, "a press outside folds the grown bar"
assert commits and commits[-1] == "24", commits
assert window.get_focus() is None or not window.get_focus().is_ancestor(center)

# A press on another layer over the island (a sheet) is that layer's.
center.grow("style", panel)
settle()
host = LayerHost.for_widget(body)
sheet = Gtk.Box(width_request=200, height_request=120, halign=Gtk.Align.START, valign=Gtk.Align.START)
host.add_overlay(sheet)
settle()
sx, sy = centre(sheet, window)
press.emit("pressed", 1, sx, sy)
assert center.grown == "style", "a press on another layer does not dismiss"
host.remove_overlay(sheet)

# Esc anywhere in the window folds it.
assert keys[0].emit("key-pressed", Gdk.KEY_Escape, 0, 0) is True
assert center.grown is None
assert keys[0].emit("key-pressed", Gdk.KEY_Escape, 0, 0) is False  # at rest Esc is not taken

# The editor folds the same way and keeps its draft.
editor = ActionEditor("Reply", "reply", placeholder="Write", primary=BarAction("send-horizontal", "Send"))
center.set_editor(editor)
center.show_bar([BarPrompt("Reply…")])
center.grow()
settle()
assert center.state == "editor"
press.emit("pressed", 1, x, y)
settle()
assert center.state == "bar", center.state
center.grow()
settle()
assert keys[0].emit("key-pressed", Gdk.KEY_Escape, 0, 0) is True
assert center.state == "bar"

# Unrooted, the window no longer carries the bar's controllers.
body.remove(canvas)
center.get_parent().remove_overlay(center)
settle()
assert not controllers(window, Gtk.GestureClick) and not controllers(window, Gtk.EventControllerKey)
window.destroy()
settle(0.1)
print("ActionCenter light dismiss: outside press, Esc, inside press, other layer, editor, field commit PASS")
