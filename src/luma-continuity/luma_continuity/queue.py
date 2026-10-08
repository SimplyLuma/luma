"""Bounded durable sender queue. Reconnect resends identical request envelopes.

The receiver's durable journal makes lost acknowledgements safe to reconcile.
An explicit 'unknown' result is terminal until native truth/user action resolves
it; a new operation ID is never generated automatically to retry a send.
"""
import json
import secrets
import time
from .policy import Journal
from .transport import encode

QUEUEABLE = frozenset({"messages.send", "messages.read", "notifications.read", "contacts.read"})


class Outbox:
    def __init__(self, path):
        self.journal = Journal(path)
        self.db = self.journal.db
        self.db.execute('''CREATE TABLE IF NOT EXISTS outbox(
            id TEXT PRIMARY KEY, peer TEXT NOT NULL, epoch TEXT NOT NULL,
            envelope TEXT NOT NULL, state TEXT NOT NULL, result TEXT)''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS reconciled_sends(
            id TEXT PRIMARY KEY, peer TEXT NOT NULL, epoch TEXT NOT NULL,
            remote TEXT NOT NULL, state TEXT NOT NULL CHECK(state='sent'), observed_at INTEGER NOT NULL)''')

    def close(self): self.journal.close()

    def enqueue(self, peer, epoch, capability, payload, *, account=None, lifetime=3600, now=None, operation_id=None):
        if capability not in QUEUEABLE or type(lifetime) is not int or not 1 <= lifetime <= 86400:
            raise ValueError("operation cannot be queued")
        if self.db.execute("SELECT count(*) FROM outbox WHERE state='pending'").fetchone()[0] >= 100:
            raise ValueError("outbox full")
        operation = operation_id or secrets.token_hex(16)
        from .policy import IDENTIFIER, DIGEST
        if (not isinstance(operation, str) or not IDENTIFIER.fullmatch(operation)
                or not isinstance(peer, str) or not DIGEST.fullmatch(peer)
                or not isinstance(epoch, str) or not IDENTIFIER.fullmatch(epoch)):
            raise ValueError("invalid outbox identity")
        existing = self.db.execute("SELECT peer,epoch,envelope FROM outbox WHERE id=?", (operation,)).fetchone()
        if existing:
            previous = json.loads(existing[2])
            if (existing[:2] != (peer, epoch) or previous.get("capability") != capability
                    or previous.get("payload") != payload or previous.get("account") != account):
                raise ValueError("conflicting outgoing operation")
            return operation
        request = dict(version=1, epoch=epoch, id=operation, account=account,
            capability=capability, expires=int(time.time() if now is None else now) + lifetime,
            payload=payload)
        self.db.execute("INSERT INTO outbox VALUES(?,?,?,?,?,NULL)",
            (operation, peer, epoch, encode(request).decode(), "pending"))
        return operation

    def pending(self, peer, epoch, *, now=None):
        now = time.time() if now is None else now
        result = []
        for operation, text in self.db.execute("SELECT id,envelope FROM outbox WHERE peer=? AND epoch=? AND state='pending' ORDER BY rowid", (peer, epoch)).fetchall():
            request = json.loads(text)
            if request["expires"] <= now:
                self.db.execute("UPDATE outbox SET state='expired' WHERE id=?", (operation,))
            else: result.append(request)
        return result

    def acknowledge(self, operation, response):
        if (set(response) != {"state", "result"} or response["state"] not in {"complete", "unknown"}
                or response["result"] is not None and not isinstance(response["result"], dict)):
            raise ValueError("invalid receipt")
        self.db.execute("UPDATE outbox SET state=?,result=? WHERE id=? AND state='pending'",
            (response["state"], encode(response).decode(), operation))

    def revoke(self, peer):
        # Logical redaction includes completed/unknown receipts and queued payloads.
        # SQLite/WAL/media forensic erasure is not claimed.
        # Keep local IDs/tombstones; phone-side receiver journal remains authority.
        self.db.execute("UPDATE outbox SET state='revoked',envelope='{}',result=NULL WHERE peer=?", (peer,))
        self.db.execute('DELETE FROM reconciled_sends WHERE peer=?',(peer,))

    def reconcile_sent(self, operation, peer, epoch, remote, *, account):
        """Record authenticated native history separately from the old receipt.

        The caller must verify a current paired read and an outgoing SENT native
        record. This never queues, retries, or rewrites an uncertain receipt.
        """
        if not isinstance(account,str) or not account:return False
        row=self.db.execute('SELECT peer,epoch,state,envelope,result FROM outbox WHERE id=?',(operation,)).fetchone()
        if not row or row[:3]!=(peer,epoch,'complete'):return False
        envelope=json.loads(row[3]);receipt=json.loads(row[4]) if row[4] else {}
        result=receipt.get('result') or {}
        if (envelope.get('account')!=account or envelope.get('capability')!='messages.send'
                or receipt.get('state')!='complete' or result.get('state')!='unknown'
                or result.get('uid')!=remote):return False
        self.db.execute("INSERT OR IGNORE INTO reconciled_sends VALUES(?,?,?,?, 'sent',?)",
                        (operation,peer,epoch,remote,int(time.time())))
        return True
