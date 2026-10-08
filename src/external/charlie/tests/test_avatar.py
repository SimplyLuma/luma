# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import tempfile
import threading
import unittest

from charlie_luma.avatar import AvatarLoader, avatar_candidates


PNG = b"\x89PNG\r\n\x1a\n" + b"avatar"


class AvatarTests(unittest.TestCase):
    def test_lookup_never_sends_the_raw_address_and_skips_consumer_brand_icons(self):
        address = "Person@Gmail.com"
        candidates = avatar_candidates(address)
        self.assertEqual(len(candidates), 1)
        self.assertIn("seccdn.libravatar.org/avatar/", candidates[0])
        self.assertNotIn("person", candidates[0].casefold())
        self.assertNotIn("gmail.com", candidates[0].casefold())

    def test_brand_fallback_sends_only_the_public_domain(self):
        candidates = avatar_candidates("receipts@stripe.com")
        self.assertEqual(len(candidates), 2)
        self.assertEqual(
            candidates[-1], "https://unavatar.io/stripe.com?fallback=false"
        )
        self.assertNotIn("receipts", candidates[-1])

    def test_only_google_profile_hosts_are_accepted_as_preferred_photos(self):
        trusted = avatar_candidates(
            "me@gmail.com", "https://lh3.googleusercontent.com/a/photo"
        )
        untrusted = avatar_candidates(
            "me@gmail.com", "https://tracking.example.test/open"
        )
        self.assertTrue(trusted[0].startswith("https://lh3.googleusercontent.com/"))
        self.assertNotIn("tracking.example.test", " ".join(untrusted))

    def test_positive_result_is_cached_without_a_second_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            calls: list[str] = []

            def fetch(url: str) -> bytes:
                calls.append(url)
                return PNG

            loader = AvatarLoader(Path(directory), fetch=fetch)
            try:
                first = threading.Event()
                second = threading.Event()
                values: list[bytes | None] = []
                loader.request(
                    "reader@example.test", "",
                    lambda data: (values.append(data), first.set()),
                )
                self.assertTrue(first.wait(2))
                loader.request(
                    "reader@example.test", "",
                    lambda data: (values.append(data), second.set()),
                )
                self.assertTrue(second.wait(2))
            finally:
                loader.close()
            self.assertEqual(values, [PNG, PNG])
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
