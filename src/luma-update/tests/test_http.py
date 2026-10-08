# SPDX-License-Identifier: Apache-2.0
"""Hub API requests never follow a redirect.

Regression test for the independent review's redirect_token_leak proof: a
redirect from the preview-credential endpoint made urllib repeat the request,
Authorization header included, at whatever host the Location named, so the
person's Luma Connect device token reached a third party. HTTP on loopback
(allow_insecure) stands in for HTTPS; urllib copies headers the same way.
"""

import http.server
import json
import threading
import unittest

import fakes  # noqa: F401  (puts the package on sys.path)
from luma_update.http import Http, HttpError


class Recorder(http.server.BaseHTTPRequestHandler):
    seen = []

    def _answer(self):
        type(self).seen.append((self.command, self.path, self.headers.get("Authorization")))
        body = json.dumps({"credential": "stolen"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_DELETE = _answer

    def log_message(self, *args):
        pass


class Evil(Recorder):
    seen = []


def redirector(target_port, code):
    class Redirect(http.server.BaseHTTPRequestHandler):
        def _answer(self):
            self.send_response(code)
            self.send_header("Location", f"http://localhost:{target_port()}/steal")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST = do_DELETE = _answer

        def log_message(self, *args):
            pass
    return Redirect


class Redirects(unittest.TestCase):
    def setUp(self):
        Evil.seen = []
        self.evil = http.server.HTTPServer(("127.0.0.1", 0), Evil)
        self.servers = [self.evil]
        threading.Thread(target=self.evil.serve_forever, daemon=True).start()

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()

    def hub(self, code):
        server = http.server.HTTPServer(("127.0.0.1", 0), redirector(lambda: self.evil.server_address[1], code))
        self.servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{server.server_address[1]}"

    def test_bearer_token_is_never_sent_after_a_redirect(self):
        for code in (301, 302, 303, 307, 308):
            hub = self.hub(code)
            for method, payload in (("POST", {"channel": "beta", "arch": "x86_64"}), ("DELETE", None)):
                with self.assertRaises(HttpError) as caught:
                    Http(allow_insecure=True).request_json(method, hub + "/api/updates/preview-credentials",
                                                           payload, headers={"Authorization": "Bearer CONNECT-TOKEN"})
                self.assertIn("refused a redirect", str(caught.exception))
        self.assertEqual(Evil.seen, [])

    def test_the_public_graph_may_still_follow_a_redirect(self):
        hub = self.hub(302)
        self.assertEqual(json.loads(Http(allow_insecure=True).get(hub + "/os/graph/stable.json", 1024)),
                         {"credential": "stolen"})
        self.assertEqual(Evil.seen, [("GET", "/steal", None)])

    def test_graph_redirect_away_from_https_is_refused(self):
        hub = self.hub(302)
        http_client = Http(allow_insecure=False)
        http_client.allow_insecure = True  # let the loopback start URL through _check_url only
        opener_handler = [h for h in http_client._opener.handlers if hasattr(h, "allow_insecure")][0]
        opener_handler.allow_insecure = False
        with self.assertRaises(HttpError):
            http_client.get(hub + "/os/graph/stable.json", 1024)
        self.assertEqual(Evil.seen, [])


if __name__ == "__main__":
    unittest.main()
