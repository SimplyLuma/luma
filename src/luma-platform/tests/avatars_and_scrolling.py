#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Avatars read as people, and every kit list scrolls with the same overlay bar in the same place.

Messages showed "(", "7" and "2" as avatars, and its list kept a dead gutter
beside the scrollbar. The initials and glyph rules are the kit's, so Contacts,
Phone and Messages agree; the scrollbar geometry is the kit's, so every
sidebar and list agrees.
"""

from __future__ import annotations

import time

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from luma_appkit import (  # noqa: E402
    AVATAR_GLYPHS, Avatar, Island, NavigationRow, NavigationSidebar, ScrollView, avatar_initials, avatar_kind,
    avatar_tone, install_appkit)


def settle(seconds: float = 0.3) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.005)


INITIALS = {
    "Nick McMillan": "NM",
    "Nick": "N",
    "nick mcmillan": "NM",
    "Alexandra Montgomery-Richardson": "AM",
    "Dr. Priya Raman": "PR",
    "Martin Luther King Jr.": "MK",
    "(Mom) ❤️": "M",
    "❤️ Mom": "M",
    "Émile Zola-Durand": "ÉZ",
    "李小龙": "李",
    "김민수": "김",
    "山田 太郎": "山",
    "محمد علي": "مع",
    "राहुल शर्मा": "रश",
    "Сергей Иванов": "СИ",
    "straße": "S",
    "+1 (512) 555-0147": "",
    "72975": "",
    "🙂": "",
    "": "",
    "   ": "",
}
KINDS = {
    "Nick McMillan": "initials",
    "+1 (512) 555-0147": "person",
    "+15125550147": "person",
    "512-555-0147": "person",
    "72975": "business",
    "262966": "business",
    "🙂": "person",
    "": "person",
    "(": "person",
}

failures = []
for name, expected in INITIALS.items():
    if avatar_initials(name) != expected:
        failures.append(f"initials of {name!r}: {avatar_initials(name)!r}, expected {expected!r}")
for name, expected in KINDS.items():
    if avatar_kind(name) != expected:
        failures.append(f"kind of {name!r}: {avatar_kind(name)!r}, expected {expected!r}")
if avatar_kind("Weekend Crew", group=True) != "group":
    failures.append("a group is drawn as a group")
if avatar_tone("Nick McMillan") != avatar_tone("nick  mcmillan"):
    failures.append("the tone is stable across spacing and case")

app = Adw.Application(application_id="org.projectluma.AvatarScrollTest", flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
install_appkit()

# Each avatar shows exactly one of: initials, a glyph, a photo.
for name, group, kind in (("Nick McMillan", False, "initials"), ("72975", False, "business"),
                          ("+1 (512) 555-0147", False, "person"), ("Hive Cow Collective", True, "group")):
    avatar = Avatar(name, medium=True, group=group)
    child = avatar.get_first_child()
    if avatar.kind != kind or child.get_next_sibling() is not None:
        failures.append(f"{name}: kind {avatar.kind}")
    if kind == "initials" and not (isinstance(child, Gtk.Label) and child.get_label() == "NM"):
        failures.append(f"{name}: initials label {child}")
    if kind != "initials" and not (isinstance(child, Gtk.Image) and child.get_icon_name() in AVATAR_GLYPHS.values()):
        failures.append(f"{name}: glyph {child}")
    if not avatar.has_css_class("medium") or not avatar.has_css_class(f"kind-{kind}"):
        failures.append(f"{name}: classes {avatar.get_css_classes()}")

# The scrollbar floats over the list's trailing edge: no reserved width, the same inset everywhere.
window = Gtk.Window(default_width=260, default_height=320)
island = Island()
listbox = Gtk.ListBox()
for index in range(60):
    listbox.append(Gtk.Label(label=f"Row {index}", xalign=0))
scroll = ScrollView(listbox)
scroll.set_overlay_scrolling(False)  # an application cannot opt a kit list into a gutter
island.append(scroll)
window.set_child(island)
window.present()
settle(0.5)
scroll.get_vadjustment().set_value(40)  # scrolling shows the bar
settle(0.3)
bar = scroll.get_vscrollbar()
found, bar_bounds = bar.compute_bounds(island)
found_list, list_bounds = listbox.compute_bounds(island)
slider = bar.get_first_child().get_first_child().get_first_child()  # range > trough > slider
found_slider, slider_bounds = slider.compute_bounds(island)
if not scroll.get_overlay_scrolling():
    failures.append("ScrollView kept overlay scrolling")
if not (found and found_list and found_slider):
    failures.append("scroll geometry not measured")
else:
    if abs(list_bounds.get_width() - island.get_width()) > 0.5:
        failures.append(f"the list is {list_bounds.get_width()} wide in a {island.get_width()} island: a reserved gutter")
    inset = island.get_width() - (slider_bounds.get_x() + slider_bounds.get_width())
    if not 2.5 <= inset <= 3.5:
        failures.append(f"the scrollbar sits {inset}px from the island's edge, not 3px")
    if slider_bounds.get_width() > 8:
        failures.append(f"the resting scrollbar is {slider_bounds.get_width()}px wide")

# A navigation sidebar gets the same component.
sidebar = NavigationSidebar()
sidebar.append_row(NavigationRow("Inbox"))
scrolls = []
child = sidebar.get_first_child()
while child is not None:
    if isinstance(child, Gtk.ScrolledWindow):
        scrolls.append(child)
    child = child.get_next_sibling()
if not scrolls or not all(isinstance(item, ScrollView) for item in scrolls):
    failures.append("NavigationSidebar does not scroll with ScrollView")

# A context menu opens with its corner at the pointer, not centred on it.
from luma_appkit import Command, CommandGroup, CommandRegistry, command_popover  # noqa: E402
menu = command_popover(CommandRegistry((CommandGroup(None, (Command("test.open", "Open", lambda: None),)),)), variant="desktop")
menu.present_at_pointer(listbox, 30, 12)
settle(0.2)
ok, pointing = menu.get_pointing_to()
if menu.get_halign() != Gtk.Align.START or (pointing.x, pointing.y) != (30, 12) or menu.get_position() != Gtk.PositionType.BOTTOM:
    failures.append(f"the context menu is not anchored at its corner: {menu.get_halign()} {pointing.x},{pointing.y}")
menu.popdown()
settle(0.2)

window.close()
settle(0.1)
if failures:
    raise SystemExit("FAIL: " + "; ".join(failures))
print(f"ok: {len(INITIALS)} names, {len(KINDS)} kinds, avatars and the kit scrollbar")
