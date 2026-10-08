# SPDX-License-Identifier: Apache-2.0
"""Parity: LumaTypeLabel vs content_type.TypeLabel."""


def _c(C, text, role, unit=None, wrap=False):
    label = C.TypeLabel.new(text, role)
    label.set_unit(unit)
    if wrap:
        label.set_wrap(True)
    return label


CASES = [
    ("body", lambda C, Gtk: _c(C, "Running text", None), lambda K, Gtk: K.TypeLabel("Running text")),
    ("display-unit", lambda C, Gtk: _c(C, "9:41", "display", ":07 AM"),
     lambda K, Gtk: K.TypeLabel("9:41", role="display", unit=":07 AM")),
    ("title-1-wrap", lambda C, Gtk: _c(C, "A page", "title_1", wrap=True),
     lambda K, Gtk: K.TypeLabel("A page", role="title_1", wrap=True)),
    ("numeric", lambda C, Gtk: _c(C, "72", "numeric", "mph"), lambda K, Gtk: K.TypeLabel("72", role="numeric", unit="mph")),
]
