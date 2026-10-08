# SPDX-License-Identifier: Apache-2.0

"""What a person controls about Luma Connect on this device.

Every switch says, before it is flipped, exactly what happens — and does only
that. The rule behind all of them: turning something off never deletes
anything from this device or from another device. At most it takes back from
Luma Cloud what this device alone put there.

This module is the one place those words and those effects live, so the Luma
Connect app, the command line and tests all read the same promise.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    on: str
    off: str
    default: bool = True


SERVICES = (
    Service("calendar", "Calendar",
            "Your Luma calendar stays in step with your other devices and Luma Cloud.",
            "Your Luma events are copied into the Personal calendar on this device, then the Luma "
            "calendar is disconnected here. Nothing is deleted from Luma Cloud or your other devices."),
    Service("notes", "Notes",
            "Notes on this device are kept in Luma Cloud.",
            "Notes from this device are removed from Luma Cloud. They stay on this device, and your "
            "other devices keep theirs."),
    Service("contacts", "Contacts",
            "Your Luma contacts stay in step with your other devices and Luma Cloud.",
            "Your Luma contacts are copied into Personal on this device, then the Luma address book "
            "is disconnected here. Nothing is deleted from Luma Cloud or your other devices."),
    Service("photos", "Photos",
            "Photos from this device go to Luma Cloud, and photos from your other devices arrive in "
            "Pictures › Luma Hub.",
            "This device stops sending and receiving photos. Photos only this device had are hidden "
            "in Luma Cloud and kept for recovery. Nothing is deleted here or on your other devices."),
    Service("world-clocks", "Clocks",
            "Cities you add in Clock appear on your other devices.",
            "This device stops sharing clocks. Its cities stay as they are, here and elsewhere."),
    Service("weather-places", "Weather",
            "Places you add in Weather appear on your other devices.",
            "This device stops sharing places. Its places stay as they are, here and elsewhere."),
    # Off until a person turns them on: what people wrote and who they called
    # is sealed at rest on the Hub for now, and end-to-end encryption is a
    # launch requirement (docs/decisions/021-message-sync-privacy.md).
    Service("messages", "Messages",
            "Your text and picture messages from this device are kept in Luma Cloud and appear on your "
            "other devices. This device sends its history once, then new messages as they arrive.",
            "This device stops sending and receiving messages. Nothing is deleted from Luma Cloud or your "
            "other devices, and the messages on this device stay.", default=False),
    Service("calls", "Call history",
            "Calls made and received on this device are listed on your other devices. Call audio is never "
            "sent to Luma Cloud.",
            "This device stops sending and receiving call history. Nothing is deleted from Luma Cloud or "
            "your other devices, and the history on this device stays.", default=False),
    Service("leaf-books", "Books",
            "Where you are in each book, and your highlights, notes and bookmarks, follow you to your other "
            "devices. The books themselves are not copied.",
            "This device stops sharing reading. Its positions and highlights stay here, and your other devices "
            "keep theirs."),
    Service("tide-sources", "Music servers",
            "Music servers you add in Tide, and their passwords, appear on your other devices.",
            "This device stops sharing music servers. Servers already added here stay, with their "
            "passwords."),
)
SERVICE_IDS = tuple(service.id for service in SERVICES)


def settings_file(data_directory: Path) -> Path:
    return data_directory / "services.json"


def enabled_services(data_directory: Path) -> dict[str, bool]:
    try:
        stored = json.loads(settings_file(data_directory).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    stored = stored if isinstance(stored, dict) else {}
    return {service.id: bool(stored.get(service.id, service.default)) for service in SERVICES}


def save_enabled(data_directory: Path, enabled: dict[str, bool]) -> None:
    data_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".services-", dir=data_directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump({service.id: bool(enabled.get(service.id, service.default)) for service in SERVICES}, stream, sort_keys=True)
        os.chmod(temporary, 0o600)
        os.replace(temporary, settings_file(data_directory))
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def forget_state(state: dict, scope: str, service: str) -> None:
    """Drop what this device remembers about a service, so turning it back on
    starts from a full, safe exchange rather than assuming nothing changed."""
    keys = {
        "notes": [f"{scope}|notes"],
        "contacts": [f"{scope}|contacts", f"{scope}|contacts-dav"],
        "calendar": [f"{scope}|calendar"],
        "world-clocks": [f"{scope}|collection|world-clocks"],
        "weather-places": [f"{scope}|collection|weather-places"],
        "tide-sources": [f"{scope}|collection|tide-sources", f"{scope}|tide-secrets"],
        "leaf-books": [f"{scope}|collection|leaf-books"],
    }.get(service, [])
    for key in keys:
        state.pop(key, None)
    if service == "photos" and f"{scope}|photos" in state:
        record = dict(state[f"{scope}|photos"])
        # Keep what was already downloaded, so turning photos back on does not
        # bring back photos deleted here meanwhile; resend the library.
        for field in ("manifest", "count"):
            record.pop(field, None)
        state[f"{scope}|photos"] = record
    state.pop(f"{scope}|revision", None)


def disconnect_calendar(state: dict, scope: str) -> str:
    """Copy the Luma calendar into Personal on this device, then remove it here."""
    from .calendar_backend import CalendarUnavailable, export_ics, import_ics, list_sources, remove_calendar
    record = state.get(f"{scope}|calendar", {})
    try:
        sources = {source.uid: source for source in list_sources()}
    except CalendarUnavailable:
        return "calendar service unavailable; nothing changed on this device"
    uid = record.get("source_uid")
    if not uid or uid not in sources:
        return "no Luma calendar on this device"
    text = export_ics(uid)
    copied = import_ics("system-calendar", text) if "BEGIN:VEVENT" in text and "system-calendar" in sources else 0
    remove_calendar(uid)
    return f"copied {copied} event(s) into Personal and disconnected the Luma calendar"


def stop_units() -> None:
    for unit in ("luma-connect-sync-watch.service", "luma-connect-sync.path", "luma-connect-sync.timer"):
        try:
            subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass


def catalogue() -> list[dict]:
    return [asdict(service) for service in SERVICES]
