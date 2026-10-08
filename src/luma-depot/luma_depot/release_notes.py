# SPDX-License-Identifier: Apache-2.0
"""A Luma release's notes: fetched, verified and read, never shown raw.

The OS pipeline (scripts/os/lib/release_notes.py) publishes each release's
notes as ``org.projectluma.os-release-notes`` JSON beside a minisign signature
made with the update graph's key. Depot reads them for its "What's new" sheet:
the signature is checked against the keys luma-update trusts before a byte of
the document is parsed, then the document is reduced to what a person reads --
the release's name, its date and its changes by type. The package list stays
in ``technical`` for the sheet's small disclosure.

Nothing here imports GTK, so the whole path is tested in %check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime
import json
from pathlib import Path
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

SCHEMA_PREFIX = "org.projectluma.os-release-notes/"
NOTES_MAX_BYTES = 2 * 1024 * 1024
SIGNATURE_MAX_BYTES = 4096
TIMEOUT = 15
USER_AGENT = "Luma-Depot/4"
# Where luma-update looks for the update graph's key (luma_update/config.py).
# The notes are signed with that same key.
KEY_DIRS = (Path("/usr/share/luma/update"), Path("/usr/lib/luma-update/graph-keys.d"),
            Path("/etc/luma/update-graph-keys.d"))

# The order and words people see. The document's own section_order and
# section_labels are used for anything these do not name.
ORDER = ("feature", "improvement", "fix", "security")
LABELS = {"feature": "New features", "improvement": "Improvements", "fix": "Fixes",
          "security": "Security"}
# Schema v1 named its sections by what happened; read them as the four types.
V1_TYPES = {"added": "feature", "changed": "improvement", "fixed": "fix", "removed": "improvement",
            "security": "security"}

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")


class NotesError(Exception):
    """The notes could not be shown. ``message`` is written for a person."""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(detail or message)
        self.message = message
        self.detail = detail or message


@dataclass(frozen=True)
class Note:
    summary: str
    details: str = ""


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    notes: tuple[Note, ...]


@dataclass(frozen=True)
class PackageChange:
    name: str
    before: str | None
    after: str | None


@dataclass(frozen=True)
class ReleaseNotes:
    display_name: str
    date: str
    sections: tuple[Section, ...]
    summary: str = ""
    build_id: str = ""
    version: str = ""
    channel: str = ""
    packages: tuple[PackageChange, ...] = field(default=())
    verified: bool = False

    @property
    def count(self) -> int:
        return sum(len(section.notes) for section in self.sections)


def _text(value, limit: int = 2000) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def human_date(value: str) -> str:
    """"2026-09-17" or an ISO timestamp as "September 17, 2026"; "" if neither."""
    value = _text(value, 40)
    if not value:
        return ""
    try:
        day = datetime.date.fromisoformat(value[:10])
    except ValueError:
        return ""
    return f"{MONTHS[day.month - 1]} {day.day}, {day.year}"


def _notes(items) -> tuple[Note, ...]:
    notes = []
    for item in items if isinstance(items, list) else ():
        if isinstance(item, str):
            summary, details = _text(item, 300), ""
        elif isinstance(item, dict):
            summary, details = _text(item.get("summary"), 300), _text(item.get("details"))
        else:
            continue
        if summary:
            notes.append(Note(summary, details))
    return tuple(notes)


def parse(document, *, fallback_name: str = "", verified: bool = False) -> ReleaseNotes:
    """Read a notes document (already decoded JSON) into what the sheet shows."""
    if not isinstance(document, dict):
        raise NotesError("These release notes could not be read.", "the document is not a JSON object")
    schema = document.get("schema")
    if not isinstance(schema, str) or not schema.startswith(SCHEMA_PREFIX):
        raise NotesError("These release notes could not be read.", f"unknown schema {schema!r}")
    raw_sections = document.get("sections")
    if not isinstance(raw_sections, dict):
        raise NotesError("These release notes could not be read.", "no sections")
    labels = document.get("section_labels") if isinstance(document.get("section_labels"), dict) else {}
    order = [key for key in document.get("section_order") or () if isinstance(key, str)]

    merged: dict[str, list[Note]] = {}
    for key, items in raw_sections.items():
        if not isinstance(key, str):
            continue
        kind = key if key in ORDER else V1_TYPES.get(key, key)
        merged.setdefault(kind, []).extend(_notes(items))
    keys = [key for key in ORDER if key in merged]
    keys += [key for key in order if key in merged and key not in keys]
    keys += sorted(key for key in merged if key not in keys)
    sections = tuple(
        Section(key, LABELS.get(key) or _text(labels.get(key), 60) or key.replace("-", " ").capitalize(),
                tuple(merged[key]))
        for key in keys if merged[key])

    packages = []
    for item in document.get("packages_changed") or ():
        if isinstance(item, dict) and _text(item.get("name"), 200):
            packages.append(PackageChange(_text(item.get("name"), 200),
                                          _text(item.get("from"), 200) or None,
                                          _text(item.get("to"), 200) or None))
    return ReleaseNotes(
        display_name=_text(document.get("display_name"), 200) or fallback_name or "Luma",
        date=human_date(document.get("nightly_date")) or human_date(document.get("generated_utc")),
        sections=sections,
        summary=_text(document.get("summary"), 500),
        build_id=_text(document.get("build_id"), 80),
        version=_text(document.get("version"), 120),
        channel=_text(document.get("channel"), 40),
        packages=tuple(packages),
        verified=verified,
    )


def _keys(key_dirs) -> list[bytes]:
    keys = []
    for directory in key_dirs:
        try:
            files = sorted(Path(directory).glob("*.pub"))
        except OSError:
            continue
        for path in files:
            try:
                keys.append(path.read_bytes())
            except OSError:
                continue
    return keys


def verify(message: bytes, signature: bytes, key_dirs=KEY_DIRS) -> str:
    """Return the trusted comment of ``signature`` over ``message``.

    Any key luma-update trusts may have signed it; a key with another id is
    skipped before any arithmetic happens.
    """
    from luma_installer.depot_signature import SignatureError, verify_file

    keys = _keys(key_dirs)
    if not keys:
        raise NotesError("Luma could not check these release notes.",
                         "no update signing key is installed on this computer")
    problem = "no trusted key signed them"
    for key in keys:
        try:
            return verify_file(key, message, signature)
        except SignatureError as error:
            problem = str(error)
    raise NotesError("These release notes could not be verified.", problem)


def _comment_fields(comment: str) -> dict[str, str]:
    fields = {}
    for word in comment.split():
        if "=" in word:
            key, value = word.split("=", 1)
            fields[key] = value
    return fields


def _get(url: str, limit: int, opener, timeout: float) -> bytes:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=timeout) as response:
            data = response.read(limit + 1)
    except HTTPError as error:
        if error.code == 404:
            raise NotesError("The notes for this update are not published yet.",
                             f"HTTP 404 for {url}") from error
        raise NotesError("The update server did not send the release notes.",
                         f"HTTP {error.code} for {url}") from error
    except (URLError, TimeoutError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise NotesError("Luma could not reach the update server. Check your connection.",
                         f"{reason} ({url})") from error
    if len(data) > limit:
        raise NotesError("These release notes could not be read.", f"{url} is over {limit} bytes")
    return data


def fetch(url: str, *, fallback_name: str = "", opener=urlopen, key_dirs=KEY_DIRS,
          timeout: float = TIMEOUT) -> ReleaseNotes:
    """Download, verify and read the notes at ``url``. Raises NotesError."""
    parts = urlsplit(url or "")
    if parts.scheme != "https" or not parts.netloc:
        raise NotesError("These release notes could not be read.", f"not an https URL: {url!r}")
    message = _get(url, NOTES_MAX_BYTES, opener, timeout)
    signature = _get(url + ".minisig", SIGNATURE_MAX_BYTES, opener, timeout)
    comment = _comment_fields(verify(message, signature, key_dirs))
    try:
        document = json.loads(message.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise NotesError("These release notes could not be read.", f"invalid JSON: {error}") from error
    if not isinstance(document, dict):
        raise NotesError("These release notes could not be read.", "the document is not a JSON object")
    # The signature names the build it is for; notes moved onto another
    # release's URL are refused even though the signature itself holds.
    for key in ("build_id", "channel"):
        signed = comment.get(key)
        if signed is not None and signed != document.get(key):
            raise NotesError("These release notes could not be verified.",
                             f"signed for {key}={signed}, document says {document.get(key)!r}")
    return parse(document, fallback_name=fallback_name, verified=True)


class NotesCache:
    """One download per release per Depot session; failures are never kept."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._notes: dict[str, ReleaseNotes] = {}

    def get(self, url: str) -> ReleaseNotes | None:
        with self._lock:
            return self._notes.get(url)

    def put(self, url: str, notes: ReleaseNotes) -> None:
        with self._lock:
            self._notes[url] = notes


CACHE = NotesCache()


def journal(line: str, priority: int = 6, **fields) -> None:
    """Every fetch, and why one failed, for Luma Vitals."""
    try:
        from systemd import journal as systemd_journal
        systemd_journal.send(line, PRIORITY=str(priority), SYSLOG_IDENTIFIER="luma-depot",
                             **{f"LUMA_NOTES_{key.upper()}": str(value) for key, value in fields.items()})
    except Exception:  # noqa: BLE001 - logging never breaks the sheet
        pass
