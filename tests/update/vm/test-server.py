#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""A loopback stand-in for Luma's CDN and Hub, for the update agent VM rig only.

Serves the OSTree repository at /os/repo and /os/preview/<credential>/repo,
graphs at /os/graph/, accepts update events (recorded, and checked for the
exact ADR-030 shapes) and issues a preview credential to a test Connect token.
"""

import http.server
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/var/lib/luma-update-test")
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8471
CREDENTIAL = "rigpreviewcredential0123456789"
TOKEN = "rig-connect-device-token"
TRANSITION = {"channel", "from_version", "to_version", "arch", "result", "error_class"}
COUNTME = {"channel", "version", "arch", "countme_bucket"}


class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        path = path.split("?", 1)[0]
        prefix = f"/os/preview/{CREDENTIAL}/repo/"
        if path.startswith(prefix):
            return str(ROOT / "repo" / path[len(prefix):])
        if path.startswith("/os/preview/"):
            return str(ROOT / "forbidden")
        if path.startswith("/os/repo/"):
            return str(ROOT / "repo" / path[len("/os/repo/"):])
        if path.startswith("/os/graph/"):
            return str(ROOT / "graph" / path[len("/os/graph/"):])
        return str(ROOT / "forbidden")

    def _record(self, kind, payload, status):
        with open(ROOT / "evidence" / "hub-requests.jsonl", "a") as stream:
            stream.write(json.dumps({"at": int(time.time()), "kind": kind, "status": status,
                                     "headers": {k: v for k, v in self.headers.items()
                                                 if k.lower() in ("user-agent", "content-type")},
                                     "has_authorization": "Authorization" in self.headers,
                                     "payload": payload}) + "\n")

    def _reply(self, status, body=None):
        data = json.dumps(body or {}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            payload = None
        if self.path == "/api/updates/events":
            keys = set(payload) if isinstance(payload, dict) else set()
            status = 202 if keys in (TRANSITION, COUNTME) else 422
            self._record("countme" if keys == COUNTME else "transition", payload, status)
            return self._reply(status)
        if self.path == "/api/updates/preview-credentials":
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                self._record("preview-credential", payload, 401)
                return self._reply(401)
            self._record("preview-credential", payload, 201)
            return self._reply(201, {"credential": CREDENTIAL, "channels": ["beta"]})
        self._reply(404)

    def do_DELETE(self):
        if self.path == "/api/updates/preview-credentials/current":
            self._record("preview-revoke", None, 204)
            self.send_response(204)
            self.end_headers()
            return
        self._reply(404)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s\n" % (fmt % args))


os.makedirs(ROOT / "evidence", exist_ok=True)
http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
