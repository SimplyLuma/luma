#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Local stand-in for dl.simplyluma.com while the CDN is not live.

Serves $WEBROOT (the switched webroot generation) read-only on loopback:

  /os/repo/...                      -> os/repo/...
  /os/preview/<credential>/repo/... -> os/preview-repo/...   (credential checked)
  /os/graph/..., /os/keys/...       -> as is

The preview credential check mirrors the CDN Worker's contract: a request is
served only when the SHA-256 of <credential> is listed in --credentials (one
hex digest per line). Anything else is 404. No directory listings, no writes.
With --cert/--key it serves HTTPS.
"""

import argparse
import hashlib
import http.server
import os
import posixpath
import re
import ssl
import sys
import urllib.parse

CREDENTIAL = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


class Handler(http.server.SimpleHTTPRequestHandler):
    webroot = "."
    credentials_file = None

    def list_directory(self, path):  # noqa: D401 - no listings
        self.send_error(404)
        return None

    def allowed_credential(self, credential):
        if not CREDENTIAL.match(credential) or not self.credentials_file:
            return False
        try:
            with open(self.credentials_file, encoding="utf-8") as stream:
                allowed = {line.strip() for line in stream if line.strip()}
        except OSError:
            return False
        return hashlib.sha256(credential.encode()).hexdigest() in allowed

    def translate_path(self, path):
        path = urllib.parse.unquote(urllib.parse.urlsplit(path).path)
        parts = [p for p in posixpath.normpath(path).split("/") if p and p not in (".", "..")]
        if len(parts) >= 4 and parts[:2] == ["os", "preview"] and parts[3] == "repo":
            if not self.allowed_credential(parts[2]):
                return os.path.join(self.webroot, "__denied__")
            parts = ["os", "preview-repo"] + parts[4:]
        elif len(parts) >= 2 and parts[:2] == ["os", "preview-repo"]:
            return os.path.join(self.webroot, "__denied__")
        return os.path.join(self.webroot, *parts)

    def log_message(self, fmt, *args):
        # Never log credentials.
        message = re.sub(r"/os/preview/[^/]+/", "/os/preview/<credential>/", fmt % args)
        sys.stderr.write(f"{self.log_date_time_string()} {message}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--webroot", required=True)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--credentials")
    parser.add_argument("--cert")
    parser.add_argument("--key")
    args = parser.parse_args()
    # Keep the symlink in the path: every request resolves the current generation.
    Handler.webroot = os.path.abspath(args.webroot)
    Handler.credentials_file = args.credentials
    server = http.server.ThreadingHTTPServer((args.bind, args.port), Handler)
    if args.cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.cert, args.key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
