"""Shared Messages provider: native cache + durable per-device outbound queue.

A source-owned connection worker flushes this queue over approved device TLS.
The UI can queue offline without invoking a local modem or claiming delivery.
"""
import base64
import json
import secrets
from pathlib import Path
from .queue import Outbox
from .policy import DIGEST, IDENTIFIER


class QueuedMessages:
    def __init__(self, directory, peer, epoch, *, label, approved, store_factory, account=None, capability_approved=None):
        if not DIGEST.fullmatch(peer) or not IDENTIFIER.fullmatch(epoch):
            raise ValueError("invalid selected device")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.peer, self.epoch = peer, epoch
        self.label, self.approved = label, approved
        self.account = account
        self.capability_approved = capability_approved or (lambda _capability: approved())
        self.store_factory = store_factory
        self.store_path = self.directory / "messages.db"
        self.outbox_path = self.directory / "outbox.db"
        box = Outbox(self.outbox_path)
        try:
            box.db.execute("CREATE TABLE IF NOT EXISTS remote_messages(remote TEXT PRIMARY KEY, local TEXT NOT NULL UNIQUE)")
        finally: box.close()

    def send_message(self, uid):
        if not self.approved() or not self.capability_approved("messages.send"):
            raise PermissionError("selected phone is no longer paired")
        store = self.store_factory(self.store_path)
        outbox = Outbox(self.outbox_path)
        try:
            record = store.message(uid)
            payload = {"address": record.address, "body": record.wire_body or record.body}
            if record.attachments:
                if len(record.attachments) > 4 or sum(a.size for a in record.attachments) > 512*1024:
                    raise ValueError("picture exceeds temporary transfer limit")
                payload["attachments"] = []
                for attachment in record.attachments:
                    with store.attachment_path(attachment).open('rb') as file:
                        data = file.read(512*1024 + 1)
                    if len(data) != attachment.size:
                        raise ValueError("attachment changed")
                    payload["attachments"].append({"name": attachment.name, "data": base64.b64encode(data).decode()})
            outbox.enqueue(self.peer, self.epoch, "messages.send", payload, operation_id=uid, account=self.account)
            store.update_state(uid, "queued")
            return {"state": "queued", "uid": uid}
        finally:
            outbox.close(); store.close()

    def retry_message(self, uid):
        """One explicit user retry of an uncertain SMS, keeping its logical ID."""
        if not self.approved() or not self.capability_approved("messages.send"):
            raise PermissionError("selected phone sending grant revoked")
        box, store = Outbox(self.outbox_path), self.store_factory(self.store_path)
        try:
            record = store.message(uid)
            if record.state == "sent": return {"state": "sent", "uid": uid}
            if record.direction != "outgoing" or record.attachments:
                raise ValueError("only an uncertain SMS can be retried")
            box.db.execute("BEGIN IMMEDIATE")
            row = box.db.execute("SELECT peer,epoch,envelope,state FROM outbox WHERE id=?", (uid,)).fetchone()
            if not row or row[:2] != (self.peer,self.epoch) or row[3] not in {"complete","unknown"}:
                raise ValueError("original send must be reviewed first")
            original = json.loads(row[2])
            payload = original.get("payload",{})
            if (original.get("account") != self.account or original.get("capability") != "messages.send"
                    or set(payload) != {"address","body"}
                    or payload["address"] != record.address or payload["body"] != (record.wire_body or record.body)):
                raise ValueError("original message changed")
            pending = box.db.execute("SELECT id FROM outbox WHERE peer=? AND epoch=? AND state='pending' AND json_extract(envelope,'$.payload.retry_of')=?",
                                     (self.peer,self.epoch,uid)).fetchone()
            if not pending:
                box.enqueue(self.peer,self.epoch,"messages.send",dict(payload,retry_of=uid),
                            operation_id=secrets.token_hex(16),account=self.account)
            box.db.execute("COMMIT")
            store.update_state(uid,"queued")
            return {"state":"queued","uid":uid}
        except Exception:
            if box.db.in_transaction: box.db.execute("ROLLBACK")
            raise
        finally: store.close();box.close()

    def flush(self, exchange):
        """Injected authenticated session exchange. Network errors preserve IDs."""
        if not self.approved() or not self.capability_approved("messages.send"):
            raise PermissionError("selected phone is no longer paired")
        outbox = Outbox(self.outbox_path)
        store = self.store_factory(self.store_path)
        try:
            for request in outbox.pending(self.peer, self.epoch):
                if not self.approved() or not self.capability_approved("messages.send"):
                    raise PermissionError("selected phone sending grant revoked")
                local_uid = request["payload"].get("retry_of", request["id"])
                response = exchange(request)
                if not self.approved():
                    raise PermissionError("selected phone grant revoked before receipt import")
                if response.get("state") in {"denied", "invalid"}:
                    # Never generate a replacement operation to bypass rejection.
                    outbox.acknowledge(request["id"], {"state": "unknown", "result": None})
                    continue
                if response.get("state") == "complete" and isinstance(response.get("result"), dict):
                    remote = response["result"].get("uid")
                    if isinstance(remote, str) and 0 < len(remote) <= 512:
                        outbox.db.execute("INSERT OR IGNORE INTO remote_messages VALUES(?,?)", (remote, local_uid))
                outbox.acknowledge(request["id"], response)
                if response["state"] == "complete" and response["result"]:
                    native_state = response["result"].get("state")
                    if native_state == "sent":
                        store.update_state(local_uid, "sent")
                    elif native_state == "unknown" and hasattr(store,"mark_send_uncertain"):
                        store.mark_send_uncertain(local_uid)
                    # queued/unknown remain queued for explicit reconciliation.
        finally:
            store.close(); outbox.close()

    def sms_transport(self):
        provider = self
        class SmsQueue:
            def inspect(self):
                from prairie_apps.messages_backend import MessagingCapability
                readable = provider.approved()
                allowed = readable and provider.capability_approved("messages.send")
                reason = (f"Messages via {provider.label}. Queued messages wait for a connection." if allowed
                          else f"Read-only messages via {provider.label}." if readable
                          else "Phone access is unavailable.")
                return MessagingCapability(allowed, reason)
            def snapshot(self):
                # The connection worker updates the same private native cache.
                # This method never queries the desktop modem.
                return ()
            def send(self, _address, _body):
                raise RuntimeError("continuity send requires a stable message identity")
        return SmsQueue()

    def mms_transport(self):
        provider = self
        class MmsQueue:
            def start(self, _on_message, on_capability):
                from prairie_apps.messages_mms import MmsCapability
                on_capability(MmsCapability(provider.approved() and provider.capability_approved("messages.send"),
                    f"Pictures via {provider.label}. Temporary transfer limit: 512 KiB.", "", 512*1024, 5))
            def stop(self): pass
            def cached_message(self, _path): return None
            def mark_read(self, _path): raise NotImplementedError("remote read sync unavailable")
            def delete(self, _path): raise NotImplementedError("remote deletion unavailable")
            def send(self, _recipients, _parts):
                raise RuntimeError("continuity send requires a stable message identity")
        return MmsQueue()

    def contacts(self):
        # Never silently merge/export this desktop's local contacts as phone data.
        # EDS-backed remote contacts cache integration remains separate work.
        return ()

    def sync_recent(self, exchange):
        """Pull bounded recent native records into this device's isolated cache.

        This is a bounded explicit refresh, not full history/deletion sync.
        It never reads the desktop's native default store or subscribes a modem.
        """
        import hashlib
        import secrets
        import tempfile
        import time
        def query(payload):
            if not self.approved() or not self.capability_approved("messages.read"):
                raise PermissionError("selected phone revoked")
            response = exchange(dict(version=1, epoch=self.epoch, id=secrets.token_hex(16),
                account=self.account, capability="messages.read", expires=int(time.time()) + 60, payload=payload))
            if not self.approved() or not self.capability_approved("messages.read") or response.get("state") != "complete" or not isinstance(response.get("result"), dict):
                raise PermissionError("message refresh unavailable")
            return response["result"]
        snapshot = query({"query": "", "limit": 100})
        threads = snapshot.get("threads")
        if not isinstance(threads, list) or len(threads) > 100:
            raise ValueError("invalid thread snapshot")
        box, store = Outbox(self.outbox_path), self.store_factory(self.store_path)
        imported = 0
        try:
            for thread in threads:
                if (not isinstance(thread, dict) or not isinstance(thread.get("address"), str)
                        or not isinstance(thread.get("display_name"), str) or len(thread["display_name"]) > 512):
                    raise ValueError("invalid remote thread")
                address = store.canonical_address(thread["address"])
                if not self.approved() or not self.capability_approved("messages.read"):
                    raise PermissionError("selected phone revoked")
                store.set_display_name(address, thread["display_name"])
                messages = query({"address": address, "limit": 100}).get("messages")
                if not isinstance(messages, list) or len(messages) > 100:
                    raise ValueError("invalid remote messages")
                for record in messages:
                    if (not isinstance(record, dict) or not isinstance(record.get("uid"), str)
                            or not 0 < len(record["uid"]) <= 512
                            or record.get("address") != address or not isinstance(record.get("body"), str)
                            or len(record["body"].encode()) > 65536
                            or type(record.get("timestamp")) is not int
                            or record.get("direction") not in {"incoming", "outgoing"}
                            or record.get("state") not in {"received", "read", "queued", "sending", "sent", "failed"}
                            or not isinstance(record.get("attachments"), list)):
                        raise ValueError("invalid remote message")
                    remote_uid = record["uid"]
                    mapping = box.db.execute("SELECT local FROM remote_messages WHERE remote=?", (remote_uid,)).fetchone()
                    uid = mapping[0] if mapping else "connect:" + hashlib.sha256((self.peer + self.epoch + remote_uid).encode()).hexdigest()
                    try:
                        existing = store.message(uid)
                    except KeyError:
                        existing = None
                    if existing:
                        if not self.approved() or not self.capability_approved("messages.read"):
                            raise PermissionError("selected phone revoked")
                        if record["direction"] == "outgoing" and record["state"] in {"queued", "sending", "sent", "failed"}:
                            store.update_state(uid, record["state"])
                            if record['state']=='sent':
                                attempts=box.db.execute("SELECT id FROM outbox WHERE id=? OR json_extract(envelope,'$.payload.retry_of')=?",(uid,uid)).fetchall()
                                for (attempt,) in attempts:
                                    box.reconcile_sent(attempt,self.peer,self.epoch,remote_uid,account=self.account)
                            elif record.get('send_status')=='uncertain' and hasattr(store,'mark_send_uncertain'):
                                store.mark_send_uncertain(uid)
                        continue
                    attachments = record["attachments"]
                    if len(attachments) > 4:
                        raise ValueError("too many remote attachments")
                    created = []
                    total = 0
                    try:
                        for part in attachments:
                            if (not isinstance(part, dict) or not isinstance(part.get("uid"), str)
                                    or not 0 < len(part["uid"]) <= 512 or not isinstance(part.get("name"), str)
                                    or not 0 < len(part["name"]) <= 128 or type(part.get("size")) is not int
                                    or not 0 < part["size"] <= 512*1024):
                                raise ValueError("unsupported remote attachment")
                            total += part["size"]
                            if total > 512*1024:
                                raise ValueError("remote picture exceeds temporary transfer limit")
                            data = bytearray()
                            while len(data) < part["size"]:
                                chunk = query({"message": remote_uid, "attachment": part["uid"],
                                    "offset": len(data), "length": min(49152, part["size"] - len(data))})
                                if (chunk.get("offset") != len(data) or chunk.get("size") != part["size"]
                                        or not isinstance(chunk.get("data"), str) or len(chunk["data"]) > 65536):
                                    raise ValueError("invalid attachment chunk")
                                decoded = base64.b64decode(chunk["data"], validate=True)
                                if not decoded or len(decoded) > min(49152, part["size"] - len(data)):
                                    raise ValueError("invalid attachment chunk size")
                                data.extend(decoded)
                            with tempfile.TemporaryDirectory(prefix=".connect-cache-", dir=store.path.parent) as temporary:
                                path = Path(temporary) / "part"
                                path.write_bytes(data); path.chmod(0o600)
                                created.append(store.attach_file(address, path, name=part["name"]))
                        if not self.approved() or not self.capability_approved("messages.read"):
                            raise PermissionError("selected phone revoked")
                        store.add(address, record["body"], direction=record["direction"], state=record["state"],
                            timestamp=record["timestamp"], uid=uid, transport_id="connect:" + hashlib.sha256(remote_uid.encode()).hexdigest(),
                            attachment_uids=tuple(a.uid for a in created))
                        box.db.execute("INSERT OR IGNORE INTO remote_messages VALUES(?,?)", (remote_uid, uid))
                        if record['direction']=='outgoing' and record['state']=='sent':
                            box.reconcile_sent(uid,self.peer,self.epoch,remote_uid,account=self.account)
                        imported += 1
                    finally:
                        for part in created: store.remove_draft_attachment(address, part.uid)
        finally:
            box.close(); store.close()
        return {"imported": imported, "truncated": bool(snapshot.get("truncated"))}
