#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
"""A stand-in for dl.simplyluma.com on the build host's loopback, for VM tests.

  /os/repo/<path>                       the OSTree repository, public (Official)
  /os/preview/<credential>/repo/<path>  the same repository, only for a credential
                                        ops/update-preview-credentials/preview-auth.py accepts
  /os/graph/<file>                      signed update graphs
  /events                               update reports (202, discarded)

Every request is appended to a JSON-lines log with the credential replaced by
"<credential>". Nothing here is exposed beyond 127.0.0.1.

  dl-test-server.py PORT REPO GRAPH_DIR LOG   (credential files: LUMA_DL_PREVIEW_CREDENTIAL_FILES)
"""
import importlib.util, json, os, re, sys, threading, time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

port, repo, graphs, log_path = int(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
spec = importlib.util.spec_from_file_location(
    "auth", Path(__file__).resolve().parents[3] / "ops/update-preview-credentials/preview-auth.py")
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)
lock = threading.Lock()
PREVIEW = re.compile(r"^/os/preview/([^/]+)/repo/(.*)$")


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def record(self, kind, status):
        path = PREVIEW.sub(r"/os/preview/<credential>/repo/\2", self.path)
        with lock, open(log_path, "a") as stream:
            stream.write(json.dumps({"at": time.time(), "kind": kind, "status": status, "path": path}) + "\n")

    def serve(self, root: Path, relative: str, kind: str):
        target = (root / relative).resolve()
        if root.resolve() not in target.parents and target != root.resolve() or not target.is_file():
            self.record(kind, 404)
            self.send_error(404)
            return
        data = target.read_bytes()
        self.record(kind, 200)
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        match = PREVIEW.match(path)
        if match:
            if not auth.valid(match.group(1)):
                self.record("preview", 403)
                self.send_error(403)
                return
            return self.serve(repo, match.group(2), "preview")
        if path.startswith("/os/repo/"):
            return self.serve(repo, path[len("/os/repo/"):], "public")
        if path.startswith("/os/graph/"):
            return self.serve(graphs, path[len("/os/graph/"):], "graph")
        self.record("other", 404)
        self.send_error(404)

    do_HEAD = do_GET

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        self.record("events", 202)
        self.send_response(202)
        self.send_header("Content-Length", "0")
        self.end_headers()


ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
