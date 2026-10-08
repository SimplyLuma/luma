#!/usr/bin/env python3
"""A luma-messages-bridge/1 helper with a fake network, for tests only.

Sign-in: ``qr`` shows a code and finishes when Messages submits ``confirm``
(the test plays the phone); ``cookies`` asks for a browser, then shows an emoji
and finishes. The fake network remembers sends in ``<data-dir>/sent.jsonl`` and
``fake.incoming`` makes a message arrive.

Media: ``fake.incoming_media`` delivers a message whose attachments carry
states, ``fake.media`` reports a change to one of them, and ``media.fetch`` is
recorded in ``media-fetch.jsonl`` and answered from ``media-script.json``
(``{message: [response, ...]}``, one response used per fetch) or, without a
script, with the file downloaded.
"""
import json
import os
from pathlib import Path
import sys
import threading
import time

data_dir = Path(sys.argv[sys.argv.index("--data-dir") + 1])
lock = threading.Lock()
state = {"session": None, "sent": 0}
CONVERSATION = {"id": "chat.one", "name": "Fake Friend", "kind": "direct",
                "participants": [{"id": "friend", "name": "Fake Friend"}], "updated": 200, "unread": 1}
GROUP = {"id": "group.one", "name": "Fake Group", "kind": "group",
         "participants": [{"id": "friend", "name": "Fake Friend"}, {"id": "other", "name": "Other Person"}], "updated": 150, "unread": 0}
HISTORY = {
    "chat.one": [{"id": "m1", "conversation": "chat.one", "sender": {"id": "friend", "name": "Fake Friend"}, "outgoing": False,
                  "text": "Hello from the fake network", "time": 200, "state": "received", "attachments": []}],
    "group.one": [{"id": "g1", "conversation": "group.one", "sender": {"id": "other", "name": "Other Person"}, "outgoing": False,
                   "text": "Group hello", "time": 150, "state": "read", "attachments": []}],
}


def send(obj):
    with lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def event(name, data):
    send({"event": name, "data": data})


def finish_login():
    state["session"] = "fake-session-token"
    event("session", {"session": state["session"]})
    event("login.done", {"account": {"name": "Fake Person", "handle": "+15550100"}})


def media_part(message, conversation, spec):
    """An attachment descriptor; a spec with ``content`` is a file already downloaded."""
    part = {"part": spec.get("part", "0"), "mime": spec.get("mime", "image/jpeg"), "name": spec.get("name", "photo.jpg"),
            "size": spec.get("size", 0), "state": spec.get("state", "done"), "attempt": spec.get("attempt", 0)}
    for key in ("error", "retryable"):
        if key in spec:
            part[key] = spec[key]
    media = data_dir / "media"
    media.mkdir(mode=0o700, exist_ok=True)
    if "content" in spec:
        path = media / f"{message}-{part['part']}-{part['name']}"
        path.write_bytes(spec["content"].encode("latin-1"))
        part.update(path=str(path), size=path.stat().st_size, state="done")
    if "preview" in spec:
        preview = media / f"{message}-{part['part']}-preview.jpg"
        preview.write_bytes(spec["preview"].encode("latin-1"))
        part["preview"] = str(preview)
    return part


class NoAnswer(Exception):
    pass


class Refused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def handle(command, args):
    with (data_dir / "commands.jsonl").open("a") as stream:
        stream.write(json.dumps({"cmd": command, "user_token": bool(args.get("user_token"))}) + "\n")
    if command == "hello":
        return {"protocol": "luma-messages-bridge/1", "network": "fake", "helper_version": "test",
                "capabilities": {"login": ["qr", "cookies"], "media": True, "max_media_bytes": 1048576, "reactions": True, "reaction_emoji": ["😍", "😂", "👍"],
                                 "replies": True, "typing": True, "read_receipts": True, "groups": True,
                                 "create_conversations": True, "unofficial": True, "media_fetch": True}}
    if command == "session.load":
        state["session"] = args.get("session")
        return {}
    if command == "connect":
        stall = data_dir / "fake-stall-connect"
        if stall.exists():  # a connection that never finishes, once
            stall.unlink()
            event("status", {"state": "connecting"})
        elif state["session"] == "fake-session-token":
            event("status", {"state": "connecting"})
            event("status", {"state": "connected"})
        else:
            event("status", {"state": "needs_login"})
        return {}
    if command == "login.start":
        if args.get("method") == "cookies":
            event("login.browser", {"url": "https://fake.example/signin", "cookies": ["SID"], "domains": ["fake.example"]})
        else:
            event("login.qr", {"data": "fake-network://link/abc"})
        return {}
    if command == "login.submit":
        if args.get("field") == "cookies":
            cookies = json.loads(args.get("value") or "{}")
            if cookies.get("SID") != "cookie-value":
                raise ValueError("bad cookies")
            event("login.code", {"code": "🦊", "kind": "emoji"})
            return {}
        if args.get("field") == "confirm":
            finish_login()
            return {}
        raise ValueError("unknown field")
    if command == "login.cancel":
        return {}
    if command == "conversations.list":
        once = data_dir / "fail-conversations-once"
        if once.exists():
            once.unlink()
            raise TimeoutError("the phone didn't answer")
        for conversation in (CONVERSATION, GROUP):
            times = [int(item.get("time") or 0) for item in HISTORY.get(conversation["id"], [])]
            conversation["updated"] = max([int(conversation["updated"])] + times)
        return {"conversations": [CONVERSATION, GROUP]}
    if command == "messages.list":
        return {"messages": HISTORY.get(args["conversation"], []), "more": False}
    if command == "conversation.create":
        number = args["participants"][0]
        return {"conversation": {"id": "chat.new", "name": number, "kind": "direct", "participants": [{"id": number, "name": number}],
                                 "updated": 300, "unread": 0}}
    if command in {"message.send", "media.send", "message.react"}:
        # As the real helper: a delivery needs a person's token, used once, ever.
        token = args.get("user_token")
        if not isinstance(token, str) or len(token) != 32:
            raise Refused("blocked")
        if (data_dir.parent / "outbound-disabled").exists():
            raise Refused("outbound_disabled")
        ledger = data_dir / "fake-ledger.json"
        used = json.loads(ledger.read_text()) if ledger.exists() else []
        key = f"{command}:{token}:{args.get('part_index', 0)}"
        if key in used:
            raise Refused("already_attempted")
        ledger.write_text(json.dumps(used + [key]))
    if command in {"message.send", "media.send"}:
        state["sent"] += 1
        message_id = f"sent{state['sent']}-{os.getpid()}"
        with (data_dir / "sent.jsonl").open("a") as stream:
            stream.write(json.dumps({"cmd": command, **args}) + "\n")
        echo = {"id": message_id, "conversation": args["conversation"], "sender": None, "outgoing": True,
                "client_id": args["client_id"], "text": args.get("text") or args.get("caption") or "",
                "time": int(time.time()), "state": "sent", "attachments": []}
        # The network keeps what was delivered: a restarted helper lists it, with its client id.
        history = data_dir / "fake-sent-history.json"
        kept = json.loads(history.read_text()) if history.exists() else []
        history.write_text(json.dumps(kept + [echo]))
        HISTORY.setdefault(args["conversation"], []).append(echo)
        if (data_dir / "fake-exit-after-send").exists():
            (data_dir / "fake-exit-after-send").unlink()
            os._exit(3)  # delivered, then the helper dies before answering
        if (data_dir / "fake-swallow-send").exists():
            (data_dir / "fake-swallow-send").unlink()
            raise NoAnswer()  # delivered, but the answer and the echo never come
        event("message", echo)
        return {"message": message_id}
    if command == "message.react":
        with (data_dir / "reacted-commands.jsonl").open("a") as stream:
            stream.write(json.dumps(args) + "\n")
        if data_dir.joinpath("fail-react").exists():
            raise TimeoutError("the phone didn't answer")
        with (data_dir / "reacted.jsonl").open("a") as stream:
            stream.write(json.dumps(args) + "\n")
        return {}
    if command == "message.read":
        with (data_dir / "read.jsonl").open("a") as stream:
            stream.write(json.dumps(args) + "\n")
        return {}
    if command == "fake.incoming":
        # outgoing: a message this account wrote on another device (the phone).
        outgoing = bool(args.get("outgoing"))
        event("message", {"id": args["id"], "conversation": args.get("conversation", "chat.one"),
                          "sender": None if outgoing else {"id": "friend", "name": "Fake Friend"}, "outgoing": outgoing,
                          "text": args["text"], "time": args.get("time", int(time.time()) + 1),
                          "state": args.get("state", "sent" if outgoing else "received"), "attachments": []})
        return {}
    if command == "fake.conversation":
        event("conversation", {**CONVERSATION, **args})
        return {}
    if command == "fake.incoming_media":
        conversation = args.get("conversation", "chat.one")
        event("message", {"id": args["id"], "conversation": conversation, "sender": {"id": "friend", "name": "Fake Friend"},
                          "outgoing": bool(args.get("outgoing")), "text": args.get("text", ""),
                          "time": args.get("time", int(time.time()) + 1),
                          "state": "sent" if args.get("outgoing") else "received",
                          "attachments": [media_part(args["id"], conversation, spec) for spec in args.get("parts", [])]})
        return {}
    if command == "fake.media":
        spec = dict(args)
        if spec.get("part") is None:
            event("media", {"conversation": args.get("conversation", "chat.one"), "message": args["message"],
                            "state": args["state"], "error": args.get("error", ""), "retryable": args.get("retryable", True)})
        else:
            event("media", {"conversation": args.get("conversation", "chat.one"), "message": args["message"],
                            **media_part(args["message"], args.get("conversation", "chat.one"), spec)})
        return {}
    if command == "media.fetch":
        with (data_dir / "media-fetch.jsonl").open("a") as stream:
            stream.write(json.dumps(args) + "\n")
        script_file = data_dir / "media-script.json"
        script = json.loads(script_file.read_text()) if script_file.exists() else {}
        responses = script.get(args["message"])
        if responses:
            response = responses.pop(0)
            script_file.write_text(json.dumps(script))
        elif responses is not None:
            return {}  # scripted to stay silent
        else:
            response = {"part": args.get("part", "0"), "content": "\xff\xd8\xff fetched"}
        threading.Thread(target=lambda: fake_media_later(args, response), daemon=True).start()
        return {}
    if command == "fake.status":
        event("status", {"state": args["state"]})
        return {}
    if command == "fake.receipt":
        event("receipt", {"conversation": args["conversation"], "message": args["message"], "state": args["state"]})
        return {}
    if command == "fake.react":
        # A reaction added on the phone; the network reports it the next time
        # the message is listed.
        for message in HISTORY.get(args["conversation"], []):
            if message["id"] == args["message"]:
                message["reactions"] = args["reactions"]
        return {}
    if command == "logout":
        marker = data_dir.parent / "logged-out.json"
        marker.write_text(json.dumps({"account": data_dir.name, **args}))
        return {}
    if command == "shutdown":
        return {}
    raise LookupError(command)


def fake_media_later(args, response):
    time.sleep(0.05)  # after the answer, as a real download would be
    if response.get("part") is None:
        event("media", {"conversation": args["conversation"], "message": args["message"], "state": response["state"],
                        "error": response.get("error", ""), "retryable": response.get("retryable", True)})
    else:
        event("media", {"conversation": args["conversation"], "message": args["message"],
                        **media_part(args["message"], args["conversation"], response)})


def load_sent_history():
    path = data_dir / "fake-sent-history.json"
    if path.exists():
        for message in json.loads(path.read_text()):
            HISTORY.setdefault(message["conversation"], []).append(message)


def load_backlog():
    """Messages that "arrived while this device was away": kept on disk across helper restarts."""
    path = data_dir / "fake-backlog.json"
    if not path.exists():
        return
    for message in json.loads(path.read_text()):
        conversation = message.get("conversation", "chat.one")
        if not any(item["id"] == message["id"] for item in HISTORY.setdefault(conversation, [])):
            HISTORY[conversation].append(message)
        target = CONVERSATION if conversation == "chat.one" else GROUP
        target["updated"] = max(int(target["updated"]), int(message["time"]))


def watch_inbox():
    """Tests outside Messages drop ``<data-dir>/fake-inbox/*.json`` to make a message arrive now.

    ``{"id": ..., "text": ...}`` arrives as a live event; with ``"backlog": true`` it
    is only added to the history, as if it arrived while the account was offline.
    """
    inbox = data_dir / "fake-inbox"
    while True:
        try:
            for path in sorted(inbox.glob("*.json")):
                request = json.loads(path.read_text())
                path.unlink()
                message = {"id": request["id"], "conversation": request.get("conversation", "chat.one"),
                           "sender": {"id": "friend", "name": "Fake Friend"}, "outgoing": False,
                           "text": request["text"], "time": int(request.get("time") or time.time()),
                           "state": "received", "attachments": []}
                if request.get("backlog"):
                    backlog = data_dir / "fake-backlog.json"
                    items = json.loads(backlog.read_text()) if backlog.exists() else []
                    backlog.write_text(json.dumps(items + [message]))
                else:
                    event("message", message)
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.2)


load_backlog()
load_sent_history()
threading.Thread(target=watch_inbox, daemon=True).start()

for line in sys.stdin:
    request = json.loads(line)
    try:
        result = handle(request["cmd"], request.get("args") or {})
        send({"id": request["id"], "ok": True, "result": result})
    except NoAnswer:
        pass
    except Refused as refused:
        send({"id": request["id"], "ok": False, "error": {"code": refused.code, "message": refused.code}})
    except LookupError:
        send({"id": request["id"], "ok": False, "error": {"code": "unsupported", "message": request["cmd"]}})
    except Exception as error:  # noqa: BLE001 - a test helper reports every failure
        send({"id": request["id"], "ok": False, "error": {"code": "invalid", "message": str(error)}})
    if request["cmd"] == "shutdown":
        break
