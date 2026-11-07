# SPDX-License-Identifier: Apache-2.0
"""What the item page's bar grows into (8.8, 8.9): Install on… and the Uninstall confirmation.

No sheets, no dialogs: the bar grows upward into these, its buttons stay under them, and Escape, ×
or the same button folds them back.
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from luma_appkit import Button, Checkbox, IconOnlyButton, Switch, icons, lumaui

from ..model.world import Item, World
from ..pages.base import label

__all__ = ["install_on_panel", "install_word", "uninstall_panel"]


def install_word(count: int) -> str:
    """"Install on 1 device", "Install on 3 devices", or "Pick a device" when nothing is ticked."""
    return f"Install on {count} device{'s' if count != 1 else ''}" if count else "Pick a device"


def install_on_panel(world: World, item: Item, *, on_close: Callable[[], None],
                     on_confirm: Callable[[list[str], bool], None]) -> Gtk.Widget:
    panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    panel.add_css_class("dq-gwhere")
    head = Gtk.Box()
    head.add_css_class("dq-gh")
    head.append(label("Install on", "dq-gh-title"))
    head.get_first_child().set_hexpand(True)
    head.append(IconOnlyButton("x", "Close", size="small", on_click=on_close))
    panel.append(head)
    on = world.on_devices(item)
    picks: list[str] = []
    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    rows.add_css_class("dq-gdevs")
    confirm = Button(install_word(0), variant=Button.KEY, size=Button.LG, sensitive=False)
    confirm.add_css_class("dq-gok")

    def refresh() -> None:
        confirm.set_label_text(install_word(len(picks)))
        confirm.set_sensitive(bool(picks))

    for device in world.devices:
        has, can = device.id in on, device.form in item.runs
        row = Gtk.Button(sensitive=can and not has, accessible_role=Gtk.AccessibleRole.CHECKBOX)
        row.add_css_class("dq-gdev")
        lumaui.set_css_class(row, "has", has)
        line = Gtk.Box()
        box = Checkbox(checked=has)
        line.append(box)
        glyph = icons.image(device.icon)
        glyph.add_css_class("dq-gdev-glyph")
        line.append(glyph)
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        words.append(label(device.name, "dq-gdev-name"))
        note = (("Already on" if item.kind == "tweak" else "Installed") if has
                else (device.note or device.form_name) if can else f"No {device.form_name.lower()} version")
        words.append(label(note, "dq-gdev-note"))
        line.append(words)
        row.set_child(line)
        row.update_property([Gtk.AccessibleProperty.LABEL], [f"{device.name}, {note}"])

        def toggled(_button, d=device.id, b=box) -> None:
            if d in picks:
                picks.remove(d)
            else:
                picks.append(d)
            b.set_checked(d in picks)
            refresh()

        row.connect("clicked", toggled)
        rows.append(row)
    panel.append(rows)
    keep_state = {"on": item.id in world.matched}
    if item.kind == "app":
        keep = Gtk.Box()
        keep.add_css_class("dq-keep")
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        words.append(label("Keep matched", "dq-gdev-name"))
        words.append(label("Add it to every device that can run it, now and later", "dq-gdev-note", wrap=True))
        keep.append(words)
        keep.append(Switch(active=keep_state["on"], label="Keep matched",
                           on_toggle=lambda active: keep_state.update(on=active)))
        panel.append(keep)
    confirm.connect("clicked", lambda _b: on_confirm(list(picks), keep_state["on"]))
    panel.append(confirm)
    return panel


def uninstall_panel(item: Item, device_name: str, *, on_cancel: Callable[[], None],
                    on_confirm: Callable[[], None]) -> Gtk.Widget:
    """"Uninstall Steam from XPS 14?" — the same words for Tweaks and Looks, never "Turn off"."""
    panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.ALERT_DIALOG)
    panel.add_css_class("dq-gunin")
    title = f"Uninstall {item.name} from {device_name}?"
    body = "You can install it again from Depot any time."
    panel.append(label(title, "dq-gunin-title", wrap=True))
    panel.append(label(body, "dq-gunin-body", wrap=True))
    panel.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION], [title, body])
    buttons = Gtk.Box(homogeneous=True)
    buttons.add_css_class("dq-gunin-buttons")
    cancel = Button("Cancel", variant=Button.FILL, size=Button.LG, on_click=on_cancel)
    buttons.append(cancel)
    buttons.append(Button("Uninstall", variant=Button.DANGER, size=Button.LG, icon="trash-2", on_click=on_confirm))
    panel.append(buttons)
    return panel
