# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path

from luma_tide import subsonic
from luma_tide.subsonic import (
    AuthenticationFailed, Cancelled, InsecureTransport, InvalidAddress, ProtocolError,
    SubsonicClient, Unreachable, has_scheme, normalize_address, redact,
)
from subsonic_fixture import FakeSubsonic, song


class AddressTests(unittest.TestCase):
    def test_typed_and_pasted_addresses_reduce_to_the_server_base(self) -> None:
        cases = {
            "music.example.com": "https://music.example.com",
            " https://Music.Example.com:4533/app/#/album/1 ": "https://music.example.com:4533",
            "https://example.com/navidrome/": "https://example.com/navidrome",
            "https://example.com/navidrome/rest": "https://example.com/navidrome",
            "http://192.168.1.20:4533/": "http://192.168.1.20:4533",
            "https://[fd00::20]:4533": "https://[fd00::20]:4533",
        }
        for typed, expected in cases.items():
            with self.subTest(typed=typed):
                self.assertEqual(normalize_address(typed), expected)

    def test_credentials_and_foreign_schemes_are_refused_in_the_address(self) -> None:
        for typed in ("", "ftp://music.example.com", "https://listener:secret@music.example.com",
                      "https://music.example.com:notaport"):
            with self.subTest(typed=typed), self.assertRaises(InvalidAddress):
                normalize_address(typed)

    def test_plain_http_needs_only_consent(self) -> None:
        with self.assertRaises(InsecureTransport):
            SubsonicClient("http://music.example.com", "a", "b").check_transport()
        for address in ("http://192.168.1.2", "http://8.8.8.8", "http://music.example.com"):
            with self.subTest(address=address):
                SubsonicClient(address, "a", "b", allow_plaintext=True).check_transport()
        SubsonicClient("https://8.8.8.8", "a", "b").check_transport()
        self.assertFalse(has_scheme("music.example.com:4533"))
        self.assertTrue(has_scheme("http://music.example.com"))

    def test_redaction_removes_every_credential_parameter(self) -> None:
        text = "Not Found, URL: https://m.example/rest/stream?id=1&u=me&t=abc&s=salt&p=enc:00"
        self.assertEqual(
            redact(text),
            "Not Found, URL: https://m.example/rest/stream?id=1&u=[redacted]&t=[redacted]"
            "&s=[redacted]&p=[redacted]",
        )


class ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = FakeSubsonic(songs=3)
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.client = SubsonicClient(
            self.server.address, "listener", "correct horse", allow_plaintext=True, timeout=5
        )

    def tearDown(self) -> None:
        self.server.close()
        self.temporary.cleanup()

    def test_token_authentication_never_sends_the_password(self) -> None:
        info = self.client.ping()
        self.assertEqual((info.display_name, info.open_subsonic), ("Navidrome", True))
        self.assertIn("transcodeOffset", info.extensions)
        salts = []
        for path in self.server.requests:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            self.assertNotIn("p", query)
            self.assertNotIn("correct", urllib.parse.unquote(path))
            salts.append(query["s"][0])
        self.assertEqual(len(salts), len(set(salts)), "every request uses a fresh salt")

    def test_wrong_password_is_an_authentication_failure(self) -> None:
        client = SubsonicClient(self.server.address, "listener", "wrong", allow_plaintext=True)
        with self.assertRaisesRegex(AuthenticationFailed, "incorrect"):
            client.ping()

    def test_an_address_that_is_not_subsonic_says_so(self) -> None:
        self.server.not_subsonic = True
        with self.assertRaisesRegex(ProtocolError, "doesn’t answer like"):
            self.client.ping()

    def test_redirects_are_refused_without_revealing_the_request(self) -> None:
        self.server.redirect_to = "https://elsewhere.example"
        with self.assertRaises(ProtocolError) as caught:
            self.client.ping()
        self.assertIn("https://elsewhere.example", str(caught.exception))
        self.assertNotIn("t=", str(caught.exception))

    def test_unreachable_server_is_reported(self) -> None:
        self.server.close()
        with self.assertRaises(Unreachable):
            self.client.ping()

    def test_songs_page_through_the_whole_library(self) -> None:
        self.server.songs = [song(index) for index in range(1203)]
        pages = list(self.client.songs())
        self.assertEqual([len(page) for page in pages], [500, 500, 203])
        self.assertEqual(len({item["id"] for page in pages for item in page}), 1203)

    def test_a_server_that_ignores_paging_still_ends(self) -> None:
        self.server.songs = [song(index) for index in range(600)]
        self.server.ignore_offset = True
        pages = list(self.client.songs())
        self.assertEqual([len(page) for page in pages], [500])

    def test_empty_search_falls_back_to_walking_albums(self) -> None:
        self.server.songs = [song(index) for index in range(25)]
        self.server.empty_search = True
        songs = [item["id"] for page in self.client.songs() for item in page]
        self.assertEqual(sorted(songs), sorted(f"song-{index}" for index in range(25)))

    def test_scan_marker_is_unknown_while_scanning(self) -> None:
        self.assertEqual(self.client.scan_marker(), ("3:2026-09-01T10:00:00Z", 3))
        self.server.scan["scanning"] = True
        self.assertEqual(self.client.scan_marker(), (None, 3))

    def test_download_is_complete_and_atomic(self) -> None:
        destination = self.root / "offline/song-1.flac"
        size = self.client.download("song-1", destination, expected_size=2048)
        self.assertEqual(size, 2048)
        self.assertTrue(destination.read_bytes().startswith(b"download:song-1"))
        self.assertEqual(sorted(path.name for path in destination.parent.iterdir()), ["song-1.flac"])

    def test_truncated_download_leaves_nothing_behind(self) -> None:
        self.server.truncate_download = True
        destination = self.root / "offline/song-1.flac"
        with self.assertRaises(Unreachable):
            self.client.download("song-1", destination)
        self.assertEqual(list(destination.parent.iterdir()), [])

    def test_an_account_without_download_rights_saves_the_original_stream(self) -> None:
        self.server.forbid_download = True
        destination = self.root / "offline/song-2.flac"
        self.client.download("song-2", destination)
        self.assertTrue(destination.read_bytes().startswith(b"stream:song-2"))
        stream = urllib.parse.parse_qs(urllib.parse.urlsplit(self.server.requests[-1]).query)
        self.assertEqual(stream["format"], ["raw"])

    def test_cancelled_download_leaves_nothing_behind(self) -> None:
        cancel = threading.Event()
        cancel.set()
        destination = self.root / "offline/song-1.flac"
        with self.assertRaises(Cancelled):
            self.client.download("song-1", destination, cancel=cancel)
        self.assertFalse(destination.exists())

    def test_missing_song_error_is_mapped(self) -> None:
        with self.assertRaises(subsonic.NotFound):
            self.client.download("song-404", self.root / "missing.flac")

    def test_stream_url_is_untranscoded_and_needs_consent_for_http(self) -> None:
        with self.assertRaises(InsecureTransport):
            SubsonicClient(self.server.address, "listener", "correct horse").stream_url("song-1")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.client.stream_url("song-1")).query)
        self.assertEqual((query["id"], query["format"], query["u"]), (["song-1"], ["raw"], ["listener"]))


if __name__ == "__main__":
    unittest.main()
