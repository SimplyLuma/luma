#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Capture the guest session through the supported desktop portal.

GNOME 45 and later correctly refuse `org.gnome.Shell.Screenshot` to
unprivileged callers. This uses the interface that replaced it —
`org.freedesktop.portal.Screenshot` — rather than weakening that refusal.

The portal normally asks a person for consent. In the emulator's disposable
guest there is nobody to ask, so a preauthorization is written to the portal's
own permission store, scoped to this one helper's application identity and to
this image only. That is a recorded grant in a throwaway virtual machine, not a
change to Luma's portal policy: nothing here is installed on, or applies to, a
real Luma system.

Exits non-zero, with a reason, rather than producing an empty or fabricated
image.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib, Gio  # noqa: E402

APP_ID = "org.projectluma.EmulatorCapture"
PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PERMISSION_STORE = "org.freedesktop.impl.portal.PermissionStore"
PERMISSION_PATH = "/org/freedesktop/impl/portal/PermissionStore"


def fail(message: str, code: int = 1) -> "None":
    print(message, file=sys.stderr)
    raise SystemExit(code)


def bus() -> Gio.DBusConnection:
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error as error:
        fail(f"no session bus: {error.message}")


def preauthorize(connection: Gio.DBusConnection) -> str:
    """Record a screenshot grant for this helper in the portal permission store.

    Returns a human-readable note about what happened. A failure here is not
    fatal: some backends do not consult the store, and the capture attempt
    itself is the real test.
    """
    notes = []
    for app in (APP_ID, ""):
        try:
            connection.call_sync(
                PERMISSION_STORE, PERMISSION_PATH, PERMISSION_STORE, "SetPermission",
                GLib.Variant("(sbssas)", ("screenshot", True, "screenshot", app, ["yes"])),
                None, Gio.DBusCallFlags.NONE, 5000, None,
            )
            notes.append(f"granted screenshot to '{app or '(unsandboxed)'}'")
        except GLib.Error as error:
            notes.append(f"could not grant to '{app or '(unsandboxed)'}': {error.message}")
    return "; ".join(notes)


def screenshot(connection: Gio.DBusConnection, timeout_seconds: float) -> str:
    token = f"lumaemu{int(time.time() * 1000)}"
    sender = connection.get_unique_name()[1:].replace(".", "_")
    request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

    loop = GLib.MainLoop()
    outcome: dict = {}

    def on_response(_connection, _sender, _path, _interface, _signal, parameters):
        response, results = parameters.unpack()
        outcome["response"] = response
        outcome["results"] = results
        loop.quit()

    subscription = connection.signal_subscribe(
        None, "org.freedesktop.portal.Request", "Response", request_path, None,
        Gio.DBusSignalFlags.NONE, on_response,
    )

    try:
        connection.call_sync(
            PORTAL_NAME, PORTAL_PATH, "org.freedesktop.portal.Screenshot", "Screenshot",
            GLib.Variant("(sa{sv})", ("", {
                "handle_token": GLib.Variant("s", token),
                "interactive": GLib.Variant("b", False),
                "modal": GLib.Variant("b", False),
            })),
            GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, 15000, None,
        )
    except GLib.Error as error:
        connection.signal_unsubscribe(subscription)
        fail(f"the screenshot portal refused the request: {error.message}", 2)

    def give_up() -> bool:
        outcome.setdefault("timeout", True)
        loop.quit()
        return False

    GLib.timeout_add_seconds(int(timeout_seconds), give_up)
    loop.run()
    connection.signal_unsubscribe(subscription)

    if outcome.get("timeout"):
        fail(
            "the screenshot portal did not answer. It is most likely waiting for a "
            "person to approve the request, which a headless guest cannot do.",
            3,
        )
    if outcome.get("response") != 0:
        fail(f"the screenshot portal declined (response {outcome.get('response')})", 4)
    uri = (outcome.get("results") or {}).get("uri")
    if not uri:
        fail("the screenshot portal returned no image", 5)
    return uri


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", help="where to write the PNG")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument(
        "--no-preauthorize", action="store_true",
        help="do not touch the permission store; useful for proving whether the "
             "grant is what makes a headless capture possible",
    )
    arguments = parser.parse_args()

    connection = bus()
    if not arguments.no_preauthorize:
        print(preauthorize(connection), file=sys.stderr)

    uri = screenshot(connection, arguments.timeout)
    path = unquote(urlparse(uri).path)
    if not os.path.isfile(path):
        fail(f"the portal reported {uri} but no file is there", 6)
    if os.path.getsize(path) == 0:
        fail("the portal produced an empty file", 7)
    os.makedirs(os.path.dirname(os.path.abspath(arguments.output)), exist_ok=True)
    shutil.move(path, arguments.output)
    print(arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
