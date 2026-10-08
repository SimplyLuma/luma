#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""A Luma helper (luma-messages-bridge/1 plus luma.*) with a fake network, for tests only.

It answers the way the real helper (src/luma-messages-e2ee) does for the calls
Messages makes, with made-up people (@bob, @carol) and no cryptography. It
writes what Messages asked of it to ``<data-dir>/calls.jsonl``, and the session
key it was handed to ``<data-dir>/session``, so a test can check both.
"""
import json
from pathlib import Path
import sys
import threading

data_dir = Path(sys.argv[sys.argv.index("--data-dir") + 1])
lock = threading.Lock()
BOB = "acct-bob-00000001"
CAROL = "acct-carol-0000002"
PEOPLE = {"bob": {"account": BOB, "handle": "bob", "display_name": "Bob Example", "hue": 200},
          "carol": {"account": CAROL, "handle": "carol", "display_name": "Carol Example", "hue": 40}}
state = {"handle": None, "session": None, "blocked": set(), "verified": False}


def send(obj):
    with lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def event(name, data):
    send({"event": name, "data": data})


def record(command, args):
    with open(data_dir / "calls.jsonl", "a") as stream:
        stream.write(json.dumps({"cmd": command, "args": args}) + "\n")


def conversation(peer, *, request=False, hidden=False):
    person = PEOPLE[peer]
    return {"id": f"u:{person['account']}", "name": person["display_name"], "kind": "direct",
            "participants": [{"id": person["account"], "name": person["display_name"], "handle": "@" + peer}],
            "updated": 1700000000, "unread": 1, "archived": hidden,
            "luma": {"encrypted": True, "request": request, "request_from": person["account"] if request else "",
                     "verified": state["verified"], "safety_changed": False, "devices_changed": False, "removed": False,
                     "hidden": hidden, "account": person["account"], "handle": "@" + peer}}


def handle(command, args):
    record(command, args)
    if command == "hello":
        return {"protocol": "luma-messages-bridge/1", "network": "luma", "helper_version": "fake",
                "capabilities": {"login": [], "media": True, "max_media_bytes": 26214400, "groups": True,
                                 "create_conversations": True, "unofficial": False, "media_fetch": True, "encrypted": True}}
    if command == "session.load":
        state["session"] = args.get("session")
        (data_dir / "session").write_text(state["session"] or "")
        return {}
    if command == "connect":
        if not state["session"]:
            event("status", {"state": "needs_login", "detail": "keyring"})
            return {}
        event("status", {"state": "connected"})
        event("conversation", conversation("carol", request=True))
        event("message", {"id": "m-carol-1", "conversation": f"u:{CAROL}", "sender": {"id": CAROL, "name": "Carol Example"},
                          "outgoing": False, "text": "Hi, we met at the meetup", "time": 1700000000, "state": "received",
                          "attachments": [], "encrypted": True})
        return {}
    if command == "conversations.list":
        return {"conversations": [conversation("carol", request=True)]}
    if command == "messages.list":
        return {"messages": [], "more": False}
    if command == "status":
        return {"state": "connected", "account": {"name": "Alice Example", "handle": f"@{state['handle']}" if state["handle"] else ""}}
    if command == "luma.identity":
        present = state["handle"]
        return {"handle": present, "profile_link": f"https://simplyluma.com/@{present}" if present else None,
                "app_link": f"luma-messages://u/{present}" if present else None, "changes_left": 3,
                "suggestions": [] if present else ["alice.example", "alice_e"], "rules": {"min": 3, "max": 30, "hold_days": 30},
                "account": "acct-alice-0000000", "name": "Alice Example"}
    if command == "luma.handle.check":
        taken = args.get("handle") in PEOPLE
        return {"available": False, "code": "handle_taken", "reason": "That username is taken."} if taken else \
            {"available": True, "handle": args.get("handle")}
    if command == "luma.handle.claim":
        state["handle"] = args.get("handle")
        return handle("luma.identity", {})
    if command == "luma.people":
        person = PEOPLE.get(str(args.get("handle", "")).lstrip("@"))
        if person is None:
            raise Refused("handle_unknown", "No one on Luma has that username.")
        return {**person, "blocked": person["account"] in state["blocked"]}
    if command == "luma.devices":
        return {"account": args.get("account"), "devices": [{"device": "d1"}] if args.get("account") == BOB else [], "revoked": []}
    if command == "conversation.create":
        who = str(args["participants"][0]).lstrip("@")
        if who not in PEOPLE:
            raise Refused("handle_unknown", "No one on Luma has that username.")
        return {"conversation": conversation(who)}
    if command == "message.send":
        event("message", {"id": "sent-1", "conversation": args["conversation"], "sender": None, "outgoing": True,
                          "client_id": args["client_id"], "text": args["text"], "time": 1700000100, "state": "sent", "attachments": []})
        return {"message": "sent-1"}
    if command == "luma.request.answer":
        hidden = args.get("state") == "declined"
        event("conversation", conversation("carol", request=False, hidden=hidden))
        return {"state": args.get("state")}
    if command == "luma.block":
        state["blocked"].add(args.get("account"))
        event("conversation", conversation("carol", hidden=True))
        return {"blocked": True}
    if command == "luma.safety":
        return {"digits": " ".join(["12345", "67890"] * 6), "qr": "luma-safety:1:AAAA", "verified": state["verified"],
                "changed": False, "devices": 1}
    if command == "luma.verify":
        state["verified"] = bool(args.get("verified", True))
        return {"verified": state["verified"]}
    if command == "luma.report":
        return {"id": "r1", "blocked": bool(args.get("block"))}
    if command in ("message.read", "media.fetch", "logout"):
        return {}
    raise Refused("unsupported", "")


class Refused(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def main():
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        ident, command, args = message.get("id"), message.get("cmd"), message.get("args") or {}
        if command == "shutdown":
            send({"id": ident, "ok": True, "result": {}})
            return
        try:
            send({"id": ident, "ok": True, "result": handle(command, args)})
        except Refused as error:
            send({"id": ident, "ok": False, "error": {"code": error.code, "message": error.message, "retryable": False}})


if __name__ == "__main__":
    main()
