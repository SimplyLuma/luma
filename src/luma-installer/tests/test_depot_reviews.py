# SPDX-License-Identifier: Apache-2.0
"""Reviews (ADR-028, section 10): anonymous reading, enrolled writing, token hygiene."""

import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError

from luma_installer import depot_reviews as r

TOKEN = 'A' * 43
HUB = 'https://hub.simplyluma.com'


class Enrolment(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.env = {'XDG_DATA_HOME': self.dir.name}
        self.folder = Path(self.dir.name) / 'luma/connect'
        self.folder.mkdir(parents=True, mode=0o700)
        os.chmod(self.folder, 0o700)

    def enrol(self, hub=HUB, token=TOKEN, mode=0o600):
        path = self.folder / 'device.json'
        path.write_text(json.dumps({'device_id': 'd', 'token': token, 'hub': hub, 'name': 'n'}))
        os.chmod(path, mode)

    def test_the_issuing_hub_gets_the_token(self):
        self.enrol()
        self.assertEqual(r.device_token(HUB, self.env), TOKEN)
        self.assertEqual(r.device_token(HUB + '/', self.env), TOKEN)
        self.assertTrue(r.is_enrolled(HUB, self.env))

    def test_no_token_for_another_hub_plain_http_or_a_readable_file(self):
        self.enrol(hub='https://127.0.0.1:8443')
        self.assertEqual(r.device_token(HUB, self.env), '')
        self.enrol(hub='http://hub.simplyluma.com')
        self.assertEqual(r.device_token('http://hub.simplyluma.com', self.env), '')
        self.enrol(mode=0o644)
        self.assertEqual(r.device_token(HUB, self.env), '')
        self.enrol(token='short')
        self.assertEqual(r.device_token(HUB, self.env), '')
        (self.folder / 'device.json').unlink()
        self.assertFalse(r.is_enrolled(HUB, self.env))


class Page(unittest.TestCase):
    def test_parsing_is_bounded_and_forgiving(self):
        page = r.parse_page({
            'rating': {'average': 4.25, 'count': 12},
            'reviews': [
                {'id': 7, 'rating': 5, 'title': 'Good', 'body': 'Yes', 'author': {'name': 'Ada'},
                 'version': '1.0', 'installed_on_luma': True, 'created_at': '2026-09-01T00:00:00Z',
                 'reply': {'body': 'Thanks', 'developer': {'name': 'Dev'}}},
                {'id': 'x', 'rating': 9}, 'nonsense', {'id': 'y', 'rating': 3, 'own': True, 'author': None},
            ],
            'next_cursor': 'abc:123',
        })
        self.assertEqual((page.average, page.count, page.next_cursor), (4.25, 12, 'abc:123'))
        self.assertEqual([review.id for review in page.reviews], ['7', 'y'])
        self.assertEqual(page.reviews[0].reply.author, 'Dev')
        self.assertTrue(page.reviews[0].installed_on_luma)
        self.assertEqual(page.own_review.id, 'y')
        self.assertEqual(page.reviews[1].author, 'A Luma user')
        self.assertTrue(page.enough_ratings)
        self.assertFalse(r.parse_page({'rating': {'average': 5, 'count': 2}}).enough_ratings)
        self.assertEqual(r.parse_page({'rating': {'average': 99, 'count': -1}, 'next_cursor': '../x'}).count, 0)


class Client(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        folder = Path(self.dir.name) / 'luma/connect'
        folder.mkdir(parents=True)
        os.chmod(folder, 0o700)
        (folder / 'device.json').write_text(json.dumps({'device_id': 'd', 'token': TOKEN, 'hub': HUB}))
        os.chmod(folder / 'device.json', 0o600)
        self.env = {'XDG_DATA_HOME': self.dir.name}
        self.requests = []
        self.answer = b'{}'
        self.error = None

    def opener(self, request, timeout):
        self.requests.append(request)
        if self.error:
            raise self.error
        answer = self.answer

        class Response:
            def read(self, _n):
                return answer

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False
        return Response()

    def client(self, env=None):
        return r.ReviewsClient(HUB, environment=env if env is not None else self.env, opener=self.opener)

    def test_reading_uses_the_adr_path(self):
        self.answer = json.dumps({'rating': {'average': 4, 'count': 3}, 'reviews': []}).encode()
        self.client().reviews('canvas', cursor='next:2')
        self.assertEqual(self.requests[0].full_url, HUB + '/api/depot/apps/canvas/reviews?cursor=next%3A2')
        self.assertEqual(self.requests[0].get_method(), 'GET')

    def test_writing_sends_the_review_with_the_device_token(self):
        self.answer = json.dumps({'review': {'id': 'r1', 'rating': 4}}).encode()
        review = self.client().save('canvas', rating=4, title=' Nice ', body='Body', version='0.1.0',
                                    installed_on_luma=True)
        request = self.requests[0]
        self.assertEqual((request.get_method(), request.full_url), ('PUT', HUB + '/api/depot/apps/canvas/review'))
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + TOKEN)
        self.assertEqual(json.loads(request.data), {'rating': 4, 'title': 'Nice', 'body': 'Body',
                                                    'version': '0.1.0', 'installed_on_luma': True})
        self.assertEqual(review.id, 'r1')
        self.client().delete('canvas')
        self.assertEqual(self.requests[1].get_method(), 'DELETE')

    def test_writing_without_enrolment_never_reaches_the_network(self):
        with self.assertRaises(r.NotEnrolled):
            self.client(env={'XDG_DATA_HOME': self.dir.name + '/nobody'}).save(
                'canvas', rating=5, title='', body='', version='', installed_on_luma=False)
        self.assertEqual(self.requests, [])

    def test_validation_before_sending(self):
        for kwargs in ({'rating': 0}, {'rating': 6}, {'rating': True}, {'title': 'x' * 101}, {'body': 'x' * 4001}):
            arguments = {'rating': 3, 'title': '', 'body': '', 'version': '', 'installed_on_luma': False, **kwargs}
            with self.subTest(kwargs=list(kwargs)), self.assertRaises(r.ReviewsError):
                self.client().save('canvas', **arguments)
        for slug in ('Canvas', '../x', ''):
            with self.assertRaises(r.ReviewsError):
                self.client().reviews(slug)
        self.assertEqual(self.requests, [])

    def test_answers_become_sentences(self):
        cases = {401: 'Sign in to Luma again', 404: 'not available', 429: 'Try again later',
                 403: 'Verify your email'}
        for status, words in cases.items():
            self.error = HTTPError(HUB, status, 'x', {}, io.BytesIO(b'{}'))
            with self.subTest(status=status), self.assertRaises(r.ReviewsError) as caught:
                self.client().reviews('canvas')
            self.assertIn(words, str(caught.exception))
            self.assertEqual(caught.exception.status, status)
        self.error = OSError('down')
        with self.assertRaises(r.ReviewsError) as caught:
            self.client().reviews('canvas')
        self.assertIn('network', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
