# SPDX-License-Identifier: Apache-2.0
"""The selection rule and the wording, without a calendar or a bus."""

import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from luma_agenda.events import MAX_REFRESH_SECONDS, Upcoming, end_of_day, refresh_delay, select, WARMUP_DELAYS
from luma_agenda import extension

ZONE = timezone(timedelta(hours=-5))
NOW = datetime(2026, 9, 12, 10, 48, tzinfo=ZONE)


@dataclass
class Fake:
    summary: str
    start: datetime
    end: datetime
    all_day: bool = False


def at(hour, minute=0):
    return datetime(2026, 9, 12, hour, minute, tzinfo=ZONE)


class Selection(unittest.TestCase):
    def test_nothing_today_shows_nothing(self):
        self.assertIsNone(select([], NOW))

    def test_the_next_event_to_start_wins(self):
        chosen = select([Fake("Late", at(16), at(17)),
                         Fake("Design review", at(11, 30), at(12))], NOW)
        self.assertEqual(chosen.summary, "Design review")
        self.assertFalse(chosen.in_progress)

    def test_an_event_already_running_beats_one_still_to_come(self):
        chosen = select([Fake("Design review", at(11, 30), at(12)),
                         Fake("Stand-up", at(10, 45), at(11))], NOW)
        self.assertEqual(chosen.summary, "Stand-up")
        self.assertTrue(chosen.in_progress)

    def test_finished_events_are_not_shown(self):
        self.assertIsNone(select([Fake("Earlier", at(9), at(9, 30))], NOW))

    def test_an_all_day_event_only_shows_with_nothing_timed_left(self):
        both = select([Fake("Conference", at(0), at(23, 59), all_day=True),
                       Fake("Design review", at(11, 30), at(12))], NOW)
        self.assertEqual(both.summary, "Design review")
        alone = select([Fake("Conference", at(0), at(23, 59), all_day=True)], NOW)
        self.assertEqual(alone.summary, "Conference")

    def test_an_event_with_no_name_still_reads_as_something(self):
        self.assertEqual(select([Fake("   ", at(11), at(12))], NOW).summary, "Busy")

    def test_the_day_ends_at_midnight_in_the_same_zone(self):
        self.assertEqual(end_of_day(NOW).date(), NOW.date())
        self.assertEqual(end_of_day(NOW).tzinfo, NOW.tzinfo)


class Wording(unittest.TestCase):
    def state(self, **kw):
        base = dict(summary="Design review", start=at(11, 30), end=at(12),
                    all_day=False, in_progress=False)
        base.update(kw)
        return Upcoming(**base)

    def test_a_countdown_inside_the_hour(self):
        self.assertEqual(extension.standing(self.state(), NOW), "Starts in 42 min")

    def test_a_clock_time_beyond_the_hour(self):
        state = self.state(start=at(16), end=at(17))
        self.assertEqual(extension.standing(state, NOW, clock24=True), "Later today")

    def test_a_running_event_says_when_it_ends(self):
        state = self.state(start=at(10, 30), in_progress=True)
        self.assertEqual(extension.standing(state, NOW, clock24=True), "Now · until 12:00")

    def test_an_event_about_to_begin(self):
        state = self.state(start=NOW)
        self.assertEqual(extension.standing(state, NOW), "Starting now")

    def test_an_all_day_event_has_no_countdown(self):
        self.assertEqual(extension.standing(self.state(all_day=True), NOW), "All day")

    def test_the_payload_matches_the_wire_contract(self):
        payload = extension.build(self.state(), now=NOW)
        self.assertEqual(
            set(payload),
            {"schema_version", "id", "app_id", "category", "title", "subtitle",
             "privacy", "progress", "actions", "starts_at", "expires_at"},
        )
        self.assertEqual(payload["category"], "event")
        self.assertEqual(payload["app_id"], "org.projectluma.Calendar")
        self.assertEqual(payload["privacy"], "private")
        self.assertLessEqual(len(payload["actions"]), 3)

    def test_bidi_controls_never_reach_the_shelf(self):
        payload = extension.build(self.state(summary="a‮b"), now=NOW)
        self.assertEqual(payload["title"], "ab")


class BrokerContract(unittest.TestCase):
    """Check the payload against the broker's own validator, not a copy of it.

    The bug this exists for: the action carried risk "safe", which is not in
    the broker's vocabulary. Every publication was refused, the shelf stayed
    empty, and nothing anywhere said why — the producer was running, the unit
    was active, and the payload looked perfectly reasonable. A hand-written
    assertion about which strings are allowed would have been just as wrong as
    the code it was checking.
    """

    def setUp(self):
        try:
            from luma_semantic_broker import validation
        except ImportError:  # pragma: no cover - broker absent in some builders
            self.skipTest("luma_semantic_broker is not installed here")
        self.validation = validation

    def state(self):
        return Upcoming("Design review", at(11, 30), at(12), False, False)

    def test_the_payload_is_accepted_as_published(self):
        self.validation.live_extension(extension.build(self.state(), now=NOW))

    def test_every_wording_the_producer_can_emit_is_accepted(self):
        for state in (self.state(),
                      Upcoming("Now", at(10, 30), at(12), False, True),
                      Upcoming("All day", at(0), at(23, 59), True, True),
                      Upcoming("Later", at(16), at(17), False, False)):
            self.validation.live_extension(extension.build(state, now=NOW))

    def test_the_action_risk_is_one_the_broker_knows(self):
        action = extension.open_action()
        self.assertIn(action["risk"], self.validation.RISK)


class Pacing(unittest.TestCase):
    def test_the_publication_outlives_the_longest_sleep(self):
        """The bug this guards: a sleep longer than the expiry empties the shelf.

        The producer republishes on waking. If it can sleep for longer than the
        publication lives, the event is on screen for part of each cycle and
        gone for the rest, which looks exactly like a feature that does not
        work.
        """
        self.assertGreater(extension.EXPIRY_SECONDS, 2 * MAX_REFRESH_SECONDS)
        for state in (None,
                      Upcoming("x", at(11, 30), at(12), False, False),
                      Upcoming("x", at(16), at(17), False, False),
                      Upcoming("x", at(10, 30), at(12), False, True),
                      Upcoming("x", at(0), at(23, 59), True, True)):
            self.assertLessEqual(refresh_delay(state, NOW), MAX_REFRESH_SECONDS)

    def test_an_empty_day_is_not_polled_hard(self):
        self.assertEqual(refresh_delay(None, NOW), MAX_REFRESH_SECONDS)

    def test_a_countdown_redraws_every_half_minute(self):
        state = Upcoming("x", at(11, 30), at(12), False, False)
        self.assertEqual(refresh_delay(state, NOW), 30)

    def test_a_distant_event_waits_longer_than_a_countdown_does(self):
        state = Upcoming("x", at(16), at(17), False, False)
        self.assertGreater(refresh_delay(state, NOW), 30)


if __name__ == "__main__":
    unittest.main()


class WarmUp(unittest.TestCase):
    """A producer that starts with the session races the calendar backend.

    The first reads can be empty because no calendar has registered yet, not
    because the day is empty. Waiting the full ceiling then left the shelf
    blank for two minutes after login and the event only appeared once the
    Calendar application had been opened by hand.
    """

    def test_looks_again_soon_while_nothing_has_been_shown(self):
        now = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)
        delays = [refresh_delay(None, now, warmup=n) for n in range(len(WARMUP_DELAYS))]
        self.assertEqual(delays, list(WARMUP_DELAYS))
        for delay in delays:
            self.assertLess(delay, MAX_REFRESH_SECONDS)

    def test_settles_at_the_ceiling_once_the_backend_has_had_its_chance(self):
        now = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(refresh_delay(None, now, warmup=len(WARMUP_DELAYS)),
                         MAX_REFRESH_SECONDS)

    def test_an_empty_day_is_not_polled_forever(self):
        """Once something has been published, warmup is over: no ladder."""
        now = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(refresh_delay(None, now), MAX_REFRESH_SECONDS)

    def test_warm_up_never_outlives_the_publication(self):
        now = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)
        for n in range(len(WARMUP_DELAYS) + 2):
            self.assertLessEqual(refresh_delay(None, now, warmup=n),
                                 extension.EXPIRY_SECONDS)
