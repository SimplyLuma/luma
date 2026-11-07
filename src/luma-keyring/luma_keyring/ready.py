# SPDX-License-Identifier: MPL-2.0
"""What the session service decides before GTK is loaded, and telling systemd.

luma-keyring.service is ordered before graphical-session.target, so the
session waits for it: it says READY=1 at once when there is nothing to ask,
once the sheet is up when there is, and never later than
READY_BOUND_SECONDS (a GLib timer in the service).
"""

from __future__ import annotations

import logging
import os
import socket

__all__ = ("READY_BOUND_SECONDS", "notify_ready", "decide")

log = logging.getLogger("luma-keyring")

READY_BOUND_SECONDS = 8
DECIDE_TIMEOUT_MS = 3000
# How long, at the session's start, the keyring daemon gets to come onto the bus.
DAEMON_WAIT_SECONDS = 8
# How long the service waits, before the Shell, for the display the sheet needs.
DISPLAY_WAIT_SECONDS = 120

_ready_sent = False


def notify_ready() -> bool:
    """sd_notify(READY=1), once; nothing outside a Type=notify unit.

    Returns False so it can be a GLib timeout callback.
    """
    global _ready_sent
    if _ready_sent:
        return False
    _ready_sent = True
    address = os.environ.get("NOTIFY_SOCKET", "")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as sock:
            sock.connect(address)
            sock.sendall(b"READY=1")
    except OSError as error:
        log.info("could not tell systemd the service is ready: %s", error)
    return False


def decide(argv: list[str]):
    """Whether this service run starts with the first-unlock sheet, and its prompter.

    Runs before graphical-session-pre.target, before the Shell: when Luma will
    ask, it takes the keyring prompter's name now, so no prompt in the session
    reaches anything else first. Then it tells systemd it is ready and waits
    for the display the sheet needs. Returns (ask, prompter or None).
    """
    if "--gapplication-service" not in argv:
        return False, None
    from .autounlock import is_autologin
    from .first_unlock import is_automatic, should_ask
    from .secrets import Keyring

    # The name first, before anything that can take time: an automatic sign-in
    # with no device seal is the case Luma asks in, and that much is two file
    # reads. An app whose prompt came before this would find no prompter.
    prompter = None
    if is_autologin() and not is_automatic():
        try:
            from .prompter import LumaPrompter

            prompter = LumaPrompter()
            if not prompter.take(replace=True):
                prompter = None
        except Exception as error:  # noqa: BLE001 - the sheet still asks, the Shell may too
            log.info("could not become the keyring prompter: %s", error)
            prompter = None
    try:
        # The session waits for this: a keyring daemon that does not answer
        # is "nothing to ask" after a few seconds, not a stalled login.
        keyring = Keyring(timeout_ms=DECIDE_TIMEOUT_MS)
        if prompter is not None:
            # This early the keyring daemon may not be on the bus yet: start it and give it a
            # moment, rather than read "no daemon" as "nothing to ask" and let the name go.
            _wait_for_keyring(keyring, DAEMON_WAIT_SECONDS)
        ask = should_ask(keyring)
    except Exception as error:  # noqa: BLE001 - never hold the session up over this
        log.info("could not tell whether to ask: %s", error)
        ask = False
    if not ask and prompter is not None:
        log.info("nothing to ask; keyring prompts go to the Shell")
        prompter.release()
        prompter = None
    notify_ready()
    if not wait_for_display():
        log.info("no display came; nothing to ask on")
        if prompter is not None:
            prompter.release()
        return False, None
    return ask, prompter


def _wait_for_keyring(keyring, seconds: float) -> bool:
    """Until the daemon is on the bus and has the login keyring (its file exists)."""
    import time

    from .state import keyring_path

    def ready() -> bool:
        return keyring.running() and (keyring.collection() is not None or not keyring_path().exists())

    if ready():
        return True
    if not keyring.running():
        keyring.activate()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if ready():
            return True
        time.sleep(0.1)
    log.info("the keyring daemon was not ready in %d s", seconds)
    return ready()


def wait_for_display(seconds: float = DISPLAY_WAIT_SECONDS) -> bool:
    """Until the session's display exists (the user manager's environment says so).

    Started before the Shell, this process has no WAYLAND_DISPLAY of its own;
    the Shell exports it to the user manager when it is up. The main loop
    turns meanwhile, so prompts that arrive already are held, not lost.
    """
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"):
        return True
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    loop = GLib.MainLoop()
    state = {"ok": False, "left": int(seconds * 4)}

    def look() -> bool:
        try:
            env = bus.call_sync("org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                                "org.freedesktop.DBus.Properties", "Get",
                                GLib.Variant("(ss)", ("org.freedesktop.systemd1.Manager", "Environment")),
                                GLib.VariantType.new("(v)"), Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
        except GLib.Error:
            env = []
        found = dict(item.split("=", 1) for item in env if "=" in item)
        if found.get("WAYLAND_DISPLAY") or found.get("DISPLAY"):
            for name in ("WAYLAND_DISPLAY", "DISPLAY", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE"):
                if found.get(name):
                    os.environ[name] = found[name]
            state["ok"] = True
            loop.quit()
            return False
        state["left"] -= 1
        if state["left"] <= 0:
            loop.quit()
            return False
        return True

    if look():
        GLib.timeout_add(250, look)
        loop.run()
    return state["ok"]
