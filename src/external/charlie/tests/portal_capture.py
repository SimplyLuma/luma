#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Capture one user-approved desktop screenshot through the standard portal."""
from __future__ import annotations

from pathlib import Path
import sys
import uuid

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib


def capture(destination: Path) -> Path:
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    token = "charlie_" + uuid.uuid4().hex
    sender = connection.get_unique_name().lstrip(":").replace(".", "_")
    request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
    loop = GLib.MainLoop()
    result: dict[str, object] = {}

    def response(_connection, _sender, _path, _interface, _signal, parameters):
        code, values = parameters.unpack()
        result["code"] = code
        result["uri"] = values.get("uri", "")
        loop.quit()

    connection.signal_subscribe(
        "org.freedesktop.portal.Desktop",
        "org.freedesktop.portal.Request",
        "Response",
        request_path,
        None,
        Gio.DBusSignalFlags.NONE,
        response,
    )
    connection.call_sync(
        "org.freedesktop.portal.Desktop",
        "/org/freedesktop/portal/desktop",
        "org.freedesktop.portal.Screenshot",
        "Screenshot",
        GLib.Variant("(sa{sv})", ("", {"handle_token": GLib.Variant("s", token), "interactive": GLib.Variant("b", False)})),
        GLib.VariantType("(o)"),
        Gio.DBusCallFlags.NONE,
        30_000,
        None,
    )
    timeout = GLib.timeout_add_seconds(30, lambda: (loop.quit(), GLib.SOURCE_REMOVE)[1])
    loop.run()
    if timeout:
        GLib.source_remove(timeout)
    if result.get("code") != 0 or not result.get("uri"):
        raise RuntimeError(f"Screenshot portal did not grant capture: {result.get('code', 'timeout')}")
    source = Gio.File.new_for_uri(str(result["uri"]))
    source.copy(Gio.File.new_for_path(str(destination)), Gio.FileCopyFlags.OVERWRITE, None, None)
    return destination


if __name__ == "__main__":
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "charlie.png").resolve()
    print(capture(output))
