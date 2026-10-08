# SPDX-License-Identifier: Apache-2.0
"""Parity: LumaContactActions and the mini cards vs content_contact."""
from datetime import datetime

from gi.repository import Gdk, GLib

PRIYA = dict(phone="+1 555 0101", email="priya@example.org", username="priya")


def _c_person(C, name, phone="", email="", username="", hue=None):
    person = C.Person.new(name)
    person.set_phone(phone)
    person.set_email(email)
    person.set_username(username)
    if hue is not None:
        person.set_hue(hue)
    return person


def _start():
    return GLib.DateTime.new_local(2026, 9, 25, 14, 30, 0)


def _hidden_add(card):
    card.set_add_label(None)
    return card


CASES = [
    ("actions", lambda C, Gtk: C.ContactActions.new(_c_person(C, "Priya Raman", **PRIYA), False),
     lambda K, Gtk: K.ContactActions(K.Person("Priya Raman", **PRIYA))),
    ("actions-small", lambda C, Gtk: C.ContactActions.new(_c_person(C, "Sam Kim"), True),
     lambda K, Gtk: K.ContactActions(K.Person("Sam Kim"), small=True)),
    ("contact-on-luma", lambda C, Gtk: C.ContactCard.new(_c_person(C, "Priya Raman", hue=330, **PRIYA)),
     lambda K, Gtk: K.ContactCard(K.Person("Priya Raman", hue=330, **PRIYA))),
    ("contact-phone", lambda C, Gtk: C.ContactCard.new(_c_person(C, "Tom Hale", phone="+1 555 0199")),
     lambda K, Gtk: K.ContactCard(K.Person("Tom Hale", phone="+1 555 0199"))),
    ("contact-bare", lambda C, Gtk: C.ContactCard.new(_c_person(C, "Nora")), lambda K, Gtk: K.ContactCard(K.Person("Nora"))),
    ("event", lambda C, Gtk: C.EventCard.new("Launch rehearsal", _start(), False, "Studio", None),
     lambda K, Gtk: K.EventCard("Launch rehearsal", datetime(2026, 9, 25, 14, 30), where="Studio",
                                on_add=lambda: None)),
    ("event-all-day", lambda C, Gtk: C.EventCard.new("Offsite", _start(), True, None, "media"),
     lambda K, Gtk: K.EventCard("Offsite", datetime(2026, 9, 25), all_day=True, tone="media", on_add=lambda: None)),
    ("song", lambda C, Gtk: C.SongCard.new("God Only Knows", "The Beach Boys", "Pet Sounds", None),
     lambda K, Gtk: K.SongCard("God Only Knows", "The Beach Boys", "Pet Sounds")),
    ("song-artwork", lambda C, Gtk: C.SongCard.new("God Only Knows", "The Beach Boys", None, Gdk.Paintable.new_empty(8, 8)),
     lambda K, Gtk: K.SongCard("God Only Knows", "The Beach Boys", artwork=Gdk.Paintable.new_empty(8, 8))),
    ("place", lambda C, Gtk: C.PlaceCard.new("Duende", "468 19th St", "9 min", "utensils"),
     lambda K, Gtk: K.PlaceCard("Duende", "468 19th St", eta="9 min", icon="utensils", on_directions=lambda: None)),
]
