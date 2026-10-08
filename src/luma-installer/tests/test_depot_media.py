# SPDX-License-Identifier: Apache-2.0
"""Catalogue images are drawn only when their bytes match the published digest."""

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from luma_installer import depot_media as m

PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 64


def opener_for(content, calls):
    class Response:
        def read(self, _n):
            return content

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def opener(request, timeout):
        calls.append(request.full_url)
        return Response()
    return opener


class Media(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.folder = Path(self.dir.name)

    def test_a_matching_image_is_kept_by_digest_and_not_fetched_twice(self):
        digest = hashlib.sha256(PNG).hexdigest()
        calls = []
        path = m.fetch('https://dl.simplyluma.com/i.png', digest, directory=self.folder, opener=opener_for(PNG, calls))
        self.assertEqual(path, self.folder / (digest + '.png'))
        m.fetch('https://dl.simplyluma.com/i.png', digest, directory=self.folder, opener=opener_for(PNG, calls))
        self.assertEqual(len(calls), 1)
        self.assertEqual(m.cached(digest, self.folder), path)

    def test_a_substituted_image_is_refused_and_not_kept(self):
        calls = []
        with self.assertRaises(m.MediaError):
            m.fetch('https://dl.simplyluma.com/i.png', 'b' * 64, directory=self.folder,
                    opener=opener_for(PNG, calls))
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_only_https_only_known_formats_only_with_a_digest(self):
        digest = hashlib.sha256(b'plain text').hexdigest()
        for url, sha in (('http://dl.simplyluma.com/i.png', hashlib.sha256(PNG).hexdigest()),
                         ('file:///etc/passwd', hashlib.sha256(PNG).hexdigest()),
                         ('https://dl.simplyluma.com/i.txt', digest),
                         ('https://dl.simplyluma.com/i.png', '')):
            with self.subTest(url=url), self.assertRaises(m.MediaError):
                m.fetch(url, sha, directory=self.folder, opener=opener_for(b'plain text', []))


if __name__ == '__main__':
    unittest.main()
