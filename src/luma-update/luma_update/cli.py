# SPDX-License-Identifier: Apache-2.0
"""luma-update: the command line for Luma's system updates.

    luma-update status [--json]
    luma-update check
    luma-update download
    luma-update cancel
    luma-update apply
    luma-update channel <stable|beta|nightly> [--now]
    luma-update rollback
    luma-update enroll-preview <beta|nightly>
    luma-update leave-preview
    luma-update adopt <stable|beta|nightly>
    luma-update automatic-download <on|off>
    luma-update ignore [version]
    luma-update unignore

Every command talks to luma-updated over D-Bus, so the same polkit rules
apply as in Depot. ``status --json`` is the contract Depot reads; its schema
is in docs/os/luma-update.md.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

__all__ = ("main",)

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_UNAVAILABLE, EXIT_NOT_AUTHORIZED = 0, 1, 2, 3, 4


def _gio():
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib
    return Gio, GLib


class Client:
    def __init__(self) -> None:
        from .dbus_interface import BUS_NAME, INTERFACE, OBJECT_PATH
        Gio, GLib = _gio()
        self.Gio, self.GLib = Gio, GLib
        self.bus_name, self.interface, self.path = BUS_NAME, INTERFACE, OBJECT_PATH
        self.connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    def properties(self) -> dict:
        result = self.connection.call_sync(self.bus_name, self.path, "org.freedesktop.DBus.Properties", "GetAll",
                                           self.GLib.Variant("(s)", (self.interface,)),
                                           self.GLib.VariantType.new("(a{sv})"), self.Gio.DBusCallFlags.NONE,
                                           30000, None)
        return result.unpack()[0]

    def call(self, method: str, args=None, signature: str | None = None, timeout_ms: int = 10 * 60 * 1000) -> None:
        variant = self.GLib.Variant(signature, args) if signature else None
        self.connection.call_sync(self.bus_name, self.path, self.interface, method, variant, None,
                                  self.Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, timeout_ms, None)


def _status_dict(properties: dict) -> dict:
    from .status import JSON_SCHEMA_VERSION, snake_name
    data = {snake_name(key): value for key, value in properties.items()}
    data["schema_version"] = JSON_SCHEMA_VERSION
    return data


def _read_status(paths_root: str | None = None) -> tuple[dict | None, str]:
    """From the service, or the last published file when the bus is unavailable."""
    try:
        return _status_dict(Client().properties()), "dbus"
    except Exception as error:  # bus missing, service failed to start
        from .config import Paths
        paths = Paths.from_environment()
        try:
            data = json.loads(paths.status_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data, "file"
        except (OSError, ValueError):
            pass
        return None, str(error)


def _names(data: dict) -> dict:
    """What each version is called. The agent publishes the names; an agent too old
    to publish them is named here by the same rules (luma_update/names.py)."""
    from . import names
    from .config import Paths
    booted = None

    def booted_info() -> dict:
        nonlocal booted
        if booted is None:
            booted = names.read_os_release(Paths.from_environment().os_release)
        return booted

    result = {}
    for key in ("booted", "staged", "available", "waiting", "rolled_back", "ignored"):
        name = names.clean(data.get(f"{key}_name"))
        version = str(data.get(f"{key}_version") or "")
        if not name and version:
            name = (names.booted_name(booted_info(), version) if key == "booted"
                    else names.display_name(version, booted=booted_info()))
        result[key] = name
    return result


def _human(data: dict) -> str:
    lines = []
    state = data.get("state", "")
    name = _names(data)
    lines.append((name["booted"] or "Luma (unknown version)")
                 + (f" on the {data['channel']} channel" if data.get("channel") else ""))
    if not data.get("managed", True) and state != "error":
        lines.append("This computer doesn't follow a Luma update channel.")
        lines.append({
            "other-origin": "It can start following one: `luma-update adopt stable`.",
            "no-remote": "Luma's update remote is not configured here, so it cannot start following one.",
            "no-key": "Luma's update-signing key is not installed here, so it cannot start following one.",
            "no-image-system": "This is not an image-based system, so Luma cannot update it in place.",
            "busy": "Another change is already waiting for a restart; finish it first.",
        }.get(data.get("unmanaged_reason", ""), "Luma cannot start following one here."))
    descriptions = {
        "idle": "Luma is up to date.",
        "checking": "Checking for updates…",
        "available": f"{name['available'] or 'A Luma update'} is available"
                     + (" (metered connection: run `luma-update download`)." if data.get("metered") else "."),
        "downloading": f"Downloading {name['available'] or 'the Luma update'}… {round(100 * float(data.get('progress') or 0))}%",
        "staged": f"{name['staged'] or 'The Luma update'} is ready. Restart to finish updating (`luma-update apply`).",
        "restart-required": "Restart to finish the change (`luma-update apply`).",
        "error": data.get("last_error") or "The last update attempt failed.",
        "barrier-blocked": f"{name['waiting'] or 'A Luma update'} must be installed before anything newer, "
                           "and it did not finish last time. Luma will try it again later.",
    }
    lines.append(descriptions.get(state, state))
    if data.get("importance") == "security" and state in ("available", "downloading", "staged"):
        lines.append("Important security update.")
    if data.get("available_summary") and state in ("available", "downloading", "staged"):
        lines.append(data["available_summary"])
    if data.get("notes_url") and state in ("available", "downloading", "staged"):
        lines.append(f"Release notes: {data['notes_url']}")
    if data.get("waiting_version") and state == "idle":
        lines.append(f"{name['waiting']} is rolling out and will reach this computer soon.")
    if data.get("booted_deadend_reason"):
        lines.append(f"This version was withdrawn: {data['booted_deadend_reason']}")
    if data.get("rolled_back_version"):
        lines.append(f"Luma couldn't finish updating to {name['rolled_back']} and went back to the previous version.")
    if data.get("removed_packages") and state in ("staged", "restart-required", "idle"):
        lines.append("Now part of Luma, so your added copy goes with the update: "
                     + ", ".join(data["removed_packages"]))
    if data.get("kept_packages"):
        lines.append("Waiting: your added copy is newer than the one in this update, so it was kept: "
                     + ", ".join(data["kept_packages"]))
        lines.append("Luma updates once a release carries that version, or after you remove your copy "
                     "(rpm-ostree uninstall NAME).")
    if data.get("last_error") and state != "error":
        lines.append(f"Last problem: {data['last_error']}")
    if data.get("ignored_version"):
        lines.append(f"Ignored: {name['ignored']} (`luma-update unignore` to hear about it again)")
    if data.get("last_check"):
        lines.append("Last checked " + time.strftime("%Y-%m-%d %H:%M", time.localtime(int(data["last_check"]))))
    if data.get("repository_url"):
        lines.append(f"Updates come from {data['repository_url']}")
    if data.get("signature_verified"):
        lines.append("Update metadata verified against key " + (data.get("signing_key_id") or "unknown"))
    lines.append("Automatic download: " + ("on" if data.get("automatic_download", True) else "off")
                 + ". Nothing is ever installed or restarted without you asking.")
    if data.get("preview_enrolled"):
        lines.append("Early updates: on")
    return "\n".join(lines)


def _wait(client: Client, busy_states: tuple[str, ...], timeout: float, show_progress: bool) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    time.sleep(0.3)
    while True:
        data = _status_dict(client.properties())
        state = data.get("state")
        if show_progress and state == "downloading":
            percent = round(100 * float(data.get("progress") or 0))
            if percent != last and sys.stderr.isatty():
                print(f"\rDownloading… {percent}%", end="", file=sys.stderr, flush=True)
            last = percent
        if state not in busy_states or time.monotonic() > deadline:
            if last is not None and sys.stderr.isatty():
                print(file=sys.stderr)
            return data
        time.sleep(0.5)


def _connect_token() -> str | None:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    path = Path(base) / "luma" / "connect" / "device.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    token = value.get("token") if isinstance(value, dict) else None
    return token if isinstance(token, str) and token else None


def _dbus_error(error) -> tuple[int, str]:
    Gio, _ = _gio()
    remote = Gio.DBusError.get_remote_error(error) or ""
    # Gio.DBusError.strip_remote_error changes only PyGObject's copy of the error.
    message = error.message or ""
    if message.startswith("GDBus.Error:"):
        message = message.partition(": ")[2]
    if remote.endswith(".NotAuthorized") or remote == "org.freedesktop.DBus.Error.AccessDenied":
        return EXIT_NOT_AUTHORIZED, f"not authorized: {message}"
    if remote.endswith(".NothingToDo"):
        return EXIT_OK, message
    if remote == "org.freedesktop.DBus.Error.UnknownMethod":
        return EXIT_UNAVAILABLE, ("the running update service is older than this command; restart it "
                                  "(systemctl restart luma-updated) or update Luma first")
    if remote in ("org.freedesktop.DBus.Error.ServiceUnknown", "org.freedesktop.DBus.Error.NameHasNoOwner"):
        return EXIT_UNAVAILABLE, "the Luma update service is not available"
    return EXIT_FAILED, message


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="luma-update", description="Luma system updates", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command")
    status = sub.add_parser("status", help="show update status")
    status.add_argument("--json", action="store_true", help="machine-readable status (schema 1)")
    sub.add_parser("check", help="check for an update now")
    sub.add_parser("download", help="download and prepare the update now, even on a metered connection")
    sub.add_parser("cancel", help="stop the download that is running")
    sub.add_parser("apply", help="restart to finish updating")
    channel = sub.add_parser("channel", help="choose the update channel: stable (Official), beta or nightly")
    channel.add_argument("name", choices=("stable", "beta", "nightly"))
    channel.add_argument("--now", action="store_true",
                         help="switch immediately, even to an older version")
    sub.add_parser("rollback", help="go back to the previous version at the next restart")
    enroll = sub.add_parser("enroll-preview", help=argparse.SUPPRESS)  # compatibility: channels are public
    enroll.add_argument("name", choices=("beta", "nightly"))
    sub.add_parser("leave-preview", help="stop getting early updates")
    adopt = sub.add_parser("adopt", help="start following a Luma channel on a computer that follows none")
    adopt.add_argument("name", choices=("stable", "beta", "nightly"))
    download_setting = sub.add_parser("automatic-download",
                                      help="download updates in the background, or wait to be asked")
    download_setting.add_argument("value", choices=("on", "off"))
    ignore = sub.add_parser("ignore", help="stop reminding about one version")
    ignore.add_argument("version", nargs="?", default="",
                        help="the version to ignore; the one offered now by default")
    sub.add_parser("unignore", help="stop ignoring that version")
    sub.add_parser("automatic", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.command in (None, "status"):
        data, source = _read_status()
        if data is None:
            print(f"luma-update: the update service is not available ({source})", file=sys.stderr)
            return EXIT_UNAVAILABLE
        if getattr(args, "json", False):
            print(json.dumps(data, indent=2, sort_keys=True))
        else:
            print(_human(data))
        return EXIT_OK

    Gio, GLib = _gio()
    try:
        client = Client()
        if args.command == "check":
            client.call("Check")
            data = _wait(client, ("checking", "downloading"), 3600, True)
            print(_human(data))
            return EXIT_FAILED if data.get("state") == "error" else EXIT_OK
        if args.command == "download":
            client.call("Download")
            data = _wait(client, ("checking", "downloading"), 6 * 3600, True)
            print(_human(data))
            return EXIT_FAILED if data.get("state") == "error" else EXIT_OK
        if args.command == "cancel":
            client.call("Cancel")
            print("The download was stopped. Nothing was installed.")
            return EXIT_OK
        if args.command == "apply":
            client.call("Apply")
            print("Restarting to finish updating…")
            return EXIT_OK
        if args.command == "channel":
            client.call("SetChannelNow" if args.now else "SetChannel", (args.name,), "(s)")
            print(_human(_status_dict(client.properties())))
            return EXIT_OK
        if args.command == "rollback":
            client.call("Rollback")
            print("The previous version will start at the next restart (`luma-update apply`).")
            return EXIT_OK
        if args.command == "enroll-preview":
            token = _connect_token()
            if token is None:
                print("luma-update: this account has no Luma Connect enrollment on this computer. "
                      "Sign in to Luma Connect first, then run this again as yourself (not with sudo).",
                      file=sys.stderr)
                return EXIT_FAILED
            client.call("EnrollPreview", (args.name, token), "(ss)")
            print(f"Early updates are on. This computer now follows {args.name}.")
            return EXIT_OK
        if args.command == "leave-preview":
            client.call("LeavePreview")
            print("Early updates are off. This computer follows stable and will move to it when stable catches up.")
            return EXIT_OK
        if args.command == "adopt":
            client.call("AdoptChannel", (args.name,), "(s)")
            data = _wait(client, ("checking", "downloading"), 6 * 3600, True)
            print(_human(data))
            print("Restart to finish. The version you are running now is kept, so you can go back to it.")
            return EXIT_FAILED if data.get("state") == "error" else EXIT_OK
        if args.command == "automatic-download":
            client.call("SetAutomaticDownload", (args.value == "on",), "(b)")
            print("Updates download in the background; nothing is installed until you ask."
                  if args.value == "on" else
                  "Updates are not downloaded until you ask for them.")
            return EXIT_OK
        if args.command == "ignore":
            data = _status_dict(client.properties())
            version = args.version or data.get("staged_version") or data.get("available_version") or ""
            if not version:
                print("luma-update: there is no update to ignore.", file=sys.stderr)
                return EXIT_FAILED
            client.call("IgnoreVersion", (version,), "(s)")
            ignored = _names(_status_dict(client.properties())).get("ignored") or _names(
                {"available_version": version})["available"]
            print(f"{ignored} will not be mentioned again. Depot still offers it.")
            return EXIT_OK
        if args.command == "unignore":
            client.call("ClearIgnoredVersion")
            print("No version is ignored any more.")
            return EXIT_OK
        if args.command == "automatic":
            client.call("Automatic", timeout_ms=6 * 3600 * 1000)
            _wait(client, ("checking", "downloading"), 6 * 3600, False)
            return EXIT_OK
    except GLib.Error as error:
        code, message = _dbus_error(error)
        print(f"luma-update: {message}", file=sys.stderr if code else sys.stdout)
        return code
    parser.print_help()
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
