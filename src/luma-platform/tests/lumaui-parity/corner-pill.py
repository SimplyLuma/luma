# SPDX-License-Identifier: Apache-2.0
"""Parity: LumaCornerPill (C) and structure_placement.CornerPill (Python)."""

from gi.repository import Gio

MODES = [("view", "View", "eye"), ("markup", "Mark up", "pen-line")]


def _noop(*_args):
    return None


def c_info_pill(C):
    pill = C.CornerPill.new()
    pill.add_share()
    pill.set_info_pane(C.DetailsPane.new(None, False))
    return pill


def c_pill(C, *, modes=False, open_in=False, share=False, actions=(), states=(), info=False, more=False,
           labelled=False, primary=None, editing=None):
    pill = C.CornerPill.new()
    # Added in a scrambled order: the pill places them.
    if more:
        menu = Gio.Menu()
        menu.append("Print", "win.print")
        pill.set_more_menu(menu)
    if info:
        pill.set_info_action("win.info")
    for icon, label in states:
        pill.add_state(icon, label, "win.state")
    for icon, label in actions:
        pill.add_action(icon, label, "win.action")
    if share:
        pill.add_share()
    if open_in:
        pill.add_open_in()
    if modes:
        switch = C.ModeSwitch.new("Mode")
        for key, label, icon in MODES:
            switch.add(key, label, icon)
        pill.set_modes(switch)
    pill.set_labelled(labelled)
    if primary is not None:
        pill.set_primary(primary)
    if editing is not None:
        pill.edit(editing)
    return pill


def py_pill(K, *, modes=False, open_in=False, share=False, actions=(), states=(), info=False, more=False,
            labelled=False, primary=None, editing=None):
    registry = None
    if more:
        registry = K.CommandRegistry()
    pill = K.CornerPill(
        modes=K.ModeSwitch(MODES) if modes else None,
        open_in=_noop if open_in else None,
        share=_noop if share else None,
        actions=[(icon, label, _noop) for icon, label in actions],
        states=[(icon, label, False, _noop) for icon, label in states],
        info=_noop if info else None,
        more=registry,
        labelled=labelled, primary=primary)
    if editing is not None:
        pill.edit(done=editing)
    return pill


FULL = dict(modes=True, open_in=True, share=True, actions=[("pencil", "Edit")], states=[("heart", "Favourite")],
            info=True, more=True)
CONTACTS = dict(share=True, actions=[("pencil", "Edit"), ("trash-2", "Delete")], labelled=True, primary="Edit",
                more=True)

CASES = [
    ("full", lambda C, Gtk: c_pill(C, **FULL), lambda K, Gtk: py_pill(K, **FULL)),
    ("share-only", lambda C, Gtk: c_pill(C, share=True), lambda K, Gtk: py_pill(K, share=True)),
    ("labelled-primary", lambda C, Gtk: c_pill(C, **CONTACTS), lambda K, Gtk: py_pill(K, **CONTACTS)),
    ("primary-unlabelled", lambda C, Gtk: c_pill(C, share=True, actions=[("pencil", "Edit")], primary="Edit"),
     lambda K, Gtk: py_pill(K, share=True, actions=[("pencil", "Edit")], primary="Edit")),
    ("editing", lambda C, Gtk: c_pill(C, editing="Save", **CONTACTS),
     lambda K, Gtk: py_pill(K, editing="Save", **CONTACTS)),
    ("info-pane", lambda C, Gtk: c_info_pill(C), lambda K, Gtk: K.CornerPill(share=_noop, info=K.DetailsPane())),
]
