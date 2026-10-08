#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Seal evidence harness: stands in for an app asking a mechanism for something.
# Runs inside the app's own systemd scope, sends the request with its own pid
# and prints the mechanism's answer as one JSON line.
import json, os, socket, sys
req = json.loads(sys.argv[1])
req["pid"] = os.getpid()
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect("/run/seal-mechanism.sock")
s.sendall((json.dumps(req) + "\n").encode())
data = b""
while not data.endswith(b"\n"):
    chunk = s.recv(4096)
    if not chunk:
        break
    data += chunk
print(json.dumps({"pid": os.getpid(), **json.loads(data)}), flush=True)
