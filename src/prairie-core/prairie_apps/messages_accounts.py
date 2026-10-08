# SPDX-License-Identifier: Apache-2.0
"""Network accounts in Messages (ADR-023).

Each account (Google Messages, WhatsApp, Signal, Telegram) is a message service
(ADR-022) backed by one local helper process that speaks
``luma-messages-bridge/1`` (docs/research/messages-bridge-protocol.md). This
module owns the account directories, the keyring items, the helper process, the
provider Messages uses for the service, and sign-in.

Nothing here talks to a network directly; helpers do. Messages never logs or
shows helper stderr, session material or cookie values.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import base64
import re
import secrets as token
import mimetypes
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time
import uuid

from .messages_backend import MAX_ATTACHMENT_BYTES, AttachmentRecord, MessageStore, MessagingCapability
from .messages_mms import MmsCapability
from . import messages_outbound as outbound

PROTOCOL = "luma-messages-bridge/1"
CONVERSATION_ID = re.compile(r"[A-Za-z0-9._:@+=/-]{1,256}")
ACCOUNT_ID = re.compile(r"[a-z]+-[0-9a-f]{16}")
MAX_LINE = 16 * 1024 * 1024
PENDING_LIFETIME = 3600
# What a Luma conversation (ADR-051) says about itself, as the helper reports it.
LUMA_FLAGS = ("encrypted", "request", "request_from", "verified", "safety_changed", "devices_changed", "removed",
              "hidden", "account", "handle")
# Commands Messages may send a Luma helper outside the bridge's own: identity,
# requests, blocks, reports, safety numbers and starting a conversation. None
# of them delivers anything a person wrote.
LUMA_COMMANDS = frozenset({"luma.identity", "luma.handle.check", "luma.handle.claim", "luma.people", "luma.requests",
                           "luma.request.answer", "luma.block", "luma.blocks", "luma.report", "luma.safety", "luma.verify",
                           "luma.devices", "luma.sync", "conversation.create", "status"})
log = logging.getLogger(__name__)


def sniff_image(head: bytes) -> str | None:
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"heif"):
        return "image/heic"
    return None


def helper_directory() -> Path:
    return Path(os.environ.get("LUMA_MESSAGES_HELPER_DIR", "/usr/libexec/luma-messages"))


def accounts_directory() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return data_home / "prairie/messages/accounts"


@dataclass(frozen=True)
class Network:
    id: str
    name: str
    description: str
    warning: str
    # Sign-in methods in the order Messages offers them.
    methods: tuple[str, ...]
    # Added by Messages itself (Luma, for a signed-in Luma Connect device),
    # never from the Add Account list, and never removed there.
    automatic: bool = False


NETWORKS = (
    Network("gmessages", "Google Messages",
            "Texts and RCS chats from Google Messages on your Android phone. Your phone stays the one that sends them.",
            "Google doesn't offer this officially. It signs in the way Messages for web does, and Google could change or stop it at any time.",
            ("cookies",)),
    Network("whatsapp", "WhatsApp",
            "Link Luma as one of your WhatsApp devices, like WhatsApp on a computer.",
            "WhatsApp doesn't allow third-party apps. It has warned or banned some accounts that use them, so consider this before linking an account you depend on.",
            ("qr", "code")),
    Network("signal", "Signal",
            "Link Luma as one of your Signal devices. Messages from before linking stay on your phone.",
            "Signal doesn't make this app. It uses signal-cli, which needs updates to keep working.",
            ("qr",)),
    Network("telegram", "Telegram (unofficial client)",
            "Sign in with your phone number or by scanning a code in Telegram. Secret chats stay on the device that started them.",
            "This is an unofficial Telegram client built on Telegram's own TDLib.",
            ("qr", "phone")),
    # ADR-051: conversations between Luma accounts, end-to-end encrypted.
    Network("luma", "Luma",
            "Conversations with people on Luma, encrypted on your devices.",
            "", (), automatic=True),
)


def network(network_id: str) -> Network:
    for item in NETWORKS:
        if item.id == network_id:
            return item
    raise KeyError(network_id)


def available_networks(directory: Path | None = None) -> tuple[Network, ...]:
    """Networks whose helper is installed. A missing helper is simply not offered."""
    directory = directory or helper_directory()
    return tuple(item for item in NETWORKS if os.access(directory / item.id, os.X_OK))


# ── Storage ─────────────────────────────────────────────────────────────────

MEDIA_STATES = ("pending", "downloading", "done", "failed")
MEDIA_PART = re.compile(r"[A-Za-z0-9._-]{1,16}")
MEDIA_ERROR = re.compile(r"[a-z_]{1,32}")
# Automatic retries of a picture that didn't download wait longer each round,
# up to six hours; opening its conversation or tapping it retries at once.
MEDIA_RETRY_BASE = 60
MEDIA_RETRY_CAP = 6 * 3600
# What helpers before luma-messages-bridge media states wrote into a message
# instead of a picture that didn't download.
LEGACY_MEDIA_TEXT = re.compile(
    r"\[(A photo|A video|An audio message|An attachment)"
    r"(?: couldn't be downloaded\. Open it on your phone\.| \((\d+) MB\) is on your phone\. It's too large to download here\.)\]")
# A claimed send not back from the network after this long is marked for review.
UNCONFIRMED_SECONDS = 120
# Failures a working connection fixes; reconnecting retries them at once.
CONNECTION_ERRORS = ("network", "not_connected", "phone_offline", "server", "truncated", "unauthorized", "timeout")
# A part still on its way is asked for again only after this long with no word
# from the helper. The helper is re-reading it on its own backoff, so asking in
# the meantime would only repeat the same read of the conversation. Reconnecting
# ignores this and asks at once.
MEDIA_PENDING_REASK = 300
# A picture the phone has not published the file for yet. The helper spends its
# own re-reads over about half an hour and then reports one of these, so the row
# resolves while a person is still looking at it. After that it is re-read
# quietly, because the only thing missing may be the phone being awake, and
# nobody should have to press Try Again for that.
MEDIA_WAITING_ERRORS = ("no_full_size", "waiting_for_phone")
# How often that quiet re-read happens, and how long it goes on for. A steady
# half hour rather than a widening backoff: the attempt is one cheap read, and
# the answer can change the moment the phone wakes. After a day the picture has
# not been coming and the row waits for a tap instead.
MEDIA_WAITING_REASK = 1800
MEDIA_WAITING_WINDOW = 24 * 3600
# The longest a picture shows "Getting this photo from your phone…" with a
# spinner. The helper's own give-up lives in its memory and starts over on every
# helper restart, reconnect and tap, so without a bound kept here a picture the
# phone never publishes waited behind a spinner for ever. Past it the part is a
# plain, retryable "not available yet"; a file that turns up later still lands.
MEDIA_WAITING_BOUND = 10 * 60
# A tap on Try Again gets this long of fresh waiting before it resolves again.
MEDIA_TAP_WINDOW = 90
LEGACY_MEDIA_MIME = {"A photo": "image/*", "A video": "video/*", "An audio message": "audio/*", "An attachment": "application/octet-stream"}


@dataclass(frozen=True)
class MediaPart:
    """One attachment of a network message and how far it got."""
    message_uid: str
    part: str
    mime: str
    name: str
    size: int
    state: str
    error: str
    retryable: bool
    attempts: int
    next_attempt: int
    attachment_uid: str | None
    preview: Path | None
    waiting_since: int = 0

    @property
    def noun(self) -> str:
        if self.part == "mms":
            return "picture message"
        for prefix, noun in (("image/", "photo"), ("video/", "video"), ("audio/", "audio message")):
            if self.mime.startswith(prefix):
                return noun
        return "attachment"


def media_retry_delay(rounds: int, error: str = "") -> int:
    if error in MEDIA_WAITING_ERRORS:
        return MEDIA_WAITING_REASK
    return min(MEDIA_RETRY_CAP, MEDIA_RETRY_BASE * 4 ** max(0, rounds - 1))

class AccountStore(MessageStore):
    """A message store keyed by a network's conversation ids instead of phone numbers."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS message_senders(
                message_uid TEXT PRIMARY KEY REFERENCES messages(uid) ON DELETE CASCADE,
                sender_id TEXT NOT NULL, name TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS conversations(
                id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('direct','group')),
                participants TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS conversation_aliases(
                alias TEXT PRIMARY KEY, canonical TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS send_tokens(
                token TEXT PRIMARY KEY CHECK(length(token)=32),
                message_uid TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('send','react')),
                state TEXT NOT NULL CHECK(state IN ('authorized','claimed')),
                created INTEGER NOT NULL,
                claimed INTEGER);
            CREATE INDEX IF NOT EXISTS send_tokens_message ON send_tokens(message_uid, kind, state);
            CREATE TABLE IF NOT EXISTS message_receipts(
                message_uid TEXT PRIMARY KEY REFERENCES messages(uid) ON DELETE CASCADE,
                receipt TEXT NOT NULL CHECK(receipt IN ('delivered','read')));
            CREATE TABLE IF NOT EXISTS account_media(
                message_uid TEXT NOT NULL REFERENCES messages(uid) ON DELETE CASCADE,
                part TEXT NOT NULL,
                mime TEXT NOT NULL DEFAULT 'application/octet-stream',
                name TEXT NOT NULL DEFAULT '',
                size INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL CHECK(state IN ('pending','downloading','done','failed')),
                error TEXT NOT NULL DEFAULT '',
                retryable INTEGER NOT NULL DEFAULT 1,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt INTEGER NOT NULL DEFAULT 0,
                attachment_uid TEXT,
                preview_key TEXT,
                updated INTEGER NOT NULL,
                PRIMARY KEY(message_uid, part));
        """)
        self._connection.commit()
        self._add_media_wait_column()
        self._add_luma_column()
        self._adopt_legacy_media_text()

    def _add_luma_column(self) -> None:
        """Luma conversations (ADR-051) carry their request, verification and
        device-change state from the helper; other networks leave it empty."""
        columns = {row[1] for row in self._connection.execute("PRAGMA table_info(conversations)")}
        if "luma" not in columns:
            self._connection.execute("ALTER TABLE conversations ADD COLUMN luma TEXT NOT NULL DEFAULT ''")
            self._connection.commit()

    def luma_flags(self, conversation: str) -> dict:
        row = self._connection.execute("SELECT luma FROM conversations WHERE id=?", (conversation,)).fetchone()
        try:
            flags = json.loads(row[0]) if row and row[0] else {}
        except ValueError:
            return {}
        return flags if isinstance(flags, dict) else {}

    def luma_conversations(self) -> dict[str, dict]:
        found = {}
        for conversation, text in self._connection.execute("SELECT id, luma FROM conversations WHERE luma != ''"):
            try:
                flags = json.loads(text)
            except ValueError:
                continue
            if isinstance(flags, dict):
                found[conversation] = flags
        return found

    def canonical_address(self, value: str) -> str:
        value = value.strip()
        if not CONVERSATION_ID.fullmatch(value):
            raise ValueError("That isn't a conversation this account knows.")
        return value

    def reconcile_phone_identities(self) -> int:
        return 0  # conversation ids are not phone numbers

    def sender(self, uid: str) -> str:
        row = self._connection.execute("SELECT name FROM message_senders WHERE message_uid=?", (uid,)).fetchone()
        return row[0] if row else ""

    def uid_for_transport(self, transport_id: str) -> str | None:
        row = self._connection.execute("SELECT uid FROM messages WHERE transport_id=?", (transport_id,)).fetchone()
        return row[0] if row else None

    def transport_for_uid(self, uid: str) -> str | None:
        row = self._connection.execute("SELECT transport_id FROM messages WHERE uid=?", (uid,)).fetchone()
        return row[0] if row else None

    def remember_conversation(self, conversation: dict) -> None:
        participants = [p for p in conversation.get("participants") or [] if isinstance(p, dict)][:1024]
        kind = "group" if conversation.get("kind") == "group" else "direct"
        luma = conversation.get("luma")
        flags = {key: luma[key] for key in LUMA_FLAGS if key in luma} if isinstance(luma, dict) else {}
        self._connection.execute(
            """INSERT INTO conversations(id, kind, participants, archived, luma) VALUES(?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, participants=excluded.participants, archived=excluded.archived,
               luma=excluded.luma""",
            (self.canonical_address(conversation["id"]), kind, json.dumps(participants), int(bool(conversation.get("archived"))),
             json.dumps(flags, sort_keys=True) if flags else ""),
        )
        self._connection.commit()

    def conversation_kind(self, conversation: str) -> str:
        row = self._connection.execute("SELECT kind FROM conversations WHERE id=?", (conversation,)).fetchone()
        return row[0] if row else "direct"

    def conversation_participants(self, conversation: str) -> tuple[dict, ...]:
        """Read the helper's participant roster without changing its record."""
        row = self._connection.execute("SELECT participants FROM conversations WHERE id=?", (conversation,)).fetchone()
        if not row:
            return ()
        try:
            people = json.loads(row[0])
        except (TypeError, ValueError):
            return ()
        return tuple(person for person in people if isinstance(person, dict)) if isinstance(people, list) else ()

    # ── A person's requests to deliver (see messages_outbound) ──────────────────

    def authorize_send(self, uid: str, *, kind: str = "send", retry: bool = False) -> str:
        """Record that a person asked to deliver this message (or a reaction to it); returns the token.

        Pressing Send twice reuses the unclaimed token. A message whose request
        was already claimed gets a new token only when the person confirms a
        retry of a send that was not confirmed.
        """
        with self._connection:
            if kind == "send":
                row = self._connection.execute(
                    "SELECT token FROM send_tokens WHERE message_uid=? AND kind='send' AND state='authorized'", (uid,)).fetchone()
                if row:
                    return row[0]
                claimed = self._connection.execute(
                    "SELECT 1 FROM send_tokens WHERE message_uid=? AND kind='send' AND state='claimed'", (uid,)).fetchone()
                if claimed and not retry:
                    raise ValueError("This message was already sent once; only a person's retry can send it again.")
            value = token.token_hex(16)
            self._connection.execute("INSERT INTO send_tokens(token, message_uid, kind, state, created) VALUES(?,?,?,?,?)",
                                     (value, uid, kind, "authorized", int(time.time())))
        return value

    def authorized_token(self, uid: str) -> str | None:
        row = self._connection.execute(
            "SELECT token FROM send_tokens WHERE message_uid=? AND kind='send' AND state='authorized'", (uid,)).fetchone()
        return row[0] if row else None

    def claim_send(self, uid: str, value: str, *, kind: str = "send") -> "outbound.SendClaim | None":
        """Claim a person's request exactly once, before anything is asked of the helper."""
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE send_tokens SET state='claimed', claimed=? WHERE token=? AND message_uid=? AND kind=? AND state='authorized'",
                (int(time.time()), value, uid, kind))
        return outbound.issue_claim(uid, value, kind) if cursor.rowcount == 1 else None

    def release_send(self, claim: "outbound.SendClaim") -> None:
        """The helper proved nothing was delivered: the request may be claimed again later."""
        with self._connection:
            self._connection.execute("UPDATE send_tokens SET state='authorized', claimed=NULL WHERE token=? AND state='claimed'",
                                     (claim.token,))

    def unconfirmed_claims(self, older_than: int) -> list[str]:
        """Messages whose request was claimed but that never came back from the network."""
        return [row[0] for row in self._connection.execute(
            """SELECT DISTINCT m.uid FROM messages m JOIN send_tokens t ON t.message_uid=m.uid AND t.kind='send'
                WHERE m.direction='outgoing' AND m.state IN ('queued','sending') AND m.transport_id IS NULL
                  AND t.state='claimed' AND t.claimed<=?
                  AND NOT EXISTS (SELECT 1 FROM send_tokens a WHERE a.message_uid=m.uid AND a.state='authorized')""",
            (int(time.time()) - older_than,))]

    def conversation_phone(self, conversation: str) -> str:
        """The other person's phone number in a one-to-one conversation, or ""."""
        row = self._connection.execute("SELECT kind, participants FROM conversations WHERE id=?", (conversation,)).fetchone()
        if not row or row[0] != "direct":
            return ""
        try:
            people = [p for p in json.loads(row[1]) if isinstance(p, dict)]
        except ValueError:
            return ""
        phones = {str(p.get("phone") or "").strip() for p in people} - {""}
        return phones.pop() if len(phones) == 1 else ""

    def known_conversation(self, conversation: str) -> bool:
        return self._connection.execute("SELECT 1 FROM conversations WHERE id=?", (conversation,)).fetchone() is not None

    def move_conversation(self, old: str, new: str) -> None:
        """A conversation started with a phone number gets the network's id once it exists."""
        old, new = self.canonical_address(old), self.canonical_address(new)
        if old == new:
            return
        with self._connection:
            self._connection.execute("INSERT INTO conversation_aliases(alias,canonical) VALUES(?,?) "
                "ON CONFLICT(alias) DO UPDATE SET canonical=excluded.canonical", (old, new))
            for table, column in (("messages", "address"), ("drafts", "address"), ("contacts", "address"),
                                  ("message_attachments", "draft_address"), ("draft_quotes", "address")):
                self._connection.execute(f"UPDATE OR IGNORE {table} SET {column}=? WHERE {column}=?", (new, old))

    def resolve_sent_conversation(self, uid: str, original_address: str):
        """Preserve a next draft when this outgoing record acquires its real id.

        The target comes from the stored outgoing UID, never a caller-supplied
        destination. No delivery request, token, attachment bytes or host path
        crosses this operation.
        """
        record = self.message(uid)
        original = self.canonical_address(original_address)
        if record.direction != "outgoing":
            raise ValueError("Only an outgoing message can resolve its conversation")
        target = record.address
        if target != original:
            alias = self._connection.execute("SELECT canonical FROM conversation_aliases WHERE alias=?", (original,)).fetchone()
            if alias is None or alias[0] != target:
                raise ValueError("This message does not belong to the original conversation")
            with self._connection:
                old = self._connection.execute("SELECT body,updated FROM drafts WHERE address=?", (original,)).fetchone()
                new = self._connection.execute("SELECT body,updated FROM drafts WHERE address=?", (target,)).fetchone()
                old_wins = old is not None and (new is None or old["updated"] >= new["updated"])
                if old_wins:
                    self._connection.execute("INSERT INTO drafts(address,body,updated) VALUES(?,?,?) "
                        "ON CONFLICT(address) DO UPDATE SET body=excluded.body,updated=excluded.updated",
                        (target, old["body"], old["updated"]))
                quote = self._connection.execute("SELECT target_uid FROM draft_quotes WHERE address=?", (original,)).fetchone()
                if quote is not None and (old_wins or new is None):
                    self._connection.execute("DELETE FROM draft_quotes WHERE address=?", (target,))
                    if quote is not None:
                        self._connection.execute("INSERT INTO draft_quotes(address,target_uid) VALUES(?,?)", (target, quote[0]))
                self._connection.execute("DELETE FROM drafts WHERE address=?", (original,))
                self._connection.execute("DELETE FROM draft_quotes WHERE address=?", (original,))
                self.move_conversation(original, target)
        return next((thread for thread in self.threads() if thread.address == target), None)

    def set_attachment_type(self, uid: str, content_type: str) -> None:
        content_type = content_type.split(";", 1)[0].strip().lower()
        if re.fullmatch(r"[a-z]+/[a-z0-9.+-]{1,100}", content_type):
            self._connection.execute("UPDATE message_attachments SET content_type=? WHERE uid=?", (content_type, uid))
            self._connection.commit()

    def repair_attachment_types(self) -> int:
        """Attachments saved before helpers named files by type are recognised by their first bytes."""
        repaired = 0
        for row in self._connection.execute(
                "SELECT uid, name, content_type, size, storage_key FROM message_attachments WHERE content_type='application/octet-stream'").fetchall():
            try:
                with self.attachment_path(AttachmentRecord(*row)).open("rb") as stream:
                    head = stream.read(32)
            except OSError:
                continue
            sniffed = sniff_image(head)
            if sniffed:
                self.set_attachment_type(row[0], sniffed)
                repaired += 1
        return repaired

    # Network attachments ───────────────────────────────────────────────────

    def _adopt_legacy_media_text(self) -> int:
        """Pictures earlier helpers replaced with a sentence become attachments to fetch again."""
        rows = self._connection.execute(
            "SELECT uid, body FROM messages WHERE transport_id LIKE 'gmessages:%' AND body LIKE '%on your phone.%'").fetchall()
        adopted = 0
        now = int(time.time())
        with self._connection:
            for uid, body in rows:
                found = list(LEGACY_MEDIA_TEXT.finditer(body))
                if not found:
                    continue
                for index, match in enumerate(found):
                    too_large = match.group(2) is not None
                    self._connection.execute(
                        """INSERT OR IGNORE INTO account_media(message_uid, part, mime, size, state, error, retryable, updated)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (uid, f"legacy-{index}", LEGACY_MEDIA_MIME[match.group(1)],
                         int(match.group(2) or 0) * 1024 * 1024, "failed" if too_large else "pending",
                         "too_large" if too_large else "legacy", 0 if too_large else 1, now))
                rest = "\n".join(line for line in LEGACY_MEDIA_TEXT.sub("", body).splitlines() if line.strip())
                self._connection.execute("UPDATE messages SET body=? WHERE uid=?", (rest.strip(), uid))
                adopted += 1
        return adopted

    def add_media_message(self, address: str, text: str, *, direction: str, state: str, timestamp: int,
                          transport_id: str) -> str:
        """A network message whose only content is attachments still on their way."""
        if text.strip():
            return self.add(address, text, direction=direction, state=state, timestamp=timestamp,
                            transport_id=transport_id).uid
        uid = uuid.uuid4().hex
        with self._connection:
            self._connection.execute(
                "INSERT INTO messages(uid,address,body,timestamp,direction,state,transport_id) VALUES(?,?,?,?,?,?,?)",
                (uid, self.canonical_address(address), "", timestamp, direction, state, transport_id))
        return uid

    def _add_media_wait_column(self) -> None:
        """Stores from before the wait was bounded count an existing wait from its last report."""
        columns = {row[1] for row in self._connection.execute("PRAGMA table_info(account_media)")}
        if "waiting_since" in columns:
            return
        try:
            with self._connection:
                self._connection.execute("ALTER TABLE account_media ADD COLUMN waiting_since INTEGER NOT NULL DEFAULT 0")
                self._connection.execute(
                    f"UPDATE account_media SET waiting_since=updated WHERE state!='done' AND error IN ({','.join('?' * len(MEDIA_WAITING_ERRORS))})",
                    MEDIA_WAITING_ERRORS)
        except sqlite3.OperationalError as error:
            if "duplicate column" not in str(error):  # the window and the agent both open this store
                raise

    @staticmethod
    def _waiting_overdue(state: str, error: str, since: int, now: int) -> bool:
        return (state in ("pending", "downloading") and error in MEDIA_WAITING_ERRORS and since > 0
                and now - since >= MEDIA_WAITING_BOUND)

    def _media_row(self, row, *, raw: bool = False) -> MediaPart:
        preview = None
        if row["preview_key"] and re.fullmatch(r"[0-9a-f]{64}", row["preview_key"]):
            preview = self.path.parent / "previews" / row["preview_key"]
        state, error = row["state"], row["error"]
        since = row["waiting_since"] if "waiting_since" in row.keys() else 0
        if not raw and self._waiting_overdue(state, error, since, int(time.time())):
            # Shown as resolved the moment the bound passes, whether or not
            # the agent has written that yet: never a spinner past it.
            state, error = "failed", "no_full_size"
        return MediaPart(row["message_uid"], row["part"], row["mime"], row["name"], row["size"], state, error,
                         bool(row["retryable"]), row["attempts"], row["next_attempt"], row["attachment_uid"], preview, since)

    def media_parts(self, uid: str) -> tuple[MediaPart, ...]:
        rows = self._connection.execute("SELECT * FROM account_media WHERE message_uid=? ORDER BY length(part), part", (uid,))
        return tuple(self._media_row(row) for row in rows)

    def media_part(self, uid: str, part: str, *, raw: bool = False) -> MediaPart | None:
        row = self._connection.execute("SELECT * FROM account_media WHERE message_uid=? AND part=?", (uid, part)).fetchone()
        return self._media_row(row, raw=raw) if row else None

    def expire_waiting_media(self, *, now: int | None = None) -> int:
        """Parts past the bound with no word from the helper become a plain, retryable failure."""
        now = int(time.time() if now is None else now)
        waiting = ",".join("?" * len(MEDIA_WAITING_ERRORS))
        with self._connection:
            return self._connection.execute(
                f"""UPDATE account_media SET state='failed', error='no_full_size', retryable=1, attempts=attempts+1,
                      next_attempt=?, updated=?
                    WHERE state IN ('pending','downloading') AND error IN ({waiting})
                      AND waiting_since>0 AND waiting_since<=?""",
                (now + MEDIA_WAITING_REASK, now, *MEDIA_WAITING_ERRORS, now - MEDIA_WAITING_BOUND)).rowcount

    def restart_media_wait(self, uid: str, part: str, *, now: int | None = None) -> None:
        """A person's tap: a short fresh wait, bounded like the first one."""
        now = int(time.time() if now is None else now)
        waiting = ",".join("?" * len(MEDIA_WAITING_ERRORS))
        with self._connection:
            self._connection.execute(
                f"""UPDATE account_media SET waiting_since=?, state='pending', updated=?
                    WHERE message_uid=? AND part=? AND state!='done' AND error IN ({waiting})""",
                (now - MEDIA_WAITING_BOUND + MEDIA_TAP_WINDOW, now, uid, part, *MEDIA_WAITING_ERRORS))

    def set_media(self, uid: str, part: str, *, state: str, mime: str = "", name: str = "", size: int = 0,
                  error: str = "", retryable: bool = True, now: int | None = None) -> tuple[str, str]:
        """Records a part's state; returns (previous, current). A part that is done stays done."""
        if state not in MEDIA_STATES or state == "done" or not MEDIA_PART.fullmatch(part):
            raise ValueError("That isn't an attachment state.")
        now = int(time.time() if now is None else now)
        error = error if MEDIA_ERROR.fullmatch(error or "") else ("" if not error else "error")
        current = self.media_part(uid, part, raw=True)
        if current is not None and current.state == "done":
            return "done", "done"
        waiting_since = 0
        if error in MEDIA_WAITING_ERRORS:
            # A wait is counted from when it began, however often the helper
            # starts its own count over.
            waiting_since = (current.waiting_since if current is not None and current.error in MEDIA_WAITING_ERRORS
                             and current.waiting_since else now)
            if self._waiting_overdue(state, error, waiting_since, now):
                state, error, retryable = "failed", "no_full_size", True
        attempts = current.attempts if current else 0
        next_attempt = current.next_attempt if current else 0
        if state == "failed" and (current is None or current.state != "failed"):
            attempts += 1
            next_attempt = now + media_retry_delay(attempts, error) if retryable else 0
        mime = mime if re.fullmatch(r"[A-Za-z0-9.+*-]{1,64}/[A-Za-z0-9.+*-]{1,100}", mime or "") else (current.mime if current else "application/octet-stream")
        name = Path(str(name or (current.name if current else ""))).name[:255]
        size = int(size or (current.size if current else 0))
        with self._connection:
            self._connection.execute(
                """INSERT INTO account_media(message_uid, part, mime, name, size, state, error, retryable, attempts, next_attempt,
                                             updated, waiting_since)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(message_uid, part) DO UPDATE SET mime=excluded.mime, name=excluded.name, size=excluded.size,
                     state=excluded.state, error=excluded.error, retryable=excluded.retryable, attempts=excluded.attempts,
                     next_attempt=excluded.next_attempt, updated=excluded.updated, waiting_since=excluded.waiting_since""",
                (uid, part, mime, name, max(0, size), state, error, int(retryable), attempts, next_attempt, now, waiting_since))
        return (current.state if current else ""), state

    def _import_blob(self, source: Path, directory: str, limit: int) -> tuple[str, int]:
        blobs = self.path.parent / directory
        blobs.mkdir(mode=0o700, exist_ok=True)
        descriptor = os.open(source, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        fd, temporary = tempfile.mkstemp(prefix=".import-", dir=blobs)
        try:
            digest, size = hashlib.sha256(), 0
            with os.fdopen(descriptor, "rb") as reader, os.fdopen(fd, "wb") as writer:
                info = os.fstat(reader.fileno())
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
                    raise ValueError("not a nonempty regular file within the limit")
                while chunk := reader.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise ValueError("the file grew past the limit")
                    digest.update(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            key = digest.hexdigest()
            os.replace(temporary, blobs / key)
            temporary = None
            return key, size
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def complete_media(self, uid: str, part: str, source: Path, *, name: str, mime: str) -> AttachmentRecord | None:
        """The part's file arrived: it becomes one of the message's attachments. Returns None if it already was."""
        current = self.media_part(uid, part)
        replaced = None
        if current is not None and current.state == "done" and current.attachment_uid:
            # Done stays done, except that the full file replaces a preview an
            # earlier helper saved as the file (a kilobyte for a photo).
            row = self._connection.execute("SELECT * FROM message_attachments WHERE uid=?", (current.attachment_uid,)).fetchone()
            try:
                incoming_size = Path(source).stat().st_size
            except OSError:
                return None
            if row is None or incoming_size <= row["size"]:
                return None
            replaced = self._attachment(row)
        key, size = self._import_blob(Path(source), "attachments", MAX_ATTACHMENT_BYTES)
        filename = Path(name or Path(source).name).name[:255]
        content_type = (mime or "").split(";", 1)[0].strip().lower()
        if not re.fullmatch(r"[a-z]+/[a-z0-9.+-]{1,100}", content_type):
            content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if content_type == "application/octet-stream":
            with Path(source).open("rb") as stream:
                content_type = sniff_image(stream.read(32)) or content_type
        now = int(time.time())
        # Stored before attachments had states: the same file is already this message's.
        same = self._connection.execute("SELECT * FROM message_attachments WHERE message_uid=? AND storage_key=?",
                                        (uid, key)).fetchone()
        record = self._attachment(same) if same else AttachmentRecord(
            replaced.uid if replaced else uuid.uuid4().hex, filename, content_type, size, key)
        with self._connection:
            if replaced is not None and not same:
                self._connection.execute(
                    "UPDATE message_attachments SET name=?, content_type=?, size=?, storage_key=? WHERE uid=?",
                    (record.name, record.content_type, record.size, key, replaced.uid))
            elif not same:
                self._connection.execute("INSERT INTO message_attachments VALUES(?,?,NULL,?,?,?,?)",
                                         (record.uid, uid, record.name, record.content_type, record.size, key))
            self._connection.execute(
                """INSERT INTO account_media(message_uid, part, mime, name, size, state, error, retryable, attempts, updated, attachment_uid)
                   VALUES(?,?,?,?,?,'done','',1,0,?,?)
                   ON CONFLICT(message_uid, part) DO UPDATE SET mime=excluded.mime, name=excluded.name, size=excluded.size,
                     state='done', error='', next_attempt=0, updated=excluded.updated, attachment_uid=excluded.attachment_uid""",
                (uid, part, record.content_type, record.name, record.size, now, record.uid))
        if replaced is not None and not same:
            self._collect_blobs([replaced.storage_key])
        return None if same else record

    def suspect_previews(self, limit: int = 50) -> list[tuple[str, str, str, str]]:
        """Pictures marked done whose file is a preview-sized image, asked about once each.

        luma-messages-bridges before 0.7 saved a message's kilobyte preview as
        the picture and reported it done; the fixed helper fetches the full file
        when asked, and complete_media() puts it in place of the preview.
        """
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS media_upgrade_asked(message_uid TEXT NOT NULL, part TEXT NOT NULL, PRIMARY KEY(message_uid, part))")
        rows = self._connection.execute(
            """SELECT m.uid, m.address, m.transport_id, a.part FROM account_media a
                 JOIN messages m ON m.uid=a.message_uid JOIN message_attachments f ON f.uid=a.attachment_uid
                WHERE a.state='done' AND f.content_type LIKE 'image/%' AND f.size < 16384 AND m.transport_id LIKE '%:%'
                  AND NOT EXISTS (SELECT 1 FROM media_upgrade_asked u WHERE u.message_uid=a.message_uid AND u.part=a.part)
                ORDER BY m.timestamp DESC LIMIT ?""", (limit,)).fetchall()
        with self._connection:
            self._connection.executemany("INSERT OR IGNORE INTO media_upgrade_asked VALUES(?,?)",
                                         [(uid, part) for uid, _address, _transport, part in rows])
        return [(uid, address, transport.split(":", 1)[1], part) for uid, address, transport, part in rows]

    def set_media_preview(self, uid: str, part: str, source: Path) -> bool:
        current = self.media_part(uid, part)
        if current is None or current.preview is not None:
            return False
        key, _size = self._import_blob(Path(source), "previews", 4 * 1024 * 1024)
        with self._connection:
            self._connection.execute("UPDATE account_media SET preview_key=? WHERE message_uid=? AND part=?", (key, uid, part))
        return True

    def drop_media_placeholders(self, uid: str) -> None:
        """A message's real parts replace the stand-ins for it (an MMS the phone was fetching, or legacy text)."""
        with self._connection:
            self._connection.execute(
                "DELETE FROM account_media WHERE message_uid=? AND (part='mms' OR part LIKE 'legacy-%') AND state!='done'", (uid,))

    def fail_unfinished_media(self, uid: str, error: str, *, retryable: bool) -> None:
        for part in self.media_parts(uid):
            if part.state != "done":
                self.set_media(uid, part.part, state="failed", error=error, retryable=retryable)

    def media_due(self, *, now: int | None = None, address: str | None = None, immediate: bool = False,
                  reconnected: bool = False, limit: int = 100) -> list[tuple[str, str, str, str]]:
        """Unfinished parts to ask the helper for: (message uid, conversation, network message id, part).

        Without ``immediate``, a failed part waits for its retry time, unless
        ``reconnected`` and the connection was what failed it.
        """
        now = int(time.time() if now is None else now)
        values: list = []
        if immediate:
            condition = "a.state != 'done'"
        else:
            waiting = ",".join("?" * len(MEDIA_WAITING_ERRORS))
            # Reconnecting retries at once what a working connection fixes, and
            # a picture that was only waiting on the phone: it may have woken.
            connection = ""
            if reconnected:
                connection = (f" OR a.error IN ({','.join('?' * len(CONNECTION_ERRORS))})"
                              f" OR a.error IN ({waiting})")
            # A file Google no longer holds is asked for again only by a tap:
            # the helper has already asked the phone once for it this run. A
            # picture whose file the phone never published is re-read quietly
            # while it is young enough that it may still turn up.
            condition = (f"((a.state IN ('pending','downloading') AND a.error!='gone' AND a.updated<=?)"
                         f" OR (a.state='failed' AND a.retryable=1 AND a.error!='gone'"
                         f" AND (a.next_attempt<=?{connection})"
                         f" AND (a.error NOT IN ({waiting}) OR m.timestamp>?)))")
            # Reconnecting asks for everything on its way; otherwise a part the
            # helper reported on recently is left to the helper's own backoff.
            values.append(now if reconnected else now - MEDIA_PENDING_REASK)
            values.append(now)
            if reconnected:
                values.extend(CONNECTION_ERRORS)
                values.extend(MEDIA_WAITING_ERRORS)
            values.extend(MEDIA_WAITING_ERRORS)
            values.append(now - MEDIA_WAITING_WINDOW)
        if address is not None:
            condition += " AND m.address=?"
            values.append(address)
        rows = self._connection.execute(
            f"""SELECT m.uid, m.address, m.transport_id, a.part FROM account_media a JOIN messages m ON m.uid=a.message_uid
                WHERE {condition} AND m.transport_id LIKE '%:%' ORDER BY m.timestamp DESC LIMIT ?""", (*values, limit)).fetchall()
        return [(uid, address, transport.split(":", 1)[1], part) for uid, address, transport, part in rows]

    def next_media_retry(self) -> int | None:
        """When the next attachment pass is due, or None while nothing is waiting.

        A part still on its way counts. Only failed parts used to, so an
        attachment left pending was asked for once and then had no timer at
        all: it waited for a message update that might never arrive.
        """
        now = int(time.time())
        waiting = ",".join("?" * len(MEDIA_WAITING_ERRORS))
        row = self._connection.execute(
            f"""SELECT MIN(due) FROM (
                 SELECT a.next_attempt AS due FROM account_media a JOIN messages m ON m.uid=a.message_uid
                  WHERE a.state='failed' AND a.retryable=1
                    AND (a.error NOT IN ({waiting}) OR m.timestamp>?)
                 UNION ALL
                 SELECT updated + ? AS due FROM account_media WHERE state IN ('pending','downloading'))""",
            (*MEDIA_WAITING_ERRORS, now - MEDIA_WAITING_WINDOW, MEDIA_PENDING_REASK)).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def mark_media_requested(self, uid: str, part: str) -> None:
        with self._connection:
            self._connection.execute(
                "UPDATE account_media SET state='downloading', updated=? WHERE message_uid=? AND part=? AND state!='done'",
                (int(time.time()), uid, part))

    def media_counts(self) -> dict[str, int]:
        return dict(self._connection.execute("SELECT state, COUNT(*) FROM account_media GROUP BY state").fetchall())

    def collect_previews(self) -> int:
        directory = self.path.parent / "previews"
        if not directory.is_dir():
            return 0
        used = {row[0] for row in self._connection.execute("SELECT preview_key FROM account_media WHERE preview_key IS NOT NULL")}
        removed = 0
        for item in directory.iterdir():
            if re.fullmatch(r"[0-9a-f]{64}", item.name) and item.name not in used:
                item.unlink(missing_ok=True)
                removed += 1
        return removed

    def threads(self, query: str = ""):
        records = super().threads(query)
        if not any(not record.preview for record in records):
            return records
        from dataclasses import replace
        patched = []
        for record in records:
            if not record.preview:
                row = self._connection.execute(
                    """SELECT a.mime, a.part FROM messages m JOIN account_media a ON a.message_uid=m.uid
                       WHERE m.address=? ORDER BY m.timestamp DESC, m.rowid DESC LIMIT 1""", (record.address,)).fetchone()
                if row:
                    noun = MediaPart("", row[1], row[0], "", 0, "", "", True, 0, 0, None, None).noun
                    record = replace(record, preview=noun[0].upper() + noun[1:])
            patched.append(record)
        return tuple(patched)

    def correct_to_outgoing(self, uid: str, state: str) -> bool:
        """A message stored as someone else's that this account wrote on another device.

        Helpers before luma-messages-bridges 0.7 reported messages sent from the
        phone as incoming, so they counted as unread. Seeing one again fixes it.
        """
        cursor = self._connection.execute(
            "UPDATE messages SET direction='outgoing', state=? WHERE uid=? AND direction='incoming'",
            ("failed" if state == "failed" else "sent", uid))
        if cursor.rowcount:
            self._connection.execute("DELETE FROM message_senders WHERE message_uid=?", (uid,))
        self._connection.commit()
        return bool(cursor.rowcount)

    def mark_message_read(self, uid: str) -> bool:
        """An incoming message read somewhere else, on the phone or another client."""
        cursor = self._connection.execute(
            "UPDATE messages SET state='read' WHERE uid=? AND direction='incoming' AND state='received'", (uid,))
        self._connection.commit()
        return bool(cursor.rowcount)

    def mark_read_through(self, address: str, timestamp: int) -> int:
        """The network says a conversation has nothing unread as of its latest message."""
        cursor = self._connection.execute(
            """UPDATE messages SET state='read' WHERE address=? AND direction='incoming' AND state='received'
               AND timestamp<=?""", (self.canonical_address(address), int(timestamp)))
        self._connection.commit()
        return cursor.rowcount

    def set_receipt(self, uid: str, receipt: str) -> None:
        """Delivered or read, for a message sent from this account; read is never taken back."""
        if receipt not in {"delivered", "read"}:
            return
        current = self.receipt(uid)
        if current == receipt or current == "read":
            return
        self._connection.execute("INSERT OR REPLACE INTO message_receipts(message_uid, receipt) VALUES(?,?)",
                                 (uid, receipt))
        self._connection.commit()

    def receipt(self, uid: str) -> str:
        row = self._connection.execute("SELECT receipt FROM message_receipts WHERE message_uid=?", (uid,)).fetchone()
        return row[0] if row else ""

    def replace_reactions(self, uid: str, reactions: list) -> bool:
        """The network's complete set of reactions on a message; returns whether it changed.

        A network reports every reaction each time it reports the message, so
        what it sends replaces what is stored, including reactions removed.
        """
        chosen: dict[str, str] = {}
        for item in reactions[:200]:
            if not isinstance(item, dict):
                continue
            sender, emoji = str(item.get("sender") or "")[:256], str(item.get("emoji") or "")
            # Stored as one reaction per person, as RCS shows them.
            if sender and emoji and len(emoji) <= 16 and emoji.isprintable() and sender not in chosen:
                chosen[sender] = emoji
        wanted = {(sender, emoji, "sent" if sender == "self" else "received") for sender, emoji in chosen.items()}
        current = {tuple(row) for row in self._connection.execute(
            "SELECT sender,emoji,state FROM message_reactions WHERE message_uid=? AND state IN ('sent','received')",
            (uid,)).fetchall()}
        if current == wanted:
            return False
        with self._connection:
            self._connection.execute(
                "DELETE FROM message_reactions WHERE message_uid=? AND state IN ('sent','received')", (uid,))
            self._connection.executemany("INSERT OR REPLACE INTO message_reactions VALUES(?,?,?,?)",
                                         [(uid, *row) for row in sorted(wanted)])
        return True

    def set_own_reaction(self, uid: str, emoji: str | None, *, state: str) -> None:
        """This account's one reaction to a message, or none; queued until the network confirms it."""
        if state not in {"queued", "sent", "failed"} or (emoji is not None and not (0 < len(emoji) <= 16 and emoji.isprintable())):
            raise ValueError("That isn't a reaction.")
        with self._connection:
            self._connection.execute("DELETE FROM message_reactions WHERE message_uid=? AND sender='self'", (uid,))
            if emoji:
                self._connection.execute("INSERT INTO message_reactions VALUES(?,?,?,?)", (uid, "self", emoji, state))

    def own_reaction(self, uid: str) -> str:
        row = self._connection.execute(
            "SELECT emoji FROM message_reactions WHERE message_uid=? AND sender='self' AND state='sent'", (uid,)).fetchone()
        return row[0] if row else ""

    def set_sender(self, uid: str, sender_id: str, name: str) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO message_senders(message_uid, sender_id, name) VALUES(?,?,?)",
            (uid, sender_id[:256], name[:256]))
        self._connection.commit()


@dataclass
class Account:
    id: str
    network: str
    name: str = ""
    handle: str = ""
    created: int = field(default_factory=lambda: int(time.time()))
    pending: bool = True

    @property
    def label(self) -> str:
        return self.name or network(self.network).name


class Accounts:
    """Account directories: ``<root>/<id>/account.json`` plus the helper's own files."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or accounts_directory())

    def directory(self, account_id: str) -> Path:
        if not ACCOUNT_ID.fullmatch(account_id):
            raise ValueError("invalid account id")
        return self.root / account_id

    def list(self, *, include_pending: bool = False) -> list[Account]:
        found = []
        if not self.root.is_dir():
            return found
        for child in sorted(self.root.iterdir()):
            if not ACCOUNT_ID.fullmatch(child.name) or child.is_symlink() or not child.is_dir():
                continue
            try:
                data = json.loads((child / "account.json").read_text())
                account = Account(**{key: data[key] for key in ("id", "network", "name", "handle", "created", "pending")})
                network(account.network)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if account.id != child.name:
                continue
            if account.pending and time.time() - account.created > PENDING_LIFETIME:
                shutil.rmtree(child, ignore_errors=True)  # an abandoned sign-in
                continue
            if include_pending or not account.pending:
                found.append(account)
        return found

    def create(self, network_id: str) -> Account:
        network(network_id)
        account = Account(f"{network_id}-{token.token_hex(8)}", network_id)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.directory(account.id).mkdir(mode=0o700)
        self.save(account)
        return account

    def save(self, account: Account) -> None:
        directory = self.directory(account.id)
        fd, temporary = tempfile.mkstemp(prefix=".account-", dir=directory)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(asdict(account), stream)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, directory / "account.json")
        finally:
            Path(temporary).unlink(missing_ok=True)

    def remove(self, account_id: str) -> None:
        directory = self.directory(account_id)
        if directory.is_symlink():
            raise ValueError("account directory is a link")
        shutil.rmtree(directory, ignore_errors=True)


class KeyringLocked(Exception):
    pass


class AccountSecrets:
    """Session material in the Secret Service only (Luma never falls back to a file)."""

    def __init__(self) -> None:
        import gi
        gi.require_version("Secret", "1")
        from gi.repository import Secret
        self.Secret = Secret
        self.schema = Secret.Schema.new("org.projectluma.Messages.Account", Secret.SchemaFlags.NONE,
                                        {"account": Secret.SchemaAttributeType.STRING})

    def get(self, account_id: str) -> str | None:
        try:
            return self.Secret.password_lookup_sync(self.schema, {"account": account_id}, None)
        except Exception as error:
            raise KeyringLocked(str(error)) from None

    def set(self, account_id: str, label: str, value: str) -> None:
        try:
            self.Secret.password_store_sync(self.schema, {"account": account_id}, self.Secret.COLLECTION_DEFAULT,
                                            f"Messages: {label}", value, None)
        except Exception as error:
            raise KeyringLocked(str(error)) from None

    def delete(self, account_id: str) -> None:
        try:
            self.Secret.password_clear_sync(self.schema, {"account": account_id}, None)
        except Exception:
            pass


# ── Helper process ──────────────────────────────────────────────────────────

class BridgeError(Exception):
    def __init__(self, code: str, message: str = "", retryable: bool = False) -> None:
        super().__init__(message or code)
        self.code, self.retryable = code, retryable


class BridgeProcess:
    """One helper process: numbered commands, results matched by id, events to a callback."""

    def __init__(self, executable: Path, data_dir: Path, on_event, *, on_exit=lambda code: None) -> None:
        self.executable, self.data_dir = Path(executable), Path(data_dir)
        self.on_event, self.on_exit = on_event, on_exit
        self._pending: dict[str, Future] = {}
        self._lock = threading.Lock()
        self._next = 0
        self._process: subprocess.Popen | None = None

    def start(self) -> None:
        environment = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TZ", "HOME", "XDG_RUNTIME_DIR",
                                                        "LUMA_MESSAGES_OUTBOUND") if key in os.environ}
        self._process = subprocess.Popen(
            [str(self.executable), "--data-dir", str(self.data_dir)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=self.data_dir, env=environment, start_new_session=True)
        threading.Thread(target=self._read, daemon=True, name="messages-bridge-read").start()
        threading.Thread(target=self._drain_log, daemon=True, name="messages-bridge-log").start()

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def request(self, command: str, args: dict | None = None, *, timeout: float = 60) -> dict:
        if command in outbound.DELIVERY_COMMANDS:
            # Deliveries go through deliver() with a person's claimed request, never through here.
            outbound.trip(self.data_dir.parent, f"{command} requested without a person's claimed request")
            raise BridgeError("blocked", "A delivery without a person's request was refused.")
        return self._call(command, args, timeout)

    def deliver(self, command: str, args: dict, claim: "outbound.SendClaim", *, timeout: float = 120) -> dict:
        """The only way a delivery command reaches a helper: a person's request, claimed once."""
        if command not in outbound.DELIVERY_COMMANDS or not isinstance(claim, outbound.SendClaim) or not claim.valid:
            outbound.trip(self.data_dir.parent, f"{command} delivered without a valid claim")
            raise BridgeError("blocked", "A delivery without a person's request was refused.")
        reason = outbound.disabled_reason(self.data_dir.parent)
        if reason:
            raise BridgeError("outbound_disabled", reason)
        return self._call(command, {**args, "user_token": claim.token}, timeout)

    def _call(self, command: str, args: dict | None, timeout: float) -> dict:
        if not self.running:
            raise BridgeError("not_running", "The helper isn't running.", True)
        with self._lock:
            self._next += 1
            ident = str(self._next)
            future: Future = Future()
            self._pending[ident] = future
            line = json.dumps({"id": ident, "cmd": command, "args": args or {}}, separators=(",", ":")) + "\n"
            try:
                self._process.stdin.write(line.encode())
                self._process.stdin.flush()
            except (BrokenPipeError, OSError):
                self._pending.pop(ident, None)
                raise BridgeError("not_running", "The helper stopped.", True) from None
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            with self._lock:
                self._pending.pop(ident, None)
            raise BridgeError("timeout", "The helper didn't answer in time.", True) from None

    def _read(self) -> None:
        stream = self._process.stdout
        while True:
            line = stream.readline(MAX_LINE + 1)
            if not line:
                break
            if len(line) > MAX_LINE:
                log.warning("messages helper sent an oversized line; stopping it")
                self._process.kill()
                break
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if "id" in message:
                with self._lock:
                    future = self._pending.pop(str(message["id"]), None)
                if future is None:
                    continue
                if message.get("ok"):
                    future.set_result(message.get("result") if isinstance(message.get("result"), dict) else {})
                else:
                    error = message.get("error") if isinstance(message.get("error"), dict) else {}
                    future.set_exception(BridgeError(str(error.get("code", "error")), str(error.get("message", "")), bool(error.get("retryable"))))
            elif isinstance(message.get("event"), str):
                data = message.get("data") if isinstance(message.get("data"), dict) else {}
                try:
                    self.on_event(message["event"], data)
                except Exception:
                    log.exception("messages helper event handler failed")
        code = self._process.wait()
        self._close_pipes()
        with self._lock:
            pending, self._pending = self._pending, {}
        for future in pending.values():
            # The helper may have acted on a request it had read: not "not_running", which proves nothing was done.
            future.set_exception(BridgeError("helper_exited", "The helper stopped before answering.", True))
        self.on_exit(code)

    def _drain_log(self) -> None:
        # Helpers may print protocol details on stderr; only a bounded prefix reaches the journal.
        for line in self._process.stderr:
            text = line[:300].decode(errors="replace").rstrip()
            # Helpers log JSON lines; their warnings and errors are what explains a stuck account.
            important = '"level":"warn"' in text or '"level":"error"' in text or '"level":"fatal"' in text
            log.log(logging.WARNING if important else logging.DEBUG, "helper %s: %s", self.executable.name, text)

    def _close_pipes(self) -> None:
        for stream in (self._process.stdin, self._process.stdout, self._process.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    def stop(self) -> None:
        if self._process is None:
            return
        if not self.running:
            self._close_pipes()
            return
        try:
            self.request("shutdown", timeout=3)
        except BridgeError:
            pass
        try:
            self._process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()


# ── The account as a message service ────────────────────────────────────────

STATE_TEXT = {
    "needs_login": "Sign in to {network} again in Accounts.",
    "phone_offline": "{network} can't reach your phone. Make sure it's on and connected.",
    "elsewhere": "{network} is open somewhere else.",
    "outdated": "Update Luma to keep using {network}.",
    "error": "{network} ran into a problem. Messages will try again.",
    "connecting": "Connecting to {network}…",
    "keyring": "Unlock your keyring to use {network}.",
    "missing": "{network} isn't installed on this device any more.",
}
RETRY_DELAYS = (2, 5, 15, 30, 60, 300)
# Offered when a network supports reactions but doesn't list its own.
DEFAULT_REACTIONS = ("❤️", "👍", "😂", "😮", "😢", "🙏")
# A helper that reports a problem but stays running is restarted if it has not
# recovered by then. After sleep, a network's long poll can stay broken for
# good; Messages said it would try again and never did.
RECOVERY_SECONDS = 20
# A helper still connecting after this long is restarted too. At sign-in the
# network is often not up yet, and a connection begun then can wait forever.
CONNECT_SECONDS = 60
REFETCH_SECONDS = 60
SYNC_RETRY = 30
# Media asked for at once; the rest follow on the next pass.
MEDIA_BATCH = 100
MEDIA_PASS_SECONDS = 60
# Media events about a message Messages hasn't stored yet wait this long for it.
MEDIA_WAIT_SECONDS = 600


class AccountProvider:
    """Messages' provider for one network account; the helper owns the connection."""

    def __init__(self, account: Account, accounts: Accounts, *, secrets, dispatch, helper_dir: Path | None = None,
                 process_factory=BridgeProcess, timer=None, sync_conversations: int = 50, sync_messages: int = 30) -> None:
        self.account, self.accounts, self.secrets, self.dispatch = account, accounts, secrets, dispatch
        self.network = network(account.network)
        self.peer = account.id
        self.label = account.label
        self.store_path = accounts.directory(account.id) / "messages.db"
        self.helper = (helper_dir or helper_directory()) / account.network
        self.process_factory = process_factory
        self.sync_conversations, self.sync_messages = sync_conversations, sync_messages
        self.status = {"state": "idle", "network_state": "connecting", "detail": ""}
        self.capabilities: dict = {}
        self.on_message = lambda address, name, text: None
        # Told when sending is turned off by the kill switch, with the reason.
        self.on_outbound_disabled = lambda reason: None
        self._burst = outbound.BurstGuard(accounts.root)
        self.send_timeout, self.media_send_timeout = 120, 300
        self._notify = lambda _state: None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"messages-{account.network}")
        self._process = None
        self._store: AccountStore | None = None
        self._closed = False
        self._synced = False
        self._fetched: dict[str, float] = {}
        self._media_fetched: dict[str, float] = {}
        self._media_waiting: dict[str, list[tuple[float, dict]]] = {}
        self._media_timer = None
        self._attempt = 0
        self._retry_timer = None
        self._timer = timer or (lambda delay, callback: threading.Timer(delay, callback))

    # Provider interface used by MessagesWindow
    def start(self, notify) -> None:
        self._notify = notify
        self._submit(self._launch)

    def refresh(self):
        return self._submit(lambda: None if self._process and self._process.running else self._launch())

    def retry(self) -> None:
        self._attempt = 0
        self._submit(self._launch)

    invalidate = retry

    def contacts(self):
        return ()

    def close(self, *, wait: bool = False) -> None:
        """Stops the helper. Quitting Messages never waits for it; tests may."""
        if self._closed:
            return
        self._closed = True
        for timer in (self._retry_timer, getattr(self, "_sync_timer", None), getattr(self, "_recovery_timer", None),
                      self._media_timer):
            if timer:
                timer.cancel()
        finished = self._submit(self._finish)
        self._executor.shutdown(wait=False)
        if wait:
            try:
                finished.result(timeout=8)
            except Exception:
                pass
        reader, self._ui_store = getattr(self, "_ui_store", None), None
        if reader is not None:
            reader.close()  # opened on this (UI) thread, so closed here

    def _finish(self) -> None:
        self._shutdown()
        if self._store is not None:
            self._store.close()
            self._store = None

    def sms_transport(self):
        provider = self

        class Transport:
            def inspect(self):
                state = provider.status.get("network_state")
                if state == "connected":
                    return MessagingCapability(True, "")
                text = STATE_TEXT.get(state, STATE_TEXT["connecting"])
                # Messages queue while connecting; they send as soon as the account is back.
                return MessagingCapability(state in {"connecting", "phone_offline", "error"}, text.format(network=provider.network.name))

            def snapshot(self):
                return ()

            def send(self, _address, _body):
                raise RuntimeError("account sends require a stored message")
        return Transport()

    def mms_transport(self):
        provider = self

        class Media:
            def start(self, _on_message, on_capability):
                provider._media_capability = on_capability
                provider._publish_media()

            def stop(self):
                provider._media_capability = None

            def cached_message(self, _path):
                return None

            def mark_read(self, _path):
                raise NotImplementedError

            def delete(self, _path):
                raise NotImplementedError

            def send(self, _recipients, _parts):
                raise RuntimeError("account sends require a stored message")
        return Media()

    _media_capability = None

    def _publish_media(self) -> None:
        callback = self._media_capability
        if callback is None:
            return
        allowed = bool(self.capabilities.get("media"))
        limit = int(self.capabilities.get("max_media_bytes") or 25 * 1024 * 1024)
        reason = "" if allowed else f"{self.network.name} can't send files from Luma yet."
        self.dispatch(lambda: (callback(MmsCapability(allowed, reason, "", limit, 10)), False)[1])

    # Whether this provider needs a person's token for every send (messages_outbound).
    requires_user_token = True

    def send_message(self, uid: str, user_token: str) -> dict:
        """Deliver a message a person sent; ``user_token`` is from ``AccountStore.authorize_send``."""
        return self._submit(self._send, uid, user_token).result(timeout=180)

    def retry_message(self, uid: str, user_token: str) -> dict:
        """A person confirmed Retry in Review Send; the token is a new authorization for it."""
        return self.send_message(uid, user_token)

    def outbound_disabled(self) -> str | None:
        return outbound.disabled_reason(self.accounts.root)

    def resume_outbound(self) -> None:
        outbound.resume(self.accounts.root)

    @property
    def reaction_emoji(self) -> tuple[str, ...]:
        """The reactions this network offers under a message, or none if it has no reactions."""
        if not self.capabilities.get("reactions"):
            return ()
        offered = self.capabilities.get("reaction_emoji")
        if isinstance(offered, list):
            return tuple(item for item in offered if isinstance(item, str) and 0 < len(item) <= 16)[:12]
        return DEFAULT_REACTIONS

    def react(self, uid: str, emoji: str | None) -> dict:
        """Sets this account's reaction to a message (None removes it); returns {"state": "sent" | "failed"}."""
        return self._submit(self._react, uid, emoji).result(timeout=90)

    def conversation_seen(self, address: str) -> None:
        """A message arrived in the conversation on screen: tell the sender it was read."""
        self._submit(self._mark_read, address)

    def wake(self) -> None:
        """After sleep or a network change, reconnect rather than trust a connection that may be dead."""
        now = time.monotonic()
        if self._closed or now - getattr(self, "_woken", -60.0) < 10:
            return  # sleep and the network returning often arrive together
        self._woken = now
        self._attempt = 0
        self._submit(self._launch)

    def conversation_opened(self, address: str) -> None:
        self._submit(self._mark_read, address)
        # Opening a conversation shows its latest messages and reactions, even
        # one never synced; not more than once a minute each.
        if time.monotonic() - self._fetched.get(address, -REFETCH_SECONDS) >= REFETCH_SECONDS:
            self._submit(self._fetch_conversation, address)
        # Pictures in it that haven't arrived are asked for now, whatever their retry time.
        if time.monotonic() - self._media_fetched.get(address, -REFETCH_SECONDS) >= REFETCH_SECONDS:
            self._media_fetched[address] = time.monotonic()
            self._submit(self._resume_media, address, True)

    def media_parts(self, uid: str):
        """The message's network attachments and their states, for the conversation view."""
        try:
            return self._reader().media_parts(uid)
        except Exception:
            return ()

    def retry_media(self, uid: str, part: str) -> None:
        """Tap to retry: ask the helper for this attachment now and ask the phone again."""
        self._submit(self._retry_media, uid, part)

    def sender(self, uid: str) -> str:
        try:
            return self._reader().sender(uid)
        except Exception:
            return ""

    def _reader(self) -> AccountStore:
        # The UI thread reads senders through its own connection.
        reader = getattr(self, "_ui_store", None)
        if reader is None:
            reader = self._ui_store = AccountStore(self.store_path)
        return reader

    # Worker thread
    def _submit(self, work, *args) -> Future:
        try:
            return self._executor.submit(work, *args)
        except RuntimeError:  # closed
            future: Future = Future(); future.set_result(None); return future

    def _db(self) -> AccountStore:
        if self._store is None:
            self._store = AccountStore(self.store_path)
        return self._store

    def _set_state(self, network_state: str, detail: str = "") -> None:
        mapped = {"connected": "ready", "connecting": "connecting", "phone_offline": "offline", "error": "offline",
                  "elsewhere": "attention"}.get(network_state, "unavailable")
        self.status = {"state": mapped, "network_state": network_state, "detail": detail}
        if network_state == "error" and self._process is not None:
            self._watch_recovery(RECOVERY_SECONDS)
        state = dict(self.status)
        self.dispatch(lambda: (self._notify(state), False)[1])

    def _launch(self) -> None:
        if self._closed:
            return
        if not getattr(self, "_repaired", False):
            self._repaired = True
            try:
                store = self._db()
                store.repair_attachment_types()
                store.collect_previews()
                counts = store.media_counts()
                if counts.get("pending") or counts.get("downloading") or counts.get("failed"):
                    log.warning("%s attachments not yet downloaded: pending=%d downloading=%d failed=%d",
                                self.account.network, counts.get("pending", 0), counts.get("downloading", 0),
                                counts.get("failed", 0))
            except (sqlite3.Error, OSError):
                pass
        if not os.access(self.helper, os.X_OK):
            self._set_state("missing"); return
        self._shutdown()
        self._set_state("connecting")
        process = self.process_factory(self.helper, self.accounts.directory(self.account.id), self._event_from_helper,
                                       on_exit=lambda code: self._submit(self._exited, process))
        try:
            process.start()
            hello = process.request("hello", {"protocol": PROTOCOL}, timeout=20)
            if hello.get("protocol") != PROTOCOL:
                process.stop(); self._set_state("outdated"); return
            self.capabilities = hello.get("capabilities") if isinstance(hello.get("capabilities"), dict) else {}
            self._process = process
            self._publish_media()
            try:
                session = self._session()
            except KeyringLocked:
                process.stop(); self._process = None; self._set_state("keyring"); return
            if session:
                process.request("session.load", {"session": session})
            process.request("connect", {})
            if self.status.get("network_state") == "connecting":
                self._watch_recovery(CONNECT_SECONDS)
        except (BridgeError, OSError) as error:
            log.info("%s helper did not start: %s", self.account.network, getattr(error, "code", type(error).__name__))
            process.stop()
            self._process = None
            self._set_state("error")
            self._schedule_retry()

    def _session(self) -> str | None:
        """The keyring item a helper is handed. A Luma helper seals its keys with a
        key Messages makes and stores first, so nothing is ever sealed with a key
        the keyring does not already hold."""
        session = self.secrets.get(self.account.id)
        if not session and self.network.automatic:
            session = base64.b64encode(os.urandom(32)).decode()
            self.secrets.set(self.account.id, "Luma Messages", session)
            if self.secrets.get(self.account.id) != session:
                raise KeyringLocked("the new key did not reach the keyring")
        return session

    def luma_call(self, command: str, args: dict, *, timeout: float = 60) -> dict:
        """Identity, requests, blocks, reports and safety numbers (ADR-051). Raises BridgeError."""
        if command not in LUMA_COMMANDS or command in outbound.DELIVERY_COMMANDS or not self.network.automatic:
            raise BridgeError("unsupported", "That isn't something this account does.")
        process = self._process
        if process is None or self.status.get("network_state") != "connected":
            raise BridgeError("not_connected", "Luma isn't connected right now.", True)
        result = process.request(command, args, timeout=timeout)
        created = result.get("conversation") if command == "conversation.create" else None
        if isinstance(created, dict) and created.get("id"):
            self._submit(self._conversation, created).result(timeout=30)
        return result

    def _watch_recovery(self, delay: float) -> None:
        process = self._process
        if self._closed:
            return
        pending = getattr(self, "_recovery_timer", None)
        if pending is not None:
            if pending.due <= time.monotonic() + delay:
                return
            pending.cancel()  # an error is given less time than a slow connection

        def check() -> None:
            self._recovery_timer = None
            if (not self._closed and self._process is process
                    and self.status.get("network_state") not in {"connected", "needs_login", "elsewhere"}):
                log.info("%s helper did not recover; restarting it", self.account.network)
                self._launch()
        self._recovery_timer = self._timer(delay, lambda: self._submit(check))
        self._recovery_timer.due = time.monotonic() + delay
        self._recovery_timer.daemon = True
        self._recovery_timer.start()

    def _schedule_retry(self) -> None:
        if self._closed:
            return
        delay = RETRY_DELAYS[min(self._attempt, len(RETRY_DELAYS) - 1)]
        self._attempt += 1
        self._retry_timer = self._timer(delay, lambda: self._submit(self._launch))
        self._retry_timer.daemon = True
        self._retry_timer.start()

    def _exited(self, process) -> None:
        if process is not self._process or self._closed:
            return
        self._process = None
        if self.status.get("network_state") not in {"needs_login", "outdated", "missing"}:
            self._set_state("error")
            self._schedule_retry()

    def _shutdown(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            process.stop()

    def _event_from_helper(self, name: str, data: dict) -> None:
        self._submit(self._event, name, data)

    def _event(self, name: str, data: dict) -> None:
        if self._closed:
            return
        try:
            if name == "status":
                state = str(data.get("state", "error"))
                self._set_state(state, str(data.get("detail", ""))[:500])
                if state == "connected":
                    if self._online_at is None:
                        self._online_at = int(time.time())
                    self._attempt = 0
                    if not self._synced:
                        self._synced = True
                        self._initial_sync()
                    else:
                        # Back after sleep, a network change or a restarted helper:
                        # bring in what arrived meanwhile.
                        self._catch_up()
                    self._flush_queued()
                    # After the conversation fetches queued above, so parts they report come first.
                    self._submit(self._resume_media, None, False, True)
            elif name == "session" and isinstance(data.get("session"), str):
                self.secrets.set(self.account.id, self.account.label, data["session"])
            elif name == "conversation":
                self._conversation(data)
            elif name == "message":
                self._message(data, notify=True)
            elif name == "outbound_disabled":
                reason = str(data.get("reason") or "the helper turned sending off")[:300]
                callback = self.on_outbound_disabled
                self.dispatch(lambda: (callback(reason), False)[1])
            elif name == "receipt":
                self._receipt(data)
            elif name == "media":
                self._media(data)
        except (ValueError, KeyError, TypeError, sqlite3.Error) as error:
            log.info("%s helper sent an unusable %s event: %s", self.account.network, name, type(error).__name__)
        except KeyringLocked:
            self._set_state("keyring")

    def _initial_sync(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            listed = process.request("conversations.list", {"limit": self.sync_conversations}, timeout=120)
        except BridgeError as error:
            log.warning("%s conversation sync failed (%s); retrying", self.account.network, error.code)
            self._synced = False
            self._sync_timer = self._timer(SYNC_RETRY, lambda: self._submit(self._resync))
            self._sync_timer.daemon = True
            self._sync_timer.start()
            return
        conversations = [c for c in listed.get("conversations") or [] if isinstance(c, dict)]
        for conversation in conversations:
            self._conversation(conversation)
        ordered = sorted(conversations, key=lambda c: int(c.get("updated") or 0), reverse=True)
        # Messages that came while Messages wasn't running at all (the computer
        # was off) are announced once, if this account has been connected
        # before; a first sign-in announces nothing from the history.
        announce = self._last_online()
        for conversation in ordered[:20]:
            self._fetch_conversation(conversation["id"], announce)
        # Every other conversation too, each as its own step so a message sent
        # meanwhile isn't held up behind the whole list. Conversations beyond
        # the newest twenty used to stay empty until a new message arrived.
        for conversation in ordered[20:]:
            self._submit(self._fetch_conversation, conversation["id"], announce)
        self._remember_online()

    def _last_online(self) -> int | None:
        try:
            value = json.loads((self.accounts.directory(self.account.id) / "online.json").read_text()).get("at")
        except (OSError, ValueError, AttributeError):
            return None
        return int(value) if isinstance(value, int) and value > 0 else None

    def _remember_online(self) -> None:
        """Persist when this account was last connected, for announcing what arrives before the next start."""
        self._online_at = int(time.time())
        path = self.accounts.directory(self.account.id) / "online.json"
        try:
            fd, temporary = tempfile.mkstemp(prefix=".online-", dir=path.parent)
            with os.fdopen(fd, "w") as stream:
                json.dump({"at": self._online_at}, stream)
            os.replace(temporary, path)
        except OSError:
            pass

    # When this account was last known connected; a conversation that first
    # appears after that while catching up is announced.
    _online_at: int | None = None

    def _catch_up(self) -> None:
        process = self._process
        if process is None or self.status.get("network_state") != "connected":
            return
        try:
            listed = process.request("conversations.list", {"limit": self.sync_conversations}, timeout=120)
        except BridgeError as error:
            log.warning("%s catch-up after reconnecting failed (%s)", self.account.network, error.code)
            return
        store = self._db()
        for conversation in listed.get("conversations") or []:
            if not isinstance(conversation, dict) or not isinstance(conversation.get("id"), str):
                continue
            self._conversation(conversation)
            row = store._connection.execute("SELECT MAX(timestamp) FROM messages WHERE address=?",
                                            (conversation["id"],)).fetchone()
            if int(conversation.get("updated") or 0) > int(row[0] or 0):
                self._fetched.pop(conversation["id"], None)
                # What arrived while this device was asleep or offline is new to
                # the person too: announce it, as a phone does when it reconnects.
                self._submit(self._fetch_conversation, conversation["id"], int(row[0] or self._online_at or 0) or None)
        self._remember_online()

    def _fetch_conversation(self, conversation: str, announce_after: int | None = None) -> None:
        process = self._process
        if self._closed or process is None or self.status.get("network_state") != "connected":
            return
        self._fetched[conversation] = time.monotonic()
        try:
            page = process.request("messages.list", {"conversation": conversation, "limit": self.sync_messages},
                                   timeout=120)
        except BridgeError:
            self._fetched.pop(conversation, None)
            return
        for message in page.get("messages") or []:
            if isinstance(message, dict):
                announce = announce_after is not None and int(message.get("time") or 0) > announce_after
                self._message(message, notify=announce)

    def _resync(self) -> None:
        if self._closed or self._synced or self.status.get("network_state") != "connected":
            return
        self._synced = True
        self._initial_sync()

    def _conversation(self, data: dict) -> None:
        store = self._db()
        luma = data.get("luma") if isinstance(data.get("luma"), dict) else {}
        if luma.get("hidden"):
            # Declined, blocked or deleted in Luma: the conversation leaves this device.
            if store.known_conversation(str(data.get("id", ""))):
                store.delete_thread(store.canonical_address(str(data["id"])))
            store.remember_conversation(data)
            return
        store.remember_conversation(data)
        name = str(data.get("name") or "").strip()
        if name:
            store.set_display_name(data["id"], name[:512])
        # Read on the phone (or anywhere else): nothing up to its latest message is unread here either.
        updated = data.get("updated")
        if data.get("unread") == 0 and isinstance(updated, int) and updated > 0:
            store.mark_read_through(data["id"], updated)

    def _transport(self, message_id: str) -> str:
        if not CONVERSATION_ID.fullmatch(message_id):
            raise ValueError("message id")
        return f"{self.account.network}:{message_id}"

    def _message(self, data: dict, *, notify: bool) -> None:
        store = self._db()
        conversation = store.canonical_address(str(data["conversation"]))
        transport = self._transport(str(data["id"]))
        state = str(data.get("state") or "")
        client_id = data.get("client_id")
        existing = store.uid_for_transport(transport)
        if existing is None and isinstance(client_id, str) and re.fullmatch(r"[0-9a-f]{32}", client_id):
            try:
                record = store.message(client_id)
            except KeyError:
                record = None
            if record is not None and record.direction == "outgoing":
                if record.address != conversation:
                    store.move_conversation(record.address, conversation)
                store.update_state(client_id, "failed" if state == "failed" else "sent", transport)
                store.set_receipt(client_id, state)
                return
        if data.get("deleted"):
            if existing:
                store.delete_message(existing)
            return
        parts = [part for part in (data.get("attachments") or [])[:20] if isinstance(part, dict)]
        tracked = [part for part in parts if isinstance(part.get("part"), str) and MEDIA_PART.fullmatch(part["part"])]
        if existing is not None:
            if data.get("outgoing") and store.correct_to_outgoing(existing, state):
                log.warning("%s message %s was stored as incoming; this account wrote it", self.account.network, data["id"])
            elif not data.get("outgoing") and state == "read":
                store.mark_message_read(existing)
            if data.get("outgoing") and state in {"sent", "delivered", "read", "failed"}:
                store.update_state(existing, "failed" if state == "failed" else "sent")
                store.set_receipt(existing, state)
            # A message seen again carries its current reactions, including ones
            # added since it was first stored.
            if isinstance(data.get("reactions"), list):
                store.replace_reactions(existing, data["reactions"])
            # ...and its attachments as they are now: a picture that was still
            # on its way when the message was first stored arrives this way.
            self._apply_media(store, existing, tracked)
            return
        outgoing = bool(data.get("outgoing"))
        attachments = []
        for part in parts:
            if part in tracked:
                continue  # helpers that report media states: stored below, once the message exists
            path = self._helper_file(part.get("path"))
            if path is not None:
                attachment = store.attach_file(conversation, path, name=str(part.get("name") or path.name))
                if attachment.content_type == "application/octet-stream" and isinstance(part.get("mime"), str):
                    store.set_attachment_type(attachment.uid, part["mime"])
                attachments.append(attachment.uid)
        text = str(data.get("text") or "")
        if not text.strip() and not attachments and not tracked:
            return
        message_state = ("failed" if state == "failed" else "sent") if outgoing else ("read" if state == "read" else "received")
        timestamp = int(data.get("time") or time.time())
        if attachments or text.strip():
            uid = store.add(conversation, text, direction="outgoing" if outgoing else "incoming", state=message_state,
                            timestamp=timestamp, transport_id=transport, attachment_uids=tuple(attachments)).uid
        else:
            uid = store.add_media_message(conversation, text, direction="outgoing" if outgoing else "incoming",
                                          state=message_state, timestamp=timestamp, transport_id=transport)
        self._apply_media(store, uid, tracked)
        if isinstance(data.get("reactions"), list):
            store.replace_reactions(uid, data["reactions"])
        if outgoing:
            store.set_receipt(uid, state)
        sender = data.get("sender") if isinstance(data.get("sender"), dict) else None
        if sender and not outgoing and store.conversation_kind(conversation) == "group":
            store.set_sender(uid, str(sender.get("id", "")), str(sender.get("name") or sender.get("id") or ""))
        for _received, event in self._media_waiting.pop(transport, []):
            self._media(event)
        if notify and not outgoing and state != "read":
            name = next((thread.display_name for thread in store.threads() if thread.address == conversation), conversation)
            callback = self.on_message
            summary = text.strip() or next((part.noun.capitalize() for part in store.media_parts(uid)), "Attachment")
            self.dispatch(lambda: (callback(conversation, name, summary), False)[1])

    def _helper_file(self, value) -> Path | None:
        """A file a helper hands over, only from its own media directory."""
        if not isinstance(value, str) or not value:
            return None
        path = Path(value)
        media_root = self.accounts.directory(self.account.id) / "media"
        try:
            path.resolve().relative_to(media_root.resolve())
        except ValueError:
            return None
        return path if path.is_file() and not path.is_symlink() and path.stat().st_size > 0 else None

    def _apply_media(self, store: AccountStore, uid: str, parts: list[dict]) -> None:
        real = [part for part in parts if part["part"] != "mms"]
        if real:
            store.drop_media_placeholders(uid)
        for part in parts:
            self._apply_media_part(store, uid, part)

    def _apply_media_part(self, store: AccountStore, uid: str, part: dict) -> None:
        name, state = part["part"], str(part.get("state") or "")
        mime, filename = str(part.get("mime") or ""), str(part.get("name") or "")
        size = part.get("size") if isinstance(part.get("size"), int) else 0
        previous = store.media_part(uid, name)
        preview = self._helper_file(part.get("preview"))
        if state == "done":
            path = self._helper_file(part.get("path"))
            if path is None:
                state = "failed"
                part = {**part, "error": "missing_file"}
            else:
                try:
                    record = store.complete_media(uid, name, path, name=filename or path.name, mime=mime)
                except (OSError, ValueError) as error:
                    log.warning("%s media import failed: message=%s part=%s mime=%s size=%s error=%s", self.account.network,
                                self._network_id(store, uid), name, mime, size, type(error).__name__)
                    store.set_media(uid, name, state="failed", mime=mime, name=filename, size=size, error="import")
                    return
                if record is not None and previous is not None and (previous.attempts or previous.error):
                    log.warning("%s media recovered: message=%s part=%s mime=%s size=%s after=%s rounds=%d",
                                self.account.network, self._network_id(store, uid), name, record.content_type, record.size,
                                previous.error, previous.attempts)
        if state in {"pending", "downloading", "failed"}:
            error = str(part.get("error") or "")
            retryable = part.get("retryable") is not False
            before, _after = store.set_media(uid, name, state=state, mime=mime, name=filename, size=size, error=error,
                                             retryable=retryable)
            if state == "failed" and before != "failed":
                log.warning("%s media failed: message=%s part=%s mime=%s size=%s attempt=%s error=%s retryable=%s",
                            self.account.network, self._network_id(store, uid), name, mime, size, part.get("attempt"),
                            error or "unknown", retryable)
                self._schedule_media_pass()
        if preview is not None:
            try:
                store.set_media_preview(uid, name, preview)
            except (OSError, ValueError):
                pass

    @staticmethod
    def _network_id(store: AccountStore, uid: str) -> str:
        return (store.transport_for_uid(uid) or "").split(":", 1)[-1]

    def _media(self, data: dict) -> None:
        store = self._db()
        message = str(data.get("message") or "")
        transport = self._transport(message)
        uid = store.uid_for_transport(transport)
        if uid is None:
            # The message itself may still be on its way; it takes these when it lands.
            now = time.monotonic()
            waiting = [(t, e) for t, e in self._media_waiting.get(transport, []) if now - t < MEDIA_WAIT_SECONDS]
            self._media_waiting[transport] = (waiting + [(now, data)])[-20:]
            if len(self._media_waiting) > 500:
                self._media_waiting.pop(next(iter(self._media_waiting)))
            return
        part = data.get("part")
        if not isinstance(part, str) or not part:
            # About the whole message: the helper couldn't look it up.
            error = str(data.get("error") or "not_found")
            retryable = data.get("retryable") is not False
            store.fail_unfinished_media(uid, error, retryable=retryable)
            log.warning("%s media lookup failed: message=%s error=%s retryable=%s", self.account.network, message, error, retryable)
            return
        if not MEDIA_PART.fullmatch(part):
            raise ValueError("media part")
        self._apply_media(store, uid, [data])

    def _resume_media(self, address: str | None = None, immediate: bool = False, reconnected: bool = False) -> None:
        """Ask the helper again for attachments that haven't arrived, newest first."""
        process = self._process
        if (self._closed or process is None or self.status.get("network_state") != "connected"
                or not self.capabilities.get("media_fetch")):
            return
        store = self._db()
        expired = store.expire_waiting_media()
        if expired:
            log.warning("%s %d attachments still not published by the phone after %d minutes; shown as not available yet",
                        self.account.network, expired, MEDIA_WAITING_BOUND // 60)
        due = store.media_due(address=address, immediate=immediate, reconnected=reconnected, limit=MEDIA_BATCH)
        if address is None and len(due) < MEDIA_BATCH:
            due += store.suspect_previews(MEDIA_BATCH - len(due))
        if not due:
            self._schedule_media_pass()
            return
        messages: dict[tuple[str, str, str], list[str]] = {}
        for uid, conversation, message, part in due:
            messages.setdefault((uid, conversation, message), []).append(part)
        if address is None:
            counts = store.media_counts()
            log.warning("%s asking again for %d attachments in %d messages (pending=%d downloading=%d failed=%d done=%d)",
                        self.account.network, len(due), len(messages), counts.get("pending", 0),
                        counts.get("downloading", 0), counts.get("failed", 0), counts.get("done", 0))
        for (uid, conversation, message), parts in messages.items():
            if self._closed or self._process is not process:
                return
            # Stand-ins (legacy text, an MMS) don't name a part; the helper looks the message up.
            single = parts[0] if len(parts) == 1 and parts[0].isdigit() else None
            try:
                process.request("media.fetch", {"conversation": conversation, "message": message,
                                                **({"part": single} if single else {})}, timeout=30)
            except BridgeError as error:
                log.warning("%s media.fetch failed: message=%s error=%s", self.account.network, message, error.code)
                if error.code in {"not_connected", "not_running", "timeout"}:
                    break
                continue
            for part in parts:
                store.mark_media_requested(uid, part)
        if len(due) >= MEDIA_BATCH:
            self._schedule_media_pass(MEDIA_PASS_SECONDS)
        else:
            self._schedule_media_pass()

    def _retry_media(self, uid: str, part: str) -> None:
        process = self._process
        store = self._db()
        record = store.media_part(uid, part)
        transport = store.transport_for_uid(uid)
        if record is None or record.state == "done" or not transport:
            return
        if process is None or self.status.get("network_state") != "connected" or not self.capabilities.get("media_fetch"):
            return  # retried as soon as the account is connected again
        message = store.message(uid)
        conversation = message.address
        # Nick, 2026-09-22: "received photos only". A tap on a part of a message
        # someone else sent, waiting for a file the phone hasn't published, asks
        # the phone for it once. Never for this account's own messages, never
        # from any automatic pass: this is the only place ask_phone is set.
        ask_phone = (message.direction == "incoming" and part.isdigit() and record.error in MEDIA_WAITING_ERRORS)
        store.restart_media_wait(uid, part)
        try:
            # Downloads again or looks the message up; the helper never asks the
            # phone to act. "reread" marks a person's own tap: the helper reads
            # the conversation again, even for a file Google says it no longer
            # holds. It is deliberately not the old "force", which older
            # helpers documented as asking the phone to send the file again.
            process.request("media.fetch", {"conversation": conversation, "message": transport.split(":", 1)[1],
                                            "reread": True,
                                            **({"part": part} if part.isdigit() else {}),
                                            **({"ask_phone": True} if ask_phone else {})}, timeout=30)
            if ask_phone:
                log.warning("%s asked the phone for a received picture's file: message=%s part=%s",
                            self.account.network, transport.split(":", 1)[1], part)
        except BridgeError as error:
            log.warning("%s media retry failed: message=%s part=%s error=%s", self.account.network,
                        transport.split(":", 1)[1], part, error.code)
            return
        store.mark_media_requested(uid, part)

    def _schedule_media_pass(self, delay: float | None = None) -> None:
        """One timer for the next attachment retry that is due; nothing runs while none is."""
        if self._closed:
            return
        if delay is None:
            due = self._db().next_media_retry()
            if due is None:
                return
            delay = max(MEDIA_PASS_SECONDS, due - time.time())
        pending = self._media_timer
        if pending is not None and pending.due <= time.monotonic() + delay:
            return
        if pending is not None:
            pending.cancel()
        self._media_timer = self._timer(delay, lambda: self._submit(self._media_pass))
        self._media_timer.due = time.monotonic() + delay
        self._media_timer.daemon = True
        self._media_timer.start()

    def _media_pass(self) -> None:
        self._media_timer = None
        self._resume_media()

    def _receipt(self, data: dict) -> None:
        store = self._db()
        uid = store.uid_for_transport(self._transport(str(data["message"])))
        if uid and data.get("state") in {"sent", "delivered", "read"}:
            try:
                if store.message(uid).direction == "outgoing":
                    store.update_state(uid, "sent")
                    store.set_receipt(uid, data["state"])
            except KeyError:
                pass

    def _send(self, uid: str, user_token: str | None = None) -> dict:
        """Deliver a person's message exactly once (messages_outbound).

        The token is claimed in the store before the helper is asked, so this
        request can never be delivered again. A failure the helper proves
        happened before delivery releases it; any other failure is uncertain
        and waits for the person to review it, never for an automatic retry.
        """
        store = self._db()
        record = store.message(uid)
        if record.state == "sent" and record.transport_id:
            return {"state": "sent"}
        if user_token is None:
            user_token = store.authorized_token(uid)
        if user_token is None:
            log.error("%s send refused: message %s has no person's request to deliver", self.account.network, uid[:8])
            if record.state in {"queued", "sending"}:
                store.mark_send_uncertain(uid)
            return {"state": "uncertain"}
        if outbound.disabled_reason(self.accounts.root):
            store.update_state(uid, "queued")
            return {"state": "queued", "reason": "outbound_disabled"}
        process = self._process
        if process is None or self.status.get("network_state") != "connected":
            store.update_state(uid, "queued")
            return {"state": "queued"}
        conversation = record.address
        try:
            if not store.known_conversation(conversation) and self.capabilities.get("create_conversations"):
                created = process.request("conversation.create", {"participants": [conversation]}, timeout=60).get("conversation") or {}
                if isinstance(created, dict) and created.get("id"):
                    self._conversation(created)
                    store.move_conversation(conversation, created["id"])
                    conversation = store.canonical_address(created["id"])
        except BridgeError as error:
            if self.network.automatic and not error.retryable and error.code not in {"not_connected", "timeout", "helper_exited"}:
                # A username nobody has, someone not on Luma Messages, a block:
                # waiting will not change it, so the message says it failed.
                log.info("%s conversation not started: %s", self.account.network, error.code)
                store.update_state(uid, "failed")
                return {"state": "failed", "reason": error.code, "message": str(error)}
            store.update_state(uid, "queued")
            return {"state": "queued"}
        if not self._burst.admit():
            self._outbound_tripped("more deliveries in a minute than a person makes")
            store.update_state(uid, "queued")
            return {"state": "queued", "reason": "outbound_disabled"}
        claim = store.claim_send(uid, user_token)
        if claim is None:
            # Already claimed once: it may have been delivered. Never again without the person.
            log.warning("%s send refused: message %s was already attempted", self.account.network, uid[:8])
            store.mark_send_uncertain(uid)
            return {"state": "uncertain"}
        reply = None
        if record.quote is not None:
            target = store.transport_for_uid(record.quote.message_uid)
            reply = target.split(":", 1)[1] if target else None
        body = record.body
        delivered_any = False
        result: dict = {}
        try:
            if record.attachments:
                last = len(record.attachments) - 1
                for index, part in enumerate(record.attachments):
                    # Stable per part, so an echo of any part is recognised after a restart.
                    client_id = uid if index == last else hashlib.sha256(f"{uid}:{index}".encode()).hexdigest()[:32]
                    result = process.deliver("media.send", {
                        "conversation": conversation, "client_id": client_id, "part_index": index,
                        "path": str(store.attachment_path(part)), "mime": part.content_type, "name": part.name,
                        **({"caption": body} if body and index == last else {})}, claim, timeout=self.media_send_timeout)
                    delivered_any = True
            else:
                result = process.deliver("message.send", {"conversation": conversation, "client_id": uid, "text": body,
                                                 **({"reply_to": reply} if reply else {})}, claim, timeout=self.send_timeout)
                delivered_any = True
        except BridgeError as error:
            if error.code in outbound.NOT_ATTEMPTED and not delivered_any:
                store.release_send(claim)
                store.update_state(uid, "queued")
                if error.code == "outbound_disabled":
                    self._outbound_tripped(str(error))
                return {"state": "queued"}
            log.warning("%s send not confirmed: message=%s code=%s; waiting for the person to review it",
                        self.account.network, uid[:8], error.code)
            try:
                store.update_state(uid, "sending")
                store.mark_send_uncertain(uid)
            except (KeyError, ValueError):
                pass
            return {"state": "uncertain"}
        message_id = result.get("message")
        transport = self._transport(message_id) if isinstance(message_id, str) and message_id else None
        try:
            current = store.message(uid).state
        except KeyError:
            return {"state": "sent"}
        if current != "sent":
            store.update_state(uid, "sent" if transport else "sending", transport)
        return {"state": "sent" if transport or current == "sent" else "sending"}

    def _outbound_tripped(self, reason: str) -> None:
        callback = self.on_outbound_disabled
        self.dispatch(lambda: (callback(reason), False)[1])

    def _react(self, uid: str, emoji: str | None) -> dict:
        store = self._db()
        process = self._process
        target = store.transport_for_uid(uid)
        if (process is None or self.status.get("network_state") != "connected" or not target
                or (emoji is not None and emoji not in self.reaction_emoji)):
            return {"state": "failed"}
        previous = store.own_reaction(uid)
        if (emoji or "") == previous:
            return {"state": "sent"}
        record = store.message(uid)
        if outbound.disabled_reason(self.accounts.root) or not self._burst.admit():
            return {"state": "failed"}
        # A reaction is a person's choice, delivered once: authorized and claimed together, never retried.
        claim = store.claim_send(uid, store.authorize_send(uid, kind="react"), kind="react")
        store.set_own_reaction(uid, emoji, state="queued")
        try:
            process.deliver("message.react", {"conversation": record.address, "message": target.split(":", 1)[1],
                                              "emoji": emoji, **({"previous": previous} if previous else {})}, claim, timeout=60)
        except BridgeError:
            # Nothing changed on the network, so the reaction shown is the one it has.
            store.set_own_reaction(uid, previous or None, state="sent")
            return {"state": "failed"}
        store.set_own_reaction(uid, emoji, state="sent")
        return {"state": "sent"}

    def _flush_queued(self) -> None:
        """On connecting: deliver what a person sent while offline, once; never repeat anything.

        Only a message with an authorized, never-claimed request is sent. A
        message whose request was claimed but that did not come back from the
        network (after the conversation fetches queued before this) is marked
        for the person to review; so is one with no request at all.
        """
        store = self._db()
        # Reconcile first: read the conversations of sends that were claimed but
        # never confirmed, so one the network did deliver is recognised by its
        # client id and confirmed rather than left for review.
        pending = store._connection.execute(
            """SELECT DISTINCT m.address FROM messages m JOIN send_tokens t ON t.message_uid=m.uid AND t.kind='send' AND t.state='claimed'
                WHERE m.direction='outgoing' AND m.state IN ('queued','sending') AND m.transport_id IS NULL""").fetchall()
        for (conversation,) in pending[:20]:
            self._fetch_conversation(conversation)
        for uid in store.unconfirmed_claims(older_than=UNCONFIRMED_SECONDS):
            log.warning("%s send %s not confirmed by the network; marked for review, not resent", self.account.network, uid[:8])
            store.mark_send_uncertain(uid)
        rows = store._connection.execute(
            """SELECT m.uid, (SELECT token FROM send_tokens t WHERE t.message_uid=m.uid AND t.kind='send' AND t.state='authorized')
                 FROM messages m WHERE m.direction='outgoing' AND m.state IN ('queued','sending') AND m.transport_id IS NULL
                ORDER BY m.timestamp""").fetchall()
        for uid, authorized in rows[:100]:
            if authorized:
                self._send(uid, authorized)
            elif not store._connection.execute("SELECT 1 FROM send_tokens WHERE message_uid=?", (uid,)).fetchone():
                # From before sends carried a person's token: never sent automatically.
                store.mark_send_uncertain(uid)

    def _mark_read(self, address: str) -> None:
        process = self._process
        if process is None or not self.capabilities.get("read_receipts"):
            return
        store = self._db()
        row = store._connection.execute(
            "SELECT transport_id FROM messages WHERE address=? AND direction='incoming' AND transport_id IS NOT NULL ORDER BY timestamp DESC LIMIT 1",
            (address,)).fetchone()
        if row:
            try:
                process.request("message.read", {"conversation": address, "message": row[0].split(":", 1)[1]}, timeout=30)
            except BridgeError:
                pass


# ── Sign-in ─────────────────────────────────────────────────────────────────

class AccountLogin:
    """Adds one account: a pending directory, its helper, and the steps it asks for.

    ``on_step(name, data)`` runs on the UI thread with the helper's ``login.*``
    events (plus ``browser.waiting`` while Firefox is open). Cancelling, or any
    failure before ``login.done``, removes the pending account.
    """

    def __init__(self, network_id: str, accounts: Accounts, *, secrets, dispatch, on_step,
                 helper_dir: Path | None = None, process_factory=BridgeProcess, capture=None,
                 account: Account | None = None) -> None:
        self.network = network(network_id)
        # Signing in again reuses the account, so its chats and settings stay.
        self.existing = account
        self.accounts, self.secrets, self.dispatch, self.on_step = accounts, secrets, dispatch, on_step
        self.helper = (helper_dir or helper_directory()) / network_id
        self.process_factory = process_factory
        self.capture = capture or capture_browser_cookies
        self.account: Account | None = None
        self._process = None
        self._finished = False
        self._cancel = threading.Event()
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="messages-login")

    def start(self, method: str, **args) -> None:
        self._later(self._start, method, args)

    def submit(self, field_name: str, value: str) -> None:
        self._later(self._request, "login.submit", {"field": field_name, "value": value})

    def _later(self, work, *args) -> None:
        try:
            self._worker.submit(work, *args)
        except RuntimeError:
            pass  # cancelled

    def cancel(self) -> None:
        self._cancel.set()
        self._later(self._abandon)
        self._worker.shutdown(wait=False)

    def _step(self, name: str, data: dict) -> None:
        self.dispatch(lambda: (self.on_step(name, data), False)[1])

    def _start(self, method: str, args: dict) -> None:
        if self._cancel.is_set():
            return
        try:
            self.account = self.existing or self.accounts.create(self.network.id)
            self._process = self.process_factory(self.helper, self.accounts.directory(self.account.id), self._event,
                                                 on_exit=lambda code: self._later(self._exited))
            self._process.start()
            hello = self._process.request("hello", {"protocol": PROTOCOL}, timeout=20)
            if hello.get("protocol") != PROTOCOL:
                raise BridgeError("outdated", "This helper needs an update.")
            self._process.request("login.start", {"method": method, **args}, timeout=60)
        except (BridgeError, OSError) as error:
            self._fail(getattr(error, "code", "unavailable"), f"{self.network.name} couldn't start signing in.")

    def _request(self, command: str, args: dict) -> None:
        if self._process is None:
            return
        try:
            self._process.request(command, args, timeout=120)
        except BridgeError as error:
            self._fail(error.code, str(error))

    def _event(self, name: str, data: dict) -> None:
        if name == "session" and isinstance(data.get("session"), str) and self.account is not None:
            try:
                self.secrets.set(self.account.id, self.network.name, data["session"])
            except KeyringLocked:
                self._later(self._fail, "keyring", "Unlock your keyring, then try again.")
            return
        if name == "login.browser":
            self._later(self._browser, data)
            return
        if name == "login.done":
            self._later(self._done, data)
            return
        if name == "login.error":
            self._later(self._fail, str(data.get("code", "error")), str(data.get("message", "")))
            return
        if name.startswith("login."):
            self._step(name, data)

    def _browser(self, data: dict) -> None:
        self._step("browser.waiting", {"url": str(data.get("url", ""))})
        try:
            cookies = self.capture(str(data["url"]), [str(n) for n in data.get("cookies") or []],
                                   [str(d) for d in data.get("domains") or []],
                                   optional=[str(n) for n in data.get("optional_cookies") or []], cancelled=self._cancel)
        except CaptureError as error:
            if not self._cancel.is_set():
                self._step("login.needs", {"field": "cookies", "hint": str(error)})
            return
        self._request("login.submit", {"field": "cookies", "value": json.dumps(cookies)})

    def _done(self, data: dict) -> None:
        if self.account is None or self._finished:
            return
        details = data.get("account") if isinstance(data.get("account"), dict) else {}
        self.account.name = str(details.get("name") or "")[:128]
        self.account.handle = str(details.get("handle") or "")[:128]
        self.account.pending = False
        self.accounts.save(self.account)
        self._finished = True
        if self._process is not None:
            self._process.stop()
            self._process = None
        account = self.account
        self._step("login.done", {"account": account})

    def _fail(self, code: str, message: str) -> None:
        if self._finished:
            return
        self._abandon()
        if not self._cancel.is_set():
            self._step("login.error", {"code": code, "message": message or f"{self.network.name} couldn't sign in."})

    def _exited(self) -> None:
        if not self._finished and not self._cancel.is_set() and self.account is not None:
            self._fail("stopped", f"{self.network.name} stopped before signing in finished.")

    def _abandon(self) -> None:
        if self._finished:
            return
        self._finished = True
        process, self._process = self._process, None
        if process is not None:
            try:
                process.request("login.cancel", timeout=5)
            except BridgeError:
                pass
            process.stop()
        if self.account is not None and self.existing is None:
            self.secrets.delete(self.account.id)
            self.accounts.remove(self.account.id)


def remove_account(account: Account, accounts: Accounts, *, secrets, provider: AccountProvider | None = None,
                   process_factory=BridgeProcess, helper_dir: Path | None = None) -> None:
    """Sign out where the network allows it, then delete the keyring item and every local file."""
    if provider is not None:
        process = provider._process
        if process is not None and process.running:
            try:
                process.request("logout", {"remote": True}, timeout=20)
            except BridgeError:
                pass
        provider.close(wait=True)  # the directory is deleted next
    else:
        helper = (helper_dir or helper_directory()) / account.network
        if os.access(helper, os.X_OK):
            process = process_factory(helper, accounts.directory(account.id), lambda *_: None)
            try:
                process.start()
                process.request("hello", {"protocol": PROTOCOL}, timeout=10)
                session = secrets.get(account.id)
                if session:
                    process.request("session.load", {"session": session}, timeout=10)
                process.request("logout", {"remote": True}, timeout=20)
            except (BridgeError, OSError, KeyringLocked):
                pass
            finally:
                process.stop()
    secrets.delete(account.id)
    accounts.remove(account.id)


# ── Browser cookies (Google Messages) ───────────────────────────────────────

class CaptureError(Exception):
    pass


FIREFOX_PREFERENCES = """\
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("signon.rememberSignons", false);
user_pref("browser.privatebrowsing.autostart", false);
"""


def capture_browser_cookies(url: str, names: list[str], domains: list[str], *, optional: list[str] = (),
                            cancelled: threading.Event | None = None, timeout: int = 900,
                            firefox: str | None = None, poll: float = 2.0) -> dict[str, str]:
    """Sign in with Firefox in a temporary profile and read the named cookies from it.

    The profile exists only for this sign-in: Firefox is closed and the profile
    deleted as soon as the cookies appear, when the person closes the window, or
    after ``timeout``. Google blocks sign-in inside embedded web views, which is
    why a real browser is used.
    """
    firefox = firefox or shutil.which("firefox")
    if not firefox:
        raise CaptureError("Firefox isn't installed. Install Firefox, or paste the cookies instead.")
    if not url.startswith("https://") or not names:
        raise CaptureError("The sign-in page isn't valid.")
    cancelled = cancelled or threading.Event()
    profile = Path(tempfile.mkdtemp(prefix="luma-messages-signin-"))
    os.chmod(profile, 0o700)
    (profile / "user.js").write_text(FIREFOX_PREFERENCES)
    process = subprocess.Popen([firefox, "--no-remote", "--new-instance", "--profile", str(profile), url],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancelled.is_set():
                raise CaptureError("Signing in was cancelled.")
            found = _read_firefox_cookies(profile, set(names) | set(optional), domains)
            if all(name in found for name in names):
                return found
            if process.poll() is not None:
                raise CaptureError("Firefox closed before signing in finished. Try again.")
            time.sleep(poll)
        raise CaptureError("Signing in took too long. Try again.")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        shutil.rmtree(profile, ignore_errors=True)


def _read_firefox_cookies(profile: Path, names: set[str], domains: list[str]) -> dict[str, str]:
    database = profile / "cookies.sqlite"
    if not database.exists():
        return {}
    with tempfile.TemporaryDirectory(prefix=".cookies-", dir=profile) as copy_dir:
        copy = Path(copy_dir) / "cookies.sqlite"
        try:
            shutil.copyfile(database, copy)
            for suffix in ("-wal", "-shm"):
                if (profile / f"cookies.sqlite{suffix}").exists():
                    shutil.copyfile(profile / f"cookies.sqlite{suffix}", Path(f"{copy}{suffix}"))
            connection = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
            try:
                rows = connection.execute("SELECT host, name, value FROM moz_cookies").fetchall()
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            return {}
    found = {}
    for host, name, value in rows:
        if name in names and any(host == domain or host.endswith("." + domain.lstrip(".")) or host == "." + domain.lstrip(".")
                                 for domain in domains):
            found[name] = value
    return found
