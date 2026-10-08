# SPDX-License-Identifier: Apache-2.0
"""Parity: ActionCenter, ActionEditor and the bar items (action_center.py).

The bar, double-height, split and editor states, each on an unattached
center (the harness puts it in a window itself).
"""


def c_action(C, icon, label=None, tooltip=None, primary=False, danger=False, active=False, sensitive=True):
    item = C.BarItem.new_action(icon, label, None)
    if tooltip:
        item.set_tooltip(tooltip)
    item.set_primary(primary)
    item.set_danger(danger)
    item.set_active(active)
    item.set_sensitive(sensitive)
    return item


def c_editor(C):
    editor = C.ActionEditor.new("Reply all", "reply-all")
    editor.set_summary("to {}", "Priya Raman, Nora Feld")
    editor.add_mode("one", "Reply", "reply")
    editor.add_mode("all", "Reply all", "reply-all")
    editor.set_mode("all")
    editor.add_field("To", None)
    editor.add_tool(c_action(C, "bold", tooltip="Bold", active=True))
    editor.add_tool(C.BarItem.new_separator())
    editor.add_tool(C.BarItem.new_spacer())
    editor.add_tool(c_action(C, "paperclip", tooltip="Attach"))
    editor.set_placeholder("Write your reply")
    editor.set_primary(c_action(C, "send-horizontal", "Send"))
    return editor


def py_editor(K):
    return K.ActionEditor(
        "Reply all", "reply-all", summary="to {}", summary_emphasis="Priya Raman, Nora Feld",
        modes=[("one", "Reply", "reply"), ("all", "Reply all", "reply-all")], mode="all",
        fields=[("To", "")],
        tools=[K.BarAction("bold", tooltip="Bold", active=True), K.SEPARATOR, K.SPACER,
               K.BarAction("paperclip", tooltip="Attach")],
        placeholder="Write your reply", primary=K.BarAction("send-horizontal", "Send"))


def c_bar(C):
    center = C.ActionCenter.new(None)
    center.show_bar([C.BarItem.new_chip("3 photos", "image", True), c_action(C, "share-2", "Share"),
                     C.BarItem.new_separator(), c_action(C, "heart", tooltip="Favourite", active=True),
                     C.BarItem.new_spacer(), c_action(C, "trash-2", tooltip="Delete", danger=True),
                     c_action(C, "check", "Done", primary=True, sensitive=False)], None)
    return center


def py_bar(K):
    center = K.ActionCenter()
    center.show_bar([K.BarChip("3 photos", icon="image", on_dismiss=lambda: None), K.BarAction("share-2", "Share"),
                     K.SEPARATOR, K.BarAction("heart", tooltip="Favourite", active=True),
                     K.SPACER, K.BarAction("trash-2", tooltip="Delete", danger=True),
                     K.BarAction("check", "Done", primary=True, sensitive=False)])
    return center


def c_double(C):
    center = C.ActionCenter.new(c_editor(C))
    center.show_bar([C.BarItem.new_prompt("Reply to Priya and Nora…"), c_action(C, "paperclip", tooltip="Attach")],
                    C.BarItem.new_context("reply", "Replying to {}’s message", "Priya", True))
    return center


def py_double(K):
    center = K.ActionCenter(py_editor(K))
    center.show_bar([K.BarPrompt("Reply to Priya and Nora…"), K.BarAction("paperclip", tooltip="Attach")],
                    context=K.BarContext("reply", "Replying to {}’s message", emphasis="Priya",
                                         on_dismiss=lambda: None))
    return center


def c_split(C):
    center = C.ActionCenter.new(None)
    center.show_split([C.BarItem.new_chip("3 photos", "image", False), c_action(C, "share-2", "Share")],
                      [c_action(C, "heart", "Favourite"), c_action(C, "trash-2", tooltip="Delete")])
    return center


def py_split(K):
    center = K.ActionCenter()
    center.show_split([K.BarChip("3 photos", icon="image"), K.BarAction("share-2", "Share")],
                      [K.BarAction("heart", "Favourite"), K.BarAction("trash-2", tooltip="Delete")])
    return center


def c_editor_state(C):
    center = c_double(C)
    center.grow()
    return center


def py_editor_state(K):
    center = py_double(K)
    center.grow()
    return center


def c_plain_editor(C):
    center = C.ActionCenter.new(C.ActionEditor.new("New event", "calendar-plus"))
    center.show_bar([C.BarItem.new_prompt("New event")], None)
    center.grow()
    return center


def py_plain_editor(K):
    center = K.ActionCenter(K.ActionEditor("New event", "calendar-plus"))
    center.show_bar([K.BarPrompt("New event")])
    center.grow()
    return center


CASES = [
    ("hidden", lambda C, Gtk: C.ActionCenter.new(None), lambda K, Gtk: K.ActionCenter()),
    ("bar", lambda C, Gtk: c_bar(C), lambda K, Gtk: py_bar(K)),
    ("double", lambda C, Gtk: c_double(C), lambda K, Gtk: py_double(K)),
    ("split", lambda C, Gtk: c_split(C), lambda K, Gtk: py_split(K)),
    ("editor", lambda C, Gtk: c_editor_state(C), lambda K, Gtk: py_editor_state(K)),
    ("editor-plain", lambda C, Gtk: c_plain_editor(C), lambda K, Gtk: py_plain_editor(K)),
    ("editor-alone", lambda C, Gtk: c_editor(C), lambda K, Gtk: py_editor(K)),
]


def headed_bar(module, Gtk, python=False):
    center = py_bar(module) if python else c_bar(module)
    center.set_head(Gtk.Label(label="Now playing"))
    return center


CASES += [
    ("headed", lambda C, Gtk: headed_bar(C, Gtk), lambda K, Gtk: headed_bar(K, Gtk, True)),
]
