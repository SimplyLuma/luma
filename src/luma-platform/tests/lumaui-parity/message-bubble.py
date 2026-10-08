# SPDX-License-Identifier: Apache-2.0
"""Parity: LumaMessageBubble vs content_message.MessageBubble."""


def _joined(bubble, above, below, selected=False):
    bubble.set_joins(above, below)
    bubble.set_selected(selected)
    return bubble


CASES = [
    ("theirs", lambda C, Gtk: _joined(C.MessageBubble.new("Freeze strings on the 10th?", False), False, True),
     lambda K, Gtk: K.MessageBubble("Freeze strings on the 10th?", joined_below=True)),
    ("mine", lambda C, Gtk: C.MessageBubble.new("Works for me.", True), lambda K, Gtk: K.MessageBubble("Works for me.", mine=True)),
    ("mine-selected", lambda C, Gtk: _joined(C.MessageBubble.new("Yes", True), True, False, True),
     lambda K, Gtk: K.MessageBubble("Yes", mine=True, joined_above=True, selected=True)),
    ("media", lambda C, Gtk: C.MessageBubble.new_with_child(Gtk.Picture(), True),
     lambda K, Gtk: K.MessageBubble(child=Gtk.Picture(), mine=True)),
]
