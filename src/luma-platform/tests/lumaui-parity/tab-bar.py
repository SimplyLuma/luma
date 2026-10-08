# SPDX-License-Identifier: Apache-2.0
"""Parity: TabBar (structure_tabs.py): Clock's places, full and compact."""

PLACES = [("world", "World", "globe"), ("alarms", "Alarms", "alarm-clock"), ("timer", "Timer", "hourglass")]


def _c(compact):
    def make(C, Gtk):
        bar = C.TabBar.new(compact)
        for key, label, icon in PLACES:
            bar.add(key, label, icon)
        return bar
    return make


CASES = [
    ("full", _c(False), lambda K, Gtk: K.TabBar(PLACES)),
    ("compact", _c(True), lambda K, Gtk: K.TabBar(PLACES, compact=True)),
]
