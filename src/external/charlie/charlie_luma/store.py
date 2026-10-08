# SPDX-License-Identifier: Apache-2.0
"""Transactional local mail metadata store."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Iterator

from .model import Account, Attachment, Conversation, Message, ServerConfig, normalized_subject


SCHEMA_VERSION = 4


class MailStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                yield self._connection
            except Exception:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def _migrate(self) -> None:
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("Mail database was created by a newer Charlie")
            if version == 0:
                db.executescript(
                    """
                    CREATE TABLE accounts (
                      id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                      address TEXT NOT NULL, provider TEXT NOT NULL,
                      colour TEXT NOT NULL, enabled INTEGER NOT NULL,
                      avatar_url TEXT NOT NULL
                    );
                    CREATE TABLE messages (
                      id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                      folder TEXT NOT NULL, uid INTEGER NOT NULL,
                      message_id TEXT NOT NULL, thread_id TEXT NOT NULL,
                      subject TEXT NOT NULL, sender_name TEXT NOT NULL,
                      sender_address TEXT NOT NULL, recipients TEXT NOT NULL,
                      sent_at TEXT NOT NULL, snippet TEXT NOT NULL,
                      body_text TEXT NOT NULL, body_html TEXT NOT NULL,
                      unread INTEGER NOT NULL, flagged INTEGER NOT NULL,
                      outgoing INTEGER NOT NULL, attachments TEXT NOT NULL,
                      refs TEXT NOT NULL,
                      FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE,
                      UNIQUE(account_id, folder, uid)
                    );
                    CREATE INDEX messages_thread ON messages(thread_id, sent_at);
                    CREATE INDEX messages_folder ON messages(folder, sent_at DESC);
                    CREATE VIRTUAL TABLE message_search USING fts5(
                      id UNINDEXED, subject, sender, recipients, body,
                      tokenize='unicode61 remove_diacritics 2'
                    );
                    CREATE TABLE attachment_payloads (
                      message_id TEXT NOT NULL, attachment_id TEXT NOT NULL,
                      content BLOB NOT NULL,
                      PRIMARY KEY(message_id, attachment_id),
                      FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
                    );
                    CREATE TABLE server_configs (
                      account_id TEXT PRIMARY KEY,
                      imap_host TEXT NOT NULL, imap_port INTEGER NOT NULL,
                      smtp_host TEXT NOT NULL, smtp_port INTEGER NOT NULL,
                      username TEXT NOT NULL, use_starttls INTEGER NOT NULL,
                      FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                    );
                    PRAGMA user_version=4;
                    """
                )
            else:
                if version == 1:
                    db.executescript(
                        """
                        CREATE TABLE attachment_payloads (
                          message_id TEXT NOT NULL, attachment_id TEXT NOT NULL,
                          content BLOB NOT NULL,
                          PRIMARY KEY(message_id, attachment_id),
                          FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
                        );
                        PRAGMA user_version=2;
                        """
                    )
                    version = 2
                if version == 2:
                    db.executescript(
                        """
                        CREATE TABLE server_configs (
                          account_id TEXT PRIMARY KEY,
                          imap_host TEXT NOT NULL, imap_port INTEGER NOT NULL,
                          smtp_host TEXT NOT NULL, smtp_port INTEGER NOT NULL,
                          username TEXT NOT NULL, use_starttls INTEGER NOT NULL,
                          FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                        );
                        PRAGMA user_version=3;
                        """
                    )
                    version = 3
                if version == 3:
                    db.executescript(
                        """
                        ALTER TABLE accounts ADD COLUMN avatar_url TEXT NOT NULL DEFAULT '';
                        PRAGMA user_version=4;
                        """
                    )

    def upsert_account(self, account: Account) -> None:
        with self.transaction() as db:
            db.execute(
                """INSERT INTO accounts
                     (id, display_name, address, provider, colour, enabled, avatar_url)
                     VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name,
                   address=excluded.address, provider=excluded.provider,
                   colour=excluded.colour, enabled=excluded.enabled,
                   avatar_url=excluded.avatar_url""",
                (account.id, account.display_name, account.address, account.provider,
                 account.colour, account.enabled, account.avatar_url),
            )

    def accounts(self) -> tuple[Account, ...]:
        rows = self._connection.execute("SELECT * FROM accounts ORDER BY rowid").fetchall()
        return tuple(Account(row["id"], row["display_name"], row["address"],
                             row["provider"], row["colour"], bool(row["enabled"]),
                             row["avatar_url"]) for row in rows)

    def delete_account(self, account_id: str) -> None:
        with self.transaction() as db:
            message_ids = tuple(
                row["id"] for row in db.execute(
                    "SELECT id FROM messages WHERE account_id=?", (account_id,)
                ).fetchall()
            )
            db.execute("DELETE FROM accounts WHERE id=?", (account_id,))
            if message_ids:
                placeholders = ",".join("?" for _ in message_ids)
                db.execute(f"DELETE FROM message_search WHERE id IN ({placeholders})", message_ids)

    def upsert_server_config(self, account_id: str, config: ServerConfig) -> None:
        with self.transaction() as db:
            db.execute(
                """INSERT INTO server_configs VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(account_id) DO UPDATE SET
                   imap_host=excluded.imap_host, imap_port=excluded.imap_port,
                   smtp_host=excluded.smtp_host, smtp_port=excluded.smtp_port,
                   username=excluded.username, use_starttls=excluded.use_starttls""",
                (account_id, config.imap_host, config.imap_port, config.smtp_host,
                 config.smtp_port, config.username, config.use_starttls),
            )

    def server_config(self, account_id: str) -> ServerConfig | None:
        row = self._connection.execute(
            "SELECT * FROM server_configs WHERE account_id=?", (account_id,)
        ).fetchone()
        if row is None:
            return None
        return ServerConfig(
            row["imap_host"], row["imap_port"], row["smtp_host"],
            row["smtp_port"], row["username"], bool(row["use_starttls"]),
        )

    def server_configs(self) -> dict[str, ServerConfig]:
        return {
            account.id: config
            for account in self.accounts()
            if (config := self.server_config(account.id)) is not None
        }

    def _attachments(self, message_id: str, value: str) -> tuple[Attachment, ...]:
        payloads = {
            row["attachment_id"]: bytes(row["content"])
            for row in self._connection.execute(
                "SELECT attachment_id, content FROM attachment_payloads WHERE message_id=?",
                (message_id,),
            ).fetchall()
        }
        return tuple(Attachment(**item, data=payloads.get(item["id"], b"")) for item in json.loads(value))

    def _message(self, row: sqlite3.Row) -> Message:
        return Message(
            id=row["id"], account_id=row["account_id"], folder=row["folder"], uid=row["uid"],
            message_id=row["message_id"], thread_id=row["thread_id"], subject=row["subject"],
            sender_name=row["sender_name"], sender_address=row["sender_address"],
            recipients=tuple(json.loads(row["recipients"])), sent_at=datetime.fromisoformat(row["sent_at"]),
            snippet=row["snippet"], body_text=row["body_text"], body_html=row["body_html"],
            unread=bool(row["unread"]), flagged=bool(row["flagged"]), outgoing=bool(row["outgoing"]),
            attachments=self._attachments(row["id"], row["attachments"]),
            references=tuple(json.loads(row["refs"])),
        )

    def upsert_message(self, message: Message) -> None:
        attachment_json = json.dumps([
            {"id": value.id, "filename": value.filename, "content_type": value.content_type,
             "size": value.size, "content_id": value.content_id}
            for value in message.attachments
        ])
        values = (
            message.id, message.account_id, message.folder, message.uid, message.message_id,
            message.thread_id, message.subject, message.sender_name, message.sender_address,
            json.dumps(message.recipients), message.sent_at.isoformat(), message.snippet,
            message.body_text, message.body_html, message.unread, message.flagged,
            message.outgoing, attachment_json, json.dumps(message.references),
        )
        with self.transaction() as db:
            db.execute(
                """INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET folder=excluded.folder, uid=excluded.uid,
                   thread_id=excluded.thread_id, subject=excluded.subject,
                   sender_name=excluded.sender_name, sender_address=excluded.sender_address,
                   recipients=excluded.recipients, sent_at=excluded.sent_at,
                   snippet=excluded.snippet, body_text=excluded.body_text,
                   body_html=excluded.body_html, unread=excluded.unread,
                   flagged=excluded.flagged, outgoing=excluded.outgoing,
                   attachments=excluded.attachments, refs=excluded.refs""",
                values,
            )
            db.execute("DELETE FROM message_search WHERE id=?", (message.id,))
            db.execute(
                "INSERT INTO message_search VALUES (?,?,?,?,?)",
                (message.id, message.subject, f"{message.sender_name} {message.sender_address}",
                 " ".join(message.recipients), message.body_text),
            )
            db.execute("DELETE FROM attachment_payloads WHERE message_id=?", (message.id,))
            db.executemany(
                "INSERT INTO attachment_payloads VALUES (?,?,?)",
                ((message.id, value.id, value.data) for value in message.attachments if value.data),
            )

    def set_read(self, message_ids: tuple[str, ...], read: bool) -> None:
        if not message_ids:
            return
        placeholders = ",".join("?" for _ in message_ids)
        with self.transaction() as db:
            db.execute(f"UPDATE messages SET unread=? WHERE id IN ({placeholders})",
                       (not read, *message_ids))

    def set_flagged(self, message_ids: tuple[str, ...], flagged: bool) -> None:
        if not message_ids:
            return
        placeholders = ",".join("?" for _ in message_ids)
        with self.transaction() as db:
            db.execute(f"UPDATE messages SET flagged=? WHERE id IN ({placeholders})",
                       (flagged, *message_ids))

    def move_messages(self, message_ids: tuple[str, ...], folder: str) -> None:
        if not message_ids:
            return
        placeholders = ",".join("?" for _ in message_ids)
        with self.transaction() as db:
            db.execute(f"UPDATE messages SET folder=? WHERE id IN ({placeholders})",
                       (folder, *message_ids))

    def conversations(
        self, folder: str = "inbox", query: str = "", account_id: str | None = None
    ) -> tuple[Conversation, ...]:
        parameters: list[object] = []
        condition = "1=1"
        if account_id:
            condition += " AND m.account_id=?"
            parameters.append(account_id)
        rows = self._connection.execute(
            f"SELECT m.* FROM messages m WHERE {condition} ORDER BY m.sent_at DESC",
            parameters,
        ).fetchall()
        messages = tuple(self._message(row) for row in rows)

        matching_ids: set[str] | None = None
        if query.strip():
            expression = " ".join(
                f'"{token.replace(chr(34), "")}"*' for token in query.split()
            )
            search_parameters: list[object] = [expression]
            search_condition = "s.message_search MATCH ?"
            if account_id:
                search_condition += " AND m.account_id=?"
                search_parameters.append(account_id)
            matching_ids = {
                row["id"] for row in self._connection.execute(
                    "SELECT m.id FROM messages m JOIN message_search s ON s.id=m.id "
                    f"WHERE {search_condition}",
                    search_parameters,
                ).fetchall()
            }

        # IMAP conversation ancestry is authoritative when its root is in the
        # local window. Some providers omit References/In-Reply-To, however, so
        # orphan messages fall back to normalized subject + external peer. The
        # fallback remains account-scoped to avoid joining unrelated mailboxes.
        by_message_id = {
            message.message_id: message for message in messages if message.message_id
        }
        own_addresses = {
            account.address.casefold() for account in self.accounts()
        }
        key_cache: dict[str, str] = {}

        def conversation_key(message: Message, active: set[str] | None = None) -> str:
            if message.id in key_cache:
                return key_cache[message.id]
            active = set() if active is None else active
            if message.id not in active:
                active.add(message.id)
                for reference in message.references:
                    if parent := by_message_id.get(reference):
                        key = conversation_key(parent, active)
                        key_cache[message.id] = key
                        return key
            if message.references:
                key = f"{message.account_id}:{message.thread_id}"
            else:
                external = sorted({
                    address.casefold()
                    for address in (message.sender_address, *message.recipients)
                    if address and address.casefold() not in own_addresses
                })
                peer = external[0] if external else message.sender_address.casefold()
                seed = f"{message.account_id}\0{normalized_subject(message.subject).casefold()}\0{peer}"
                key = f"{message.account_id}:{sha256(seed.encode()).hexdigest()[:24]}"
            key_cache[message.id] = key
            return key

        grouped: dict[str, list[Message]] = {}
        candidates = (
            messages if folder in {"inbox", "sent"}
            else tuple(message for message in messages if message.folder == folder)
        )
        for message in candidates:
            grouped.setdefault(conversation_key(message), []).append(message)
        result = []
        for key, messages in grouped.items():
            ordered = tuple(sorted(messages, key=lambda item: item.sent_at))
            if not any(message.folder == folder for message in ordered):
                continue
            if matching_ids is not None and not any(message.id in matching_ids for message in ordered):
                continue
            result.append(Conversation(
                key, ordered[-1].subject, ordered,
                tuple(dict.fromkeys(item.account_id for item in ordered)),
            ))
        return tuple(sorted(result, key=lambda item: item.latest.sent_at, reverse=True))

    def unread_count(self, folder: str = "inbox", account_id: str | None = None) -> int:
        condition = "folder=?"
        parameters: tuple[object, ...] = (folder,)
        if account_id:
            condition += " AND account_id=?"
            parameters = (folder, account_id)
        return int(self._connection.execute(
            f"SELECT count(*) FROM messages WHERE {condition} AND unread=1", parameters
        ).fetchone()[0])

    def messages_by_ids(self, message_ids: tuple[str, ...]) -> tuple[Message, ...]:
        if not message_ids:
            return ()
        placeholders = ",".join("?" for _ in message_ids)
        rows = self._connection.execute(
            f"SELECT * FROM messages WHERE id IN ({placeholders})", message_ids
        ).fetchall()
        indexed = {row["id"]: self._message(row) for row in rows}
        return tuple(indexed[value] for value in message_ids if value in indexed)

    def search_message_ids(self, terms: tuple[str, ...], limit: int = 20) -> tuple[str, ...]:
        query = " ".join(term.strip() for term in terms if term.strip())
        if not query:
            return ()
        conversations = self.conversations("inbox", query)[:max(1, min(limit, 50))]
        return tuple(conversation.latest.id for conversation in conversations)

    def close(self) -> None:
        with self._lock:
            self._connection.close()
