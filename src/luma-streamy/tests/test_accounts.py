# SPDX-License-Identifier: Apache-2.0
import os
import sys
import threading
import unittest
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from luma_streamy import accounts  # noqa: E402
from luma_streamy.accounts import SignInError, TwitchSignIn, YouTubeSignIn  # noqa: E402


#: The browser coming back to the loopback answer, never through a proxy.
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class FakeHttp:
    """Answers from a script: (method, url prefix) -> list of (status, body), consumed in order."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def __call__(self, method, url, *, form=None, body=None, headers=None, timeout=15):
        self.calls.append((method, url, form, body, headers))
        for (m, prefix), answers in self.script.items():
            if m == method and url.startswith(prefix) and answers:
                return answers.pop(0) if len(answers) > 1 else answers[0]
        raise AssertionError(f"unexpected {method} {url}")


class TwitchTest(unittest.TestCase):
    def setUp(self):
        os.environ["STREAMY_TWITCH_CLIENT_ID"] = "test-client"

    def test_device_flow_to_account(self):
        http = FakeHttp({
            ("POST", accounts.ENDPOINTS["twitch_device"]): [(200, {
                "device_code": "dc", "user_code": "ABCD-EFGH", "verification_uri": "https://www.twitch.tv/activate",
                "interval": 5, "expires_in": 1800})],
            ("POST", accounts.ENDPOINTS["twitch_token"]): [
                (400, {"status": 400, "message": "authorization_pending"}),
                (200, {"access_token": "at", "refresh_token": "rt", "expires_in": 14000})],
            ("GET", accounts.ENDPOINTS["twitch_api"] + "/users"): [(200, {"data": [{"id": "42", "login": "nova",
                                                                                   "display_name": "Nova"}]})],
            ("GET", accounts.ENDPOINTS["twitch_api"] + "/streams/key"): [(200, {"data": [{"stream_key": "live_42_x"}]})],
        })
        slept = []
        flow = TwitchSignIn(http=http, sleep=slept.append)
        code = flow.start()
        self.assertEqual(code.user_code, "ABCD-EFGH")
        tokens = flow.wait(code)
        self.assertEqual((tokens.access, tokens.refresh), ("at", "rt"))
        self.assertEqual(slept, [5.0])
        account = flow.account(tokens)
        self.assertEqual((account.name, account.key, account.server), ("Nova", "live_42_x", accounts.TWITCH_INGEST))
        self.assertEqual(http.calls[-1][4]["Client-Id"], "test-client")
        self.assertIn("broadcaster_id=42", http.calls[-1][1])

    def test_placeholder_id_is_refused_in_words(self):
        os.environ.pop("STREAMY_TWITCH_CLIENT_ID")
        os.environ["XDG_CONFIG_HOME"] = "/nonexistent"
        http = FakeHttp({("POST", accounts.ENDPOINTS["twitch_device"]): [(400, {"status": 400,
                                                                                 "message": "invalid client"})]})
        flow = TwitchSignIn(http=http)
        self.assertEqual(flow.client_id, accounts.PLACEHOLDER)
        with self.assertRaises(SignInError) as caught:
            flow.start()
        self.assertIn("client id", str(caught.exception))

    def test_declined(self):
        http = FakeHttp({("POST", accounts.ENDPOINTS["twitch_token"]): [(400, {"message": "access_denied"})]})
        code = accounts.DeviceCode("X", "u", "dc", 1, 9e12)
        with self.assertRaises(SignInError):
            TwitchSignIn(http=http, sleep=lambda s: None).wait(code)


class YouTubeTest(unittest.TestCase):
    def setUp(self):
        os.environ["STREAMY_YOUTUBE_CLIENT_ID"] = "yt-client"
        os.environ["STREAMY_YOUTUBE_CLIENT_SECRET"] = "yt-secret"

    def test_loopback_flow_to_account(self):
        http = FakeHttp({
            ("POST", accounts.ENDPOINTS["google_token"]): [(200, {"access_token": "gat", "expires_in": 3599})],
            ("GET", accounts.ENDPOINTS["youtube_api"] + "/channels"): [(200, {"items": [{"id": "UC1",
                                                                                         "snippet": {"title": "Nova"}}]})],
            ("GET", accounts.ENDPOINTS["youtube_api"] + "/liveStreams"): [(200, {"items": []})],
            ("POST", accounts.ENDPOINTS["youtube_api"] + "/liveStreams"): [(200, {"cdn": {"ingestionInfo": {
                "streamName": "abcd-efgh", "rtmpsIngestionAddress": "rtmps://a.rtmps.youtube.com/live2"}}})],
        })
        flow = YouTubeSignIn(http=http)
        url = flow.begin()
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.assertEqual(query["code_challenge_method"], "S256")
        self.assertTrue(query["redirect_uri"].startswith("http://127.0.0.1:"))

        # The browser coming back (loopback only; no network).
        def browser():
            LOCAL.open(f"{query['redirect_uri']}/?code=c0de&state={query['state']}", timeout=5).read()

        threading.Thread(target=browser, daemon=True).start()
        tokens = flow.wait(timeout=10)
        self.assertEqual(tokens.access, "gat")
        form = http.calls[0][2]
        self.assertEqual((form["code"], form["client_secret"]), ("c0de", "yt-secret"))
        self.assertEqual(len(form["code_verifier"]), 64)
        account = flow.account(tokens)
        self.assertEqual((account.name, account.key, account.server),
                         ("Nova", "abcd-efgh", "rtmps://a.rtmps.youtube.com/live2"))

    def test_state_mismatch_is_refused(self):
        flow = YouTubeSignIn(http=FakeHttp({}))
        url = flow.begin()
        redirect = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["redirect_uri"]
        threading.Thread(target=lambda: LOCAL.open(f"{redirect}/?code=x&state=wrong", timeout=5).read(),
                         daemon=True).start()
        with self.assertRaises(SignInError):
            flow.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
