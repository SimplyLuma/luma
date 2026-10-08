"""Durable receiver authorization and at-most-once dispatch journal.

A crash between a hardware side effect and its receipt is UNKNOWN, not retryable.
Pairing and grant methods are local administrative APIs, never network methods.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from .transport import encode

NATIVE_CAPABILITIES = frozenset({"messages.read", "messages.send", "notifications.read",
    "notifications.act", "contacts.read", "calls.read", "calls.control", "calls.audio",
    "screen.view", "screen.control"})
# ADR-021 companion capabilities, read from the receiver's side. Keep identical to
# src/luma-connect-android/protocol/.../Capabilities.kt.
COMPANION_CAPABILITIES = frozenset({"device.status", "device.ring", "clipboard.write",
    "clipboard.read", "links.open", "files.write", "notifications.mirror", "media.mirror",
    "media.control", "input.control", "camera.stream", "device.rotate", "dnd.set",
    "hotspot.request", "auth.request", "auth.response", "bluetooth.bond"})
CAPABILITIES = NATIVE_CAPABILITIES | COMPANION_CAPABILITIES
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[0-9a-f]{32}\Z")


class Denied(PermissionError):
    pass


class Journal:
    def __init__(self, path: Path):
        self.path=path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.parent.stat().st_mode & 0o077:
            raise PermissionError("state directory must be private")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        if path.stat().st_mode & 0o077:
            raise PermissionError("state file must be private")
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS continuity_control(
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            enabled INTEGER NOT NULL CHECK(enabled IN (0,1)));
          CREATE TABLE IF NOT EXISTS peers(
            fingerprint TEXT PRIMARY KEY, epoch TEXT NOT NULL,
            account TEXT, grants TEXT NOT NULL, revoked INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS pairing_epochs(peer TEXT, epoch TEXT, PRIMARY KEY(peer,epoch));
          CREATE TABLE IF NOT EXISTS requests(
            peer TEXT NOT NULL, epoch TEXT NOT NULL, id TEXT NOT NULL,
            digest TEXT NOT NULL, state TEXT NOT NULL, response TEXT,
            PRIMARY KEY(peer,epoch,id));
        ''')
        # Earlier experimental approvals applied the same grants in both
        # directions. Preserve those approvals on migration; new pairing uses
        # explicit incoming/outgoing sets and never infers reverse permission.
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(peers)')}
        if 'outgoing_grants' not in columns:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                columns = {row[1] for row in self.db.execute('PRAGMA table_info(peers)')}
                if 'outgoing_grants' not in columns:
                    self.db.execute("ALTER TABLE peers ADD COLUMN outgoing_grants TEXT NOT NULL DEFAULT '[]'")
                    self.db.execute('UPDATE peers SET outgoing_grants=grants')
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
        # Companion traffic (notifications, file chunks) would exhaust the fixed
        # request quota. Recording expiry lets tombstones of expired requests be
        # pruned: dispatch rejects an expired request before consulting the table.
        if 'expires' not in {row[1] for row in self.db.execute('PRAGMA table_info(requests)')}:
            try:self.db.execute('ALTER TABLE requests ADD COLUMN expires INTEGER')
            except sqlite3.OperationalError:
                if 'expires' not in {row[1] for row in self.db.execute('PRAGMA table_info(requests)')}:raise

    def enabled(self):
        from .quick_state import LinkIntent
        try:
            if not LinkIntent(self.path.parent.parent/'enabled.json').load():return False
        except (OSError,ValueError):return False
        row=self.db.execute('SELECT enabled FROM continuity_control WHERE singleton=1').fetchone()
        return row is None or row[0]==1

    def set_enabled(self, enabled):
        if type(enabled) is not bool:raise ValueError('boolean required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('INSERT INTO continuity_control VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET enabled=excluded.enabled',(int(enabled),))
            self.db.execute('COMMIT')
        finally:
            if self.db.in_transaction:self.db.execute('ROLLBACK')

    def close(self):
        self.db.close()

    def approve(self, peer: str, epoch: str, grants, *, account: str | None = None, outgoing_grants=None):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._approve_locked(peer, epoch, grants, account=account, outgoing_grants=outgoing_grants)
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def _approve_locked(self, peer, epoch, grants, *, account=None, outgoing_grants=None):
        """Used by local pairing to consume its invitation in the same transaction."""
        if not self.db.in_transaction: raise RuntimeError('approval transaction required')
        grants = set(grants)
        outgoing = grants if outgoing_grants is None else set(outgoing_grants)
        if (not DIGEST.fullmatch(peer) or not IDENTIFIER.fullmatch(epoch)
                or not grants <= CAPABILITIES or not outgoing <= CAPABILITIES or not (grants or outgoing)
                or (account is not None and (not isinstance(account, str) or not account))):
            raise ValueError("invalid approval")
        # Re-pairing must create a fresh epoch; old requests cannot become valid again.
        prior = self.db.execute("SELECT 1 FROM pairing_epochs WHERE peer=? AND epoch=?", (peer, epoch)).fetchone()
        if prior:
            raise ValueError("pairing requires a fresh epoch")
        self.db.execute("INSERT INTO pairing_epochs VALUES(?,?)", (peer, epoch))
        self.db.execute("INSERT OR REPLACE INTO peers(fingerprint,epoch,account,grants,revoked,outgoing_grants) VALUES(?,?,?,?,0,?)",
                        (peer, epoch, account, json.dumps(sorted(grants)), json.dumps(sorted(outgoing))))

    def rotate(self, old: str, new: str):
        """Moves an approved peer to a replacement certificate pin, keeping epoch, account and grants.

        The caller has already authenticated with `old` over mutual TLS and proven possession
        of the key behind `new`. The old pin stops being accepted in the same transaction.
        """
        if not DIGEST.fullmatch(old) or not DIGEST.fullmatch(new) or old == new:
            raise ValueError("invalid rotation")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT epoch,revoked FROM peers WHERE fingerprint=?", (old,)).fetchone()
            if not row or row[1]:
                raise Denied("device is not approved")
            if self.db.execute("SELECT 1 FROM peers WHERE fingerprint=?", (new,)).fetchone():
                raise Denied("replacement certificate is already paired")
            self.db.execute("UPDATE peers SET fingerprint=? WHERE fingerprint=?", (new, old))
            self.db.execute("INSERT OR IGNORE INTO pairing_epochs VALUES(?,?)", (new, row[0]))
            tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'companion_devices' in tables:
                self.db.execute("UPDATE companion_devices SET fingerprint=? WHERE fingerprint=?", (new, old))
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def revoke(self, peer: str):
        self.db.execute("UPDATE peers SET revoked=1 WHERE fingerprint=?", (peer,))

    def set_grants(self, peer: str, grants, *, outgoing_grants=None):
        grants = set(grants)
        if not grants <= CAPABILITIES:
            raise ValueError("invalid capability")
        if outgoing_grants is None:
            self.db.execute("UPDATE peers SET grants=? WHERE fingerprint=? AND revoked=0",
                            (json.dumps(sorted(grants)), peer))
        else:
            outgoing = set(outgoing_grants)
            if not outgoing <= CAPABILITIES:
                raise ValueError("invalid capability")
            self.db.execute("UPDATE peers SET grants=?,outgoing_grants=? WHERE fingerprint=? AND revoked=0",
                            (json.dumps(sorted(grants)), json.dumps(sorted(outgoing)), peer))

    def dispatch(self, peer: str, request: dict, handler, *, now=None):
        now = time.time() if now is None else now
        fields = {"version", "epoch", "id", "account", "capability", "expires", "payload"}
        if (set(request) != fields or type(request["version"]) is not int or request["version"] != 1
                or not isinstance(request["epoch"], str) or not IDENTIFIER.fullmatch(request["epoch"])
                or not isinstance(request["id"], str) or not IDENTIFIER.fullmatch(request["id"])
                or not isinstance(request["capability"], str)
                or request["capability"] not in CAPABILITIES
                or type(request["expires"]) is not int
                or not now < request["expires"] <= now + 86400
                or not isinstance(request["payload"], dict)):
            raise Denied("invalid or expired request")
        digest = hashlib.sha256(encode(request)).hexdigest()
        key = (peer, request["epoch"], request["id"])
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if not self.enabled():raise Denied('continuity disabled')
            row = self.db.execute("SELECT epoch,account,grants,revoked FROM peers WHERE fingerprint=?", (peer,)).fetchone()
            if (not row or row[3] or row[0] != request["epoch"] or row[1] != request["account"]
                    or request["capability"] not in json.loads(row[2])):
                raise Denied("device, account or capability not approved")
            previous = self.db.execute("SELECT digest,state,response FROM requests WHERE peer=? AND epoch=? AND id=?", key).fetchone()
            if previous:
                if previous[0] != digest:
                    raise Denied("request identity reused with different content")
                self.db.execute("COMMIT")
                return {"state": previous[1], "result": json.loads(previous[2]) if previous[2] else None}
            if self.db.execute("SELECT count(*) FROM requests WHERE peer=?", (peer,)).fetchone()[0] >= 10000:
                self.db.execute("DELETE FROM requests WHERE expires IS NOT NULL AND expires<?", (int(now),))
                if self.db.execute("SELECT count(*) FROM requests WHERE peer=?", (peer,)).fetchone()[0] >= 10000:
                    raise Denied("request quota exhausted; local maintenance required")
            self.db.execute("INSERT INTO requests(peer,epoch,id,digest,state,response,expires) VALUES(?,?,?,?,?,NULL,?)",
                            (*key, digest, "unknown", request["expires"]))
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        # Persist uncertainty before invoking any native side effect. Never auto retry.
        # Serialize revocation with admission to the effect. Revoke may wait for an
        # already admitted bounded call, but once it returns no later effect starts.
        # A crash here rolls back only this transaction; UNKNOWN above survives.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if not self.enabled():raise Denied('continuity disabled')
            current = self.db.execute("SELECT epoch,account,grants,revoked FROM peers WHERE fingerprint=?", (peer,)).fetchone()
            if (not current or current[3] or current[0] != request["epoch"]
                    or current[1] != request["account"]
                    or request["capability"] not in json.loads(current[2])):
                raise Denied("grant changed before dispatch")
            result = handler(request["capability"], request["payload"])
            encoded = encode(result).decode()
            self.db.execute("UPDATE requests SET state='complete',response=? WHERE peer=? AND epoch=? AND id=?", (encoded, *key))
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return {"state": "complete", "result": result}
