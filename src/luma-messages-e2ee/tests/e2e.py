#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
"""End to end: real `luma` helpers, speaking luma-messages-bridge/1 the way
Messages does, through tests/fake_hub.py.

Alice has two devices (laptop, desk), Bob one, Carol one. The run checks, in
order, and names the check that failed:

  request   Alice's first message to Bob lands in Bob's Requests; he cannot
            reply until he accepts, and then can.
  send      Alice's message reaches Bob, and her own other device, encrypted.
  receive   Bob's reply reaches both of Alice's devices; a photo arrives
            byte-for-byte.
  restart   Bob's helper killed with SIGKILL and started again reads what
            arrived meanwhile.
  group     Alice makes a group with Bob and Carol; Carol accepts; everyone
            reads everyone.
  safety    Alice and Bob see one safety number; a scanned code that differs
            is refused.
  removed   Alice's desk is revoked; her laptop removes it from every group;
            a hostile server that keeps delivering to the desk gives it
            ciphertext it cannot read, and Bob's verified number shows the change.
  block     Carol blocks Alice; Alice's next group message reaches Bob and is
            never queued for Carol.
  plaintext Nothing the server stored contains any text or photo sent.

`--break CHECK` sabotages the mechanism that check exists for (the server
drops application messages, drops Welcomes, ignores requests or blocks, or the
removal is never committed) and the run must then fail at that check.
`--self-test` runs every break and fails unless each one fails where it should.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BREAKS = ("request", "send", "group", "removed", "block")
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82")
TIMEOUT = float(os.environ.get("LUMA_E2E_TIMEOUT", "25"))


class CheckFailed(Exception):
    def __init__(self, check: str, message: str) -> None:
        super().__init__(f"{check}: {message}")
        self.check = check


class HelperError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class FakeHub:
    def __init__(self, work: Path) -> None:
        ready = work / "hub.port"
        self.process = subprocess.Popen([sys.executable, str(HERE / "fake_hub.py"), "--ready-file", str(ready)])
        deadline = time.time() + 10
        while not ready.exists() or not ready.read_text():
            if time.time() > deadline or self.process.poll() is not None:
                raise RuntimeError("the fake hub did not start")
            time.sleep(0.05)
        self.url = f"http://127.0.0.1:{ready.read_text()}"

    def call(self, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, method="POST" if data is not None else "GET",
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read() or b"{}")

    def stop(self) -> None:
        self.process.terminate()
        self.process.wait(timeout=5)


class Helper:
    """One device: its own HOME (with Luma Connect's device.json), its own
    account directory, and a helper process driven over stdin and stdout."""

    def __init__(self, binary: Path, work: Path, name: str, hub: FakeHub, account: str) -> None:
        self.name, self.binary = name, binary
        self.home = work / name / "home"
        self.data = work / name / "accounts" / f"luma-{secrets.token_hex(8)}"
        (self.home / ".local/share/luma/connect").mkdir(parents=True)
        self.data.mkdir(parents=True, mode=0o700)
        enrolled = hub.call("/fake/devices", {"account": account})
        self.device = enrolled["device"]
        device_file = self.home / ".local/share/luma/connect/device.json"
        device_file.write_text(json.dumps({"device_id": enrolled["device"], "token": enrolled["token"], "hub": hub.url,
                                           "name": name, "registered_at": ""}))
        device_file.chmod(0o600)
        self.key = base64.b64encode(os.urandom(32)).decode()
        self.events: list[tuple[str, dict]] = []
        self.cond = threading.Condition()
        self.results: dict[str, dict] = {}
        self.next = 0
        self.process = None
        self.stderr = work / name / "stderr.log"

    def start(self) -> None:
        env = {"PATH": os.environ.get("PATH", "/usr/bin"), "HOME": str(self.home), "LANG": "C.UTF-8"}
        self.process = subprocess.Popen([str(self.binary), "--data-dir", str(self.data)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=open(self.stderr, "ab"), env=env)
        threading.Thread(target=self._read, args=(self.process,), daemon=True).start()
        hello = self.call("hello", {"protocol": "luma-messages-bridge/1"})
        assert hello["network"] == "luma" and hello["capabilities"]["encrypted"], hello
        self.call("session.load", {"session": self.key})
        self.call("connect", {})
        self.wait("status", lambda d: d.get("state") == "connected", what="connected")

    def kill(self) -> None:
        self.process.send_signal(signal.SIGKILL)
        self.process.wait()

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            try:
                self.call("shutdown", {}, timeout=5)
            except Exception:
                pass
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def _read(self, process) -> None:
        for line in process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            with self.cond:
                if "id" in message:
                    self.results[str(message["id"])] = message
                elif "event" in message:
                    self.events.append((message["event"], message.get("data") or {}))
                self.cond.notify_all()

    def call(self, command: str, args: dict, timeout: float = TIMEOUT) -> dict:
        with self.cond:
            self.next += 1
            ident = f"{self.name}-{self.next}"
        self.process.stdin.write((json.dumps({"id": ident, "cmd": command, "args": args}) + "\n").encode())
        self.process.stdin.flush()
        deadline = time.time() + timeout
        with self.cond:
            while ident not in self.results:
                if time.time() > deadline:
                    raise TimeoutError(f"{self.name} did not answer {command}")
                self.cond.wait(0.2)
            message = self.results.pop(ident)
        if not message.get("ok"):
            error = message.get("error") or {}
            raise HelperError(error.get("code", "error"), error.get("message", ""))
        return message.get("result") or {}

    def send(self, conversation: str, text: str) -> dict:
        return self.call("message.send", {"conversation": conversation, "client_id": secrets.token_hex(16),
                                          "text": text, "user_token": secrets.token_hex(16)})

    def wait(self, event: str, predicate, *, what: str, timeout: float = TIMEOUT, check: str = "") -> dict:
        deadline = time.time() + timeout
        with self.cond:
            while True:
                for name, data in self.events:
                    if name == event and predicate(data):
                        return data
                if time.time() > deadline:
                    raise CheckFailed(check or "setup", f"{self.name} never saw {what}")
                self.cond.wait(0.2)

    def saw(self, event: str, predicate) -> bool:
        with self.cond:
            return any(name == event and predicate(data) for name, data in self.events)

    def message_text(self, text: str, **kw) -> dict:
        return self.wait("message", lambda d: d.get("text") == text, what=f"the message {text!r}", **kw)


def expect(check: str, condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(check, message)


def scenario(binary: Path, work: Path, sabotage: str | None) -> None:
    hub = FakeHub(work)
    helpers: list[Helper] = []
    try:
        accounts = {name: f"acct-{name}-{secrets.token_hex(6)}" for name in ("alice", "bob", "carol")}
        for name, account in accounts.items():
            hub.call("/fake/users", {"account": account, "name": name.title() + " Example"})
        laptop = Helper(binary, work, "alice-laptop", hub, accounts["alice"])
        desk = Helper(binary, work, "alice-desk", hub, accounts["alice"])
        bob = Helper(binary, work, "bob", hub, accounts["bob"])
        carol = Helper(binary, work, "carol", hub, accounts["carol"])
        helpers += [laptop, desk, bob, carol]
        for h in helpers:
            h.start()

        # Onboarding: claim a username; a taken one is refused.
        identity = bob.call("luma.identity", {})
        expect("setup", identity.get("handle") is None and identity.get("suggestions"), f"unclaimed identity {identity}")
        for h, handle in ((laptop, "alice"), (bob, "bob"), (carol, "carol")):
            claimed = h.call("luma.handle.claim", {"handle": handle})
            expect("setup", claimed.get("handle") == handle, f"claim {claimed}")
        taken = bob.call("luma.handle.check", {"handle": "alice"})
        expect("setup", taken.get("available") is False, f"a taken username was offered: {taken}")

        # ── request ──
        if sabotage == "request":
            hub.call("/fake/config", {"sabotage": ["no_requests"]})
        if sabotage == "send":
            hub.call("/fake/config", {"sabotage": ["drop_application"]})
        created = laptop.call("conversation.create", {"participants": ["@bob"]})["conversation"]
        to_bob = created["id"]
        expect("setup", to_bob == f"u:{accounts['bob']}", f"direct conversation id {to_bob}")
        laptop.send(to_bob, "Hi Bob, it's Alice")
        got = bob.message_text("Hi Bob, it's Alice", check="send")
        to_alice = got["conversation"]
        expect("send", got["sender"]["id"] == accounts["alice"] and got.get("encrypted") is True, f"sender {got}")
        request = bob.wait("conversation", lambda d: d.get("id") == to_alice, what="the conversation", check="request")
        expect("request", request["luma"]["request"] is True, f"a stranger's first message was not a request: {request['luma']}")
        try:
            bob.send(to_alice, "too soon")
            raise CheckFailed("request", "Bob replied to a request he had not accepted")
        except HelperError as e:
            expect("request", e.code == "not_attempted", f"refusal code {e.code}")
        bob.call("luma.request.answer", {"conversation": to_alice, "state": "accepted"})
        bob.wait("conversation", lambda d: d.get("id") == to_alice and d["luma"]["request"] is False, what="accepted", check="request")
        # Alice's own other device has her sent message, as hers.
        mine = desk.message_text("Hi Bob, it's Alice", check="send")
        expect("send", mine["outgoing"] is True, f"desk copy not outgoing: {mine}")

        # ── receive ──
        bob.send(to_alice, "Hi Alice")
        for device in (laptop, desk):
            got = device.message_text("Hi Alice", check="receive")
            expect("receive", got["conversation"] == to_bob and got["outgoing"] is False, f"{device.name}: {got}")
        photo = work / "photo.png"
        photo.write_bytes(PNG)
        bob.call("media.send", {"conversation": to_alice, "client_id": secrets.token_hex(16), "path": str(photo),
                                "mime": "image/png", "name": "photo.png", "caption": "", "user_token": secrets.token_hex(16)})
        media = laptop.wait("media", lambda d: d.get("state") == "done", what="the photo", check="receive")
        expect("receive", Path(media["path"]).read_bytes() == PNG, "the photo's bytes differ")
        expect("receive", Path(media["path"]).parent == laptop.data / "media", "the photo is outside the account's media folder")

        # ── restart ──
        bob.kill()
        laptop.send(to_bob, "Sent while Bob was away")
        time.sleep(1)
        bob.events.clear()
        bob.start()
        bob.message_text("Sent while Bob was away", check="restart")
        bob.send(to_alice, "Back again")
        laptop.message_text("Back again", check="restart")

        # ── group ──
        if sabotage == "group":
            hub.call("/fake/config", {"sabotage": ["drop_welcome"]})
        group = laptop.call("conversation.create", {"participants": ["@bob", "@carol"], "name": "Weekend"})["conversation"]
        gid = group["id"]
        expect("group", gid.startswith("g:") and group["kind"] == "group", f"group {group}")
        carol_group = carol.wait("conversation", lambda d: d.get("id") == gid, what="the group", check="group")
        expect("group", carol_group["luma"]["request"] is True and carol_group["name"] == "Weekend", f"carol's group {carol_group}")
        bob_group = bob.wait("conversation", lambda d: d.get("id") == gid, what="the group", check="group")
        expect("group", bob_group["luma"]["request"] is False, "a group from someone Bob accepted came in as a request")
        carol.call("luma.request.answer", {"conversation": gid, "state": "accepted"})
        carol.send(gid, "Hello, everyone")
        for device in (laptop, desk, bob):
            got = device.message_text("Hello, everyone", check="group")
            expect("group", got["sender"]["id"] == accounts["carol"], f"{device.name}: sender {got['sender']}")
        laptop.send(gid, "Hi Carol")
        carol.message_text("Hi Carol", check="group")

        # ── safety ──
        mine = laptop.call("luma.safety", {"conversation": to_bob})
        theirs = bob.call("luma.safety", {"conversation": to_alice})
        expect("safety", mine["digits"] == theirs["digits"] and len(mine["digits"].replace(" ", "")) == 60, f"{mine} vs {theirs}")
        try:
            bob.call("luma.verify", {"conversation": to_alice, "scanned": "luma-safety:1:" + "A" * 80})
            raise CheckFailed("safety", "a different code verified")
        except HelperError as e:
            expect("safety", e.code == "mismatch", f"mismatch code {e.code}")
        bob.call("luma.verify", {"conversation": to_alice, "scanned": mine["qr"]})
        expect("safety", bob.call("luma.safety", {"conversation": to_alice})["verified"] is True, "not verified after scanning")

        # ── removed ──
        hub.call("/fake/config", {"hostile": True})
        hub.call("/fake/revoke", {"device": desk.device})
        if sabotage != "removed":
            laptop.call("luma.sync", {})
            bob.call("luma.sync", {})
            desk.wait("conversation", lambda d: d.get("id") == to_bob and d["luma"]["removed"], what="its removal", check="removed")
        delivered_before = hub.call("/fake/dump")["delivered"].get(desk.device, 0)
        bob.send(to_alice, "After the desk was revoked")
        laptop.message_text("After the desk was revoked", check="removed")
        time.sleep(3)
        delivered_after = hub.call("/fake/dump")["delivered"].get(desk.device, 0)
        expect("removed", delivered_after > delivered_before or sabotage == "removed",
               "the hostile server did not hand the desk the message, so this proves nothing")
        expect("removed", not desk.saw("message", lambda d: d.get("text") == "After the desk was revoked"),
               "the revoked desk read a message sent after its removal")
        changed = bob.wait("conversation", lambda d: d.get("id") == to_alice and d["luma"]["safety_changed"],
                           what="the safety number change", check="removed")
        expect("removed", changed["luma"]["verified"] is True, "verification was dropped instead of flagged")
        hub.call("/fake/config", {"hostile": False})

        # ── block ──
        if sabotage == "block":
            hub.call("/fake/config", {"sabotage": ["no_blocks"]})
        carol.call("luma.block", {"account": accounts["alice"], "on": True})
        before = hub.call("/fake/dump")["delivered"].get(carol.device, 0)
        laptop.send(gid, "Carol, are you there?")
        bob.message_text("Carol, are you there?", check="block")
        time.sleep(2)
        after = hub.call("/fake/dump")["delivered"].get(carol.device, 0)
        expect("block", after == before, f"the server delivered a blocked person's message ({after - before})")
        expect("block", not carol.saw("message", lambda d: d.get("text") == "Carol, are you there?"), "Carol saw a message from someone she blocked")
        report = carol.call("luma.report", {"account": accounts["alice"], "conversation": gid, "reason": "spam", "messages": [], "block": True})
        expect("block", report.get("blocked") is True, f"report {report}")

        # ── plaintext ──
        dump = hub.call("/fake/dump")
        stored = [base64.urlsafe_b64decode(v + "=" * (-len(v) % 4)) for v in dump["archive"] + list(dump["blobs"].values())]
        expect("plaintext", len(stored) >= 10, f"only {len(stored)} stored items to inspect")
        dump = json.dumps(dump)
        for secret_text in (b"Hi Bob", b"Hi Alice", b"Hello, everyone", b"Weekend", b"Carol, are you there", PNG[:8]):
            expect("plaintext", not any(secret_text in s for s in stored) and secret_text.decode("latin-1") not in dump,
                   f"{secret_text!r} is readable on the server")
    finally:
        for h in helpers:
            h.stop()
        hub.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--helper", required=True, type=Path)
    parser.add_argument("--break", dest="sabotage", choices=BREAKS)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    binary = args.helper.resolve()
    if args.self_test:
        failures = []
        for check in BREAKS:
            with tempfile.TemporaryDirectory(prefix=f"luma-e2e-{check}-") as work:
                try:
                    scenario(binary, Path(work), check)
                    failures.append(f"--break {check} passed; the {check} check does nothing")
                except CheckFailed as e:
                    if e.check != check:
                        failures.append(f"--break {check} failed at {e.check}, not {check}: {e}")
                    else:
                        print(f"RED as expected: --break {check}: {e}")
        if failures:
            print("\n".join("FAIL " + f for f in failures))
            return 1
        print(f"self-test: all {len(BREAKS)} checks go red when their mechanism is broken")
        return 0
    with tempfile.TemporaryDirectory(prefix="luma-e2e-") as work:
        try:
            scenario(binary, Path(work), args.sabotage)
        except (CheckFailed, HelperError, TimeoutError) as e:
            print(f"FAIL {e}")
            for log in Path(work).glob("*/stderr.log"):
                tail = log.read_text(errors="replace").splitlines()[-15:]
                print(f"--- {log.parent.name} stderr (last {len(tail)} lines)")
                print("\n".join(tail))
            return 1
    print("PASS request, send, receive, restart, group, safety, removed, block, plaintext")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
