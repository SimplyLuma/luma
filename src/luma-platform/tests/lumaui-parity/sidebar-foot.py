# SPDX-License-Identifier: Apache-2.0
"""Parity: LumaSidebarFoot and LumaFilterHeading (C) and structure_sidebar.py (Python)."""

FILTERS = [("all", "All", "users"), ("fav", "Favourites", "star")]


def _noop(*_args):
    return None


def _placed(Gtk, foot):
    """The foot after a focusable control, so each window's first focus lands
    on that control in both trees (a focused search field is styled)."""
    box = Gtk.Box()
    box.append(Gtk.Button(label="first"))
    box.append(foot)
    return box


def c_foot(C, *, filters=False, add=False, current=None):
    foot = C.SidebarFoot.new("Search people")
    if add:
        foot.set_add("Add a contact", "user-plus", "win.add")
    if filters:
        for key, label, icon in FILTERS:
            foot.add_filter(key, label, icon)
    if current is not None:
        foot.set_filter(current)
    return foot


def py_foot(K, *, filters=False, add=False, current=None):
    return K.SidebarFoot(search="Search people", filters=FILTERS if filters else (), filter=current,
                         add=("Add a contact", "user-plus", _noop) if add else None)


VARIANTS = {
    "search": {},
    "filters": {"filters": True},
    "add": {"add": True},
    "both-filtered": {"filters": True, "add": True, "current": "fav"},
}

CASES = [
    (name, lambda C, Gtk, kw=kw: _placed(Gtk, c_foot(C, **kw)), lambda K, Gtk, kw=kw: _placed(Gtk, py_foot(K, **kw)))
    for name, kw in VARIANTS.items()
] + [
    ("heading", lambda C, Gtk: c_foot(C, filters=True, current="fav").get_heading(),
     lambda K, Gtk: py_foot(K, filters=True, current="fav").heading),
    ("heading-plain", lambda C, Gtk: C.FilterHeading.new("Favourites"),
     lambda K, Gtk: K.FilterHeading("Favourites")),
]
