# SPDX-License-Identifier: Apache-2.0
"""Parity: LumaModeSwitch (C) and structure_placement.ModeSwitch (Python)."""

MODES = [("view", "View", "eye"), ("markup", "Mark up", "pen-line"), ("adjust", "Adjust", "sliders-horizontal")]


def c_modes(C, current=None):
    modes = C.ModeSwitch.new("Mode")
    for key, label, icon in MODES:
        modes.add(key, label, icon)
    if current is not None:
        modes.set_current(current)
    return modes


def py_modes(K, current=None):
    modes = K.ModeSwitch(MODES)
    if current is not None:
        modes.set_current(current)
    return modes


CASES = [
    ("three", lambda C, Gtk: c_modes(C), lambda K, Gtk: py_modes(K)),
    ("chosen", lambda C, Gtk: c_modes(C, "adjust"), lambda K, Gtk: py_modes(K, "adjust")),
]


def status_modes(module, status, python=False):
    modes = py_modes(module) if python else c_modes(module)
    modes.set_status("view", status)
    return modes


CASES += [
    ("running", lambda C, Gtk: status_modes(C, "running"), lambda K, Gtk: status_modes(K, "running", True)),
    ("time-left", lambda C, Gtk: status_modes(C, "4:12"), lambda K, Gtk: status_modes(K, "4:12", True)),
    ("cleared", lambda C, Gtk: status_modes(C, None), lambda K, Gtk: status_modes(K, None, True)),
]
