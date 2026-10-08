#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

"""Deterministic freedesktop notification scenarios for shell acceptance."""

import argparse
import json

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

SCENARIOS = {
    "messages": (
        "Messages",
        "Nora Feld",
        "The light is finally right. Ten minutes?",
        ["default", "Open", "reply", "Reply", "mark-read", "Mark read"],
        "org.projectluma.Messages",
        "im.received",
    ),
    "calendar": (
        "Calendar",
        "Design review",
        "In 42 minutes · Studio North",
        ["default", "Open", "join", "Join", "snooze", "Snooze"],
        "org.projectluma.Calendar",
        "x-gnome.calendar",
    ),
}


def publish(proxy, scenario, replaces_id=0, value=None):
    app, title, body, actions, desktop_entry, category = SCENARIOS[scenario]
    hints = {
        "desktop-entry": GLib.Variant("s", desktop_entry),
        "category": GLib.Variant("s", category),
        "transient": GLib.Variant("b", False),
        "resident": GLib.Variant("b", False),
    }
    if value is not None:
        hints["value"] = GLib.Variant("i", value)
        body = f"Exporting contact sheet · {value}%"
    result = proxy.call_sync(
        "Notify",
        GLib.Variant(
            "(susssasa{sv}i)",
            (
                f"Luma {app}",
                replaces_id,
                desktop_entry,
                title,
                body,
                actions,
                hints,
                -1,
            ),
        ),
        Gio.DBusCallFlags.NONE,
        5000,
        None,
    )
    return int(result.unpack()[0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=[*SCENARIOS, "approved", "progress"])
    parser.add_argument(
        "--listen",
        type=float,
        default=0,
        metavar="SECONDS",
        help="print ActionInvoked and NotificationClosed events for this run",
    )
    parser.add_argument(
        "--withdraw-after",
        type=float,
        metavar="SECONDS",
        help="ask the server to close the last published notification",
    )
    args = parser.parse_args()
    proxy = Gio.DBusProxy.new_for_bus_sync(
        Gio.BusType.SESSION,
        Gio.DBusProxyFlags.NONE,
        None,
        "org.freedesktop.Notifications",
        "/org/freedesktop/Notifications",
        "org.freedesktop.Notifications",
        None,
    )
    notification_ids = []
    if args.scenario == "approved":
        notification_ids.append(publish(proxy, "calendar"))
        notification_ids.append(publish(proxy, "messages"))
    elif args.scenario == "progress":
        SCENARIOS["progress"] = (
            "Photos",
            "Contact sheet",
            "Starting…",
            ["cancel", "Cancel"],
            "org.projectluma.Photos",
            "transfer",
        )
        notification_id = publish(proxy, "progress", value=43)
        notification_ids.append(
            publish(proxy, "progress", notification_id, 44)
        )
    else:
        notification_ids.append(publish(proxy, args.scenario))

    print(json.dumps({"published": notification_ids}, sort_keys=True))

    if not args.listen and args.withdraw_after is None:
        return

    loop = GLib.MainLoop()

    def on_signal(_proxy, _sender, signal_name, parameters):
        if signal_name not in {"ActionInvoked", "NotificationClosed"}:
            return
        values = parameters.unpack()
        if int(values[0]) not in notification_ids:
            return
        print(json.dumps({"event": signal_name, "values": values}, sort_keys=True))

    proxy.connect("g-signal", on_signal)
    if args.withdraw_after is not None:
        def withdraw():
            proxy.call(
                "CloseNotification",
                GLib.Variant("(u)", (notification_ids[-1],)),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
                None,
            )
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(max(1, int(args.withdraw_after * 1000)), withdraw)
    listen_seconds = max(args.listen, (args.withdraw_after or 0) + 1)
    GLib.timeout_add(max(1, int(listen_seconds * 1000)), loop.quit)
    loop.run()


if __name__ == "__main__":
    main()
