#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""luma-messages-bridge/1 conformance for a built helper, without any network account.

    python3 conformance.py /path/to/helper NETWORK [LOGIN_METHOD]

LOGIN_METHOD picks the sign-in method whose first step is checked; package
builds pass one that needs no network (WhatsApp's "code" asks for a phone number).

Checks the parts every helper must get right before it touches a network: the
hello handshake and capabilities, one answer per command with the same id,
unknown commands, commands before sign-in, the sign-in entry step for the
network, that nothing is written outside the data directory, and shutdown.
"""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time

PROTOCOL = "luma-messages-bridge/1"
LOGIN_EVENTS = {"login.qr", "login.code", "login.needs", "login.browser"}


def main(helper, network, method=None):
    failures = []

    def check(name, ok, detail=""):
        print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail and not ok else ""), flush=True)
        if not ok:
            failures.append(name)

    with tempfile.TemporaryDirectory(prefix="bridge-conformance-") as temp:
        home = Path(temp) / "home"; home.mkdir()
        data = Path(temp) / "account"; data.mkdir(mode=0o700)
        environment = dict(os.environ, HOME=str(home), XDG_DATA_HOME=str(home / ".local/share"), XDG_CONFIG_HOME=str(home / ".config"))
        process = subprocess.Popen([helper, "--data-dir", str(data)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env=environment, text=True, bufsize=1)
        lines = queue.Queue()
        threading.Thread(target=lambda: [lines.put(line) for line in process.stdout] and lines.put(None), daemon=True).start()
        events = []
        next_id = [0]

        def call(cmd, args=None, timeout=20):
            next_id[0] += 1
            ident = f"c{next_id[0]}"
            process.stdin.write(json.dumps({"id": ident, "cmd": cmd, "args": args or {}}) + "\n")
            process.stdin.flush()
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                try:
                    line = lines.get(timeout=max(0.05, end - time.monotonic()))
                except queue.Empty:
                    break
                if line is None:
                    break
                message = json.loads(line)
                if "event" in message:
                    events.append(message)
                elif message.get("id") == ident:
                    return message
            return None

        def wait_event(names, timeout=20):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                found = next((e for e in events if e["event"] in names), None)
                if found:
                    return found
                try:
                    line = lines.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line is None:
                    break
                message = json.loads(line)
                if "event" in message:
                    events.append(message)
            return None

        hello = call("hello", {"protocol": PROTOCOL})
        result = (hello or {}).get("result") or {}
        check("hello answers with the protocol", bool(hello and hello.get("ok") and result.get("protocol") == PROTOCOL), str(hello))
        capabilities = result.get("capabilities") or {}
        check("hello declares capabilities", isinstance(capabilities.get("login"), list) and "media" in capabilities
              and "unofficial" in capabilities, str(capabilities))
        check("hello names the network", result.get("network") == network, str(result.get("network")))

        unknown = call("no.such.command")
        check("unknown commands fail with unsupported", bool(unknown and not unknown.get("ok")
              and unknown.get("error", {}).get("code") == "unsupported"), str(unknown))

        listed = call("conversations.list", {"limit": 5})
        check("commands before sign-in fail as retryable not_connected", bool(listed and not listed.get("ok")
              and listed["error"].get("code") in {"not_connected", "needs_login"}), str(listed))

        if capabilities.get("media_fetch"):
            fetched = call("media.fetch", {"conversation": "c", "message": "m"})
            check("media.fetch before sign-in fails as retryable not_connected", bool(fetched and not fetched.get("ok")
                  and fetched["error"].get("code") == "not_connected"), str(fetched))

        status = call("status")
        check("status before sign-in is needs_login", bool(status and status.get("ok")
              and status["result"].get("state") == "needs_login"), str(status))

        method = method or capabilities.get("login", ["qr"])[0]
        started = call("login.start", {"method": method}, timeout=60)
        check(f"login.start {method} is accepted", bool(started and started.get("ok")), str(started))
        step = wait_event(LOGIN_EVENTS, timeout=60)
        check("sign-in shows its first step", step is not None, str(events[-3:]))
        cancelled = call("login.cancel")
        check("login.cancel is accepted", bool(cancelled and cancelled.get("ok")), str(cancelled))

        stray = [p for p in home.rglob("*") if p.is_file()]
        check("nothing is written outside the data directory", not stray, str(stray[:5]))
        session_files = [p for p in data.rglob("*") if p.is_file() and p.suffix in {".json", ".session"}]
        check("no session file in the data directory before sign-in", not session_files, str(session_files[:5]))

        done = call("shutdown")
        check("shutdown answers", bool(done and done.get("ok")), str(done))
        try:
            code = process.wait(timeout=10)
            check("the helper exits after shutdown", code == 0, f"exit {code}")
        except subprocess.TimeoutExpired:
            process.kill()
            check("the helper exits after shutdown", False, "still running")
    print(f"{'PASS' if not failures else 'FAIL'} {network} conformance ({len(failures)} failures)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
