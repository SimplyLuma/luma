# SPDX-License-Identifier: Apache-2.0
"""Parity: ListFirst (structure_listfirst.py): a list and its page."""


def _c(C, Gtk):
    return C.ListFirst.new(Gtk.ListBox(), Gtk.Label(label="page"), "Settings")


def _k(K, Gtk):
    return K.ListFirst(Gtk.ListBox(), Gtk.Label(label="page"), title="Settings")


CASES = [("settings", _c, _k)]
