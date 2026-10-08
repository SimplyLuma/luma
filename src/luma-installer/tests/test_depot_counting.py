# SPDX-License-Identifier: Apache-2.0
"""ADR-028 section 9: install events and the weekly count carry nothing identifying."""

from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from luma_installer import depot_counting as counting

WEEK = 7 * 24 * 3600


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


class Buckets(unittest.TestCase):
    def test_windows_start_on_monday_midnight_utc(self):
        start = counting.window_start(at('2026-09-17T15:30:00'))  # a Thursday
        self.assertEqual(datetime.fromtimestamp(start, timezone.utc).isoformat(), '2026-09-14T00:00:00+00:00')
        self.assertEqual(counting.window_start(at('2026-09-14T00:00:00')), start)
        self.assertEqual(counting.window_start(at('2026-09-13T23:59:59')), start - WEEK)

    def test_fedora_age_buckets(self):
        first = counting.window_start(at('2026-01-05T00:00:00'))
        expected = {0: 1, 1: 1, 2: 2, 4: 2, 5: 3, 24: 3, 25: 4, 200: 4}
        for weeks, bucket in expected.items():
            with self.subTest(weeks=weeks):
                self.assertEqual(counting.bucket(first, first + weeks * WEEK), bucket)


class Weekly(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.env = {'XDG_STATE_HOME': self.dir.name + '/state', 'XDG_CONFIG_HOME': self.dir.name + '/config'}
        self.now = at('2026-09-17T10:00:00')

    def counter(self, **settings):
        return counting.Countme(environment=self.env, clock=lambda: self.now,
                                settings=counting.Settings(**settings) if settings else None)

    def test_once_per_calendar_week_and_the_age_grows(self):
        counter = self.counter()
        self.assertEqual(counter.pending(), 1)
        counter.counted()
        self.assertIsNone(counter.pending())
        self.now += 3 * 24 * 3600  # Sunday: same week
        self.assertIsNone(counter.pending())
        self.now += 24 * 3600      # Monday: a new week
        self.assertEqual(counter.pending(), 1)
        counter.counted()
        self.now += 5 * WEEK
        self.assertEqual(counter.pending(), 3)

    def test_the_state_is_two_week_numbers_and_nothing_else(self):
        counter = self.counter()
        counter.counted()
        state = json.loads((Path(self.env['XDG_STATE_HOME']) / 'luma/depot/countme.json').read_text())
        self.assertEqual(set(state), {'first_window', 'counted_window'})

    def test_the_setting_turns_it_off(self):
        self.assertIsNone(self.counter(countme=False).pending())
        counting.save_settings(counting.Settings(install_events=True, countme=False), self.env)
        self.assertIsNone(self.counter().pending())
        self.assertEqual(counting.load_settings(self.env), counting.Settings(True, False, True))

    def test_settings_default_on_and_survive_a_bad_file(self):
        self.assertEqual(counting.load_settings(self.env), counting.Settings())
        path = counting.settings_path(self.env)
        path.parent.mkdir(parents=True)
        path.write_text('{nonsense')
        self.assertEqual(counting.load_settings(self.env), counting.Settings())


class Events(unittest.TestCase):
    def test_the_payload_is_exactly_four_fields(self):
        payload = counting.event_payload('org.gimp.GIMP', '3.0.4', 'x86_64', 'install')
        self.assertEqual(payload, {'app_id': 'org.gimp.GIMP', 'version': '3.0.4', 'arch': 'x86_64', 'kind': 'install'})
        self.assertEqual(counting.event_payload('org.gimp.GIMP', 'not a version!', 'aarch64', 'remove')['version'], '')
        for args in (('org.gimp.GIMP', '1', 'sparc', 'install'), ('org.gimp.GIMP', '1', 'x86_64', 'launch'),
                     ('', '1', 'x86_64', 'install'), ('a b', '1', 'x86_64', 'update')):
            with self.subTest(args=args), self.assertRaises(ValueError):
                counting.event_payload(*args)

    def test_sending_carries_no_identifier_and_respects_the_setting(self):
        sent = []

        class Response:
            def read(self, _n):
                return b''

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        def opener(request, timeout):
            sent.append(request)
            return Response()

        self.assertTrue(counting.send_install_event('org.gimp.GIMP', '3', 'x86_64', 'update',
                                                    settings=counting.Settings(), opener=opener))
        request = sent[0]
        self.assertEqual(request.full_url, counting.EVENTS_URL)
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(json.loads(request.data), {'app_id': 'org.gimp.GIMP', 'version': '3',
                                                    'arch': 'x86_64', 'kind': 'update'})
        headers = {name.lower() for name in request.header_items() and dict(request.header_items())}
        self.assertEqual(headers, {'content-type', 'user-agent'})
        self.assertFalse(counting.send_install_event('org.gimp.GIMP', '3', 'x86_64', 'update',
                                                     settings=counting.Settings(install_events=False),
                                                     opener=opener))
        self.assertEqual(len(sent), 1)

        def offline(request, timeout):
            raise OSError('no route')
        self.assertFalse(counting.send_install_event('org.gimp.GIMP', '3', 'x86_64', 'install',
                                                     settings=counting.Settings(), opener=offline))


if __name__ == '__main__':
    unittest.main()
