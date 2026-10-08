# SPDX-License-Identifier: Apache-2.0
"""Parity: LumaCard / ContentLitCard / PersonAvatar / AccountCard / ContentLitHeader vs content_cards."""
from gi.repository import Gdk


def _photo():
    return Gdk.Paintable.new_empty(64, 48)


def _then(widget, *calls):
    for name, *args in calls:
        getattr(widget, name)(*args)
    return widget


CASES = [
    ("card", lambda C, Gtk: C.Card.new(Gtk.Label(label="x"), True), lambda K, Gtk: K.Card(Gtk.Label(label="x"))),
    ("card-recessed", lambda C, Gtk: _then(C.Card.new(None, False), ("set_recessed", True)),
     lambda K, Gtk: K.Card(padded=False, recessed=True)),
    ("lit-card", lambda C, Gtk: C.ContentLitCard.new(Gtk.Label(label="x"), True),
     lambda K, Gtk: K.ContentLitCard(Gtk.Label(label="x"))),
    ("avatar", lambda C, Gtk: C.PersonAvatar.new("Priya Raman", 44), lambda K, Gtk: K.PersonAvatar("Priya Raman", 44)),
    ("avatar-small-hue", lambda C, Gtk: _then(C.PersonAvatar.new("Nick", 32), ("set_hue", 330)),
     lambda K, Gtk: K.PersonAvatar("Nick", 32, hue=330)),
    ("avatar-nameless", lambda C, Gtk: C.PersonAvatar.new("", 40), lambda K, Gtk: K.PersonAvatar("", 40)),
    ("avatar-picture", lambda C, Gtk: _then(C.PersonAvatar.new("Priya Raman", 44), ("set_picture", _photo())),
     lambda K, Gtk: K.PersonAvatar("Priya Raman", 44, picture=_photo())),
    ("account", lambda C, Gtk: C.AccountCard.new("Nick", None), lambda K, Gtk: K.AccountCard("Nick")),
    ("account-recessed", lambda C, Gtk: _then(C.AccountCard.new("Nick", "Signed in"), ("set_selected", True),
                                              ("set_recessed", True), ("set_hue", 40)),
     lambda K, Gtk: K.AccountCard("Nick", caption="Signed in", selected=True, recessed=True, hue=40)),
    ("lit-header", lambda C, Gtk: C.ContentLitHeader.new(), lambda K, Gtk: K.ContentLitHeader()),
    ("lit-header-person", lambda C, Gtk: _then(C.ContentLitHeader.new(), ("set_source", _photo(), None, "Priya Raman")),
     lambda K, Gtk: K.ContentLitHeader(picture=_photo(), name="Priya Raman")),
    ("lit-header-tone", lambda C, Gtk: _then(C.ContentLitHeader.new(), ("set_source", None, "media", None)),
     lambda K, Gtk: K.ContentLitHeader(tone="media")),
    ("lit-header-hue", lambda C, Gtk: _then(C.ContentLitHeader.new(), ("set_source", None, "media", None),
                                            ("set_hue", 200)),
     lambda K, Gtk: K.ContentLitHeader(tone="media", hue=200)),
]
