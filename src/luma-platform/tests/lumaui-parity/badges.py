# SPDX-License-Identifier: Apache-2.0
"""Parity: LumaCountBadge / LumaCategoryPill / LumaStatusPill vs content_badges / content_contact."""


def _set_kind(pill, kind, label=None):
    pill.set_kind(kind, label)
    return pill


CASES = [
    ("count-total", lambda C, Gtk: C.CountBadge.new(1284, False), lambda K, Gtk: K.CountBadge(1284)),
    ("count-attention", lambda C, Gtk: C.CountBadge.new(214, True), lambda K, Gtk: K.CountBadge(214, attention=True)),
    ("count-zero", lambda C, Gtk: C.CountBadge.new(0, True), lambda K, Gtk: K.CountBadge(0, attention=True)),
    ("category-media", lambda C, Gtk: C.CategoryPill.new("media"), lambda K, Gtk: K.CategoryPill("media")),
    ("category-unknown", lambda C, Gtk: C.CategoryPill.new("Home Automation"),
     lambda K, Gtk: K.CategoryPill("Home Automation")),
    ("status-on-luma", lambda C, Gtk: C.StatusPill.new("on-luma", None), lambda K, Gtk: K.StatusPill("on-luma")),
    ("status-offline", lambda C, Gtk: C.StatusPill.new("offline", None), lambda K, Gtk: K.StatusPill("offline")),
    ("status-syncing", lambda C, Gtk: C.StatusPill.new("syncing", "Syncing 214 songs"),
     lambda K, Gtk: K.StatusPill("syncing", "Syncing 214 songs")),
    ("status-changed", lambda C, Gtk: _set_kind(C.StatusPill.new("online", None), "error"),
     lambda K, Gtk: _set_kind(K.StatusPill("online"), "error")),
]
