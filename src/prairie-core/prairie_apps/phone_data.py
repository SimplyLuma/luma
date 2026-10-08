# SPDX-License-Identifier: Apache-2.0
"""Phone's existing read-only data and its contained local incoming-call policy."""
from __future__ import annotations

from datetime import datetime
import json
import re
import os
from pathlib import Path
import sqlite3
import fcntl
import tempfile

from .phone_fixture import FixtureCall, PhonePerson, TogetherItem, digits


class PreviewContactsSource:
    """Keep the review's sample calls, but show the user's address book in Contacts.

    This is enabled explicitly by the review launcher. The conformance fixture
    remains self-contained when that flag is absent. Contact UIDs are prefixed
    so a real record cannot replace a sample caller with the same UID.
    """

    def __init__(self, fixture, records):
        self.fixture = fixture
        self.calls = fixture.calls
        self.voicemails = fixture.voicemails
        self.favourites = fixture.favourites
        self.initial = fixture.initial
        self.spam = fixture.spam
        self.audio_outputs = fixture.audio_outputs
        self.contacts = tuple(PhonePerson("eds:" + record.uid, record.name,
                                          record.phone, getattr(record, "handle", ""),
                                          record.email, hue=getattr(record, "hue", None),
                                          online=getattr(record, "on_luma", None) is True)
                              for record in records)
        self.people = (*fixture.people, *self.contacts)
        self._photos = {"eds:" + record.uid: getattr(record, "photo", b"") for record in records}

    @classmethod
    def load(cls, fixture):
        from .eds_backend import load_contacts
        return cls(fixture, load_contacts())

    def photo(self, person: PhonePerson) -> bytes:
        if person.uid.startswith("eds:"):
            return self._photos.get(person.uid, b"")
        return self.fixture.photo(person)

    def together(self, uid: str) -> tuple[TogetherItem, ...]:
        return () if uid.startswith("eds:") else self.fixture.together(uid)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class BlockList:
    """Phone-local call policy; never changes contacts, messages or carrier settings.

    The authorized additive store is luma/phone/blocked.json under XDG_DATA_HOME.
    Reads create nothing. Edits lock, reread, preserve unrelated fields and numbers,
    back up the first existing version once, and replace atomically (mode 0600).
    Carrier voicemail routing and blocking texts are outside this local policy.
    """

    def __init__(self, path: Path | None = None) -> None:
        from .phone_shared_data import directory
        self.path = path or directory("luma/phone") / "blocked.json"

    def _document(self, path: Path | None = None) -> dict:
        path = self.path if path is None else path
        if path.is_symlink():
            raise ValueError("Phone block list must be a regular file")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"numbers": []}
        if not isinstance(value, dict) or not isinstance(value.get("numbers"), list) or any(
                not isinstance(number, str) for number in value["numbers"]):
            raise ValueError("Invalid Phone block list; it was not changed")
        return value

    def _backup(self) -> None:
        backup = self.path.with_suffix(".json.bak")
        if backup.is_symlink() or (backup.exists() and not backup.is_file()):
            raise ValueError("Phone block list backup must be a regular file")
        if backup.is_file():
            self._document(backup)  # A partial backup from an older writer is not accepted.
            return
        descriptor, temporary = tempfile.mkstemp(prefix=".blocked-backup-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(self.path.read_bytes())
                target.flush()
                os.fsync(target.fileno())
            try:
                os.link(temporary, backup)
                _sync_directory(self.path.parent)
            except FileExistsError:
                if not backup.is_file() or backup.is_symlink():
                    raise ValueError("Phone block list backup must be a regular file")
                self._document(backup)
        finally:
            os.unlink(temporary)

    def contains(self, number: str) -> bool:
        key = digits(number)
        return bool(key) and key in self._document()["numbers"]

    def set_blocked(self, number: str, blocked: bool) -> bool:
        key = digits(number)
        if not key:
            raise ValueError("A phone number is required")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.path.parent / "blocked.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            document = self._document()
            before = key in document["numbers"]
            if before == blocked:
                return before
            if self.path.exists():
                self._backup()
            document["numbers"] = ([*document["numbers"], key] if blocked else
                                   [item for item in document["numbers"] if item != key])
            descriptor, name = tempfile.mkstemp(prefix=".blocked-", dir=self.path.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                    json.dump(document, target, ensure_ascii=False)
                    target.write("\n")
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(name, self.path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
            return before


def moment(timestamp: int, *, now: datetime | None = None) -> str:
    date = datetime.fromtimestamp(timestamp)
    today = now or datetime.now()
    days = (today.date() - date.date()).days
    if days == 0:
        return date.strftime("Today, %-I:%M %p")
    if days == 1:
        return date.strftime("Yesterday, %-I:%M %p")
    return date.strftime("%A" if days < 7 else "%b %-d, %Y")


def read_rows(path: Path, query: str, parameters: tuple = ()) -> tuple[sqlite3.Row, ...]:
    """A missing database is empty. Existing databases are opened mode=ro, never migrated."""
    if not path.is_file():
        return ()
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    connection.row_factory = sqlite3.Row
    try:
        return tuple(connection.execute(query, parameters))
    finally:
        connection.close()


def back_up_call_log(path: Path) -> None:
    """Save the first existing log before the controller adds a real call.

    SQLite's backup includes committed WAL data. A completed private snapshot
    is linked into place atomically; concurrent writers never replace it.
    Missing logs create nothing here, and a failed backup prevents the write.
    """
    backup = path.with_suffix(".db.bak")
    if path.is_symlink() or backup.is_symlink():
        raise ValueError("Phone call log and backup must be regular files")
    if not path.exists() or backup.is_file():
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".calls-backup-", dir=path.parent)
    os.close(descriptor)
    try:
        source = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            target = sqlite3.connect(temporary)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
        with open(temporary, "rb") as snapshot:
            os.fsync(snapshot.fileno())
        try:
            os.link(temporary, backup)
            _sync_directory(path.parent)
        except FileExistsError:
            if not backup.is_file() or backup.is_symlink():
                raise ValueError("Phone call log backup must be a regular file")
    finally:
        os.unlink(temporary)


def phone_contact(record):
    """Keep carrier numbers and positively verified Luma identities distinct."""
    account = getattr(record, "luma_account", "")
    handle = getattr(record, "handle", "")
    verified = (getattr(record, "on_luma", None) is True
                and isinstance(account, str) and re.fullmatch(r"[A-Za-z0-9_-]{8,200}", account) is not None
                and isinstance(handle, str) and re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", handle) is not None)
    if not record.phone and not verified:
        return None
    name = next((str(value).strip() for value in (record.name, record.phone, record.email)
                 if str(value or "").strip()), "@" + handle if verified else "")
    return PhonePerson(record.uid, name, record.phone, handle if verified else "", record.email,
                       hue=getattr(record, "hue", None), online=verified,
                       luma_account=account if verified else "")


class LiveSource:
    """Uses the same call/favourite stores as Phone; no new store and no writes."""

    def __init__(self, data_home: Path | None = None) -> None:
        self.base = data_home or Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        self.people: tuple[PhonePerson, ...] = ()
        self.calls: tuple[FixtureCall, ...] = ()
        self.favourites: tuple[str, ...] = ()
        self.voicemails = ()  # No native voicemail source is currently available.
        self.initial = {"tab": "pad", "selected": "", "person": "", "number": ""}
        self.audio_outputs = ()
        self.spam = {}
        self._photos = {}

    def load(self) -> "LiveSource":
        from .eds_backend import load_contacts
        records = load_contacts()
        self._photos = {record.uid: getattr(record, "photo", b"") for record in records}
        self.people = tuple(person for record in records if (person := phone_contact(record)) is not None)
        by_number = {digits(person.phone): person.uid for person in self.people if digits(person.phone)}
        from .phone_shared_data import directory
        call_path = directory("prairie/phone") / "calls.db" if os.environ.get("FLATPAK_ID") == "org.projectluma.Phone" else self.base / "prairie/phone/calls.db"
        rows = read_rows(call_path, "SELECT * FROM calls ORDER BY started DESC, uid")
        from .phone_backend import CallRecord
        records = tuple(CallRecord(row["uid"], row["address"], row["direction"], row["started"],
                                   row["duration"], row["outcome"]) for row in rows)
        # Existing cloud history is also read-only. An unavailable cloud
        # database must not hide this device's contacts or call log.
        try:
            from .connect_messages import cloud_calls
            from .messages_cloud import cloud_call_records, merge_calls
            if os.environ.get("FLATPAK_ID") == "org.projectluma.Phone":
                from .phone_host import call
                cloud = call("GetPhoneHistory")["calls"]
            else:
                cloud = cloud_calls()
            records = merge_calls(records, cloud_call_records(cloud))
        except (OSError, sqlite3.Error, ValueError, ImportError):
            pass
        self.calls = tuple(FixtureCall(row.uid, (by_number.get(digits(row.address), "?"),), "voice",
                                       "missed" if row.outcome == "missed" else
                                       "in" if row.direction == "incoming" else "out",
                                       moment(row.started),
                                       f'{max(1, row.duration // 60)} min' if row.duration else "",
                                       number=row.address) for row in records)
        try:
            folder = directory("prairie/phone") if os.environ.get("FLATPAK_ID") == "org.projectluma.Phone" else self.base / "prairie/phone"
            raw = json.loads((folder / "favourites.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = []
        if not isinstance(raw, list):
            raise ValueError("Phone favourites must be a list")
        self.favourites = tuple(uid for uid in raw if uid in {p.uid for p in self.people})
        return self

    def photo(self, person: PhonePerson) -> bytes:
        return self._photos.get(person.uid, b"")

    def together(self, uid: str) -> tuple[TogetherItem, ...]:
        person = next(p for p in self.people if p.uid == uid)
        calls = tuple(TogetherItem(call.icon, "Call, missed" if call.direction == "missed" else
                                  f"Call from {person.name.split()[0]}" if call.direction == "in" else "Call",
                                  call.when, call.duration, call.direction == "missed")
                      for call in self.calls if call.people == (uid,))
        if os.environ.get("FLATPAK_ID") == "org.projectluma.Phone":
            from .phone_host import call
            message = call("GetPhoneTogether", person.phone).get("message")
        else:
            rows = read_rows(self.base / "prairie/messages/messages.db",
                             "SELECT address, body, timestamp, direction FROM messages ORDER BY timestamp DESC")
            message = next((row for row in rows if digits(row["address"]) == digits(person.phone)), None)
        if message is None:
            return calls
        text = message["body"]
        sender = "You" if message["direction"] == "outgoing" else person.name.split()[0]
        title = f'{sender} texted: “{text[:44]}{"…" if len(text) > 44 else ""}”'
        return (*calls, TogetherItem("message-square", title, moment(message["timestamp"])))
