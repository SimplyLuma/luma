# SPDX-License-Identifier: MPL-2.0
"""Luma's sheet as the session's keyring prompter, until the first unlock is done.

gnome-keyring asks for passwords through the system prompter
(``org.gnome.keyring.SystemPrompter``, gcr's prompter protocol): GNOME Shell
in a normal session. On an automatic sign-in whose keyring is not enrolled
yet, luma-keyring takes that name at the very start of the session (its unit
runs before graphical-session-pre.target, before the Shell exists), so every
keyring prompt in the session's first moments, an app's included, comes here
and is answered by Luma's sheet: one question, Luma's design. The Shell asks
for the name when it starts and is queued behind this process; it gets the
name back when it is released, and if this process dies.

The password goes back only through gcr's secret exchange (Diffie-Hellman
and AES, Gcr.SecretExchange), never in the clear on the bus, never logged or
kept beyond the answer.

This module speaks the protocol only; what to show is the caller's (see
`Request` and `LumaPrompter(on_request=...)`).
"""

from __future__ import annotations

import logging
from typing import Callable

import gi

gi.require_version("Gcr", "4")
from gi.repository import Gcr, Gio, GLib  # noqa: E402

__all__ = ("LumaPrompter", "Request", "PROMPTER_NAME")

log = logging.getLogger("luma-keyring")

PROMPTER_NAME = "org.gnome.keyring.SystemPrompter"
PROMPTER_PATH = "/org/gnome/keyring/Prompter"
PROMPTER_IFACE = "org.gnome.keyring.internal.Prompter"
CALLBACK_IFACE = "org.gnome.keyring.internal.Prompter.Callback"

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


class Request:
    """One client's prompt: its properties so far, and the way to answer it."""

    def __init__(self, prompter: "LumaPrompter", sender: str, path: str) -> None:
        self._prompter = prompter
        self.sender, self.path = sender, path
        self.exchange = Gcr.SecretExchange.new(None)
        self.received = False
        self.properties: dict[str, object] = {}
        self.kind = ""            # "password" or "confirm" while a prompt waits for its answer
        self.waiting = False      # PerformPrompt arrived, not answered yet
        self.closed = False
        self.watch = 0
        self.answers = 0          # how many answers this prompt has had (a retry comes back with a warning)

    @property
    def warning(self) -> str:
        return str(self.properties.get("warning") or "")

    def answer(self, password: str | None, choice: bool | None = None) -> None:
        """Answer a password prompt (None cancels), through the secret exchange."""
        changed = {}
        if choice is not None and self.properties.get("choice-label"):
            changed["choice-chosen"] = GLib.Variant("b", bool(choice))
        self._prompter._ready(self, "yes" if password is not None else "no", changed, password)

    def confirm(self, yes: bool) -> None:
        self._prompter._ready(self, "yes" if yes else "no", {}, None)


class LumaPrompter:
    """gcr's prompter protocol (one prompt at a time), answered by the caller."""

    def __init__(self, on_request: Callable[[Request], None] | None = None,
                 on_closed: Callable[[Request], None] | None = None,
                 bus: Gio.DBusConnection | None = None) -> None:
        self._bus = bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.on_request = on_request
        self.on_closed = on_closed
        self._registration = 0
        self._queue: list[Request] = []
        self.active: Request | None = None

    # -- the name ----------------------------------------------------------
    @property
    def held(self) -> bool:
        return self._registration != 0

    def take(self, replace: bool = False) -> bool:
        """Own the prompter's name: first in the session, or by replacing the Shell."""
        if self.held:
            return True
        try:
            self._registration = self._bus.register_object(
                PROMPTER_PATH, _INTROSPECTION.interfaces[0], self._on_call, None, None)
        except GLib.Error as error:
            log.info("the prompter's object path is taken here: %s", error.message)
            return False
        flags = _DO_NOT_QUEUE | (_REPLACE_EXISTING if replace else 0)
        try:
            reply = self._bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "RequestName",
                GLib.Variant("(su)", (PROMPTER_NAME, flags)), GLib.VariantType.new("(u)"),
                Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
        except GLib.Error as error:
            log.info("could not ask for the prompter's name: %s", error.message)
            reply = 0
        if reply in (_PRIMARY_OWNER, _ALREADY_OWNER):
            log.info("Luma answers keyring prompts until the first unlock is done")
            return True
        self._bus.unregister_object(self._registration)
        self._registration = 0
        log.info("the system prompter's name is held by another (reply %s)", reply)
        return False

    def release(self) -> None:
        """Give the name back; the Shell, queued for it, has it again."""
        for request in list(self._queue) + ([self.active] if self.active else []):
            if request.waiting:
                # Never left hanging: a prompt nobody will answer now is cancelled.
                self._ready(request, "no", {}, None)
        if self._registration:
            try:
                self._bus.call_sync(
                    "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ReleaseName",
                    GLib.Variant("(s)", (PROMPTER_NAME,)), GLib.VariantType.new("(u)"),
                    Gio.DBusCallFlags.NONE, 5000, None)
            except GLib.Error as error:
                log.info("could not release the prompter's name: %s", error.message)
            self._bus.unregister_object(self._registration)
            self._registration = 0

    def pending(self) -> list[Request]:
        return ([self.active] if self.active else []) + list(self._queue)

    # -- the protocol --------------------------------------------------------
    def _on_call(self, _bus, sender, _path, _iface, method, params, invocation) -> None:
        if method == "BeginPrompting":
            (path,) = params.unpack()
            if any(r.sender == sender and r.path == path for r in self.pending()):
                invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed",
                                             "Already begun prompting for this prompt callback")
                return
            request = Request(self, sender, path)
            request.watch = Gio.bus_watch_name_on_connection(
                self._bus, sender, Gio.BusNameWatcherFlags.NONE, None,
                lambda *_a, r=request: self._stop(r, done=False))
            self._queue.append(request)
            invocation.return_value(None)
            self._next()
            return
        path = params.unpack()[0]
        request = self.active if self.active and (self.active.sender, self.active.path) == (sender, path) else None
        if method == "StopPrompting":
            invocation.return_value(None)
            target = request or next((r for r in self._queue if (r.sender, r.path) == (sender, path)), None)
            if target is not None:
                self._stop(target, done=True)
            return
        # PerformPrompt
        _path, kind, properties, exchange = params.unpack()
        if request is None or request.waiting:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed",
                                         "Not ready for a prompt on this callback")
            return
        if not request.exchange.receive(exchange):
            invocation.return_dbus_error("org.freedesktop.DBus.Error.InvalidArgs",
                                         "Invalid secret exchange received")
            return
        if kind not in ("password", "confirm"):
            invocation.return_dbus_error("org.freedesktop.DBus.Error.InvalidArgs", "Invalid type argument")
            return
        request.received = True
        request.properties.update(properties)
        request.kind, request.waiting = kind, True
        invocation.return_value(None)
        if self.on_request is not None:
            self.on_request(request)

    def _next(self) -> None:
        if self.active is not None or not self._queue:
            return
        self.active = self._queue.pop(0)
        self._ready(self.active, "", {}, None)

    def _ready(self, request: Request, reply: str, changed: dict, secret: str | None) -> None:
        if request.closed:
            return
        log.info("keyring prompt %s: %s", request.path, reply or "ready")
        if request.received:
            sent = request.exchange.send(secret, -1) if secret is not None else request.exchange.send(None, 0)
            request.answers += 1
        else:
            sent = request.exchange.begin()
        request.waiting = False
        self._bus.call(request.sender, request.path, CALLBACK_IFACE, "PromptReady",
                       GLib.Variant("(sa{sv}s)", (reply, changed, sent)), None,
                       Gio.DBusCallFlags.NO_AUTO_START, -1, None, None, None)

    def _stop(self, request: Request, done: bool) -> None:
        if request.closed:
            return
        log.info("keyring prompt %s %s", "stopped by its client" if done else "gone with its client", request.path)
        request.closed = True
        if request.watch:
            Gio.bus_unwatch_name(request.watch)
        if request in self._queue:
            self._queue.remove(request)
        if done:
            self._bus.call(request.sender, request.path, CALLBACK_IFACE, "PromptDone", None, None,
                           Gio.DBusCallFlags.NO_AUTO_START, -1, None, None, None)
        if self.active is request:
            self.active = None
        if self.on_closed is not None:
            self.on_closed(request)
        self._next()
