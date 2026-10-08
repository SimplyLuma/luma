# SPDX-License-Identifier: Apache-2.0
"""A read-only 'Luma Cloud' history source for Messages.

Rows come from the Connect cloud-history cache (other devices' messages, pulled
by luma-connect-sync). They are merged into what Messages shows and never
written into this device's message store or any provider. A row this device
already has (same id, or same direction/body within a couple of seconds) is
shown once, from the local store: a connected phone's live copy always wins.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .connect_messages import _hub_id, cloud_messages
from .messages_backend import MessageRecord, ThreadRecord, normalize_address

CLOUD_STATUS = "cloud"
CLOUD_LABEL = "From Luma Cloud"
_MATCH_SECONDS = 2


def cache_path(environment: dict[str, str] | None = None) -> Path:
    environment = os.environ if environment is None else environment
    data = environment.get("XDG_DATA_HOME") or str(Path(environment.get("HOME", str(Path.home()))) / ".local/share")
    return Path(data) / "luma/connect/cloud-history.db"


class CloudHistory:
    """Reads the cache once per change of the file, so list reloads stay cheap."""

    def __init__(self, environment: dict[str, str] | None = None, reader=None) -> None:
        self.environment = environment
        self._reader = reader
        self._stamp = None
        self._by_address: dict[str, tuple[MessageRecord, ...]] = {}

    def _rows(self) -> list[dict]:
        if self._reader is not None:
            return self._reader()
        return cloud_messages(self.environment)

    def by_address(self, canonical=lambda address: address) -> dict[str, tuple[MessageRecord, ...]]:
        path = cache_path(self.environment)
        try:
            info = path.stat()
            stamp = (info.st_mtime_ns, info.st_size)
        except OSError:
            stamp = None
        if self._reader is None and stamp is None:
            self._stamp, self._by_address = None, {}
            return {}
        if self._reader is None and stamp == self._stamp:
            return self._by_address
        grouped: dict[str, list[MessageRecord]] = {}
        for row in self._rows():
            addresses = row.get("addresses") or []
            if len(addresses) != 1:
                continue  # group threads are not shown from the cloud yet
            try:
                address = canonical(normalize_address(addresses[0]))
            except ValueError:
                continue
            incoming = row.get("direction") != "out"
            grouped.setdefault(address, []).append(MessageRecord(
                uid=f"cloud:{row['id']}", address=address, body=row.get("body") or "",
                timestamp=int(row.get("sent_at") or 0) // 1000,
                direction="incoming" if incoming else "outgoing",
                state=("read" if row.get("read") else "received") if incoming else "sent",
                send_status=CLOUD_STATUS))
        self._stamp = stamp
        self._by_address = {address: tuple(sorted(items, key=lambda m: (m.timestamp, m.uid)))
                            for address, items in grouped.items()}
        return self._by_address


def is_cloud(message: MessageRecord) -> bool:
    return message.send_status == CLOUD_STATUS


def _digest(body: str) -> str:
    return hashlib.sha256(body.strip().encode()).hexdigest()


def merge_thread(local: tuple[MessageRecord, ...], cloud: tuple[MessageRecord, ...]) -> tuple[MessageRecord, ...]:
    """Local rows, plus cloud rows this device does not already hold."""
    if not cloud:
        return local
    local_ids = {_hub_id(message.uid) for message in local}
    fingerprints = [(message.direction, message.timestamp, _digest(message.body)) for message in local]
    extra = []
    for message in cloud:
        hub_id = message.uid.removeprefix("cloud:")
        if hub_id in local_ids:
            continue
        digest = _digest(message.body)
        if any(direction == message.direction and body == digest and abs(stamp - message.timestamp) <= _MATCH_SECONDS
               for direction, stamp, body in fingerprints):
            continue
        extra.append(message)
    if not extra:
        return local
    return tuple(sorted((*local, *extra), key=lambda m: (m.timestamp, m.uid)))


def merge_threads(local: tuple[ThreadRecord, ...], cloud: dict[str, tuple[MessageRecord, ...]],
                  local_thread, query: str = "") -> tuple[ThreadRecord, ...]:
    """Thread list with cloud-only conversations and newer cloud activity folded in.

    ``local_thread(address)`` returns that conversation's local rows, used to
    decide whether a cloud row is genuinely new.
    """
    if not cloud:
        return local
    needle = query.strip().casefold()
    by_address = {record.address: record for record in local}
    for address, messages in cloud.items():
        record = by_address.get(address)
        fresh = merge_thread(local_thread(address), messages) if record else messages
        cloud_only = [m for m in fresh if is_cloud(m)]
        if not cloud_only:
            continue
        latest = cloud_only[-1]
        if record is None:
            if needle and needle not in address.casefold() and not any(needle in m.body.casefold() for m in messages):
                continue
            unread = sum(1 for m in cloud_only if m.direction == "incoming" and m.state == "received")
            by_address[address] = ThreadRecord(address, address, f"{CLOUD_LABEL} · {latest.body}".strip(" ·"),
                                               latest.timestamp, unread)
        elif latest.timestamp > record.updated and not record.preview.startswith("Draft: "):
            by_address[address] = ThreadRecord(record.address, record.display_name, latest.body,
                                               latest.timestamp, record.unread)
    return tuple(sorted(by_address.values(), key=lambda record: (-record.updated, record.address)))


def cloud_call_records(rows: list[dict]) -> tuple:
    """Other devices' call log rows as Phone CallRecords (uid ``cloud:<id>``)."""
    from .phone_backend import CallRecord
    records = []
    for row in rows:
        try:
            address = normalize_address(row.get("number") or "")
        except ValueError:
            continue
        kind = row.get("direction")
        if kind == "out":
            direction, outcome = "outgoing", "completed" if row.get("answered") else "cancelled"
        elif kind in {"in", "missed"}:
            direction, outcome = "incoming", "completed" if kind == "in" else "missed"
        else:
            continue
        records.append(CallRecord(f"cloud:{row['id']}", address, direction, int(row.get("started_at") or 0),
                                  max(0, int(row.get("duration_s") or 0)), outcome))
    return tuple(records)


def merge_calls(local: tuple, cloud: tuple) -> tuple:
    """Local call log plus cloud calls not already on this device, newest first."""
    if not cloud:
        return local
    local_ids = {_hub_id(record.uid) for record in local}
    extra = [record for record in cloud
             if record.uid.removeprefix("cloud:") not in local_ids
             and not any(mine.direction == record.direction and mine.address[-7:] == record.address[-7:]
                         and abs(mine.started - record.started) <= _MATCH_SECONDS for mine in local)]
    if not extra:
        return local
    return tuple(sorted((*local, *extra), key=lambda record: (-record.started, record.uid)))
