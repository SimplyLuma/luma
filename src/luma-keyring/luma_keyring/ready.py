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


def decide(argv: list[str]) -> bool:
    """Whether this service run starts with the first-unlock sheet."""
    if "--gapplication-service" not in argv:
        return False
    from .first_unlock import should_ask
    from .secrets import Keyring

    try:
        # The session waits for this: a keyring daemon that does not answer
        # is "nothing to ask" after a few seconds, not a stalled login.
        ask = should_ask(Keyring(timeout_ms=DECIDE_TIMEOUT_MS))
    except Exception as error:  # noqa: BLE001 - never hold the session up over this
        log.info("could not tell whether to ask: %s", error)
        ask = False
    if ask and not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        log.info("no display to ask on; the notification will ask instead")
        ask = False
    if not ask:
        notify_ready()
    return ask
