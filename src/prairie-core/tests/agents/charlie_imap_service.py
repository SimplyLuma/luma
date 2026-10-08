#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Charlie's test IMAP server as a session service, for the Charlie mail agent suite.

    charlie_imap_service.py --charlie-tests DIR --state DIR

Runs Charlie's own tests/fake_imap.py FakeImapServer (TLS with a throwaway
authority) and writes DIR/server.json with its port and CA. Mail dropped as
DIR/inbox/*.json ({"sender": ..., "subject": ...}) is delivered at once; every
message's flags are mirrored to DIR/flags.json.
"""
import argparse
import json
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--charlie-tests", type=Path, required=True)
parser.add_argument("--state", type=Path, required=True)
options = parser.parse_args()
sys.path.insert(0, str(options.charlie_tests))
from fake_imap import FakeImapServer, make_certificates, make_message  # noqa: E402

state = options.state
state.mkdir(parents=True, exist_ok=True)
ca, cert, key = make_certificates(state / "tls")
server = FakeImapServer(cert, key, password="session password").start()
(state / "server.json").write_text(json.dumps({"port": server.port, "ca": str(ca),
                                               "username": server.username, "password": server.password}))
inbox = state / "inbox"
inbox.mkdir(exist_ok=True)
print(f"fake IMAP on {server.port}", flush=True)
while True:
    for path in sorted(inbox.glob("*.json")):
        request = json.loads(path.read_text())
        path.unlink()
        server.deliver(make_message(server.uidnext, sender=request["sender"], subject=request["subject"]))
    with server.lock:
        flags = {str(message.uid): sorted(message.flags) for message in server.messages}
        logins = server.logins
    (state / "flags.tmp").write_text(json.dumps({"flags": flags, "logins": logins}))
    (state / "flags.tmp").replace(state / "flags.json")
    time.sleep(0.2)
