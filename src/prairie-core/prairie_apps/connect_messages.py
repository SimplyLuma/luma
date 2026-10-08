# SPDX-License-Identifier: Apache-2.0
"""This device's messages and call history, sent to the Luma Hub as change logs.

Both logs are off until the person turns them on: unlike notes or photos they
carry who someone talked to and what they said. The Hub seals them at rest with
a server key today; end-to-end encryption is a launch requirement, so this
module only ever hands the Hub the fields its log API defines.

Only rows this device recorded are sent. Each row's digest is remembered, so a
round sends what is new or changed and a tombstone for what was deleted here.
Reading other devices' history back is a separate step (not done here).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

MAX_BATCH = 1000
SERVICES = ("messages", "calls")
_ID = re.compile(r"^[A-Za-z0-9_.:@+-]{1,128}$")
_ADDRESS_STRIP = re.compile(r"[^A-Za-z0-9+@._ ()-]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


from .connect_sync import ConnectError, HubResponseError


class MessageSyncError(ConnectError):
    pass


def _data_home(environment: dict[str, str] | None) -> Path:
    environment = os.environ if environment is None else environment
    return Path(environment.get("XDG_DATA_HOME") or Path(environment.get("HOME", str(Path.home()))) / ".local/share")


def messages_path(environment: dict[str, str] | None = None) -> Path:
    return _data_home(environment) / "prairie/messages/messages.db"


def calls_path(environment: dict[str, str] | None = None) -> Path:
    return _data_home(environment) / "prairie/phone/calls.db"


def explicitly_enabled(data_directory: Path, service: str) -> bool:
    """Messages and calls need an explicit 'on'; a missing setting means off."""
    try:
        stored = json.loads((data_directory / "services.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(stored, dict) and stored.get(service) is True


def _hub_id(value: str) -> str:
    return value if _ID.match(value) else "h:" + hashlib.sha256(value.encode()).hexdigest()


def _address(value: str) -> str:
    cleaned = _ADDRESS_STRIP.sub("", (value or "").strip())[:64].strip()
    return cleaned or "unknown"


def _read_rows(path: Path, query: str) -> list[sqlite3.Row]:
    if not path.exists():
        return []
    # Read-only: the Messages and Phone daemons own these databases.
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(query).fetchall()
    except sqlite3.OperationalError as error:
        raise MessageSyncError(f"could not read {path.name}: {error}") from None
    finally:
        connection.close()


# Luma Messages conversations (ADR-051 §7) never enter this log: the Hub seals
# it with a key the Hub holds, which would undo their end-to-end encryption.
# They live in an account store, never this device's messages.db; a row that
# carries the Luma transport tag, or a store under the accounts directory, is
# refused here anyway.
LUMA_TRANSPORT = "luma:"


def _account_store(path: Path) -> bool:
    parts = Path(path).parts
    return any(parts[i:i + 3] == ("prairie", "messages", "accounts") for i in range(len(parts)))


def message_items(path: Path) -> dict[str, dict]:
    items = {}
    if _account_store(path):
        return items
    for row in _read_rows(path, "SELECT uid,address,body,timestamp,direction,state,transport_id FROM messages"):
        if str(row["transport_id"] or "").startswith(LUMA_TRANSPORT):
            continue
        stamp = int(row["timestamp"])
        address = _address(row["address"])
        item_id = _hub_id(str(row["uid"]))
        items[item_id] = {
            "id": item_id,
            # The thread is the counterpart, derived the same way on every device.
            "thread_id": "t:" + hashlib.sha256(address.encode()).hexdigest()[:40],
            "direction": "out" if row["direction"] == "outgoing" else "in",
            "addresses": [address],
            "body": _CONTROL.sub("", str(row["body"] or ""))[:65536],
            "sent_at": stamp if stamp > 10**12 else stamp * 1000,
            "read": row["direction"] == "outgoing" or row["state"] == "read",
            "deleted": False,
            "attachments": [],
        }
    return items


def call_items(path: Path) -> dict[str, dict]:
    items = {}
    for row in _read_rows(path, "SELECT uid,address,direction,started,duration,outcome FROM calls"):
        outcome = str(row["outcome"])
        incoming = row["direction"] == "incoming"
        item_id = _hub_id(str(row["uid"]))
        started = int(row["started"])
        items[item_id] = {
            "id": item_id,
            "direction": "out" if not incoming else ("in" if outcome == "completed" else "missed"),
            "number": _address(row["address"]),
            "contact_uid": "",
            "started_at": started // 1000 if started > 10**12 else started,
            "duration_s": max(0, min(int(row["duration"] or 0), 604800)),
            "answered": outcome == "completed",
            "deleted": False,
        }
    return items


def _digest(item: dict) -> str:
    return hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def _skeleton(kind: str, item: dict) -> dict:
    # A tombstone still has to satisfy the log's shape; it carries no content.
    if kind == "messages":
        return {"thread_id": item["thread_id"], "direction": item["direction"], "sent_at": item["sent_at"]}
    return {"direction": item["direction"], "started_at": item["started_at"]}


def _tombstone(kind: str, item_id: str, skeleton: dict) -> dict:
    if kind == "messages":
        return {"id": item_id, **skeleton, "addresses": ["unknown"], "body": "", "read": True,
                "deleted": True, "attachments": []}
    return {"id": item_id, **skeleton, "number": "unknown", "contact_uid": "", "duration_s": 0,
            "answered": False, "deleted": True}


def sync_log(kind: str, current: dict[str, dict], *, address: str, token: str, device_id: str,
             http, state: dict, scope: str, dry_run: bool = False) -> str:
    if kind not in SERVICES:
        raise ValueError(kind)
    key = f"{scope}|{kind}|sent"
    sent: dict[str, dict] = state.get(key, {})
    pending = [item for item_id, item in current.items() if sent.get(item_id, {}).get("digest") != _digest(item)]
    removed = [item_id for item_id in sent if item_id not in current and not sent[item_id].get("deleted")]
    pending += [_tombstone(kind, item_id, sent[item_id]["skeleton"]) for item_id in removed]
    unchanged = len(current) - (len(pending) - len(removed))
    if dry_run:
        return f"{kind}: would send {len(pending) - len(removed)}, delete {len(removed)}, unchanged {unchanged} (nothing sent)"
    url = f"{address}/api/hub/sync/{kind}"
    for start in range(0, len(pending), MAX_BATCH):
        chunk = pending[start:start + MAX_BATCH]
        try:
            http.post_json(url, {"device_id": device_id, "items": chunk}, token=token)
        except HubResponseError as error:
            if error.status == 409:
                # The Hub has this device's switch off: nothing to do, not a failure.
                return f"{kind}: off on the hub"
            raise
        for item in chunk:
            if item["deleted"]:
                sent[item["id"]] = {"deleted": True, "skeleton": sent.get(item["id"], {}).get("skeleton", {})}
            else:
                sent[item["id"]] = {"digest": _digest(item), "skeleton": _skeleton(kind, item)}
        state[key] = sent  # committed per accepted batch, so a failure resends only the rest
    return f"{kind}: sent {len(pending) - len(removed)}, deleted {len(removed)}, unchanged {unchanged}"


def sync_messages_and_calls(*, address: str, token: str, device_id: str, http, state: dict, scope: str,
                            data_directory: Path, environment: dict[str, str] | None = None,
                            dry_run: bool = False) -> list[str]:
    lines = []
    for kind, path, load in (("messages", messages_path(environment), message_items),
                             ("calls", calls_path(environment), call_items)):
        if not explicitly_enabled(data_directory, kind):
            lines.append(f"{kind}: off")
            continue
        if not path.exists() and any(not entry.get("deleted") for entry in state.get(f"{scope}|{kind}|sent", {}).values()):
            # A vanished store is not an emptied one: never tombstone history for it.
            raise MessageSyncError(f"refusing to sync {kind}: {path} is missing but history was sent before")
        lines.append(sync_log(kind, load(path), address=address, token=token, device_id=device_id,
                              http=http, state=state, scope=scope, dry_run=dry_run))
    return lines


def _device_id(environment: dict[str, str] | None) -> str:
    from .connect_sync import load_identity
    identity = load_identity(environment)
    if identity is None:
        raise MessageSyncError("this device is not enrolled with a Connect hub")
    return identity.device_id


def _run(kind: str, *, address: str, token: str, http, state: dict, scope: str,
         environment: dict[str, str] | None = None, remote_changed: bool = False,
         device_id: str | None = None, data_directory: Path | None = None) -> str:
    # connect_sync only calls this when the switch is on; the stored setting is
    # checked again here so a caller that forgets the switch still sends nothing.
    from .connect_sync import connect_data_directory
    directory = data_directory or connect_data_directory(environment)
    if not explicitly_enabled(directory, kind):
        return f"{kind}: off"
    path, load = (messages_path(environment), message_items) if kind == "messages" else (calls_path(environment), call_items)
    if not path.exists() and any(not entry.get("deleted") for entry in state.get(f"{scope}|{kind}|sent", {}).values()):
        raise MessageSyncError(f"refusing to sync {kind}: {path} is missing but history was sent before")
    device = device_id or _device_id(environment)
    sent = sync_log(kind, load(path), address=address, token=token, device_id=device, http=http, state=state, scope=scope)
    if sent.endswith("off on the hub"):
        return sent
    received = receive_log(kind, address=address, token=token, device_id=device, http=http, scope=scope,
                           environment=environment)
    return sent if received.endswith("off on the hub") else f"{sent}, {received}"


def sync_messages(**kwargs) -> str:
    return _run("messages", **kwargs)


def sync_calls(**kwargs) -> str:
    return _run("calls", **kwargs)


# ---- receiving: other devices' history into a read-only local cache ---------
#
# Pulled rows live in their own store, never in a device's messages.db or
# calls.db and never in the paired provider. Messages and Phone read it as a
# "Luma Cloud" history source; a live provider row always wins in the UI.
# Turning a service off deletes only this cache.

MAX_PAGES = 50
_CACHE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, device_id TEXT NOT NULL, thread_id TEXT NOT NULL,"
    " direction TEXT NOT NULL, sent_at INTEGER NOT NULL, read INTEGER NOT NULL, deleted INTEGER NOT NULL,"
    " addresses TEXT NOT NULL, body TEXT NOT NULL, attachments TEXT NOT NULL, updated_at INTEGER, seq INTEGER)",
    "CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, device_id TEXT NOT NULL, direction TEXT NOT NULL,"
    " started_at INTEGER NOT NULL, duration_s INTEGER NOT NULL, answered INTEGER NOT NULL, deleted INTEGER NOT NULL,"
    " number TEXT NOT NULL, contact_uid TEXT NOT NULL, updated_at INTEGER, seq INTEGER)",
)


def cloud_history_path(environment: dict[str, str] | None = None) -> Path:
    return _data_home(environment) / "luma/connect/cloud-history.db"


def _open_cache(environment: dict[str, str] | None, scope: str) -> sqlite3.Connection:
    path = cloud_history_path(environment)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fresh = not path.exists()
    if fresh:
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
    os.chmod(path, 0o600)
    connection = sqlite3.connect(path, timeout=10)
    for statement in _CACHE_SCHEMA:
        connection.execute(statement)
    row = connection.execute("SELECT value FROM meta WHERE key='scope'").fetchone()
    if row is None or row[0] != scope:
        # Another account or hub: never mix histories.
        connection.execute("DELETE FROM messages")
        connection.execute("DELETE FROM calls")
        connection.execute("DELETE FROM meta")
        connection.execute("INSERT INTO meta VALUES('scope', ?)", (scope,))
    connection.commit()
    return connection


def _apply(connection: sqlite3.Connection, kind: str, item: dict) -> None:
    existing = connection.execute(f"SELECT read, deleted FROM {kind} WHERE id=?", (item["id"],)).fetchone() if kind == "messages" \
        else connection.execute("SELECT 0, deleted FROM calls WHERE id=?", (item["id"],)).fetchone()
    deleted = bool(item.get("deleted")) or bool(existing and existing[1])
    if kind == "messages":
        read = bool(item.get("read")) or bool(existing and existing[0])
        content = ("[]", "", "[]") if deleted else (json.dumps(item.get("addresses") or []), str(item.get("body") or ""),
                                                  json.dumps(item.get("attachments") or []))
        connection.execute("INSERT OR REPLACE INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           (item["id"], item.get("device_id", ""), item.get("thread_id", ""), item.get("direction", "in"),
                            int(item.get("sent_at") or 0), int(read), int(deleted), *content,
                            item.get("updated_at"), item.get("seq")))
    else:
        number, contact = ("", "") if deleted else (str(item.get("number") or ""), str(item.get("contact_uid") or ""))
        connection.execute("INSERT OR REPLACE INTO calls VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (item["id"], item.get("device_id", ""), item.get("direction", "in"), int(item.get("started_at") or 0),
                            int(item.get("duration_s") or 0), int(bool(item.get("answered"))), int(deleted),
                            number, contact, item.get("updated_at"), item.get("seq")))


def receive_log(kind: str, *, address: str, token: str, device_id: str, http, scope: str,
                environment: dict[str, str] | None = None) -> str:
    """Pull every change after this device's cursor into the cloud history cache."""
    if kind not in SERVICES:
        raise ValueError(kind)
    connection = _open_cache(environment, scope)
    try:
        row = connection.execute("SELECT value FROM meta WHERE key=?", (f"{kind}-cursor",)).fetchone()
        cursor = int(row[0]) if row else 0
        received = 0
        for _page in range(MAX_PAGES):
            try:
                reply = http.get_json(f"{address}/api/hub/sync/{kind}?after={cursor}&limit={MAX_BATCH}", token=token)
            except HubResponseError as error:
                if error.status == 409:
                    return f"{kind}: off on the hub"
                raise
            items = reply.get("items") or []
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    raise MessageSyncError(f"the hub sent an unreadable {kind} entry")
                if item.get("device_id") == device_id:
                    continue  # this device's own history is in its own store
                _apply(connection, kind, item)
                received += 1
            cursor = int(reply.get("cursor", cursor))
            connection.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (f"{kind}-cursor", str(cursor)))
            connection.commit()  # a page at a time: an interruption resumes, never repeats a gap
            if not reply.get("more"):
                break
        return f"received {received}"
    finally:
        connection.close()


def forget_cloud_history(kind: str, environment: dict[str, str] | None = None) -> None:
    """Switching a service off removes its cached rows; no cloud or device data is touched."""
    path = cloud_history_path(environment)
    if not path.exists():
        return
    if kind == "all":
        path.unlink(missing_ok=True)
        return
    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.execute(f"DELETE FROM {'messages' if kind == 'messages' else 'calls'}")
        connection.execute("DELETE FROM meta WHERE key=?", (f"{kind}-cursor",))
        remaining = sum(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("messages", "calls"))
        connection.commit()
    finally:
        connection.close()
    if remaining == 0:
        path.unlink(missing_ok=True)


def cloud_messages(environment: dict[str, str] | None = None) -> list[dict]:
    """Read-only 'Luma Cloud' messages from other devices, oldest first, deletions excluded."""
    path = cloud_history_path(environment)
    if not path.exists():
        return []
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        rows = connection.execute("SELECT id,device_id,thread_id,direction,sent_at,read,addresses,body,attachments"
                                  " FROM messages WHERE deleted=0 ORDER BY sent_at, id").fetchall()
    finally:
        connection.close()
    return [{"id": r[0], "device_id": r[1], "thread_id": r[2], "direction": r[3], "sent_at": r[4], "read": bool(r[5]),
             "addresses": json.loads(r[6]), "body": r[7], "attachments": json.loads(r[8])} for r in rows]


def cloud_calls(environment: dict[str, str] | None = None) -> list[dict]:
    path = cloud_history_path(environment)
    if not path.exists():
        return []
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        rows = connection.execute("SELECT id,device_id,direction,started_at,duration_s,answered,number,contact_uid"
                                  " FROM calls WHERE deleted=0 ORDER BY started_at DESC, id").fetchall()
    finally:
        connection.close()
    return [{"id": r[0], "device_id": r[1], "direction": r[2], "started_at": r[3], "duration_s": r[4],
             "answered": bool(r[5]), "number": r[6], "contact_uid": r[7]} for r in rows]
