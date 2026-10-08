# SPDX-License-Identifier: Apache-2.0
"""What you share with someone, for Contacts' Together card. Read only.

Messages keeps one store for texts sent through the phone and one per linked
account (accounts/<id>/messages.db, where a conversation lists its people).
Contacts never opens them through MessageStore, which migrates and reconciles
when it opens (a write). It reads each database with a read-only SQLite
connection instead, and if one is missing, locked or shaped differently it is
skipped: this never raises and never writes.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

#: How many conversations and files Together lists, newest first.
LIMIT = 12


@dataclass(frozen=True)
class TogetherItem:
    """One thing you share: a conversation, or a file sent in one."""

    kind: str               # "conversation", "file", or (fixtures) "note", "album"
    title: str
    subtitle: str
    timestamp: int          # seconds since the epoch
    address: str = ""       # the conversation's address in its store
    path: str = ""          # a file's stored copy
    content_type: str = ""
    size: int = 0
    icon: str = ""          # a Lucide name; "" lets the card choose by kind
    when: str = ""          # already worded ("10:02 AM"); "" words the timestamp


def messages_root() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return data_home / "prairie/messages"


def phone_key(value: str) -> str:
    """A number as the last ten digits (a national number), or all of them when shorter."""
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 10 else digits


def _seconds(timestamp: int) -> int:
    # Stores keep seconds or milliseconds; nothing sent today is in 1970.
    return timestamp // 1000 if timestamp > 10 ** 11 else timestamp


def _owners(conversations: list[list[dict]]) -> set[str]:
    """The account holder's own number(s): a linked account lists its owner in every conversation.

    Nothing marks the owner, so a number in at least half of three or more
    conversations is taken to be theirs.
    """
    counts: dict[str, int] = {}
    for people in conversations:
        for key in {phone_key(str(person.get("phone", ""))) for person in people}:
            if len(key) >= 3:
                counts[key] = counts.get(key, 0) + 1
    if len(conversations) < 3:
        return set()
    return {key for key, count in counts.items() if count * 2 >= len(conversations)}


def _addresses(connection: sqlite3.Connection, key: str) -> dict[str, str]:
    """This store's conversations with the person, as address → the conversation's name.

    Never the account holder's own: their number is in every conversation,
    and a card for them shares nothing "together" with anyone.
    """
    found: dict[str, str] = {}
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    names = dict(connection.execute("SELECT address, display_name FROM contacts")) if "contacts" in tables else {}
    if "conversations" in tables:
        listed: list[tuple[str, list[dict]]] = []
        for conversation, participants in connection.execute("SELECT id, participants FROM conversations"):
            try:
                people = [person for person in json.loads(participants) if isinstance(person, dict)]
            except (TypeError, ValueError):
                continue
            listed.append((str(conversation), people))
        owners = _owners([people for _conversation, people in listed])
        if key in owners:
            return {}
        for conversation, people in listed:
            if any(phone_key(str(person.get("phone", ""))) == key for person in people):
                found[conversation] = names.get(conversation, "")
    for (address,) in connection.execute("SELECT DISTINCT address FROM messages"):
        if phone_key(str(address)) == key and len(re.sub(r"\D", "", str(address))) >= 3:
            found[str(address)] = names.get(str(address), "")
    return found


def _read(database: Path, key: str, name: str) -> list[TogetherItem]:
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=0.5)
    try:
        conversations = _addresses(connection, key)
        if not conversations:
            return []
        marks = ",".join("?" * len(conversations))
        items: list[TogetherItem] = []
        for address, title in conversations.items():
            last = connection.execute(
                """SELECT m.body, m.timestamp,
                          (SELECT a.name FROM message_attachments a WHERE a.message_uid=m.uid LIMIT 1) attachment
                     FROM messages m WHERE m.address=? ORDER BY m.timestamp DESC, m.rowid DESC LIMIT 1""",
                (address,)).fetchone()
            if last is not None:
                preview = (last[0] or last[2] or "").strip().splitlines()
                items.append(TogetherItem("conversation", title or name, preview[0] if preview else "",
                                          _seconds(int(last[1])), address=address))
        attachments = database.parent / "attachments"
        for file_name, content_type, size, storage_key, timestamp, direction, address in connection.execute(
                f"""SELECT a.name, a.content_type, a.size, a.storage_key, m.timestamp, m.direction, m.address
                      FROM message_attachments a JOIN messages m ON m.uid=a.message_uid
                     WHERE m.address IN ({marks}) ORDER BY m.timestamp DESC, a.rowid DESC LIMIT ?""",
                (*conversations, LIMIT)):
            if not re.fullmatch(r"[0-9a-f]{64}", storage_key or "") or not (attachments / storage_key).is_file():
                continue
            items.append(TogetherItem(
                "file", file_name, "Sent to you" if direction == "incoming" else "You sent this",
                _seconds(int(timestamp)), address=str(address), path=str(attachments / storage_key),
                content_type=content_type or "", size=int(size or 0)))
        return items
    finally:
        connection.close()


def together(name: str, phone: str, *, root: Path | None = None) -> tuple[TogetherItem, ...]:
    """Your conversations with the person at `phone` (a group keeps its own name), then files; newest first."""
    key = phone_key(phone)
    if len(key) < 3:
        return ()
    root = root or messages_root()
    databases = [root / "messages.db", *sorted(root.glob("accounts/*/messages.db"))]
    items: list[TogetherItem] = []
    for database in databases:
        if not database.is_file():
            continue
        try:
            items.extend(_read(database, key, name))
        except (sqlite3.Error, TypeError, ValueError, OSError):
            continue
    conversations = sorted((i for i in items if i.kind == "conversation"), key=lambda i: -i.timestamp)
    files = sorted((i for i in items if i.kind == "file"), key=lambda i: -i.timestamp)
    return tuple(conversations[:LIMIT] + files[:LIMIT])
