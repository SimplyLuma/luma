# SPDX-License-Identifier: Apache-2.0
"""The producer reads off the main loop, and applies answers in order."""

import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

try:
    import luma_semantic_broker.client  # noqa: F401
except ImportError:
    # The publisher belongs to luma-developer-platform, which a clean package
    # build does not install. These tests replace it with a recorder anyway,
    # so a stand-in module is enough to import the producer.
    _broker = types.ModuleType("luma_semantic_broker")
    _broker.client = types.ModuleType("luma_semantic_broker.client")
    _broker.client.LiveExtensionPublisher = object
    sys.modules["luma_semantic_broker"] = _broker
    sys.modules["luma_semantic_broker.client"] = _broker.client

from luma_agenda import service  # noqa: E402
from luma_agenda.service import AgendaProducer, MAX_CONCURRENT_READS  # noqa: E402
from luma_agenda.events import Upcoming  # noqa: E402


NOW = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)


def event(summary, minutes):
    start = NOW + timedelta(minutes=minutes)
    return Upcoming(summary=summary, start=start, end=start + timedelta(hours=1),
                    all_day=False, in_progress=False)


class Recorder:
    """Stands in for the publisher; counts what it was asked to send."""

    def __init__(self):
        self.syncs = 0

    def sync(self):
        self.syncs += 1


class Producer(AgendaProducer):
    """A producer whose reads are run by hand rather than by a thread."""

    def __init__(self, reader):
        self.pending = []
        # The publisher wants a live session bus; the tests are about ordering.
        with mock.patch.object(service, "LiveExtensionPublisher",
                               lambda *a, **k: Recorder()):
            super().__init__(application=None, reader=reader, clock=lambda: NOW)

    def _spawn(self, work):
        # Hold the work instead of running it, so a test decides the order
        # answers come back in -- which is the whole point of the change.
        self.pending.append(work)

    def _deliver(self, *result):
        # No main loop here: apply it where a loop would have.
        self._reading_done(*result)


class Reads(unittest.TestCase):
    def _producer(self, events):
        return Producer(lambda _start, _end: events)

    def test_a_read_does_not_block_the_producer(self):
        """Construction must return with the read still outstanding."""
        p = self._producer([])
        self.assertEqual(len(p.pending), 1)
        self.assertIsNone(p._state)

    def test_an_answer_publishes_when_it_lands(self):
        p = self._producer([event("Standup", 30)])
        p.pending.pop()()
        self.assertIsNotNone(p._state)
        self.assertEqual(p._state.summary, "Standup")
        self.assertEqual(p.publisher.syncs, 1)

    def test_a_read_overtaken_while_waiting_does_not_publish_late(self):
        """The slow first read must not overwrite what overtook it.

        This is the case the change exists for: the first read of a cold
        session waits a minute, a later one answers immediately, and the
        stale answer arrives afterwards.
        """
        p = self._producer([])
        first = p.pending.pop()
        p._reader = lambda _s, _e: [event("Design review", 42)]
        p._refresh()
        second = p.pending.pop()

        second()                      # the later read lands first
        self.assertEqual(p._state.summary, "Design review")
        first()                       # the stale one arrives afterwards
        self.assertEqual(p._state.summary, "Design review")

    def test_reads_in_flight_are_bounded(self):
        p = self._producer([])
        for _ in range(MAX_CONCURRENT_READS + 4):
            p._refresh()
        self.assertLessEqual(len(p.pending), MAX_CONCURRENT_READS)

    def test_finishing_a_read_frees_a_slot(self):
        p = self._producer([])
        while len(p.pending) < MAX_CONCURRENT_READS:
            p._refresh()
        p.pending.pop(0)()
        p._refresh()
        self.assertEqual(len(p.pending), MAX_CONCURRENT_READS)

    def test_a_calendar_that_raises_is_not_fatal(self):
        def angry(_start, _end):
            raise RuntimeError("no calendar here")

        p = Producer(angry)
        p.pending.pop()()
        self.assertIsNone(p._state)


class Logging(unittest.TestCase):
    def test_a_quiet_day_is_logged_once_not_on_every_read(self):
        p = Producer(lambda _start, _end: [])
        with self.assertLogs("luma-agenda", level="INFO") as logged:
            p.pending.pop()()
            for _ in range(5):
                p._refresh()
                p.pending.pop()()
            p._reader = lambda _s, _e: [event("Standup", 30)]
            p._refresh()
            p.pending.pop()()
        reads = [line for line in logged.output if "calendar read took" in line]
        self.assertEqual(len(reads), 2)
        self.assertIn("0 event(s)", reads[0])
        self.assertIn("1 event(s)", reads[1])


if __name__ == "__main__":
    unittest.main()
