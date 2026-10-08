# SPDX-License-Identifier: Apache-2.0
"""Parity: StackedButton / StackedButtons (action_stack.py)."""

ITEMS = [("log-out", "Leave", False), ("trash-2", "Delete", True), ("share-2", "A rather long action name", False)]


def c_group(C, count, small):
    group = C.StackedButtons.new(small)
    for icon, label, danger in ITEMS[:count]:
        group.append(C.StackedButton.new(icon, label, danger))
    return group


def py_group(K, count, small):
    return K.StackedButtons([K.StackedButton(icon, label, danger=danger) for icon, label, danger in ITEMS[:count]],
                            small=small)


CASES = [
    ("button", lambda C, Gtk: C.StackedButton.new("log-out", "Leave", False),
     lambda K, Gtk: K.StackedButton("log-out", "Leave")),
    ("danger", lambda C, Gtk: C.StackedButton.new("trash-2", "Delete", True),
     lambda K, Gtk: K.StackedButton("trash-2", "Delete", danger=True)),
    ("pair", lambda C, Gtk: c_group(C, 2, False), lambda K, Gtk: py_group(K, 2, False)),
    ("trio-small", lambda C, Gtk: c_group(C, 3, True), lambda K, Gtk: py_group(K, 3, True)),
]
