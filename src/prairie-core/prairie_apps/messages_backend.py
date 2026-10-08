# SPDX-License-Identifier: Apache-2.0

"""Private, source-owned SMS conversation storage for Prairie Messages."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import sqlite3
import stat
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class ThreadRecord:
    address: str
    display_name: str
    preview: str
    updated: int
    unread: int


@dataclass(frozen=True)
class MessageRecord:
    uid: str
    address: str
    body: str
    timestamp: int
    direction: str
    state: str
    transport_id: str | None = None
    attachments: tuple["AttachmentRecord", ...] = ()
    quote: "QuoteRecord | None" = None
    wire_body: str | None = None
    send_status: str = ""


@dataclass(frozen=True)
class AttachmentRecord:
    uid: str
    name: str
    content_type: str
    size: int
    storage_key: str


@dataclass(frozen=True)
class QuoteRecord:
    message_uid: str | None
    author: str
    body: str


@dataclass(frozen=True)
class ReactionRecord:
    sender: str
    emoji: str
    state: str


MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
REACTION_EMOJI = ("♥", "👍", "👎", "😂", "‼", "?")


@dataclass(frozen=True)
class TransportMessage:
    """One truthful SMS snapshot from ModemManager.

    ``object_path`` is deliberately not used as durable identity. ModemManager
    reuses ``/SMS/N`` paths after a daemon/modem restart.
    """

    address: str
    body: str
    object_path: str
    timestamp: int
    direction: str
    state: str


def incoming_transport_id(address: str, body: str, timestamp: int) -> str:
    """Return a privacy-safe identity that survives ModemManager restarts."""
    payload = "\0".join((normalize_address(address), body, str(int(timestamp))))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"mm-received:v1:{digest}"


def normalize_address(value: str) -> str:
    """Return a stable dialable address without pretending to infer a country."""
    value = value.strip()
    leading_plus = value.startswith("+")
    digits = re.sub(r"\D", "", value)
    if not 3 <= len(digits) <= 15:
        raise ValueError("Enter a valid phone number.")
    return ("+" if leading_plus else "") + digits


def png_preview_dimensions(path: Path) -> tuple[int, int]:
    """Bound PNG previews before GTK decoding, independently of Pixbuf loaders.

    GDK's built-in PNG decoder can be present without a Pixbuf PNG loader.
    Read only the mandatory IHDR header here; GTK still validates the image.
    """
    with Path(path).open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
        return 0, 0
    width = int.from_bytes(header[16:20], "big")
    height = int.from_bytes(header[20:24], "big")
    return (width, height) if width > 0 and height > 0 and width * height <= 64_000_000 else (0, 0)


class MessageStore:
    def __init__(self, path: Path | None = None, *, memory: bool = False) -> None:
        """Open the message store, or an isolated SQLite store for a UI fixture.

        The memory form creates no directories or files and never inspects the
        person's message database. It is used only by Messages' v70 fixture.
        """
        if memory:
            path = Path(":memory:")
        elif path is None:
            data_home = Path(
                os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")
            )
            path = data_home / "prairie/messages/messages.db"
        self.path = Path(path)
        self._memory = memory
        if not memory:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.path.parent, 0o700)
        self._connection = sqlite3.connect(":memory:" if memory else self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys=ON")
        # Messages, its daemon and account providers open the same store at the same
        # moment. Switching to WAL needs an exclusive lock that SQLite's busy handler
        # does not wait for, so switch only when needed and retry briefly.
        for attempt in range(0 if memory else 50):
            try:
                if str(self._connection.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
                    self._connection.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as error:
                if "locked" not in str(error) or attempt == 49:
                    raise
                time.sleep(0.1)
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS contacts (
              address TEXT PRIMARY KEY,
              display_name TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS messages (
              uid TEXT PRIMARY KEY,
              address TEXT NOT NULL,
              body TEXT NOT NULL,
              timestamp INTEGER NOT NULL,
              direction TEXT NOT NULL CHECK(direction IN ('incoming','outgoing')),
              state TEXT NOT NULL CHECK(state IN ('received','read','queued','sending','sent','failed')),
              transport_id TEXT
            );
            CREATE INDEX IF NOT EXISTS messages_thread_time
              ON messages(address, timestamp, uid);
            CREATE UNIQUE INDEX IF NOT EXISTS messages_transport_id
              ON messages(transport_id) WHERE transport_id IS NOT NULL;
            CREATE TABLE IF NOT EXISTS drafts (
              address TEXT PRIMARY KEY,
              body TEXT NOT NULL,
              updated INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS message_attachments (
              uid TEXT PRIMARY KEY,
              message_uid TEXT REFERENCES messages(uid) ON DELETE CASCADE,
              draft_address TEXT,
              name TEXT NOT NULL,
              content_type TEXT NOT NULL,
              size INTEGER NOT NULL CHECK(size > 0),
              storage_key TEXT NOT NULL,
              CHECK((message_uid IS NULL) != (draft_address IS NULL))
            );
            CREATE TABLE IF NOT EXISTS message_quotes (
              message_uid TEXT PRIMARY KEY REFERENCES messages(uid) ON DELETE CASCADE,
              target_uid TEXT REFERENCES messages(uid) ON DELETE SET NULL,
              author TEXT NOT NULL,
              body TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS draft_quotes (
              address TEXT PRIMARY KEY,
              target_uid TEXT NOT NULL REFERENCES messages(uid) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS message_reactions (
              message_uid TEXT NOT NULL REFERENCES messages(uid) ON DELETE CASCADE,
              sender TEXT NOT NULL,
              emoji TEXT NOT NULL,
              state TEXT NOT NULL CHECK(state IN ('queued','sent','received','failed')),
              PRIMARY KEY(message_uid, sender)
            );
            CREATE TABLE IF NOT EXISTS message_wire_bodies (
              message_uid TEXT PRIMARY KEY REFERENCES messages(uid) ON DELETE CASCADE,
              body TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deleted_transport_messages (
              transport_id TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS message_send_status (
              message_uid TEXT PRIMARY KEY REFERENCES messages(uid) ON DELETE CASCADE,
              status TEXT NOT NULL
            );
            """
        )
        self._connection.commit()
        self._migrate_reusable_object_paths()
        if self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='notification_reply_requests'"
        ).fetchone():
            pending_filter = ""
            if self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='notification_reply_attempts'"
            ).fetchone():
                pending_filter = " AND NOT EXISTS (SELECT 1 FROM notification_reply_attempts a WHERE a.origin=m.uid AND a.outcome='pending')"
            self._connection.execute("""INSERT OR IGNORE INTO message_send_status
                SELECT m.uid, 'uncertain' FROM messages m
                JOIN notification_reply_requests r ON r.uid=m.uid
                WHERE m.direction='outgoing' AND m.state IN ('queued','sending')""" + pending_filter)
            self._connection.commit()
        self.reconcile_phone_identities()
        if not memory:
            try:
                os.chmod(self.path, 0o600)
            except FileNotFoundError:
                pass

    def close(self) -> None:
        self._connection.close()

    def _migrate_reusable_object_paths(self) -> None:
        """Replace legacy `/SMS/N` identities before those paths are reused."""
        rows = self._connection.execute(
            """SELECT uid,address,body,timestamp,direction,transport_id
                 FROM messages
                WHERE transport_id LIKE '/org/freedesktop/ModemManager1/SMS/%'"""
        ).fetchall()
        for row in rows:
            if row["direction"] == "incoming":
                durable = incoming_transport_id(
                    row["address"], row["body"], row["timestamp"]
                )
            else:
                durable = f"mm-outgoing:v1:{row['uid']}"
            try:
                self._connection.execute(
                    "UPDATE messages SET transport_id=? WHERE uid=?",
                    (durable, row["uid"]),
                )
            except sqlite3.IntegrityError:
                # A prior process already imported the same logical SMS under
                # its durable identity. Preserve this local row without
                # allowing the old reusable object path to block future SMS.
                self._connection.execute(
                    "UPDATE messages SET transport_id=? WHERE uid=?",
                    (f"legacy-local:v1:{row['uid']}", row["uid"]),
                )
        if rows:
            self._connection.commit()

    def set_display_name(self, address: str, display_name: str) -> None:
        address = self.canonical_address(address)
        self._connection.execute(
            """INSERT INTO contacts(address, display_name) VALUES(?, ?)
               ON CONFLICT(address) DO UPDATE SET display_name=excluded.display_name""",
            (address, display_name.strip()),
        )
        self._connection.commit()

    def add(
        self,
        address: str,
        body: str,
        *,
        direction: str,
        state: str | None = None,
        timestamp: int | None = None,
        uid: str | None = None,
        transport_id: str | None = None,
        attachment_uids: tuple[str, ...] = (),
        reply_to: str | None = None,
    ) -> MessageRecord:
        address = self.canonical_address(address)
        body = body.strip()
        if not body and not attachment_uids:
            raise ValueError("A message cannot be empty.")
        if direction not in {"incoming", "outgoing"}:
            raise ValueError("Invalid message direction.")
        if state is None:
            state = "received" if direction == "incoming" else "queued"
        if state not in {"received", "read", "queued", "sending", "sent", "failed"}:
            raise ValueError("Invalid delivery state.")
        record = MessageRecord(
            uid or uuid.uuid4().hex,
            address,
            body,
            int(time.time() if timestamp is None else timestamp),
            direction,
            state,
            transport_id,
        )
        quote = self.quote_for(reply_to, address) if reply_to else None
        if len(set(attachment_uids)) != len(attachment_uids):
            raise ValueError("An attachment cannot appear twice in a message.")
        with self._connection:
            self._connection.execute(
                """INSERT INTO messages
                   (uid,address,body,timestamp,direction,state,transport_id)
                   VALUES(?,?,?,?,?,?,?)""",
                (
                    record.uid,
                    record.address,
                    record.body,
                    record.timestamp,
                    record.direction,
                    record.state,
                    record.transport_id,
                ),
            )
            for attachment_uid in attachment_uids:
                cursor = self._connection.execute(
                    """UPDATE message_attachments SET message_uid=?,draft_address=NULL
                       WHERE uid=? AND draft_address=? AND message_uid IS NULL""",
                    (record.uid, attachment_uid, address),
                )
                if cursor.rowcount != 1:
                    raise ValueError("The attachment is no longer in this draft.")
            if quote:
                self._connection.execute(
                    "INSERT INTO message_quotes VALUES(?,?,?,?)",
                    (record.uid, quote.message_uid, quote.author, quote.body),
                )
                if direction == "outgoing":
                    # SMS/MMS have no structured-reply field. Preserve the
                    # exact on-wire fallback separately for modem reconciliation.
                    self._connection.execute(
                        "INSERT INTO message_wire_bodies VALUES(?,?)",
                        (record.uid, f"> {quote.author}: {quote.body}\n\n{body}"),
                    )
        return self.message(record.uid)

    def uid_for_transport(self, transport_id: str) -> str | None:
        row = self._connection.execute('SELECT uid FROM messages WHERE transport_id=?', (transport_id,)).fetchone()
        return row[0] if row else None

    def set_attachment_type(self, uid: str, content_type: str) -> None:
        if not re.fullmatch(r'[A-Za-z0-9.+*-]{1,64}/[A-Za-z0-9.+*-]{1,100}', content_type):
            raise ValueError('Invalid attachment type')
        with self._connection:
            self._connection.execute('UPDATE message_attachments SET content_type=? WHERE uid=?', (content_type, uid))

    def message(self, uid: str) -> MessageRecord:
        row = self._connection.execute("SELECT * FROM messages WHERE uid=?", (uid,)).fetchone()
        if row is None:
            raise KeyError(uid)
        return self._message(row)

    def attach_file(self, address: str, source: Path, *, name: str | None = None) -> AttachmentRecord:
        """Copy a user-selected regular file into private, durable draft storage."""
        address = self.canonical_address(address)
        source = Path(source)
        blobs = self.path.parent / "attachments"
        blobs.mkdir(mode=0o700, exist_ok=True)
        descriptor = os.open(source, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        temporary = None
        storage_key = None
        try:
            with os.fdopen(descriptor, "rb") as reader:
                info = os.fstat(reader.fileno())
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_ATTACHMENT_BYTES:
                    raise ValueError("Choose a nonempty regular file no larger than 25 MB.")
                fd, temporary = tempfile.mkstemp(prefix=".import-", dir=blobs)
                digest = hashlib.sha256()
                size = 0
                with os.fdopen(fd, "wb") as writer:
                    while chunk := reader.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_ATTACHMENT_BYTES:
                            raise ValueError("The attachment exceeds 25 MB.")
                        digest.update(chunk)
                        writer.write(chunk)
                    writer.flush()
                    os.fsync(writer.fileno())
                if size != info.st_size:
                    raise ValueError("The file changed while it was being attached. Try again.")
            storage_key = digest.hexdigest()
            filename = Path(name or source.name).name[:255]
            content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            record = AttachmentRecord(uuid.uuid4().hex, filename, content_type, size, storage_key)
            with self._connection:
                # Blob publication and reference creation serialize with
                # deletion in other UI/importer processes.
                self._connection.execute("BEGIN IMMEDIATE")
                os.replace(temporary, blobs / storage_key)
                temporary = None
                directory_fd = os.open(blobs, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                self._connection.execute(
                    "INSERT INTO message_attachments VALUES(?,NULL,?,?,?,?,?)",
                    (record.uid, address, record.name, record.content_type, record.size, storage_key),
                )
                self._connection.execute(
                    """INSERT INTO drafts VALUES(?,'',?) ON CONFLICT(address)
                       DO UPDATE SET updated=excluded.updated""", (address, int(time.time())),
                )
            return record
        except Exception:
            if storage_key is not None:
                self._collect_blobs({storage_key})
            raise
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def attachment_path(self, attachment: AttachmentRecord) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", attachment.storage_key):
            raise ValueError("Invalid attachment storage identity.")
        return self.path.parent / "attachments" / attachment.storage_key

    @staticmethod
    def _attachment(row: sqlite3.Row) -> AttachmentRecord:
        return AttachmentRecord(row["uid"], row["name"], row["content_type"], row["size"], row["storage_key"])

    def draft_attachments(self, address: str) -> tuple[AttachmentRecord, ...]:
        return tuple(self._attachment(row) for row in self._connection.execute(
            "SELECT * FROM message_attachments WHERE draft_address=? ORDER BY rowid",
            (self.canonical_address(address),),
        ))

    def remove_draft_attachment(self, address: str, uid: str) -> bool:
        keys = [row[0] for row in self._connection.execute(
            "SELECT storage_key FROM message_attachments WHERE uid=? AND draft_address=?",
            (uid, self.canonical_address(address)),
        )]
        with self._connection:
            cursor = self._connection.execute(
                "DELETE FROM message_attachments WHERE uid=? AND draft_address=?",
                (uid, self.canonical_address(address)),
            )
        self._collect_blobs(keys)
        return cursor.rowcount == 1

    def _collect_blobs(self, keys: list[str]) -> None:
        # Commit metadata deletion first: interruption may leave an orphan,
        # but cannot roll back a reference to a file already removed.
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            for key in set(keys):
                if not re.fullmatch(r"[0-9a-f]{64}", key):
                    continue
                if not self._connection.execute(
                    "SELECT 1 FROM message_attachments WHERE storage_key=? LIMIT 1", (key,),
                ).fetchone():
                    (self.path.parent / "attachments" / key).unlink(missing_ok=True)

    def quote_for(self, uid: str, address: str) -> QuoteRecord:
        message = self.message(uid)
        if message.address != self.canonical_address(address):
            raise ValueError("A reply must belong to this conversation.")
        names = self._connection.execute("SELECT display_name FROM contacts WHERE address=?", (message.address,)).fetchone()
        author = "You" if message.direction == "outgoing" else (names[0] if names else message.address)
        body = message.body or ", ".join(item.name for item in message.attachments)
        return QuoteRecord(uid, author, body)

    def set_draft_reply(self, address: str, uid: str | None) -> None:
        address = self.canonical_address(address)
        if uid:
            self.quote_for(uid, address)
        with self._connection:
            self._connection.execute("DELETE FROM draft_quotes WHERE address=?", (address,))
            if uid:
                self._connection.execute("INSERT INTO draft_quotes VALUES(?,?)", (address, uid))

    def draft_reply(self, address: str) -> QuoteRecord | None:
        address = self.canonical_address(address)
        row = self._connection.execute("SELECT target_uid FROM draft_quotes WHERE address=?", (address,)).fetchone()
        return self.quote_for(row[0], address) if row else None

    def reactions(self, uid: str) -> tuple[ReactionRecord, ...]:
        return tuple(ReactionRecord(*row) for row in self._connection.execute(
            "SELECT sender,emoji,state FROM message_reactions WHERE message_uid=? ORDER BY sender", (uid,),
        ))

    def set_reaction(self, uid: str, sender: str, emoji: str | None, *, state: str) -> None:
        message = self.message(uid)
        if emoji is not None and emoji not in REACTION_EMOJI:
            raise ValueError("Unsupported reaction.")
        if state not in {"queued", "sent", "received", "failed"}:
            raise ValueError("Invalid reaction state.")
        if sender != "self":
            sender = self.canonical_address(sender)
            if message.address != sender:
                raise ValueError("The reaction sender is outside this conversation.")
        with self._connection:
            self._connection.execute("DELETE FROM message_reactions WHERE message_uid=? AND sender=?", (uid, sender))
            if emoji:
                self._connection.execute("INSERT INTO message_reactions VALUES(?,?,?,?)", (uid, sender, emoji, state))

    def ingest(
        self, address: str, body: str, *, transport_id: str,
        timestamp: int | None = None,
    ) -> MessageRecord | None:
        if not (
            transport_id.startswith("/org/freedesktop/ModemManager1/SMS/")
            or transport_id.startswith("mm-received:v1:")
        ):
            raise ValueError("Invalid ModemManager message identifier.")
        if self.is_transport_deleted(transport_id):
            return None
        existing = self._connection.execute(
            "SELECT uid FROM messages WHERE transport_id=?", (transport_id,)
        ).fetchone()
        if existing is not None:
            return None
        try:
            return self.add(
                address, body, direction="incoming", state="received",
                timestamp=timestamp, transport_id=transport_id,
            )
        except sqlite3.IntegrityError:
            # The UI and importer service may observe the same modem object at
            # once. The unique transport-id index is the atomic authority.
            return None

    def update_state(
        self, uid: str, state: str, transport_id: str | None = None
    ) -> None:
        if state not in {"queued", "sending", "sent", "failed"}:
            raise ValueError("Invalid delivery state.")
        cursor = self._connection.execute(
            "UPDATE messages SET state=?, transport_id=COALESCE(?, transport_id) WHERE uid=?",
            (state, transport_id, uid),
        )
        if cursor.rowcount != 1:
            raise KeyError(uid)
        if state == "sent":
            self._connection.execute("DELETE FROM message_send_status WHERE message_uid=?", (uid,))
        self._connection.commit()

    def mark_send_uncertain(self, uid: str) -> None:
        record = self.message(uid)
        if record.direction != "outgoing":
            raise ValueError("Only outgoing messages have send status")
        if record.state == "sent":
            return
        self._connection.execute(
            "INSERT OR REPLACE INTO message_send_status VALUES (?, 'uncertain')", (uid,))
        self._connection.commit()

    def recover_interrupted(self) -> int:
        """Preserve indeterminate sends until modem truth can reconcile them.

        A process interruption is not evidence that the carrier rejected a
        message: ModemManager may already have accepted and transmitted it.
        Earlier releases converted every such row to ``failed`` and therefore
        displayed "Not delivered" for texts that demonstrably arrived.
        """
        return 0

    def reconcile_outgoing(
        self, messages: tuple[TransportMessage, ...]
    ) -> int:
        """Mark pending local rows sent when ModemManager proves submission.

        Outgoing modem objects do not reliably expose a timestamp on the FP6,
        so match the newest still-pending row with the same normalized peer and
        exact body. The durable identifier is based on the local row UID, not
        the reusable ModemManager object path.
        """
        reconciled = 0
        for message in messages:
            if message.direction != "outgoing" or message.state != "sent":
                continue
            address = self.canonical_address(message.address)
            row = self._connection.execute(
                """SELECT uid FROM messages
                    WHERE address=? AND COALESCE(
                        (SELECT body FROM message_wire_bodies WHERE message_uid=messages.uid),
                        body)=? AND direction='outgoing'
                      AND state IN ('queued','sending')
                      AND NOT EXISTS (SELECT 1 FROM message_attachments WHERE message_uid=messages.uid)
                    ORDER BY timestamp DESC, rowid DESC LIMIT 1""",
                (address, message.body.strip()),
            ).fetchone()
            if row is None:
                continue
            uid = str(row["uid"])
            self._connection.execute(
                "UPDATE messages SET state='sent', transport_id=? WHERE uid=?",
                (f"mm-outgoing:v1:{uid}", uid),
            )
            self._connection.execute("DELETE FROM message_send_status WHERE message_uid=?", (uid,))
            reconciled += 1
        if reconciled:
            self._connection.commit()
        return reconciled

    def incoming_revision(self) -> tuple[int, int]:
        """Return non-content metadata suitable for cross-process refresh."""
        row = self._connection.execute(
            """SELECT COUNT(*) message_count, COALESCE(MAX(rowid), 0) last_row
                 FROM messages WHERE direction='incoming'"""
        ).fetchone()
        return int(row["message_count"]), int(row["last_row"])

    def external_revision(self) -> int:
        """Observe other connections' content and delivery changes without polling rows."""
        return self._connection.execute("PRAGMA data_version").fetchone()[0]

    def thread(self, address: str) -> tuple[MessageRecord, ...]:
        address = self.canonical_address(address)
        rows = self._connection.execute(
            "SELECT * FROM messages WHERE address=? ORDER BY timestamp, uid", (address,)
        )
        return tuple(self._message(row) for row in rows)

    def draft(self, address: str) -> str:
        address = self.canonical_address(address)
        row = self._connection.execute(
            "SELECT body FROM drafts WHERE address=?", (address,)
        ).fetchone()
        return str(row["body"]) if row is not None else ""

    def set_draft(self, address: str, body: str) -> None:
        address = self.canonical_address(address)
        body = body.rstrip()
        if not body:
            self.delete_draft(address)
            return
        self._connection.execute(
            """INSERT INTO drafts(address, body, updated) VALUES(?,?,?)
               ON CONFLICT(address) DO UPDATE SET
                 body=excluded.body, updated=excluded.updated""",
            (address, body, int(time.time())),
        )
        self._connection.commit()

    def delete_draft(self, address: str) -> None:
        address = self.canonical_address(address)
        if self.draft_attachments(address):
            self._connection.execute("UPDATE drafts SET body='' WHERE address=?", (address,))
        else:
            self._connection.execute("DELETE FROM drafts WHERE address=?", (address,))
        self._connection.commit()

    def delete_message(self, uid: str) -> bool:
        self._connection.execute(
            """INSERT OR IGNORE INTO deleted_transport_messages
               SELECT transport_id FROM messages WHERE uid=? AND transport_id IS NOT NULL""", (uid,),
        )
        keys = [row[0] for row in self._connection.execute(
            "SELECT storage_key FROM message_attachments WHERE message_uid=?", (uid,),
        )]
        cursor = self._connection.execute("DELETE FROM messages WHERE uid=?", (uid,))
        self._connection.commit()
        self._collect_blobs(keys)
        return cursor.rowcount == 1

    def is_transport_deleted(self, transport_id: str) -> bool:
        return self._connection.execute(
            "SELECT 1 FROM deleted_transport_messages WHERE transport_id=?", (transport_id,),
        ).fetchone() is not None

    def delete_thread(self, address: str) -> bool:
        """Remove every message in one conversation from this device.

        The messages are deleted here only. Nothing is unsent, and nothing is
        withdrawn from the other person's phone or from the network.
        """
        address = self.canonical_address(address)
        had_draft = bool(self.draft(address) or self.draft_attachments(address))
        self._connection.execute(
            """INSERT OR IGNORE INTO deleted_transport_messages
               SELECT transport_id FROM messages WHERE address=? AND transport_id IS NOT NULL""", (address,),
        )
        keys = [row[0] for row in self._connection.execute(
            """SELECT storage_key FROM message_attachments WHERE
               message_uid IN (SELECT uid FROM messages WHERE address=?) OR draft_address=?""",
            (address, address),
        )]
        cursor = self._connection.execute(
            "DELETE FROM messages WHERE address=?", (address,)
        )
        self._connection.execute("DELETE FROM message_attachments WHERE draft_address=?", (address,))
        self._connection.execute("DELETE FROM draft_quotes WHERE address=?", (address,))
        self._connection.execute("DELETE FROM drafts WHERE address=?", (address,))
        self._connection.commit()
        self._collect_blobs(keys)
        return cursor.rowcount > 0 or had_draft

    def threads(self, query: str = "") -> tuple[ThreadRecord, ...]:
        pattern = f"%{query.strip()}%"
        rows = self._connection.execute(
            """
            WITH ranked AS (
              SELECT rowid message_rowid, *,
                     ROW_NUMBER() OVER (
                       PARTITION BY address ORDER BY timestamp DESC, rowid DESC
                     ) AS rank
                FROM messages
            )
            SELECT m.address, COALESCE(NULLIF(c.display_name,''), m.address) display_name,
                   CASE WHEN d.body IS NOT NULL AND d.body != ''
                        THEN 'Draft: ' || d.body
                        WHEN EXISTS (SELECT 1 FROM message_attachments WHERE draft_address=m.address)
                        THEN 'Draft: ' || (SELECT name FROM message_attachments WHERE draft_address=m.address LIMIT 1)
                        ELSE COALESCE(NULLIF(m.body,''),
                          (SELECT name FROM message_attachments WHERE message_uid=m.uid LIMIT 1), '') END preview,
                   m.timestamp updated,
                   SUM(CASE WHEN allm.direction='incoming' AND allm.state='received'
                            THEN 1 ELSE 0 END) unread
              FROM ranked m
              LEFT JOIN contacts c ON c.address=m.address
              LEFT JOIN drafts d ON d.address=m.address
              JOIN messages allm ON allm.address=m.address
             WHERE m.rank=1 AND (
                   COALESCE(NULLIF(c.display_name,''), m.address) LIKE ?
                OR m.address LIKE ? OR EXISTS (
                     SELECT 1 FROM messages searched
                      WHERE searched.address=m.address AND searched.body LIKE ?
                   ) OR COALESCE(d.body, '') LIKE ?)
             GROUP BY m.address, display_name, m.body, m.timestamp, d.body
             ORDER BY m.timestamp DESC, m.address
            """,
            (pattern, pattern, pattern, pattern),
        )
        records = [
            ThreadRecord(
                row["address"], row["display_name"], row["preview"],
                row["updated"], row["unread"]
            )
            for row in rows
        ]
        # Draft-only recipients must remain discoverable after a restart,
        # including an image with no caption and no prior messages.
        drafts = self._connection.execute(
            """SELECT d.address, COALESCE(NULLIF(c.display_name,''),d.address) display_name,
                      COALESCE(NULLIF(d.body,''), (SELECT name FROM message_attachments
                        WHERE draft_address=d.address LIMIT 1)) preview, d.updated
                 FROM drafts d LEFT JOIN contacts c ON c.address=d.address
                WHERE NOT EXISTS (SELECT 1 FROM messages WHERE address=d.address)
                  AND (d.body!='' OR EXISTS (SELECT 1 FROM message_attachments WHERE draft_address=d.address))
                  AND (d.address LIKE ? OR c.display_name LIKE ? OR d.body LIKE ? OR
                       EXISTS (SELECT 1 FROM message_attachments WHERE draft_address=d.address AND name LIKE ?))""",
            (pattern, pattern, pattern, pattern),
        )
        records.extend(ThreadRecord(row["address"], row["display_name"], "Draft: " + row["preview"],
                                    row["updated"], 0) for row in drafts)
        return tuple(sorted(records, key=lambda record: (-record.updated, record.address)))

    def mark_read(self, address: str) -> None:
        address = self.canonical_address(address)
        self._connection.execute(
            "UPDATE messages SET state='read' WHERE address=? AND direction='incoming' AND state='received'",
            (address,),
        )
        self._connection.commit()

    def mark_received_read(self, uids: tuple[str, ...]) -> None:
        """Mark only the received messages that were visible when a thread opened.

        A deferred writer must not mark an arrival received after navigation read.
        """
        if not uids:
            return
        self._connection.executemany(
            "UPDATE messages SET state='read' WHERE uid=? AND direction='incoming' AND state='received'",
            ((uid,) for uid in uids),
        )
        self._connection.commit()

    def has_incoming(self, address: str) -> bool:
        """Whether anyone else has written in this conversation; only then can it be unread."""
        try:
            address = self.canonical_address(address)
        except ValueError:
            return False
        return self._connection.execute(
            "SELECT 1 FROM messages WHERE address=? AND direction='incoming' LIMIT 1", (address,)).fetchone() is not None

    def mark_unread(self, address: str) -> bool:
        """Mark a conversation unread again: its newest incoming message becomes unread."""
        address = self.canonical_address(address)
        cursor = self._connection.execute(
            """UPDATE messages SET state='received' WHERE uid=(
                 SELECT uid FROM messages WHERE address=? AND direction='incoming'
                 ORDER BY timestamp DESC, rowid DESC LIMIT 1) AND state='read'""", (address,))
        self._connection.commit()
        return bool(cursor.rowcount)

    @staticmethod
    def _nanp_pair(address: str) -> tuple[str, str] | None:
        if re.fullmatch(r"\+1\d{10}", address):
            return address, address[2:]
        if re.fullmatch(r"\d{10}", address):
            return f"+1{address}", address
        return None

    def _merge_nanp_pair(self, e164: str, national: str) -> None:
        if e164 == national:
            return
        contacts = self._connection.execute(
            "SELECT address, display_name FROM contacts WHERE address IN (?, ?)",
            (e164, national),
        ).fetchall()
        display_name = next(
            (
                row["display_name"]
                for row in sorted(contacts, key=lambda row: row["address"] != e164)
                if row["display_name"]
            ),
            "",
        )
        self._connection.execute(
            "UPDATE messages SET address=? WHERE address=?", (e164, national)
        )
        self._connection.execute(
            "UPDATE message_attachments SET draft_address=? WHERE draft_address=?", (e164, national)
        )
        self._connection.execute(
            "UPDATE OR IGNORE draft_quotes SET address=? WHERE address=?", (e164, national)
        )
        self._connection.execute("DELETE FROM draft_quotes WHERE address=?", (national,))
        if contacts:
            self._connection.execute(
                "DELETE FROM contacts WHERE address IN (?, ?)", (e164, national)
            )
            self._connection.execute(
                "INSERT INTO contacts(address, display_name) VALUES(?, ?)",
                (e164, display_name),
            )

    def reconcile_phone_identities(self) -> int:
        addresses = {
            row[0]
            for row in self._connection.execute(
                "SELECT address FROM messages UNION SELECT address FROM contacts"
            )
        }
        merged = 0
        for e164 in sorted(address for address in addresses if re.fullmatch(r"\+1\d{10}", address)):
            national = e164[2:]
            if national in addresses:
                self._merge_nanp_pair(e164, national)
                merged += 1
        if merged:
            self._connection.commit()
        return merged

    def canonical_address(self, value: str) -> str:
        address = normalize_address(value)
        pair = self._nanp_pair(address)
        if pair is None:
            return address
        e164, national = pair
        existing = {
            row[0]
            for row in self._connection.execute(
                "SELECT address FROM messages WHERE address IN (?, ?)",
                (e164, national),
            )
        }
        if address == e164 or e164 in existing:
            if national in existing:
                self._merge_nanp_pair(e164, national)
                self._connection.commit()
            return e164
        return national

    def _message(self, row: sqlite3.Row) -> MessageRecord:
        attachments = tuple(self._attachment(part) for part in self._connection.execute(
            "SELECT * FROM message_attachments WHERE message_uid=? ORDER BY rowid", (row["uid"],),
        ))
        quoted = self._connection.execute(
            "SELECT target_uid,author,body FROM message_quotes WHERE message_uid=?", (row["uid"],),
        ).fetchone()
        wire = self._connection.execute(
            "SELECT body FROM message_wire_bodies WHERE message_uid=?", (row["uid"],),
        ).fetchone()
        send_status = self._connection.execute(
            "SELECT status FROM message_send_status WHERE message_uid=?", (row["uid"],)
        ).fetchone()
        return MessageRecord(
            row["uid"], row["address"], row["body"], row["timestamp"],
            row["direction"], row["state"], row["transport_id"], attachments,
            QuoteRecord(*quoted) if quoted else None,
            wire[0] if wire else None,
            send_status[0] if send_status else "",
        )


@dataclass(frozen=True)
class MessagingCapability:
    available: bool
    reason: str
    modem_index: str | None = None


class ModemMessagingTransport:
    """Small ModemManager adapter with no subscriber identifiers in diagnostics."""

    def __init__(self, runner=None) -> None:
        self._runner = runner or self._run
        self._use_dbus_sender = runner is None

    @staticmethod
    def _run(arguments: list[str]) -> str:
        return subprocess.run(
            arguments, check=True, capture_output=True, text=True, timeout=20
        ).stdout

    def inspect(self) -> MessagingCapability:
        try:
            listing = self._runner(["mmcli", "-L"])
        except (OSError, subprocess.SubprocessError):
            return MessagingCapability(False, "This device can't send texts. Connect your phone in Luma Connect to text from here.")
        indexes = tuple(dict.fromkeys(re.findall(r"/Modem/(\d+)", listing)))
        if len(indexes) != 1:
            return MessagingCapability(False, "No cellular modem is ready.")
        try:
            status = self._runner(
                ["mmcli", "-m", indexes[0], "--output-keyvalue"]
            )
        except (OSError, subprocess.SubprocessError):
            return MessagingCapability(False, "The cellular modem is not ready.", indexes[0])
        lowered = status.lower()
        if "sim-missing" in lowered:
            return MessagingCapability(False, "Insert a SIM card to send messages.", indexes[0])
        values = {}
        for line in status.splitlines():
            key, separator, value = line.partition(":")
            if separator:
                values[key.strip()] = value.strip().casefold()
        modem_state = _value_suffix(values, ".generic.state")
        registration = _value_suffix(values, ".3gpp.registration-state")
        # A connected bearer is not sufficient evidence that ModemManager
        # finished enabling its Messaging interface.  The FP6 can retain an
        # IMS bearer while the SMS list is absent, in which case Create fails
        # with Core.WrongState.  Require MM's explicit registration state (or
        # the corresponding 3GPP state) before exposing a send-capable UI.
        if modem_state != "registered" and registration not in {"home", "roaming"}:
            return MessagingCapability(False, "Connect to a cellular network to send.", indexes[0])
        return MessagingCapability(True, "Ready to send", indexes[0])

    def send(self, address: str, body: str) -> str:
        capability = self.inspect()
        if not capability.available or capability.modem_index is None:
            raise RuntimeError(capability.reason)
        address = normalize_address(address)
        if not body.strip():
            raise ValueError("A message cannot be empty.")
        if self._use_dbus_sender:
            return self._send_dbus(capability.modem_index, address, body)
        # Injected runners remain available for deterministic offline tests.
        created = self._runner(
            [
                "mmcli", "-m", capability.modem_index,
                f"--messaging-create-sms=number={address},text={body}",
            ]
        )
        match = re.search(r"/SMS/(\d+)", created)
        if not match:
            raise RuntimeError("ModemManager did not return a message identifier.")
        path = f"/org/freedesktop/ModemManager1/SMS/{match.group(1)}"
        self._runner(["mmcli", "-s", match.group(1), "--send"])
        return path

    @staticmethod
    def _send_dbus(modem_index: str, address: str, body: str) -> str:
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        created = bus.call_sync(
            "org.freedesktop.ModemManager1",
            f"/org/freedesktop/ModemManager1/Modem/{modem_index}",
            "org.freedesktop.ModemManager1.Modem.Messaging",
            "Create",
            GLib.Variant(
                "(a{sv})",
                (
                    {
                        "number": GLib.Variant("s", address),
                        "text": GLib.Variant("s", body),
                    },
                ),
            ),
            GLib.VariantType("(o)"),
            Gio.DBusCallFlags.NONE,
            20_000,
            None,
        )
        path = created.unpack()[0]
        if not re.fullmatch(r"/org/freedesktop/ModemManager1/SMS/\d+", path):
            raise RuntimeError("ModemManager returned an invalid message identifier.")
        bus.call_sync(
            "org.freedesktop.ModemManager1",
            path,
            "org.freedesktop.ModemManager1.Sms",
            "Send",
            None,
            None,
            Gio.DBusCallFlags.NONE,
            20_000,
            None,
        )
        return path

    def snapshot(self) -> tuple[TransportMessage, ...]:
        """Read all real SMS objects without treating their paths as IDs."""
        try:
            listing = self._runner(["mmcli", "-L"])
        except (OSError, subprocess.SubprocessError):
            return ()
        indexes = tuple(dict.fromkeys(re.findall(r"/Modem/(\d+)", listing)))
        if len(indexes) != 1:
            return ()
        if self._use_dbus_sender:
            return self._snapshot_dbus(indexes[0])
        return self._snapshot_cli(indexes[0])

    def _snapshot_cli(self, modem_index: str) -> tuple[TransportMessage, ...]:
        try:
            listing = self._runner(
                ["mmcli", "-m", modem_index, "--messaging-list-sms"]
            )
        except (OSError, subprocess.SubprocessError):
            return ()
        identifiers = tuple(dict.fromkeys(re.findall(r"/SMS/(\d+)", listing)))
        result: list[TransportMessage] = []
        for identifier in identifiers:
            try:
                detail = self._runner(["mmcli", "-s", identifier, "--output-keyvalue"])
            except (OSError, subprocess.SubprocessError):
                continue
            values = {}
            for line in detail.splitlines():
                key, separator, value = line.partition(":")
                if separator: values[key.strip()] = value.strip()
            state = _value_suffix(values, ".state").casefold()
            direction = _value_suffix(values, ".pdu-type").casefold()
            number = _value_suffix(values, ".number")
            body = _value_suffix(values, ".text")
            timestamp_text = _value_suffix(values, ".timestamp")
            if not number or not body or direction == "status-report":
                continue
            if direction not in {"deliver", "cdma-deliver", "submit", "cdma-submit"}:
                continue
            result.append(TransportMessage(
                normalize_address(number), body,
                f"/org/freedesktop/ModemManager1/SMS/{identifier}",
                _parse_timestamp(timestamp_text),
                "incoming" if "deliver" in direction else "outgoing",
                state,
            ))
        return tuple(result)

    @staticmethod
    def _snapshot_dbus(modem_index: str) -> tuple[TransportMessage, ...]:
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        listed = bus.call_sync(
            "org.freedesktop.ModemManager1",
            f"/org/freedesktop/ModemManager1/Modem/{modem_index}",
            "org.freedesktop.ModemManager1.Modem.Messaging",
            "List", None, GLib.VariantType("(ao)"),
            Gio.DBusCallFlags.NONE, 10_000, None,
        )
        result: list[TransportMessage] = []
        for path in listed.unpack()[0]:
            properties = bus.call_sync(
                "org.freedesktop.ModemManager1", path,
                "org.freedesktop.DBus.Properties", "GetAll",
                GLib.Variant("(s)", ("org.freedesktop.ModemManager1.Sms",)),
                GLib.VariantType("(a{sv})"), Gio.DBusCallFlags.NONE,
                10_000, None,
            ).unpack()[0]
            state_value = int(properties.get("State", 0))
            pdu_type = int(properties.get("PduType", 0))
            number = str(properties.get("Number", ""))
            body = str(properties.get("Text", ""))
            if not number or not body or pdu_type not in {1, 2, 32, 33}:
                continue
            state = {
                0: "unknown", 1: "stored", 2: "receiving",
                3: "received", 4: "sending", 5: "sent",
            }.get(state_value, "unknown")
            result.append(TransportMessage(
                normalize_address(number), body, path,
                _parse_timestamp(str(properties.get("Timestamp", ""))),
                "incoming" if pdu_type in {1, 32} else "outgoing",
                state,
            ))
        return tuple(result)

    def received(self) -> tuple[tuple[str, str, str, int], ...]:
        result = []
        for message in self.snapshot():
            if message.direction != "incoming" or message.state not in {
                "received", "receiving",
            }:
                continue
            result.append((
                message.address,
                message.body,
                incoming_transport_id(
                    message.address, message.body, message.timestamp
                ),
                message.timestamp,
            ))
        return tuple(result)


def _parse_timestamp(value: str) -> int:
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except (ValueError, OverflowError):
        return int(time.time())


def _value_suffix(values: dict[str, str], suffix: str) -> str:
    return next((value for key, value in values.items() if key.endswith(suffix)), "")


__all__ = [
    "MessageRecord", "MessageStore", "MessagingCapability",
    "ModemMessagingTransport", "ThreadRecord", "TransportMessage",
    "incoming_transport_id", "normalize_address"
]
