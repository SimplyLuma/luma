# SPDX-License-Identifier: MPL-2.0
"""`luma-energy`: see what the system is doing, and tell it to stop."""
from __future__ import annotations

import argparse
import json
import sys


def _proxy():
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    return Gio.DBusProxy.new_for_bus_sync(
        Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
        "org.projectluma.Energy1", "/org/projectluma/Energy1",
        "org.projectluma.Energy1", None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="luma-energy",
        description="What Luma is doing to save power, and how to stop it.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status", help="every application, its state and any limit")
    sub.add_parser("wake", help="wake everything Luma has paused, now")
    keep = sub.add_parser("keep-running", help="never limit this application")
    keep.add_argument("cgroup")
    keep.add_argument("--undo", action="store_true")
    off = sub.add_parser("off", help="turn Luma Energy off and undo everything")
    off.add_argument("--on", action="store_true", help="turn it back on")
    arguments = parser.parse_args(argv)

    try:
        proxy = _proxy()
    except Exception as error:  # noqa: BLE001 - any failure means the same thing
        print(f"Luma Energy is not running ({error}).", file=sys.stderr)
        return 1

    if arguments.command in (None, "status"):
        rows = proxy.ListApplications()
        if not rows:
            print("Nothing is being limited.")
            return 0
        for row in rows:
            limits = row.get("limits") or {}
            detail = ", ".join(f"{k} {v}" for k, v in sorted(limits.items())) or "no limit"
            if row.get("paused"):
                detail = "paused"
            reasons = row.get("reasons") or []
            why = f"  ({'; '.join(reasons)})" if reasons else ""
            print(f"{row['unit']:<52} {row['state']:<9} {detail}{why}")
        return 0

    if arguments.command == "wake":
        print(f"Woke {proxy.ThawAll()} application(s).")
        return 0

    if arguments.command == "keep-running":
        proxy.SetKeepRunning("(sb)", arguments.cgroup, not arguments.undo)
        print(json.dumps({"cgroup": arguments.cgroup, "keep": not arguments.undo}))
        return 0

    if arguments.command == "off":
        proxy.set_cached_property("Enabled", None)
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
        Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
            "org.projectluma.Energy1", "/org/projectluma/Energy1",
            "org.freedesktop.DBus.Properties", None).call_sync(
                "Set", GLib.Variant("(ssv)", ("org.projectluma.Energy1", "Enabled",
                                              GLib.Variant("b", bool(arguments.on)))),
                Gio.DBusCallFlags.NONE, 2000, None)
        print("Luma Energy is " + ("on." if arguments.on else "off; every limit is undone."))
        return 0

    parser.print_help()
    return 2
