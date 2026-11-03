# SPDX-License-Identifier: MPL-2.0
"""One keyring question at a time: Luma's sheet stands in for the prompter.

gnome-keyring asks for a password through the session's system prompter
(``org.gnome.keyring.SystemPrompter``: GNOME Shell, or gcr-prompter). An app
that wants a secret while the login keyring is locked starts such a prompt,
and gnome-keyring decides that it must ask *before* it waits for the
prompter: a prompt queued behind ours would still be shown after Luma's
sheet unlocked the keyring, asking for a password that, once the keyring is
on its device key, no longer opens it.

So while Luma's own "Unlock your saved passwords" sheet is up, this process
takes the prompter's bus name (the shell lets it be replaced and queues for
it again) and declines every prompt: gnome-keyring then reports the request
as dismissed at once, which apps treat as "locked", never "signed out". When
the sheet closes the name is released and the shell has it back; if this
process dies the bus releases it the same way. Nothing here sees a password.

Where the name cannot be taken (a prompter that does not allow replacement,
such as gcr-prompter while it is showing something), the gate says so and
the caller decides; it never waits on its own.
"""

from __future__ import annotations

import logging

from gi.repository import Gio, GLib

__all__ = ("PromptGate", "PROMPTER_NAME")

log = logging.getLogger("luma-keyring")

PROMPTER_NAME = "org.gnome.keyring.SystemPrompter"
PROMPTER_PATH = "/org/gnome/keyring/Prompter"
PROMPTER_IFACE = "org.gnome.keyring.internal.Prompter"
# gcr's own error for a prompter that will not show a prompt.
DECLINED = "org.gnome.keyring.Prompter.Failed"

# org.freedesktop.DBus.RequestName flags and replies.
_REPLACE_EXISTING = 0x2
_DO_NOT_QUEUE = 0x4
_PRIMARY_OWNER = 1
_ALREADY_OWNER = 4

_INTROSPECTION = Gio.DBusNodeInfo.new_for_xml(f"""
<node>
  <interface name="{PROMPTER_IFACE}">
    <method name="BeginPrompting"><arg name="callback" type="o" direction="in"/></method>
    <method name="PerformPrompt">
      <arg name="callback" type="o" direction="in"/>
      <arg name="type" type="s" direction="in"/>
      <arg name="properties" type="a{{sv}}" direction="in"/>
      <arg name="exchange" type="s" direction="in"/>
    </method>
    <method name="StopPrompting"><arg name="callback" type="o" direction="in"/></method>
  </interface>
</node>""")


class PromptGate:
    """Holds the system prompter's name while Luma asks, declining the rest."""

    def __init__(self, bus: Gio.DBusConnection | None = None) -> None:
        self._bus = bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._registration = 0
        self.declined = 0

    @property
    def held(self) -> bool:
        return self._registration != 0

    def take(self) -> bool:
        """Become the prompter now, or say why not (False). Idempotent."""
        if self.held:
            return True
        try:
            self._registration = self._bus.register_object(
                PROMPTER_PATH, _INTROSPECTION.interfaces[0], self._on_call, None, None)
        except GLib.Error as error:
            log.info("the prompter's object path is taken here: %s", error.message)
            return False
        try:
            reply = self._bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "RequestName", GLib.Variant("(su)", (PROMPTER_NAME, _REPLACE_EXISTING | _DO_NOT_QUEUE)),
                GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
        except GLib.Error as error:
            log.info("could not ask for the prompter's name: %s", error.message)
            reply = 0
        if reply in (_PRIMARY_OWNER, _ALREADY_OWNER):
            log.info("keyring prompts are held while Luma asks")
            return True
        self._unregister()
        log.info("the system prompter cannot be replaced right now (reply %s)", reply)
        return False

    def release(self) -> None:
        """Give the name back (the shell, queued for it, gets it again)."""
        if not self.held:
            return
        try:
            self._bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "ReleaseName", GLib.Variant("(s)", (PROMPTER_NAME,)),
                GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error as error:
            log.info("could not release the prompter's name: %s", error.message)
        self._unregister()
        if self.declined:
            log.info("declined %d keyring prompt(s) while Luma asked", self.declined)

    def _unregister(self) -> None:
        if self._registration:
            self._bus.unregister_object(self._registration)
            self._registration = 0

    def _on_call(self, _bus, sender, _path, _iface, method, _params, invocation) -> None:
        if method == "StopPrompting":
            invocation.return_value(None)
            return
        if method == "BeginPrompting":
            self.declined += 1
            log.info("declined a keyring prompt from %s while Luma asks", sender)
        invocation.return_dbus_error(DECLINED, "Luma is asking for this password already")
