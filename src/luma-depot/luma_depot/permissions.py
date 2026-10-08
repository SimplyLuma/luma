# SPDX-License-Identifier: Apache-2.0

"""What an application can reach, said the same way everywhere.

Depot and the installer both have to answer "what will this be able to do", and
if they answer differently the answer stops meaning anything. This is that
presentation, written once.

**This belongs in the Application Kit, not here.** It is in Depot only because
the kit and this branch have not met yet: `src/luma-platform/appkit/` does not
exist on stage. When they do, move `PermissionList` into `luma_appkit` unchanged
and have the installer import it from there.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from .providers import Permission


_ICONS = {
    "files": "folder-symbolic",
    "network": "network-wireless-symbolic",
    "audio": "audio-speakers-symbolic",
    "graphics": "video-display-symbolic",
    "printing": "printer-symbolic",
    "screen": "video-display-symbolic",
    "secrets": "dialog-password-symbolic",
    "system": "system-run-symbolic",
    # ADR-028, section 7.
    "files.portal": "document-open-symbolic",
    "files.home": "user-home-symbolic",
    "files.host": "drive-harddisk-symbolic",
    "devices.camera": "camera-web-symbolic",
    "devices.microphone": "audio-input-microphone-symbolic",
    "devices.all": "drive-removable-media-symbolic",
    "background": "preferences-system-time-symbolic",
    "notifications": "preferences-system-notifications-symbolic",
    "location": "find-location-symbolic",
    "system.bus": "system-run-symbolic",
    "session.bus": "view-app-grid-symbolic",
    "display.x11": "video-display-symbolic",
    "sandbox.escape": "utilities-terminal-symbolic",
}

_CHANGES = {"added": "New", "widened": "Wider", "narrowed": "Narrower", "removed": "Removed"}


def icon_for(key: str) -> str:
    return _ICONS.get(key) or _ICONS.get(key.split(".", 1)[0], "dialog-information-symbolic")


class PermissionList(Gtk.Box):
    """The "what it can reach" block, as one bordered list.

    High and sensitive grants are marked; a grant an update adds or widens
    carries a tag saying so, and one it removes is drawn struck through, so
    the change is visible before the person agrees to it.
    """

    def __init__(self, permissions: tuple[Permission, ...]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("dp-access")
        for index, permission in enumerate(permissions):
            if index:
                rule = Gtk.Box()
                rule.add_css_class("dp-acc-rule")
                self.append(rule)
            self.append(self._row(permission))

    def _row(self, permission: Permission) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("dp-acc")
        if permission.change == "removed":
            row.add_css_class("removed")
        mark = Gtk.Box()
        mark.add_css_class("dp-accmark")
        if permission.notable or permission.level in ("sensitive", "high"):
            # Not a warning — a thing worth noticing. The design marks these
            # amber rather than red, because reaching the network is normal.
            mark.add_css_class("notable")
        if permission.level == "high":
            mark.add_css_class("high")
        mark.set_valign(Gtk.Align.CENTER)
        icon = Gtk.Image(icon_name=icon_for(permission.key), pixel_size=13)
        icon.set_hexpand(True)
        icon.set_vexpand(True)
        mark.append(icon)
        mark.set_hexpand(False)
        mark.set_vexpand(False)
        row.append(mark)
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
        title_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label(label=permission.title, xalign=0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.add_css_class("dp-acc-title")
        title_line.append(title)
        if permission.change in _CHANGES:
            tag = Gtk.Label(label=_CHANGES[permission.change])
            tag.add_css_class("dp-change")
            tag.add_css_class(permission.change)
            tag.set_valign(Gtk.Align.CENTER)
            title_line.append(tag)
        detail = Gtk.Label(label=permission.detail, xalign=0, wrap=True)
        detail.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        detail.add_css_class("dp-acc-detail")
        copy.append(title_line)
        if permission.detail:
            copy.append(detail)
        row.append(copy)
        spoken = f"{permission.title}. {permission.detail}"
        if permission.change in _CHANGES:
            spoken = f"{_CHANGES[permission.change]} in this update: {spoken}"
        row.update_property([Gtk.AccessibleProperty.LABEL], [spoken])
        return row
