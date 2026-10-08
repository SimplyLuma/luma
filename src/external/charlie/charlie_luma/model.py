# SPDX-License-Identifier: Apache-2.0
"""Presentation-independent mail model.

The engine deliberately has no GTK imports.  It can sync, index, search and
mutate mail while the UI is closed or while tests run without a display.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parseaddr
from enum import StrEnum
from hashlib import sha256
from typing import Iterable


class FolderRole(StrEnum):
    INBOX = "inbox"
    SENT = "sent"
    ARCHIVE = "archive"
    TRASH = "trash"
    DRAFTS = "drafts"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    display_name: str
    address: str
    provider: str = "imap"
    colour: str = "blue"
    enabled: bool = True
    avatar_url: str = ""


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """Non-secret connection metadata persisted beside an account."""

    imap_host: str
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 465
    username: str = ""
    use_starttls: bool = False


@dataclass(frozen=True, slots=True)
class Attachment:
    id: str
    filename: str
    content_type: str
    size: int
    content_id: str = ""
    data: bytes = field(default=b"", repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class Message:
    id: str
    account_id: str
    folder: str
    uid: int
    message_id: str
    thread_id: str
    subject: str
    sender_name: str
    sender_address: str
    recipients: tuple[str, ...]
    sent_at: datetime
    snippet: str
    body_text: str
    body_html: str = ""
    unread: bool = False
    flagged: bool = False
    outgoing: bool = False
    attachments: tuple[Attachment, ...] = ()
    references: tuple[str, ...] = ()

    @property
    def sender_label(self) -> str:
        return self.sender_name or self.sender_address or "Unknown sender"


@dataclass(frozen=True, slots=True)
class Conversation:
    id: str
    subject: str
    messages: tuple[Message, ...]
    account_ids: tuple[str, ...]

    @property
    def latest(self) -> Message:
        return max(self.messages, key=lambda message: message.sent_at)

    @property
    def unread_count(self) -> int:
        return sum(message.unread for message in self.messages)

    @property
    def participants(self) -> tuple[str, ...]:
        values: list[str] = []
        for message in self.messages:
            label = message.sender_label
            if label not in values:
                values.append(label)
        return tuple(values)

    @property
    def correspondent(self) -> Message:
        """Return the newest message from someone other than the local user.

        Conversation chrome names the person on the other side even when the
        newest item is an outgoing reply.  Falling back to ``latest`` keeps
        sent-only and self-addressed threads usable.
        """
        incoming = tuple(message for message in self.messages if not message.outgoing)
        return max(incoming, key=lambda message: message.sent_at) if incoming else self.latest

    @property
    def has_attachments(self) -> bool:
        return any(message.attachments for message in self.messages)


def normalized_subject(subject: str) -> str:
    """Normalize reply/forward prefixes without destroying user text."""
    value = " ".join((subject or "(No subject)").split())
    while True:
        lowered = value.casefold()
        prefixes = ("re:", "fw:", "fwd:")
        prefix = next((item for item in prefixes if lowered.startswith(item)), None)
        if prefix is None:
            break
        value = value[len(prefix):].lstrip()
    return value or "(No subject)"


def stable_thread_id(
    message_id: str,
    subject: str,
    references: Iterable[str] = (),
) -> str:
    """Derive a stable local thread key from RFC ancestry, then subject.

    Servers with a stronger conversation identifier may pass that identifier
    as ``message_id``.  Otherwise the earliest reference is authoritative.
    """
    lineage = tuple(item.strip() for item in references if item.strip())
    seed = lineage[0] if lineage else message_id.strip()
    if not seed:
        seed = normalized_subject(subject).casefold()
    return sha256(seed.encode("utf-8", "replace")).hexdigest()[:24]


def address_parts(value: str) -> tuple[str, str]:
    name, address = parseaddr(value)
    return name.strip(), address.strip().casefold()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class Draft:
    account_id: str
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    subject: str = ""
    body: str = ""
    attachments: list[str] = field(default_factory=list)
    in_reply_to: str = ""
    references: list[str] = field(default_factory=list)
