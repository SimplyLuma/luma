# SPDX-License-Identifier: Apache-2.0
"""A stand-in Hub speaking the pipeline contract, for rehearsing the service.

  python3 fake_hub.py --port 8765 --token TOKEN --state DIR

Queue work by dropping JSON files into DIR/queue/{checks,publish,withdraw}/;
reports land in DIR/reports/. Files under DIR/files/ are served at /files/.
The catalogue is DIR/catalogue.json. Plain HTTP on localhost only.
"""
import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def make_handler(token: str, state: Path):
    class Handler(BaseHTTPRequestHandler):
        def _auth(self) -> bool:
            if self.headers.get("Authorization") != f"Bearer {token}":
                self.send_response(401)
                self.end_headers()
                return False
            return True

        def _json(self, code: int, body=None):
            data = b"" if body is None else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self._auth():
                return
            if self.path == "/api/depot/catalogue":
                return self._json(200, json.loads((state / "catalogue.json").read_text()))
            if self.path.startswith("/files/"):
                path = state / "files" / Path(self.path[7:]).name
                if path.is_file():
                    data = path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
            self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._auth():
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            parts = self.path.strip("/").split("/")  # api depot pipeline <kind> ...
            kind = parts[3] if len(parts) > 3 else ""
            if parts[-1] == "claim":
                queue = sorted((state / "queue" / kind).glob("*.json"))
                if not queue:
                    return self._json(204)
                item = json.loads(queue[0].read_text())
                queue[0].rename(queue[0].with_suffix(".claimed"))
                key = "withdrawal" if kind == "withdraw" else "submission"
                return self._json(200, {key: item})
            reports = state / "reports"
            reports.mkdir(exist_ok=True)
            name = f"{time.strftime('%H%M%S')}-{kind}-{parts[4]}-{parts[-1]}"
            if parts[-1] == "screenshot":
                (reports / (name + ".multipart")).write_bytes(body)
                return self._json(200, {"url": f"https://hub.invalid/review/{parts[4]}.png"})
            (reports / (name + ".json")).write_bytes(body)
            return self._json(200, {"ok": True})

        def log_message(self, *args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    for kind in ("checks", "publish", "withdraw"):
        (args.state / "queue" / kind).mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.token, args.state)).serve_forever()


if __name__ == "__main__":
    main()
