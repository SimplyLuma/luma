#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
"""A local stand-in for the Hub: the identity routes the helper uses and the
Luma Messages delivery service, v1 (docs/research/luma-messages-delivery-api.md).

It keeps to the contract where the contract is checkable without an MLS
library: it reads the clear header of every PrivateMessage and refuses one
whose group, epoch or content type differs from the envelope, enforces epochs
on commits, fans out by the membership the committing client declares, hands
out each one-time key package once, and applies requests and blocks the way
identity.mjs's mayDeliver does. It never decrypts anything, because it cannot.

Only for tests. `/fake/*` routes set up accounts and devices, revoke a device,
switch on a sabotage for a check's red run, and dump everything stored so a
test can prove no plaintext reached the server.

    python3 fake_hub.py --port 0 --ready-file PATH
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

CIPHERSUITE = 0x0003
ACCOUNT = r"[A-Za-z0-9_-]{8,200}"
HANDLE = re.compile(r"^[a-z0-9](?:[a-z0-9]|[._](?=[a-z0-9])){2,29}$")
MAX_CIPHERTEXT = 256 * 1024
MAX_BLOB = 25 * 1024 * 1024 + 1024
VERBOSE = os.environ.get("LUMA_FAKE_HUB_VERBOSE") == "1"


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", text):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Refused(Exception):
    def __init__(self, status: int, code: str, message: str = "", **extra) -> None:
        super().__init__(message or code)
        self.status, self.code, self.message, self.extra = status, code, message or code, extra


# ── TLS reading (RFC 9420 §2.1.2 variable-length integers) ─────────────────

class Reader:
    def __init__(self, data: bytes) -> None:
        self.data, self.at = data, 0

    def take(self, n: int) -> bytes:
        if self.at + n > len(self.data):
            raise ValueError("truncated")
        out = self.data[self.at:self.at + n]
        self.at += n
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return int.from_bytes(self.take(2), "big")

    def u64(self) -> int:
        return int.from_bytes(self.take(8), "big")

    def varint(self) -> int:
        first = self.u8()
        prefix = first >> 6
        if prefix == 0:
            return first & 0x3F
        if prefix == 1:
            return ((first & 0x3F) << 8) | self.u8()
        if prefix == 2:
            return ((first & 0x3F) << 24) | int.from_bytes(self.take(3), "big")
        raise ValueError("invalid varint")

    def vec(self) -> bytes:
        return self.take(self.varint())


def private_header(data: bytes) -> tuple[bytes, int, int]:
    """(group_id, epoch, content_type) of an MLSMessage carrying a PrivateMessage."""
    r = Reader(data)
    if r.u16() != 1 or r.u16() != 2:
        raise ValueError("not a PrivateMessage")
    return r.vec(), r.u64(), r.u8()


def is_welcome(data: bytes) -> bool:
    r = Reader(data)
    return r.u16() == 1 and r.u16() == 3


def key_package_fields(data: bytes) -> tuple[int, bytes, bytes, set[int], int]:
    """(cipher_suite, signature_key, identity, extension types, not_after) of an
    MLSMessage KeyPackage (RFC 9420 §10), read field by field."""
    r = Reader(data)
    if r.u16() != 1 or r.u16() != 5:
        raise ValueError("not a key package message")
    if r.u16() != 1:
        raise ValueError("not mls10")
    suite = r.u16()
    r.vec()  # init_key
    r.vec()  # leaf encryption_key
    signature_key = r.vec()
    if r.u16() != 1:
        raise ValueError("not a basic credential")
    identity = r.vec()
    for _ in range(5):  # capabilities: versions, ciphersuites, extensions, proposals, credentials
        r.vec()
    if r.u8() != 1:
        raise ValueError("leaf_node_source is not key_package")
    r.u64()
    not_after = r.u64()
    r.vec()  # leaf extensions
    r.vec()  # leaf signature
    extensions = Reader(r.vec())
    types = set()
    while extensions.at < len(extensions.data):
        types.add(extensions.u16())
        extensions.vec()
    r.vec()  # key package signature
    if r.at != len(data):
        raise ValueError("trailing bytes")
    return suite, signature_key, identity, types, not_after


# ── State ──────────────────────────────────────────────────────────────────

class Hub:
    def __init__(self) -> None:
        self.lock = threading.Condition()
        self.users: dict[str, dict] = {}          # account -> {name, handle, joined, only_accepted}
        self.devices: dict[str, dict] = {}        # device -> {account, token, key, revoked, registered_at}
        self.tokens: dict[str, str] = {}          # token -> device
        self.connections: dict[tuple[str, str], str] = {}  # (user, peer) -> accepted|requested|declined
        self.blocks: set[tuple[str, str]] = set()  # (user, blocked)
        self.reports: list[dict] = []
        self.key_packages: dict[str, list[str]] = {}
        self.last_resort: dict[str, str] = {}
        self.groups: dict[str, dict] = {}          # gid -> {epoch, members, ever}
        self.queue: dict[str, list[dict]] = {}     # device -> items
        self.seq = 0
        self.blobs: dict[str, bytes] = {}
        self.revoked: dict[str, list[dict]] = {}
        # Sabotage for a check's red run, and a hostile server that keeps
        # delivering to removed and revoked devices.
        self.sabotage: set[str] = set()
        self.hostile = False
        self.counts = {"application": 0, "commit": 0, "welcome": 0}
        # Every ciphertext the server ever accepted, and deliveries per device,
        # for tests: what a server saw, not only what is still queued.
        self.archive: list[str] = []
        self.delivered: dict[str, int] = {}

    # Accounts and devices, set up by tests.
    def add_user(self, account: str, name: str) -> None:
        self.users[account] = {"name": name, "handle": None, "joined": time.time(), "only_accepted": False}

    def add_device(self, account: str) -> dict:
        device, token = str(uuid.uuid4()), secrets.token_urlsafe(32)
        self.devices[device] = {"account": account, "token": token, "key": None, "revoked": False, "registered_at": 0}
        self.tokens[token] = device
        return {"device": device, "token": token}

    def authenticate(self, headers) -> dict:
        value = headers.get("Authorization", "")
        device = self.tokens.get(value[7:]) if value.startswith("Bearer ") else None
        if device is None or (self.devices[device]["revoked"] and not self.hostile):
            raise Refused(401, "", "Sign in again.")
        return {"device": device, **self.devices[device]}

    def registered(self, who: dict) -> None:
        if not self.devices[who["device"]]["key"]:
            raise Refused(403, "not_registered", "Register a Messages client first.")

    def revoke(self, device: str) -> None:
        d = self.devices[device]
        d["revoked"] = True
        if d["key"]:
            self.revoked.setdefault(d["account"], []).append({"device": device, "signature_key": d["key"], "revoked_at": int(time.time() * 1000)})
        if not self.hostile:
            self.queue.pop(device, None)
            self.key_packages.pop(device, None)
            self.last_resort.pop(device, None)
            for g in self.groups.values():
                if device in g["members"]:
                    g["members"].remove(device)

    # Identity, as identity.mjs.
    def handle_row(self, handle: str):
        canonical = handle.lower().lstrip("@")
        return next((a for a, u in self.users.items() if u["handle"] and u["handle"].lower() == canonical), None)

    def person(self, account: str, viewer: str | None = None) -> dict:
        u = self.users[account]
        out = {"account": account, "handle": u["handle"], "display_name": u["name"], "hue": sum(account.encode()) % 360,
               "profile_link": f"https://simplyluma.com/@{u['handle']}" if u["handle"] else None}
        if viewer:
            out["blocked"] = (viewer, account) in self.blocks
        return out

    def request_conversation(self, sender: str, to: str) -> dict:
        if sender == to:
            raise Refused(400, "self", "That’s your own account.")
        if (sender, to) in self.blocks:
            raise Refused(409, "blocked_by_you", "You blocked this person. Unblock them to send a message.")
        if self.connections.get((to, sender)) == "accepted":
            self.connections[(sender, to)] = "accepted"
            return {"state": "accepted"}
        if (to, sender) in self.blocks:
            self.connections[(sender, to)] = "accepted"
            return {"state": "pending"}
        if self.users[to]["only_accepted"]:
            raise Refused(403, "accepted_only", "This person only takes messages from people they’ve accepted.")
        if (to, sender) not in self.connections:
            self.connections[(to, sender)] = "requested"
        self.connections[(sender, to)] = "accepted"
        return {"state": "pending"}

    def may_deliver(self, sender: str, to: str) -> str | None:
        """The folder a message lands in, or None when it is dropped."""
        if sender == to:
            return "inbox"
        if (to, sender) in self.blocks and "no_blocks" not in self.sabotage:
            return None
        if "no_requests" in self.sabotage:
            return "inbox"
        state = self.connections.get((to, sender))
        if state is None:
            try:
                self.request_conversation(sender, to)
            except Refused:
                return None
            state = self.connections.get((to, sender))
        return {"accepted": "inbox", "requested": "requests"}.get(state)

    def enqueue(self, device: str, kind: str, folder: str, ciphertext: str, message_id: str) -> None:
        self.delivered[device] = self.delivered.get(device, 0) + 1
        self.seq += 1
        self.queue.setdefault(device, []).append({"seq": self.seq, "id": message_id, "kind": kind, "folder": folder,
                                                  "received_at": int(time.time() * 1000), "ciphertext": ciphertext})

    def send(self, who: dict, gid: str, body: dict) -> dict:
        allowed = {"application": {"kind", "epoch", "ciphertext"}, "commit": {"kind", "epoch", "ciphertext", "members"},
                   "welcome": {"kind", "epoch", "ciphertext", "recipients"}}
        kind = body.get("kind")
        if kind not in allowed or set(body) - allowed[kind] or not {"kind", "epoch", "ciphertext"} <= set(body):
            raise Refused(400, "fields", "Unsupported fields.")
        if not isinstance(body["epoch"], int) or body["epoch"] < 0:
            raise Refused(400, "fields", "epoch must be a number.")
        try:
            raw = unb64u(body["ciphertext"])
            group = unb64u(gid)
        except ValueError:
            raise Refused(400, "fields", "Not base64url.") from None
        if len(raw) > MAX_CIPHERTEXT:
            raise Refused(413, "too_large")
        if not 16 <= len(group) <= 64:
            raise Refused(400, "fields", "group id size")
        sender = who["device"]
        state = self.groups.get(gid)
        if kind == "welcome":
            if not is_welcome(raw):
                raise Refused(400, "ciphertext_invalid", "Not a Welcome.")
        else:
            try:
                header_group, header_epoch, content = private_header(raw)
            except ValueError:
                raise Refused(400, "ciphertext_invalid", "Not a PrivateMessage.") from None
            if header_group != group or header_epoch != body["epoch"] or content != {"application": 1, "commit": 3}[kind]:
                raise Refused(400, "ciphertext_invalid", "The header disagrees with the envelope.")
        if state is None:
            if kind != "commit":
                raise Refused(404, "group_unknown")
            state = self.groups[gid] = {"epoch": body["epoch"], "members": [sender], "ever": {sender}}
        if sender not in state["members"]:
            raise Refused(403, "not_member")
        message_id = secrets.token_urlsafe(16)
        self.counts[kind] += 1
        self.archive.append(body["ciphertext"])
        if kind == "commit":
            members = body.get("members")
            if not isinstance(members, list) or not members or len(members) > 1000 or sender not in members:
                raise Refused(400, "fields", "members")
            if body["epoch"] != state["epoch"]:
                raise Refused(409, "epoch", "Another change came first.", epoch=state["epoch"])
            before = set(state["ever"]) if self.hostile else set(state["members"])
            state["epoch"] += 1
            state["members"] = [m for m in members if m in self.devices and self.devices[m]["key"]]
            state["ever"] |= set(state["members"])
            targets = before - {sender}
        elif kind == "welcome":
            recipients = body.get("recipients")
            if not isinstance(recipients, list) or not recipients or any(r not in state["members"] for r in recipients):
                raise Refused(403, "not_member", "A Welcome names a device outside the group.")
            if body["epoch"] != state["epoch"]:
                raise Refused(400, "ciphertext_invalid", "A Welcome joins the current epoch.")
            targets = set(recipients)
            if "drop_welcome" in self.sabotage:
                targets = set()
        else:
            if not state["epoch"] - 3 <= body["epoch"] <= state["epoch"]:
                raise Refused(400, "ciphertext_invalid", "Too old an epoch.")
            targets = (set(state["ever"]) if self.hostile else set(state["members"])) - {sender}
            if "drop_application" in self.sabotage:
                targets = set()
        sender_account = who["account"]
        for device in sorted(targets):
            d = self.devices.get(device)
            if d is None or (d["revoked"] and not self.hostile):
                continue
            folder = self.may_deliver(sender_account, d["account"])
            if folder is None:
                continue
            self.enqueue(device, kind, folder, body["ciphertext"], message_id)
        self.lock.notify_all()
        return {"id": message_id, "epoch": state["epoch"]}

    def claim(self, who: dict, accounts: list) -> dict:
        if not isinstance(accounts, list) or not 1 <= len(accounts) <= 20:
            raise Refused(400, "fields", "accounts")
        out = []
        for account in accounts:
            if account not in self.users:
                raise Refused(404, "account_unknown")
            devices = []
            for device, d in sorted(self.devices.items()):
                if d["account"] != account or device == who["device"] or not d["key"] or d["revoked"]:
                    continue
                packages = self.key_packages.get(device) or []
                if packages:
                    kp, last = packages.pop(0), False
                elif device in self.last_resort:
                    kp, last = self.last_resort[device], True
                else:
                    continue
                devices.append({"device": device, "identity": f"luma1:{account}:{device}", "signature_key": d["key"],
                                "key_package": kp, "last_resort": last})
            out.append({"account": account, "devices": devices})
        return {"accounts": out}

    def check_key_package(self, who: dict, text: str, last_resort: bool) -> str:
        try:
            raw = unb64u(text)
            suite, signature_key, identity, extensions, not_after = key_package_fields(raw)
        except ValueError:
            raise Refused(400, "key_package_invalid", "Not a key package.") from None
        if len(raw) > 8192:
            raise Refused(400, "key_package_invalid", "Too large.")
        if suite != CIPHERSUITE:
            raise Refused(400, "key_package_invalid", "Wrong cipher suite.")
        if identity != f"luma1:{who['account']}:{who['device']}".encode():
            raise Refused(400, "key_package_invalid", "Wrong identity.")
        if b64u(signature_key) != self.devices[who["device"]]["key"]:
            raise Refused(400, "key_package_invalid", "Not the registered key.")
        if not_after <= time.time():
            raise Refused(400, "key_package_invalid", "Expired.")
        if (0x000A in extensions) != last_resort:
            raise Refused(400, "key_package_invalid", "last_resort marking.")
        return text

    def counts_for(self, device: str) -> dict:
        return {"available": len(self.key_packages.get(device, [])), "last_resort": device in self.last_resort,
                "low_water": 20, "capacity": 100}

    def dump(self) -> dict:
        return {"queue": self.queue, "blobs": {k: b64u(v) for k, v in self.blobs.items()}, "groups":
                {g: {"epoch": s["epoch"], "members": s["members"]} for g, s in self.groups.items()},
                "reports": self.reports, "counts": self.counts, "archive": self.archive, "delivered": self.delivered}


HUB = Hub()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:
        pass

    def reply(self, status: int, body, content_type: str = "application/json", headers: dict | None = None) -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def body(self) -> bytes:
        # Read once, always, so an unread body never becomes the start of the
        # next request on a kept-alive connection.
        if not hasattr(self, "_body"):
            length = int(self.headers.get("Content-Length") or 0)
            self._body = self.rfile.read(length) if length else b""
        return self._body

    def json_body(self):
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise Refused(415, "", "Send JSON.")
        try:
            return json.loads(self.body() or b"null")
        except ValueError:
            raise Refused(400, "fields", "Not JSON.") from None

    def do_GET(self): self.handle_any("GET")
    def do_POST(self): self.handle_any("POST")
    def do_PUT(self): self.handle_any("PUT")
    def do_DELETE(self): self.handle_any("DELETE")

    def handle_any(self, method: str) -> None:
        url = urlparse(self.path)
        self.__dict__.pop("_body", None)
        self.body()
        try:
            status, body = self.route(method, url.path, parse_qs(url.query))
            if isinstance(body, bytes):
                self.reply(status, body, "application/octet-stream")
            else:
                self.reply(status, body)
        except (BrokenPipeError, ConnectionResetError):
            return
        except (BrokenPipeError, ConnectionResetError):
            return
        except Refused as e:
            if VERBOSE:
                print(f"fake hub: {method} {url.path} -> {e.status} {e.code}", file=sys.stderr)
            payload = {"error": e.message, "message": e.message, **e.extra}
            if e.code:
                payload["code"] = e.code
            self.reply(e.status, payload)

    def route(self, method: str, path: str, query: dict):
        hub = HUB
        # Long poll waits outside the lock's hold.
        if method == "GET" and path == "/api/messages/v1/queue":
            with hub.lock:
                who = hub.authenticate(self.headers)
                hub.registered(who)
            after = int((query.get("after") or ["0"])[0])
            limit = max(1, min(200, int((query.get("limit") or ["100"])[0])))
            wait = max(0, min(50, int((query.get("wait") or ["0"])[0])))
            deadline = time.time() + wait
            with hub.lock:
                while True:
                    items = [i for i in hub.queue.get(who["device"], []) if i["seq"] > after]
                    if items or time.time() >= deadline:
                        break
                    hub.lock.wait(timeout=min(1.0, deadline - time.time()))
                page = items[:limit]
                return 200, {"items": page, "next": page[-1]["seq"] if page else after, "more": len(items) > limit}
        with hub.lock:
            return self.route_locked(hub, method, path, query)

    def route_locked(self, hub: Hub, method: str, path: str, query: dict):
        # ── test control ──
        if path.startswith("/fake/"):
            data = self.json_body() if method == "POST" else None
            if path == "/fake/users":
                hub.add_user(data["account"], data["name"])
                return 200, {}
            if path == "/fake/devices":
                return 200, hub.add_device(data["account"])
            if path == "/fake/revoke":
                hub.revoke(data["device"])
                return 200, {}
            if path == "/fake/config":
                hub.sabotage = set(data.get("sabotage", []))
                hub.hostile = bool(data.get("hostile", False))
                return 200, {}
            if path == "/fake/dump":
                return 200, hub.dump()
            raise Refused(404, "", "Not found.")

        who = hub.authenticate(self.headers)
        account = who["account"]
        # ── identity ──
        if path == "/api/hub/sync/account" and method == "GET":
            u = hub.users[account]
            return 200, {"account": {"name": u["name"]}, "profile": {"display_name": u["name"]}, "device": {"id": who["device"]}}
        if path == "/api/hub/sync/identity/handle":
            u = hub.users[account]
            if method == "PUT":
                data = self.json_body()
                if not isinstance(data, dict) or set(data) != {"handle"} or not isinstance(data["handle"], str):
                    raise Refused(400, "fields")
                text = data["handle"].strip().lstrip("@")
                if not HANDLE.match(text.lower()):
                    raise Refused(400, "handle_shape", "Start and end with a letter or digit.")
                owner = hub.handle_row(text)
                if owner and owner != account:
                    raise Refused(409, "handle_taken", "That username is taken.")
                u["handle"] = text
            handle = u["handle"]
            return 200, {"handle": handle, "profile_link": f"https://simplyluma.com/@{handle.lower()}" if handle else None,
                         "app_link": f"luma-messages://u/{handle.lower()}" if handle else None, "changes_left": 3,
                         "suggestions": [] if handle else [u["name"].split()[0].lower() + ".luma"],
                         "rules": {"min": 3, "max": 30, "changes_per_year": 3, "hold_days": 30}}
        if path == "/api/hub/sync/identity/handle/check":
            text = (query.get("handle") or [""])[0].lstrip("@")
            if not HANDLE.match(text.lower()):
                return 200, {"available": False, "code": "handle_shape", "reason": "Start and end with a letter or digit."}
            owner = hub.handle_row(text)
            if owner and owner != account:
                return 200, {"available": False, "code": "handle_taken", "reason": "That username is taken."}
            return 200, {"available": True, "handle": text}
        m = re.fullmatch(r"/api/hub/sync/people/(@?[A-Za-z0-9._]{1,40})", path)
        if m and method == "GET":
            owner = hub.handle_row(m.group(1))
            if owner is None:
                raise Refused(404, "handle_unknown", "No one on Luma has that username.")
            return 200, hub.person(owner, account)
        if path == "/api/hub/sync/chat/requests" and method == "GET":
            items = [dict(hub.person(peer), created_at=0) for (user, peer), s in hub.connections.items()
                     if user == account and s == "requested" and (account, peer) not in hub.blocks]
            return 200, {"items": items}
        if path == "/api/hub/sync/chat/requests" and method == "POST":
            data = self.json_body()
            peer = data.get("account") or hub.handle_row(data.get("handle", ""))
            if peer not in hub.users:
                raise Refused(404, "account_unknown")
            return 200, hub.request_conversation(account, peer)
        m = re.fullmatch(rf"/api/hub/sync/chat/requests/({ACCOUNT})", path)
        if m and method == "PUT":
            state = self.json_body().get("state")
            if state not in ("accepted", "declined"):
                raise Refused(400, "fields")
            if (account, m.group(1)) not in hub.connections:
                raise Refused(404, "request_unknown")
            hub.connections[(account, m.group(1))] = state
            return 200, {"state": state}
        if path == "/api/hub/sync/chat/blocks" and method == "GET":
            return 200, {"items": [hub.person(b) for (u, b) in hub.blocks if u == account]}
        m = re.fullmatch(rf"/api/hub/sync/chat/blocks/({ACCOUNT})", path)
        if m and method in ("PUT", "DELETE"):
            if m.group(1) not in hub.users:
                raise Refused(404, "account_unknown")
            (hub.blocks.add if method == "PUT" else hub.blocks.discard)((account, m.group(1)))
            return 200, {"blocked": method == "PUT"}
        if path == "/api/hub/sync/chat/reports" and method == "POST":
            data = self.json_body()
            if data.get("reason") not in ("spam", "harassment", "impersonation", "scam", "other"):
                raise Refused(400, "fields")
            hub.reports.append({"reporter": account, **data})
            if data.get("block"):
                hub.blocks.add((account, data["account"]))
            return 201, {"id": secrets.token_hex(12), "blocked": bool(data.get("block"))}

        # ── delivery ──
        v1 = "/api/messages/v1"
        if path == f"{v1}/devices" and method == "POST":
            data = self.json_body()
            try:
                key = unb64u(data.get("signature_key", ""))
            except ValueError:
                raise Refused(400, "fields") from None
            if len(key) != 32 or set(data) != {"signature_key"}:
                raise Refused(400, "fields")
            d = hub.devices[who["device"]]
            status = 200 if d["key"] == b64u(key) else 201
            if d["key"] and d["key"] != b64u(key):
                hub.revoked.setdefault(account, []).append({"device": who["device"], "signature_key": d["key"], "revoked_at": int(time.time() * 1000)})
                hub.queue.pop(who["device"], None)
                hub.key_packages.pop(who["device"], None)
                hub.last_resort.pop(who["device"], None)
            d["key"], d["registered_at"] = b64u(key), int(time.time() * 1000)
            return status, {"account": account, "device": who["device"], "identity": f"luma1:{account}:{who['device']}", "registered_at": d["registered_at"]}
        hub.registered(who)
        if path == f"{v1}/devices/self":
            if method == "DELETE":
                hub.revoke(who["device"])
                return 200, {"unregistered": True}
            return 200, {"account": account, "device": who["device"], "identity": f"luma1:{account}:{who['device']}",
                         "signature_key": hub.devices[who["device"]]["key"], "key_packages": hub.counts_for(who["device"]),
                         "queued": len(hub.queue.get(who["device"], []))}
        m = re.fullmatch(rf"{v1}/accounts/({ACCOUNT})/devices", path)
        if m and method == "GET":
            target = m.group(1)
            if target not in hub.users:
                raise Refused(404, "account_unknown")
            devices = [{"device": dev, "identity": f"luma1:{target}:{dev}", "signature_key": d["key"], "registered_at": d["registered_at"]}
                       for dev, d in sorted(hub.devices.items()) if d["account"] == target and d["key"] and not d["revoked"]]
            return 200, {"account": target, "devices": devices, "revoked": hub.revoked.get(target, [])}
        if path == f"{v1}/key-packages":
            if method == "GET":
                return 200, hub.counts_for(who["device"])
            data = self.json_body()
            if not isinstance(data, dict) or not data or set(data) - {"key_packages", "last_resort"}:
                raise Refused(400, "fields")
            packages = data.get("key_packages") or []
            checked = [hub.check_key_package(who, p, False) for p in packages]
            last = hub.check_key_package(who, data["last_resort"], True) if "last_resort" in data else None
            stored = hub.key_packages.setdefault(who["device"], [])
            fresh = [p for p in checked if p not in stored]
            if len(stored) + len(fresh) > 100:
                raise Refused(409, "key_packages_full")
            stored.extend(fresh)
            if last:
                hub.last_resort[who["device"]] = last
            return 200, hub.counts_for(who["device"])
        if path == f"{v1}/key-packages/claim" and method == "POST":
            return 200, hub.claim(who, self.json_body().get("accounts"))
        m = re.fullmatch(rf"{v1}/groups/([A-Za-z0-9_-]{{22,86}})/messages", path)
        if m and method == "POST":
            return 201, hub.send(who, m.group(1), self.json_body())
        m = re.fullmatch(rf"{v1}/groups/([A-Za-z0-9_-]{{22,86}})", path)
        if m and method == "GET":
            state = hub.groups.get(m.group(1))
            if state is None:
                raise Refused(404, "group_unknown")
            if who["device"] not in state["members"]:
                raise Refused(403, "not_member")
            return 200, {"group": m.group(1), "epoch": state["epoch"]}
        if path == f"{v1}/queue/ack" and method == "POST":
            seqs = set(self.json_body().get("seqs") or [])
            before = hub.queue.get(who["device"], [])
            hub.queue[who["device"]] = [i for i in before if i["seq"] not in seqs]
            return 200, {"deleted": len(before) - len(hub.queue[who["device"]])}
        if path == f"{v1}/blobs" and method == "POST":
            data = self.body()
            if len(data) > MAX_BLOB:
                raise Refused(413, "too_large")
            blob = secrets.token_urlsafe(32)
            hub.blobs[blob] = data
            return 201, {"blob": blob, "size": len(data), "expires_at": int((time.time() + 30 * 86400) * 1000)}
        m = re.fullmatch(rf"{v1}/blobs/([A-Za-z0-9_-]{{43}})", path)
        if m and method == "GET":
            if m.group(1) not in hub.blobs:
                raise Refused(404, "blob_unknown")
            return 200, hub.blobs[m.group(1)]
        raise Refused(404, "", "Not found.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--ready-file", required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    with open(args.ready_file, "w") as stream:
        stream.write(str(server.server_address[1]))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
