# SPDX-License-Identifier: Apache-2.0
"""Parity: LumaPlaceSearch vs content_place.PlaceSearch (the field; the list floats in the host)."""

CASES = [
    ("field", lambda C, Gtk: C.PlaceSearch.new(None), lambda K, Gtk: K.PlaceSearch(lambda q: [], on_pick=print)),
    ("field-placeholder", lambda C, Gtk: C.PlaceSearch.new("Find a place"),
     lambda K, Gtk: K.PlaceSearch(lambda q: [], on_pick=print, placeholder="Find a place")),
]
