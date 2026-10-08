"""Narrow adapters over existing Prairie services; no remote D-Bus forwarding.

The host supplies stores and senders. Nothing imports or opens personal state
by default. Network dispatch must pass Journal authorization before calling.
"""
import base64
import hashlib
import tempfile
from pathlib import Path
from dataclasses import asdict


class NativeMessages:
    def __init__(self, store, sender, *, pair_token, authorized, mms_transport=None, send_authorized=None):
        self.store, self.sender = store, sender
        self.pair_token, self.authorized = pair_token, authorized
        self.mms_transport = mms_transport
        self.send_authorized = send_authorized or authorized

    def __call__(self, capability, payload):
        if not self.authorized():
            raise PermissionError("grant revoked")
        if capability == "messages.send":
            if not self.send_authorized(): raise PermissionError("sending revoked")
            if "attachments" in payload:
                return self._send_mms(payload)
            if set(payload) not in ({"address", "body", "operation"}, {"address", "body", "operation", "retry_of"}):
                raise ValueError("invalid send fields")
            if not all(isinstance(value, str) for value in payload.values()):
                raise ValueError("invalid send values")
            # Existing native ReplySender owns modem dispatch and durable store.
            uid, state = self.sender.send(payload["address"], self.pair_token,
                payload["operation"], payload["body"], authorized=self.send_authorized,
                **({"retry_of":payload["retry_of"]} if "retry_of" in payload else {}))
            return {"uid": uid, "state": state}
        if capability == "messages.read":
            if set(payload) == {"query", "limit"}:
                if (not isinstance(payload["query"], str) or len(payload["query"]) > 128
                        or type(payload["limit"]) is not int or not 1 <= payload["limit"] <= 100):
                    raise ValueError("invalid thread query")
                rows = self.store.threads(payload["query"])
                return {"threads": [asdict(row) for row in rows[:payload["limit"]]],
                        "truncated": len(rows) > payload["limit"]}
            if set(payload) == {"address", "limit"}:
                if not isinstance(payload["address"], str) or type(payload["limit"]) is not int or not 1 <= payload["limit"] <= 100:
                    raise ValueError("invalid query")
                rows = self.store.thread(payload["address"])[-payload["limit"]:]
                messages = []
                for row in rows:
                    record = asdict(row)
                    # Local paths/transport handles never become remote capabilities.
                    record.pop("transport_id", None)
                    record.pop("wire_body", None)
                    for attachment in record.get("attachments", ()):
                        attachment.pop("storage_key", None)
                    messages.append(record)
                return {"messages": messages}
            if set(payload) == {"message", "attachment", "offset", "length"}:
                if (not isinstance(payload["message"], str) or not isinstance(payload["attachment"], str)
                        or type(payload["offset"]) is not int or payload["offset"] < 0
                        or type(payload["length"]) is not int or not 1 <= payload["length"] <= 49152):
                    raise ValueError("invalid attachment query")
                message = self.store.message(payload["message"])
                attachment = next((a for a in message.attachments if a.uid == payload["attachment"]), None)
                if attachment is None or payload["offset"] > attachment.size:
                    raise ValueError("attachment unavailable")
                with self.store.attachment_path(attachment).open("rb") as source:
                    source.seek(payload["offset"])
                    data = source.read(min(payload["length"], attachment.size - payload["offset"]))
                return {"data": base64.b64encode(data).decode("ascii"), "offset": payload["offset"], "size": attachment.size}
        raise NotImplementedError("native capability unavailable")

    def _send_mms(self, payload):
        if self.mms_transport is None:
            raise NotImplementedError("MMS adapter unavailable")
        if (set(payload) != {"address", "body", "operation", "attachments"}
                or not all(isinstance(payload[key], str) for key in ("address", "body", "operation"))
                or not isinstance(payload["attachments"], list) or not 1 <= len(payload["attachments"]) <= 4
                or len(payload["body"].encode()) > 4096):
            raise ValueError("invalid picture message")
        from .policy import IDENTIFIER
        if not IDENTIFIER.fullmatch(payload["operation"]):
            raise ValueError("invalid operation")
        address = self.store.canonical_address(payload["address"])
        parts = []
        total = 0
        for item in payload["attachments"]:
            if (not isinstance(item, dict) or set(item) != {"name", "data"}
                    or not isinstance(item["name"], str) or not 1 <= len(item["name"]) <= 128
                    or any(char in item["name"] for char in ("/", "\\", "\x00"))
                    or not isinstance(item["data"], str) or len(item["data"]) > 700000):
                raise ValueError("invalid picture attachment")
            data = base64.b64decode(item["data"], validate=True)
            total += len(data)
            if not data or total > 512 * 1024:
                raise ValueError("picture transfer limit")
            parts.append((item["name"], data))
        uid = "continuity-mms:" + hashlib.sha256((self.pair_token + ":" + payload["operation"]).encode()).hexdigest()
        # The receiver journal durably fences this operation before this method.
        # A pre-existing message can only be reconciled, never dispatched again.
        try:
            prior = self.store.message(uid)
        except KeyError:
            prior = None
        if prior is not None:
            return {"uid": uid, "state": "unknown"}
        capability = self.mms_transport.inspect()
        if not capability.available:
            return {"error": "mms-unavailable"}
        if not self.send_authorized():
            raise PermissionError("grant revoked")
        attachments = []
        try:
            with tempfile.TemporaryDirectory(prefix=".continuity-mms-", dir=self.store.path.parent) as directory:
                for index, (name, data) in enumerate(parts):
                    path = Path(directory) / str(index)
                    path.write_bytes(data); path.chmod(0o600)
                    attachments.append(self.store.attach_file(address, path, name=name))
                native_parts = [(a.uid, a.content_type, str(self.store.attachment_path(a))) for a in attachments]
                if payload["body"]:
                    caption = Path(directory) / "caption.txt"
                    caption.write_text(payload["body"], encoding="utf-8"); caption.chmod(0o600)
                    native_parts.insert(0, ("caption.txt", "text/plain", str(caption)))
                self.mms_transport.validate_parts(capability, native_parts)
                self.store.add(address, payload["body"], direction="outgoing", state="sending", uid=uid,
                    attachment_uids=tuple(a.uid for a in attachments))
                if not self.send_authorized():
                    self.store.update_state(uid, "failed")
                    raise PermissionError("grant revoked")
                # SendMessage acceptance is queued, never carrier-delivered.
                path = self.mms_transport.send((address,), native_parts)
                self.store.update_state(uid, "queued", "mmsd:" + path)
        finally:
            # Remove only drafts created by this operation on pre-dispatch failure.
            # Already linked outgoing attachments are retained for reconciliation.
            for attachment in attachments:
                self.store.remove_draft_attachment(address, attachment.uid)
        return {"uid": uid, "state": "queued"}


class NativeContacts:
    def __init__(self, load_contacts, *, authorized):
        self.load_contacts, self.authorized = load_contacts, authorized

    def __call__(self, capability, payload):
        if capability != "contacts.read" or not self.authorized():
            raise PermissionError("contacts not approved")
        if (set(payload) != {"search", "limit"} or not isinstance(payload["search"], str)
                or len(payload["search"]) > 128 or type(payload["limit"]) is not int
                or not 1 <= payload["limit"] <= 100):
            raise ValueError("invalid contacts query")
        rows = self.load_contacts(payload["search"])
        if not self.authorized():
            raise PermissionError("contacts grant revoked")
        return {"contacts": [asdict(row) for row in rows[:payload["limit"]]],
                "truncated": len(rows) > payload["limit"]}


class NativeCalls:
    """Live native call controls; IDs are short-lived session capabilities.

    A single native event executor must serialize refresh/control/revocation.
    Media routing remains a separate capability; no audio is captured here.
    """
    def __init__(self, transport, *, authorized, now, control_authorized=lambda:False, audio_prepare=None,
                 voice_available=lambda:True):
        self.transport, self.authorized, self.now = transport, authorized, now
        self.control_authorized=control_authorized
        self.audio_prepare=audio_prepare
        self.voice_available=voice_available
        self.calls = {}
        self.expires = 0
        self.dial_token=None
        self.generations={}

    def active_audio_generations(self):
        if not self.authorized():return []
        result=[self.generations[(call.call_id,call.started_at)] for call in self.transport.calls()[:8]
            if call.phase.value=='active' and (call.call_id,call.started_at) in self.generations]
        return result if self.authorized() else []

    def incoming(self):
        if not self.authorized():return False
        ringing=any(call.direction=='incoming' and call.phase.value=='incoming' for call in self.transport.calls())
        return ringing and self.authorized()

    def invalidate(self, *, preserve_generations=False):
        self.calls.clear(); self.expires = 0
        self.dial_token=None
        if not preserve_generations:self.generations.clear()

    def __call__(self, capability, payload):
        import secrets
        if not self.authorized():
            self.invalidate()
            raise PermissionError("calls not approved")
        if capability == "calls.read" and payload == {"operation": "snapshot"}:
            self.invalidate(preserve_generations=True)
            result = []
            generations={}
            for call in self.transport.calls()[:8]:
                token = secrets.token_hex(16)
                self.calls[token] = call
                key=(call.call_id,call.started_at)
                generations[key]=self.generations.get(key) or secrets.token_hex(16)
                result.append({"id": token, "address": call.address,
                               "direction": call.direction, "phase": call.phase.value,
                               'generation':generations[key], 'started_at':call.started_at,
                               'answered_at':call.answered_at})
            self.generations=generations
            self.expires = self.now() + 15
            available=self.voice_available() is True
            if available and not any(row['phase'] not in {'ended','failed','idle'} for row in result):
                self.dial_token=secrets.token_hex(16)
            return {"calls": result, 'dial_token':self.dial_token,'voice_available':available}
        if capability == "calls.audio":
            from .relay_control import identifier
            if (self.audio_prepare is None or set(payload)!={'operation','call','generation','attempt_id'}
                    or payload['operation']!='prepare' or not isinstance(payload['call'],str)
                    or not isinstance(payload['generation'],str)):
                raise ValueError('explicit audio preparation required')
            attempt=identifier(payload['attempt_id'])
            call=self.calls.pop(payload['call'],None)
            if not call or self.now()>=self.expires:raise PermissionError('audio call capability expired')
            current=next((row for row in self.transport.calls() if row.call_id==call.call_id),None)
            if (not current or current.started_at!=call.started_at or current.phase.value!='active'
                    or self.generations.get((call.call_id,call.started_at))!=payload['generation']
                    or not self.authorized()):raise PermissionError('active audio call changed')
            self.audio_prepare(current,payload['generation'],attempt)
            return {'prepared':True}
        if capability != "calls.control":
            raise NotImplementedError("call capability unavailable")
        if not self.control_authorized():
            self.invalidate();raise PermissionError('call control not approved')
        if payload.get('operation')=='dial':
            if self.voice_available() is not True:
                self.dial_token=None
                return {'accepted':False,'reason':'voice_unavailable'}
            if (set(payload)!={'operation','token','address'} or not isinstance(payload['token'],str)
                    or not isinstance(payload['address'],str)):
                raise ValueError('invalid dial request')
            token=self.dial_token;self.dial_token=None
            if not token or not secrets.compare_digest(token,payload['token']) or self.now()>=self.expires:
                raise PermissionError('dial capability expired')
            from prairie_apps.messages_backend import normalize_address
            address=normalize_address(payload['address'])
            if not address or len(address)>32:raise ValueError('invalid dial address')
            if any(call.phase.value not in {'ended','failed','idle'} for call in self.transport.calls()):
                raise PermissionError('phone already has a call')
            if not self.authorized() or not self.control_authorized():raise PermissionError('call control revoked')
            self.transport.dial(address)
            return {'accepted':True}
        if (set(payload) != {"operation", "call"} or not isinstance(payload["call"], str)
                or not isinstance(payload["operation"], str)
                or payload["operation"] not in {"answer", "decline", "hangup"}):
            raise ValueError("unsupported call operation")
        call = self.calls.pop(payload["call"], None)
        if not call or self.now() >= self.expires:
            raise PermissionError("call capability expired")
        current = next((row for row in self.transport.calls() if row.call_id == call.call_id), None)
        if not current or current.started_at != call.started_at or current.phase != call.phase:
            raise PermissionError("call state changed")
        if not self.authorized() or not self.control_authorized():
            self.invalidate();raise PermissionError('call control revoked')
        if payload["operation"] == "answer":
            if current.direction != "incoming" or current.phase.value != "incoming":
                raise PermissionError("call is not ringing")
            self.transport.accept(current.call_id)
        else:
            if payload['operation']=='decline' and (current.direction!='incoming' or current.phase.value!='incoming'):
                raise PermissionError('call is not ringing')
            if current.phase.value in {"ended", "failed", "idle"}:
                raise PermissionError("call has ended")
            if payload["operation"] == "decline":
                self.transport.decline(current.call_id)
            else:
                self.transport.hangup(current.call_id)
        return {"accepted": True}
