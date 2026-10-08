# SPDX-License-Identifier: Apache-2.0
"""The agent's published state: D-Bus properties and ``luma-update status --json``.

One dataclass feeds both, so the two can never disagree. JSON keys are the
D-Bus property names in snake_case; see docs/os/luma-update.md.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import json
import re

__all__ = ("Status", "STATES", "DBUS_TYPES", "JSON_SCHEMA_VERSION")

JSON_SCHEMA_VERSION = 1

IDLE, CHECKING, AVAILABLE, DOWNLOADING, STAGED, RESTART_REQUIRED, ERROR, BARRIER_BLOCKED = (
    "idle", "checking", "available", "downloading", "staged", "restart-required", "error", "barrier-blocked")
STATES = (IDLE, CHECKING, AVAILABLE, DOWNLOADING, STAGED, RESTART_REQUIRED, ERROR, BARRIER_BLOCKED)


@dataclass
class Status:
    state: str = IDLE
    channel: str = ""
    booted_version: str = ""
    booted_commit: str = ""
    staged_version: str = ""
    available_version: str = ""
    available_summary: str = ""
    notes_url: str = ""
    importance: str = "normal"
    download_bytes: int = 0
    progress: float = 0.0
    last_check: int = 0
    last_error: str = ""
    metered: bool = False
    rollback_available: bool = False
    preview_enrolled: bool = False
    available_channels: list[str] = field(default_factory=lambda: ["stable"])
    # Beyond the core list; published on D-Bus and in JSON alike.
    last_error_class: str = ""
    managed: bool = False
    staged_commit: str = ""
    available_commit: str = ""
    rolled_back_version: str = ""
    rolled_back_at: int = 0
    booted_deadend_reason: str = ""
    waiting_version: str = ""
    # What the person chose and what Depot audits. Nothing here changes what the
    # agent does on its own; it is what it will admit to having done.
    automatic_download: bool = True
    ignored_version: str = ""
    repository_url: str = ""
    graph_url: str = ""
    signature_verified: bool = False
    signing_key_id: str = ""
    last_check_reason: str = ""
    last_check_attempt: int = 0
    # An unmanaged computer is still owed an answer: whether it could start
    # following a Luma channel, and if not, exactly what is missing.
    adoptable: bool = False
    unmanaged_reason: str = ""
    # Who set up early updates here: hub (this person's Luma account), staff
    # (a credential staff issued to this one computer), staff-media (the install
    # medium's batch credential), unknown (a record older than sources), or ''.
    preview_source: str = ""
    # What people call each version above ("Luma (Prairie, Beta 1)"), or '' when
    # that version is ''. Presentation only: the *_version fields stay the machine
    # versions every comparison uses. See luma_update/names.py for the fallbacks.
    booted_name: str = ""
    staged_name: str = ""
    available_name: str = ""
    waiting_name: str = ""
    rolled_back_name: str = ""
    ignored_name: str = ""
    # Packages this computer had added (rpm-ostree install) that the staged or
    # last staged release ships itself, so the update removed the added copy.
    removed_packages: list[str] = field(default_factory=list)
    # Packages this computer had added that are NEWER than the copy the staged
    # (or last staged) release ships: kept, with the release's copy left out.
    kept_packages: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict:
        data = asdict(self)
        data["schema_version"] = JSON_SCHEMA_VERSION
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_json_dict(), indent=2, sort_keys=True)

    def copy(self) -> "Status":
        return Status(**{f.name: (list(getattr(self, f.name)) if isinstance(getattr(self, f.name), list)
                                  else getattr(self, f.name)) for f in fields(self)})


def dbus_name(snake: str) -> str:
    return "".join(part.capitalize() for part in snake.split("_"))


def snake_name(camel: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", camel).lower()


DBUS_TYPES = {
    "state": "s", "channel": "s", "booted_version": "s", "booted_commit": "s", "staged_version": "s",
    "available_version": "s", "available_summary": "s", "notes_url": "s", "importance": "s",
    "download_bytes": "t", "progress": "d", "last_check": "x", "last_error": "s", "metered": "b",
    "rollback_available": "b", "preview_enrolled": "b", "available_channels": "as",
    "last_error_class": "s", "managed": "b", "staged_commit": "s", "available_commit": "s",
    "rolled_back_version": "s", "rolled_back_at": "x", "booted_deadend_reason": "s",
    "waiting_version": "s",
    "automatic_download": "b", "ignored_version": "s", "repository_url": "s", "graph_url": "s",
    "signature_verified": "b", "signing_key_id": "s", "last_check_reason": "s",
    "last_check_attempt": "x", "adoptable": "b", "unmanaged_reason": "s",
    "preview_source": "s",
    "booted_name": "s", "staged_name": "s", "available_name": "s", "waiting_name": "s",
    "rolled_back_name": "s", "ignored_name": "s",
    "removed_packages": "as",
    "kept_packages": "as",
}
