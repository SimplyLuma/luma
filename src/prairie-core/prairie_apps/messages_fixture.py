# SPDX-License-Identifier: Apache-2.0
"""Studio v70 Messages data, isolated in SQLite memory for a conform run.

Only the JSON and its adjacent fixture pictures are read. No modem, account,
cloud, address book, message database or user state participates. Actions may
change the in-memory rows while the window is open, then disappear at close.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from pathlib import Path

from .messages_backend import MessageStore, ThreadRecord


class FixtureTransport:
    """No modem, helper, network or process can be reached from fixture mode."""

    def stop(self) -> None:
        pass

    def clear(self) -> None:
        pass

    def set(self, _mode: str) -> None:
        pass


def preview(message: dict, people: dict, *, group: bool = False) -> str:
    """The one line shown in v70's conversation row for its newest message."""
    kind = message.get("kind")
    body = {
        "photo": "Photo", "file": message.get("name", "File"),
        "voice": "Voice message", "place": message.get("card", {}).get("name", "Place"),
        "event": message.get("card", {}).get("title", "Event"),
        "song": " · ".join((message.get("card", {}).get("title", "Song"),
                              message.get("card", {}).get("artist", ""))).rstrip(" ·"),
        "contact": people.get(message.get("card"), {}).get("name", "Contact"),
    }.get(kind, message.get("text", ""))
    sender = message.get("from")
    prefix = "You: " if sender == "me" else (
        people.get(sender, {}).get("name", "").split(" ")[0] + ": " if group else "")
    return prefix + body


def _timestamp(day: str, clock: str) -> int:
    """Give fixed v70 day labels dates relative to the day the gate runs."""
    today = datetime.now().date()
    if day == "Yesterday":
        date = today - timedelta(days=1)
    elif day == "Monday":
        date = today - timedelta(days=max(2, (today.weekday() - 0) % 7))
    elif day.startswith("Sep "):
        date = today - timedelta(days=7)
    else:
        date = today
    hour = datetime.strptime(clock, "%I:%M %p").time()
    return int(datetime.combine(date, time(hour.hour, hour.minute)).timestamp())


class FixtureMessageStore(MessageStore):
    """The ordinary store's read API over fixture rows, with no disk backing."""

    def __init__(self, path: Path) -> None:
        self.fixture_path = Path(path)
        self.document = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        self.people: dict[str, dict] = self.document["people"]
        self.conversations: dict[str, dict] = {}
        self.by_id: dict[str, dict] = {}
        self.meta: dict[str, dict] = {}
        super().__init__(memory=True)
        for conversation in self.document["conversations"]:
            address = conversation["address"]
            self.conversations[address] = conversation
            self.by_id[conversation["id"]] = conversation
            name = conversation.get("name") or self.people[conversation["person"]]["name"]
            self.set_display_name(address, name)
            day = "Today"
            for item in conversation["messages"]:
                day = item.get("day", day)
                uid = f"v70-{item['id']:03d}"
                state = ("sent" if item.get("status") != "sending" else "sending") if item["from"] == "me" else (
                    "received" if conversation.get("unread") else "read")
                self._connection.execute(
                    "INSERT INTO messages(uid,address,body,timestamp,direction,state) VALUES(?,?,?,?,?,?)",
                    (uid, address, item.get("text", ""), _timestamp(day, item["at"]),
                     "outgoing" if item["from"] == "me" else "incoming", state),
                )
                self.meta[uid] = item
                for emoji, senders in item.get("reactions", {}).items():
                    for sender in senders:
                        self._connection.execute(
                            "INSERT INTO message_reactions(message_uid,sender,emoji,state) VALUES(?,?,?,?)",
                            (uid, sender, emoji, "sent" if sender == "me" else "received"),
                        )
                if item.get("reply"):
                    quoted = self.meta[f"v70-{item['reply']:03d}"]
                    author = "You" if quoted["from"] == "me" else self.people[quoted["from"]]["name"].split(" ")[0]
                    self._connection.execute(
                        "INSERT INTO message_quotes(message_uid,target_uid,author,body) VALUES(?,?,?,?)",
                        (uid, f"v70-{item['reply']:03d}", author, preview(quoted, self.people)),
                    )
        self._connection.commit()

    def asset(self, name: str) -> Path:
        """An asset adjacent to the JSON; paths outside its fixture directory are rejected."""
        result = (self.fixture_path.parent / name).resolve()
        if not result.is_relative_to(self.fixture_path.parent.resolve()):
            raise ValueError("fixture asset leaves its directory")
        return result

    def sender(self, uid: str) -> str:
        sender = self.meta.get(uid, {}).get("from", "")
        return self.people.get(sender, {}).get("name", "") if sender != "me" else ""

    def conversation_kind(self, address: str) -> str:
        return "group" if self.conversations.get(address, {}).get("group") else "direct"

    def threads(self, query: str = "") -> tuple[ThreadRecord, ...]:
        results = []
        for row in super().threads(query):
            conversation = self.conversations.get(row.address)
            if conversation is None:
                results.append(row)
                continue
            last = conversation["messages"][-1]
            results.append(ThreadRecord(row.address, row.display_name,
                                        preview(last, self.people, group=bool(conversation.get("group"))),
                                        row.updated, row.unread))
        return tuple(sorted(results, key=lambda row: (
            not bool(self.conversations.get(row.address, {}).get("pinned")), -row.updated)))
