#!/usr/bin/env python3
"""Exercise the packaged renderer; fail if hostile HTML contacts our listener.

Run as an ordinary user in a graphical test session (or under xvfb-run).
Requires the candidate Quick View package and GJS. This tests resource denial,
not the completeness of the renderer's operating-system sandbox.
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import pathlib
import subprocess
import tempfile
import threading


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gjs", default="gjs")
    args = parser.parse_args()
    requests: list[str] = []
    connections: list[tuple] = []

    class Listener(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"unexpected resource request")

        do_POST = do_GET

        def log_message(self, *unused):
            pass

    class Server(http.server.ThreadingHTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connections.append(address)
            return connection, address

    server = Server(("127.0.0.1", 0), Listener)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    html = f"""<!doctype html><html><head><title>Unchanged</title>
<link rel="stylesheet" href="{endpoint}/stylesheet">
<link rel="preload" as="image" href="{endpoint}/preload">
<link rel="preconnect" href="{endpoint}">
<link rel="dns-prefetch" href="{endpoint}">
<style>@import url('{endpoint}/import');
@font-face {{font-family:probe;src:url('{endpoint}/font')}}
body {{font-family:probe;background-image:url('{endpoint}/background')}}
</style><script>document.title='Changed';fetch('{endpoint}/script');</script>
</head><body>Selectable preview text.
<img src="{endpoint}/image"><iframe src="{endpoint}/frame"></iframe>
<svg xmlns="http://www.w3.org/2000/svg">
<image href="{endpoint}/svg-image" width="20" height="20"/>
<use href="{endpoint}/svg-use#probe"/></svg>
<object data="{endpoint}/object"></object>
<video poster="{endpoint}/poster" src="{endpoint}/video"></video>
<audio autoplay src="{endpoint}/audio"></audio>
<a href="{endpoint}/navigation">A link must not navigate</a>
</body></html>"""
    try:
        with tempfile.TemporaryDirectory(prefix="luma-quick-view-test-") as directory:
            fixture = pathlib.Path(directory) / "probe.html"
            fixture.write_text(html, encoding="utf-8")
            before = (hashlib.sha256(fixture.read_bytes()).digest(), fixture.stat().st_mtime_ns)
            result = subprocess.run(
                [args.gjs, str(pathlib.Path(__file__).with_name("documents.js")), str(fixture)],
                capture_output=True, text=True, timeout=25,
            )
            print(result.stdout, end="")
            if result.returncode:
                raise RuntimeError(f"Renderer failed ({result.returncode}): {result.stderr}")
            after = (hashlib.sha256(fixture.read_bytes()).digest(), fixture.stat().st_mtime_ns)
            if before != after:
                raise RuntimeError("Preview changed the source file")
            if requests:
                raise RuntimeError(f"Preview made network requests: {requests}")
            if connections:
                raise RuntimeError("Preview opened a connection to the resource listener")
            if "PASS:" not in result.stdout:
                raise RuntimeError("Renderer exited without completing assertions")
            print("PASS: zero resource-listener connections or HTTP requests; source content and modification time unchanged")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
