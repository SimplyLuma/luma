# SPDX-License-Identifier: Apache-2.0
"""Parity: LumaNavigationTrailBar (C) and structure_trail.NavigationTrailBar (Python)."""


def _noop(*_args):
    return None


def c_bar(C, *, title=True, back=True, meta=()):
    bar = C.NavigationTrailBar.new(title)
    bar.set_place("Albums" if back else None, "Midnight Drive")
    for item in meta:
        if isinstance(item, tuple):
            bar.add_meta(item[0], "win.go")
        else:
            bar.add_meta(item, None)
    return bar


def py_bar(K, *, title=True, back=True, meta=()):
    trail = K.NavigationTrail(K.Place("albums", "Albums"))
    if back:
        trail.open(K.Place("album", "Midnight Drive"))
    else:
        trail = K.NavigationTrail(K.Place("album", "Midnight Drive"))
    return K.NavigationTrailBar(trail, title=title,
                                meta=[(m[0], _noop) if isinstance(m, tuple) else m for m in meta])


META = ["12 songs", ("Nova", None), "48 min"]

CASES = [
    ("page", lambda C, Gtk: c_bar(C, meta=META), lambda K, Gtk: py_bar(K, meta=META)),
    ("page-no-meta", lambda C, Gtk: c_bar(C), lambda K, Gtk: py_bar(K)),
    ("root", lambda C, Gtk: c_bar(C, back=False, meta=["214 songs"]),
     lambda K, Gtk: py_bar(K, back=False, meta=["214 songs"])),
    ("pane", lambda C, Gtk: c_bar(C, title=False, meta=["3 sources", "1 off"]),
     lambda K, Gtk: py_bar(K, title=False, meta=["3 sources", "1 off"])),
]
