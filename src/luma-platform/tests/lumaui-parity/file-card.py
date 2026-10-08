# SPDX-License-Identifier: Apache-2.0
"""Parity: LumaFileCard / OpenButton vs content_file (on a real file, read through GIO).

The size is given on both sides: Python's FileCard asks the file's type
without querying standard::type (a GIO critical), so the case keeps it off
that path.
"""
import os
import tempfile

from gi.repository import Gio

_DIR = tempfile.mkdtemp(prefix="lumaui-parity-file-")
NOTES = os.path.join(_DIR, "launch-notes.txt")
with open(NOTES, "w") as handle:
    handle.write("twelve bytes")


def _card(C, compact=False, subtitle=None, tone=None, action=False):
    card = C.FileCard.new(Gio.File.new_for_path(NOTES), compact)
    card.set_size(12)
    if subtitle:
        card.set_subtitle(subtitle)
    if tone:
        card.set_tone(tone)
    if action:
        item = C.BarItem.new_action("pause", None, None)
        item.set_tooltip("Pause")
        card.add_action(item)
    return card


CASES = [
    ("card", lambda C, Gtk: _card(C), lambda K, Gtk: K.FileCard(NOTES, size=12)),
    ("card-compact", lambda C, Gtk: _card(C, compact=True), lambda K, Gtk: K.FileCard(NOTES, size=12, compact=True)),
    ("card-failed", lambda C, Gtk: _card(C, subtitle="Failed", tone="danger"),
     lambda K, Gtk: K.FileCard(NOTES, size=12, subtitle="Failed", tone="danger")),
    ("card-actions", lambda C, Gtk: _card(C, action=True),
     lambda K, Gtk: K.FileCard(NOTES, size=12, actions=[K.BarAction("pause", tooltip="Pause")])),
    ("open-button", lambda C, Gtk: C.OpenButton.new(None, "text/plain", False),
     lambda K, Gtk: K.OpenButton(content_type="text/plain")),
    ("open-button-small", lambda C, Gtk: C.OpenButton.new(Gio.File.new_for_path(NOTES), None, True),
     lambda K, Gtk: K.OpenButton(NOTES, small=True)),
]
