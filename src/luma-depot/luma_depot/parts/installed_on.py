# SPDX-License-Identifier: Apache-2.0
"""Installed on (8.5): one row per device on the person's Luma account.

Installed (green glyph) reveals a bin on hover; Not yet reveals Install (Fill, 28 px); a device that
can't run it is dimmed ("No phone version"). On touch both buttons always show. Parts of Luma never
show the bin.
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from luma_appkit import Button, IconOnlyButton, Inset, Tooltip, icons

from ..model.world import Item, World
from ..pages.base import label

__all__ = ["installed_on"]


def installed_on(world: World, item: Item, *, on_install: Callable[[str], None],
                 on_uninstall: Callable[[str], None], touch: bool = False) -> Gtk.Widget:
    card = Inset(padding="card", spacing=2)  # rows 2 apart (.dq-iside .dq-mydevs)
    card.add_css_class("dq-mydevs")
    card.append(label("Installed on", "dq-aside-heading"))
    on = world.on_devices(item)
    for device in world.devices:
        has, can = device.id in on, device.form in item.runs
        row = Gtk.Box()
        row.add_css_class("dq-md")
        row.add_css_class("m-has" if has else "m-can" if can else "m-no")
        if touch:
            row.add_css_class("touch")
        glyph = icons.image(device.icon)
        glyph.add_css_class("dq-md-glyph")
        row.append(glyph)
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        words.append(label(device.name, "dq-md-name"))
        state = "Installed" if has else "Not yet" if can else f"No {device.form_name.lower()} version"
        words.append(label(state, "dq-md-state"))
        row.append(words)
        if has and not item.is_system:
            bin_ = IconOnlyButton("trash-2", f"Uninstall on {device.name}", size="row",
                                  on_click=lambda d=device.id: on_uninstall(d))
            bin_.add_css_class("dq-md-act")
            Tooltip.attach(bin_, "Uninstall", label=False)
            row.append(bin_)
        elif not has and can:
            add = Button("Install", variant=Button.FILL, size=Button.SM,
                         icon="power" if item.kind == "tweak" else "download",
                         on_click=lambda d=device.id: on_install(d))
            add.add_css_class("dq-md-act")
            add.update_property([Gtk.AccessibleProperty.LABEL], [f"Install on {device.name}"])
            row.append(add)
        card.append(row)
    return card
