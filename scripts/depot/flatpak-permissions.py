#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Compute Depot's plain-language permissions from a Flatpak app's metadata.

ADR-028 section 7 is the contract: permissions shown in Depot and on the web
are never typed in by a developer, they are derived from the sandbox the app
actually gets. This tool reads the `metadata` keyfile a Flatpak build exports
(the `[Context]`, `[Session Bus Policy]` and `[System Bus Policy]` groups) and
emits the catalog's `permissions` array. Given the previous release's metadata
it also emits `permission_changes`, and exits 3 when the set grew, which the
publishing pipeline treats as "hold for human review".

  flatpak-permissions.py METADATA [--previous METADATA]
  flatpak-permissions.py --repo REPO --ref app/ID/ARCH/BRANCH [--previous-ref REF]

Entry shape (catalog schema 4):

  {"key": "files.documents", "level": "sensitive", "access": "read"}
  {"key": "session.bus", "level": "sensitive", "names": ["org.freedesktop.secrets"]}

`access` appears only on file keys (read or read-write); filesystem `names`
retain each exact scope and its :ro/:rw mode. Bus names retain their identities. Levels are standard < sensitive < high.

Runtime-requested portal permissions (camera, location, background, screen
capture) are granted by the person when the app asks and are not part of the
static metadata. Depot shows those from the portal permission store, not here.
"""

from __future__ import annotations

import argparse
import configparser
import json
import subprocess
import sys
from pathlib import Path

# Use the installed client's scope comparator so publication cannot silently
# disagree about subfolders or read/write changes within a grouped permission.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-installer'))
from luma_installer.depot_permissions import names_widen

LEVELS = {"standard": 0, "sensitive": 1, "high": 2}
EXIT_PERMISSIONS_GREW = 3

XDG_FOLDERS = {
    "xdg-documents": "files.documents",
    "xdg-pictures": "files.pictures",
    "xdg-music": "files.music",
    "xdg-videos": "files.videos",
    "xdg-download": "files.downloads",
}

# Keys and their levels. The ADR table first, then the additions recorded in
# ADR-028 section 7 for grants the table did not name.
VOCABULARY = {
    "network": "standard",
    "files.portal": "standard",
    "files.documents": "sensitive",
    "files.pictures": "sensitive",
    "files.music": "sensitive",
    "files.videos": "sensitive",
    "files.downloads": "sensitive",
    "files.other": "sensitive",
    "files.removable": "sensitive",
    "files.home": "high",
    "files.host": "high",
    "devices.camera": "sensitive",
    "devices.microphone": "sensitive",
    "devices.all": "high",
    "devices.input": "sensitive",
    "devices.usb": "sensitive",
    "devices.bluetooth": "sensitive",
    "devices.smartcard": "sensitive",
    "devices.kvm": "high",
    "background": "sensitive",
    "notifications": "standard",
    "location": "sensitive",
    "printing": "standard",
    "system.bus": "high",
    "system.ssh-agent": "high",
    "system.gpg-agent": "high",
    "session.bus": "sensitive",
    "display.x11": "high",
    "sandbox.escape": "high",
    "sandbox.devel": "high",
}

REMOVABLE_ROOTS = ("/media", "/run/media", "/mnt")


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(";") if item.strip()]


def read_metadata_text(text: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(delimiters=("=",), interpolation=None,
                                       strict=False, comment_prefixes=("#",))
    parser.optionxform = str  # keyfile keys and D-Bus names are case-sensitive
    parser.read_string(text)
    return parser


class Collector:
    def __init__(self) -> None:
        self.entries: dict[str, dict] = {}

    def add(self, key: str, *, level: str | None = None, access: str | None = None,
            name: str | None = None) -> None:
        if key not in VOCABULARY:
            raise KeyError(f"not in the permission vocabulary: {key}")
        level = level or VOCABULARY[key]
        entry = self.entries.setdefault(key, {"key": key, "level": level})
        if LEVELS[level] > LEVELS[entry["level"]]:
            entry["level"] = level
        if access is not None:
            if entry.get("access") != "read-write":
                entry["access"] = access
        if name is not None:
            names = entry.setdefault("names", [])
            if name not in names:
                names.append(name)
                names.sort()

    def result(self) -> list[dict]:
        order = list(VOCABULARY)
        return sorted(self.entries.values(), key=lambda e: order.index(e["key"]))


def _filesystem(collector: Collector, raw: str) -> None:
    if raw.startswith("!"):
        return  # a removal, never a grant
    path, _, mode = raw.partition(":")
    access = "read" if mode == "ro" else "read-write"
    scope = path + (":ro" if mode == "ro" else ":rw")
    if path in ("host", "host-os", "host-etc", "host-root", "/"):
        collector.add("files.host", access=access, name=scope)
    elif path in ("home", "~", "~/"):
        collector.add("files.home", access=access, name=scope)
    elif path.split("/", 1)[0] in XDG_FOLDERS:
        collector.add(XDG_FOLDERS[path.split("/", 1)[0]], access=access, name=scope)
    elif path == "xdg-run/pipewire-0":
        collector.add("devices.microphone")
    elif path.startswith(REMOVABLE_ROOTS):
        collector.add("files.removable", access=access, name=scope)
    else:
        collector.add("files.other", access=access, name=scope)


def _session_name(collector: Collector, name: str, policy: str, app_id: str) -> None:
    if policy in ("none", "see"):
        return
    if name == "org.freedesktop.Flatpak" and policy in ("talk", "own"):
        collector.add("sandbox.escape", name=name)
        return
    if name.startswith("org.freedesktop.portal."):
        return  # portals are reachable by every app and mediate their own consent
    if policy == "own" and (name == app_id or name.startswith(app_id + ".")
                            or name.startswith("org.mpris.MediaPlayer2.")):
        return  # an app may always own its own id and its media player name
    if name == "org.freedesktop.Notifications" and policy == "talk":
        collector.add("notifications")
        return
    collector.add("session.bus", name=name)


def _system_name(collector: Collector, name: str, policy: str) -> None:
    if policy in ("none", "see"):
        return
    if name == "org.freedesktop.GeoClue2":
        collector.add("location")
        return
    collector.add("system.bus", name=name)


def compute(metadata: configparser.ConfigParser) -> list[dict]:
    collector = Collector()
    app_id = ""
    for group in ("Application", "Runtime"):
        if metadata.has_section(group):
            app_id = metadata.get(group, "name", fallback="")
            break

    context = metadata["Context"] if metadata.has_section("Context") else {}
    shared = _split(context.get("shared"))
    sockets = _split(context.get("sockets"))
    devices = _split(context.get("devices"))
    features = _split(context.get("features"))
    filesystems = _split(context.get("filesystems"))

    if "network" in shared:
        collector.add("network")

    if "x11" in sockets and "fallback-x11" not in sockets:
        collector.add("display.x11")
    if "pulseaudio" in sockets:
        collector.add("devices.microphone")
    if "session-bus" in sockets:
        collector.add("session.bus", level="high", name="*")
    if "system-bus" in sockets:
        collector.add("system.bus", name="*")
    if "ssh-auth" in sockets:
        collector.add("system.ssh-agent")
    if "gpg-agent" in sockets:
        collector.add("system.gpg-agent")
    if "pcsc" in sockets:
        collector.add("devices.smartcard")
    if "cups" in sockets:
        collector.add("printing")

    if "all" in devices:
        collector.add("devices.all")
        collector.add("devices.camera")
    if "input" in devices:
        collector.add("devices.input")
    if "usb" in devices:
        collector.add("devices.usb")
    if "kvm" in devices:
        collector.add("devices.kvm")

    if "devel" in features:
        collector.add("sandbox.devel")
    if "bluetooth" in features:
        collector.add("devices.bluetooth")

    for raw in filesystems:
        _filesystem(collector, raw)

    if metadata.has_section("Session Bus Policy"):
        for name, policy in metadata.items("Session Bus Policy"):
            _session_name(collector, name, policy.strip(), app_id)
    if metadata.has_section("System Bus Policy"):
        for name, policy in metadata.items("System Bus Policy"):
            _system_name(collector, name, policy.strip())

    if "files.host" not in collector.entries and "files.home" not in collector.entries:
        collector.add("files.portal")
    return collector.result()


def changes(previous: list[dict], current: list[dict]) -> list[dict]:
    """Differences a person should see, most important first."""
    before = {entry["key"]: entry for entry in previous}
    after = {entry["key"]: entry for entry in current}
    result = []
    for key, entry in after.items():
        old = before.get(key)
        if old is None:
            result.append({"key": key, "change": "added", "level": entry["level"]})
            continue
        widened = (
            LEVELS[entry["level"]] > LEVELS[old["level"]]
            or (old.get("access") == "read" and entry.get("access") == "read-write")
            or names_widen(key, old.get("names", []), entry.get("names", []))
        )
        if widened:
            item = {"key": key, "change": "widened", "level": entry["level"]}
            new_names = sorted(set(entry.get("names", [])) - set(old.get("names", [])))
            if new_names:
                item["names"] = new_names
            if old.get("access") != entry.get("access"):
                item["access"] = entry.get("access")
            result.append(item)
    for key, entry in before.items():
        if key not in after:
            result.append({"key": key, "change": "removed", "level": entry["level"]})
    order = {"added": 0, "widened": 1, "removed": 2}
    result.sort(key=lambda c: (order[c["change"]], -LEVELS[c["level"]], c["key"]))
    return result


def grew(change_list: list[dict]) -> bool:
    return any(c["change"] in ("added", "widened") and c["key"] != "files.portal"
               for c in change_list)


def _metadata_from_repo(repo: str, ref: str) -> str:
    return subprocess.run(["ostree", "cat", f"--repo={repo}", ref, "/metadata"],
                          check=True, capture_output=True, text=True).stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("metadata", nargs="?", type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--repo")
    parser.add_argument("--ref")
    parser.add_argument("--previous-ref")
    args = parser.parse_args(argv)

    if args.repo and args.ref:
        current_text = _metadata_from_repo(args.repo, args.ref)
    elif args.metadata:
        current_text = args.metadata.read_text()
    else:
        parser.error("give a metadata file, or --repo and --ref")

    previous_text = None
    if args.previous:
        previous_text = args.previous.read_text()
    elif args.repo and args.previous_ref:
        previous_text = _metadata_from_repo(args.repo, args.previous_ref)

    current = compute(read_metadata_text(current_text))
    output: dict = {"permissions": current}
    status = 0
    if previous_text is not None:
        change_list = changes(compute(read_metadata_text(previous_text)), current)
        output["permission_changes"] = change_list
        if grew(change_list):
            status = EXIT_PERMISSIONS_GREW
    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return status


if __name__ == "__main__":
    sys.exit(main())
