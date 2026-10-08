#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

"""Everything in Clock that can be wrong without anybody seeing it.

Band slicing, fractional offsets, calendar-day rollover across the date line,
gradient run-merging, DST, monotonic timing under a clock step, 12/24-hour
formatting and alarm recurrence. None of it needs a display.
"""

from __future__ import annotations

import stat
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/prairie-core"))
from prairie_apps.clock_backend import (  # noqa: E402
    DAY, EVERY_DAY, MINUS, NIGHT, SLICES, TWILIGHT, WEEKDAYS, WEEKENDS,
    Alarm, ClockStore, Countdown, Stopwatch,
    axis_ticks, band_description, day_rollover, day_slices,
    format_clock, format_countdown, format_stopwatch, gradient_stops,
    hour_format_from_pattern, is_asleep, local_time, next_occurrence,
    now_fraction, offset_text, rollover_chip, snooze_until,
    tone_for_hour, volume_ramp, world_layout, zone_city, zone_offset,
)
from prairie_apps.clock_fixture import FixtureClockStore  # noqa: E402

UTC = ZoneInfo("UTC")


class BandSlicingTests(unittest.TestCase):
    """The axis is yours; every band is that same axis in somebody else's hours."""

    def test_own_zone_slices_at_the_tone_boundaries(self):
        now = datetime(2026, 9, 11, 14, 47, tzinfo=UTC)
        runs = day_slices("UTC", now=now, home="UTC")
        self.assertEqual(
            [(round(r.start, 6), round(r.end, 6), r.tone) for r in runs],
            [
                (0.0, round(6 / 24, 6), NIGHT),
                (round(6 / 24, 6), round(9 / 24, 6), TWILIGHT),
                (round(9 / 24, 6), round(18 / 24, 6), DAY),
                (round(18 / 24, 6), round(22 / 24, 6), TWILIGHT),
                (round(22 / 24, 6), 1.0, NIGHT),
            ],
        )

    def test_every_band_covers_the_axis_exactly_once(self):
        now = datetime(2026, 9, 11, 14, 47, tzinfo=UTC)
        for zone in ("Pacific/Auckland", "Asia/Kathmandu", "America/Los_Angeles", "Asia/Kolkata"):
            runs = day_slices(zone, now=now, home="America/Los_Angeles")
            self.assertAlmostEqual(runs[0].start, 0.0, msg=zone)
            self.assertAlmostEqual(runs[-1].end, 1.0, msg=zone)
            for earlier, later in zip(runs, runs[1:]):
                self.assertAlmostEqual(earlier.end, later.start, msg=zone)
                self.assertNotEqual(earlier.tone, later.tone, msg=zone)

    def test_rows_are_sliced_onto_your_day_not_their_own(self):
        """The failure this whole design exists to avoid: six identical pictures."""
        now = datetime(2026, 9, 11, 14, 47, tzinfo=UTC)
        shapes = {
            zone: tuple((round(r.start, 6), r.tone) for r in day_slices(zone, now=now, home="Europe/London"))
            for zone in ("Europe/London", "America/New_York", "Asia/Tokyo", "Pacific/Auckland")
        }
        self.assertEqual(len(set(shapes.values())), 4, "bands must differ between cities")

    def test_fractional_offset_slices_mid_column(self):
        """+5:45 Nepal. A boundary that does not land on an hour edge."""
        now = datetime(2026, 9, 11, 14, 47, tzinfo=UTC)
        self.assertEqual(offset_text("Asia/Kathmandu", now=now, home="UTC"), "+5:45 hrs")
        runs = day_slices("Asia/Kathmandu", now=now, home="UTC")
        edges = [round(run.end * 24, 4) for run in runs[:-1]]
        # 06:00, 09:00, 18:00 and 22:00 in Kathmandu are 00:15, 03:15, 12:15
        # and 16:15 here — a quarter of an hour inside each column.
        self.assertEqual(edges, [0.25, 3.25, 12.25, 16.25])
        for edge in edges:
            self.assertNotEqual(edge % 1, 0.0, "a whole-hour edge means the fraction was rounded away")

    def test_every_fractional_zone_lands_on_a_slice_edge(self):
        now = datetime(2026, 9, 11, 14, 47, tzinfo=UTC)
        for zone in ("Asia/Kolkata", "Asia/Kathmandu", "Australia/Adelaide", "Pacific/Chatham"):
            for run in day_slices(zone, now=now, home="UTC"):
                self.assertAlmostEqual((run.end * SLICES) % 1, 0.0, msg=zone)

    def test_dst_in_the_other_zone_moves_the_boundary(self):
        """London's boundaries sit an hour earlier on your axis once BST starts."""
        winter = day_slices("Europe/London", now=datetime(2026, 1, 15, 12, tzinfo=UTC), home="UTC")
        summer = day_slices("Europe/London", now=datetime(2026, 7, 15, 12, tzinfo=UTC), home="UTC")
        self.assertAlmostEqual(winter[0].end * 24, 6.0)
        self.assertAlmostEqual(summer[0].end * 24, 5.0)

    def test_a_band_containing_a_dst_transition_is_still_continuous(self):
        """29 March 2026: London jumps 01:00 → 02:00 inside this axis."""
        runs = day_slices("Europe/London", now=datetime(2026, 3, 29, 9, tzinfo=UTC), home="UTC")
        self.assertAlmostEqual(runs[0].start, 0.0)
        self.assertAlmostEqual(runs[-1].end, 1.0)
        self.assertAlmostEqual(runs[0].end * 24, 5.0, msg="night ends at 06:00 BST = 05:00 UTC")

    def test_offset_re_resolves_across_a_transition_without_a_restart(self):
        self.assertEqual(offset_text("Europe/London", now=datetime(2026, 1, 15, 12, tzinfo=UTC), home="UTC"), "Local time")
        self.assertEqual(offset_text("Europe/London", now=datetime(2026, 7, 15, 12, tzinfo=UTC), home="UTC"), "+1 hr")

    def test_offset_text_spelling(self):
        now = datetime(2026, 9, 11, 14, 47, tzinfo=UTC)
        self.assertEqual(offset_text("UTC", now=now, home="UTC"), "Local time")
        self.assertEqual(offset_text("Asia/Tokyo", now=now, home="UTC"), "+9 hrs")
        self.assertEqual(offset_text("Asia/Kolkata", now=now, home="UTC"), "+5:30 hrs")
        minus_five = offset_text("America/New_York", now=now, home="UTC")
        self.assertEqual(minus_five, f"{MINUS}4 hrs")
        self.assertNotIn("-", minus_five, "the minus is U+2212, not a hyphen")

    def test_tone_table(self):
        self.assertEqual([tone_for_hour(h) for h in (22, 23, 0, 5)], [NIGHT] * 4)
        self.assertEqual([tone_for_hour(h) for h in (6, 8, 18, 21)], [TWILIGHT] * 4)
        self.assertEqual([tone_for_hour(h) for h in (9, 12, 17)], [DAY] * 3)


class GradientMergingTests(unittest.TestCase):
    def test_a_band_is_a_handful_of_hard_stops_not_ninety_six_boxes(self):
        now = datetime(2026, 9, 11, 14, 47, tzinfo=UTC)
        runs = day_slices("Asia/Tokyo", now=now, home="America/Los_Angeles")
        stops = gradient_stops(runs)
        self.assertLessEqual(len(stops), 12)
        self.assertEqual(len(stops), 2 * len(runs))

    def test_stops_come_in_same_colour_pairs_so_the_edge_is_hard(self):
        runs = day_slices("UTC", now=datetime(2026, 9, 11, 14, 47, tzinfo=UTC), home="UTC")
        stops = gradient_stops(runs)
        for opening, closing in zip(stops[0::2], stops[1::2]):
            self.assertEqual(opening[0], closing[0])
            self.assertLess(opening[1], closing[1])
        # Consecutive runs share an edge at two different colours: a hard stop.
        for closing, opening in zip(stops[1::2], stops[2::2]):
            self.assertAlmostEqual(closing[1], opening[1])
            self.assertNotEqual(closing[0], opening[0])

    def test_stops_run_from_zero_to_one_monotonically(self):
        runs = day_slices("Pacific/Chatham", now=datetime(2026, 9, 11, 14, 47, tzinfo=UTC), home="UTC")
        stops = gradient_stops(runs)
        self.assertAlmostEqual(stops[0][1], 0.0)
        self.assertAlmostEqual(stops[-1][1], 1.0)
        self.assertEqual(list(stops), sorted(stops, key=lambda stop: stop[1]))


class RolloverTests(unittest.TestCase):
    def test_across_the_date_line_by_calendar_day_not_by_hour(self):
        """LA 18:00 vs Auckland 13:00: the hour is lower, the day is later."""
        now = datetime(2026, 9, 12, 1, 0, tzinfo=UTC)  # 18:00 Sep 11 in Los Angeles
        self.assertEqual(local_time("America/Los_Angeles", now).hour, 18)
        self.assertEqual(local_time("Pacific/Auckland", now).hour, 13)
        self.assertEqual(day_rollover("Pacific/Auckland", now=now, home="America/Los_Angeles"), 1)
        self.assertEqual(rollover_chip(1), "+1 day")

    def test_the_other_direction(self):
        now = datetime(2026, 9, 12, 1, 0, tzinfo=UTC)
        self.assertEqual(day_rollover("America/Los_Angeles", now=now, home="Pacific/Auckland"), -1)
        self.assertEqual(rollover_chip(-1), f"{MINUS}1 day")
        self.assertNotIn("-", rollover_chip(-1))

    def test_same_day_has_no_chip(self):
        now = datetime(2026, 9, 11, 14, 47, tzinfo=UTC)
        self.assertEqual(day_rollover("Europe/Paris", now=now, home="Europe/London"), 0)
        self.assertEqual(rollover_chip(0), "")


class NowLineTests(unittest.TestCase):
    def test_one_x_for_every_row(self):
        now = datetime(2026, 9, 11, 21, 47, 30, tzinfo=UTC)
        self.assertAlmostEqual(now_fraction(now=now, home="UTC"), (21 + 47 / 60 + 30 / 3600) / 24)

    def test_it_lands_inside_the_axis_for_every_zone_we_might_be_in(self):
        now = datetime(2026, 9, 11, 14, 47, tzinfo=UTC)
        for home in ("Pacific/Chatham", "Asia/Kathmandu", "Pacific/Auckland", "UTC"):
            fraction = now_fraction(now=now, home=home)
            self.assertTrue(0.0 <= fraction < 1.0, home)


class HourFormatTests(unittest.TestCase):
    def test_pattern_detection(self):
        self.assertTrue(hour_format_from_pattern("%H:%M:%S"))
        self.assertFalse(hour_format_from_pattern("%I:%M:%S %p"))
        self.assertFalse(hour_format_from_pattern("%r"))

    def test_formatting_both_ways(self):
        evening = datetime(2026, 9, 11, 21, 47, tzinfo=UTC)
        morning = datetime(2026, 9, 11, 0, 5, tzinfo=UTC)
        self.assertEqual(format_clock(evening, hour24=False), ("9:47", "PM"))
        self.assertEqual(format_clock(evening, hour24=True), ("21:47", ""))
        self.assertEqual(format_clock(morning, hour24=False), ("12:05", "AM"))
        self.assertEqual(format_clock(morning, hour24=True), ("00:05", ""))

    def test_no_seconds_anywhere_in_the_world_readouts(self):
        moment = datetime(2026, 9, 11, 21, 47, 33, tzinfo=UTC)
        self.assertEqual(format_clock(moment, hour24=True), ("21:47", ""))

    def test_axis_ticks_follow_the_locale(self):
        self.assertEqual(axis_ticks(False), ("12a", "6a", "12p", "6p", "12a"))
        self.assertEqual(axis_ticks(True), ("00", "06", "12", "18", "00"))


class AccessibilityTests(unittest.TestCase):
    def test_the_band_has_a_text_alternative_carrying_the_asleep_signal(self):
        now = datetime(2026, 9, 12, 1, 0, tzinfo=UTC)  # Auckland 13:00, LA 18:00
        sentence = band_description("Auckland", "Pacific/Auckland", now=now, home="America/Los_Angeles", hour24=False)
        self.assertEqual(sentence, "Auckland, 1:00 PM, 19 hours ahead, tomorrow, daytime.")
        night = band_description("London", "Europe/London", now=now, home="America/Los_Angeles", hour24=False)
        self.assertEqual(night, "London, 2:00 AM, 8 hours ahead, tomorrow, night, likely asleep.")
        self.assertTrue(is_asleep("Europe/London", now=now))
        self.assertFalse(is_asleep("Pacific/Auckland", now=now))


class ResponsiveTests(unittest.TestCase):
    """Two axes, and they are not allowed to collapse into one."""

    def test_a_desktop_window_keeps_the_side_by_side_row(self):
        layout = world_layout("wide", handheld=False)
        self.assertEqual((layout.stacked, layout.bands, layout.hint), (False, True, False))

    def test_between_520_and_640_the_row_stacks_and_keeps_its_band(self):
        layout = world_layout("stacked", handheld=False)
        self.assertEqual((layout.stacked, layout.bands, layout.hint), (True, True, False))

    def test_a_window_dragged_under_520_loses_the_bands_and_says_so(self):
        layout = world_layout("narrow", handheld=False)
        self.assertEqual((layout.stacked, layout.bands, layout.hint), (True, False, True))

    def test_a_phone_keeps_the_bands_and_never_shows_the_hint(self):
        """"Widen the window" is advice you cannot take on a phone, and losing
        the bands there would gut the feature on the device that needs it."""
        for size_class in ("wide", "stacked", "narrow"):
            layout = world_layout(size_class, handheld=True)
            self.assertTrue(layout.bands, size_class)
            self.assertFalse(layout.hint, size_class)
        self.assertTrue(world_layout("narrow", handheld=True).stacked)

    def test_the_hint_only_ever_appears_when_something_is_actually_hidden(self):
        for size_class in ("wide", "stacked", "narrow"):
            for handheld in (False, True):
                layout = world_layout(size_class, handheld=handheld)
                if layout.hint:
                    self.assertFalse(layout.bands)


class MonotonicTests(unittest.TestCase):
    class Clock:
        def __init__(self):
            self.value = 1000.0

        def __call__(self):
            return self.value

    def test_a_wall_clock_step_does_not_touch_a_running_stopwatch(self):
        clock = self.Clock()
        watch = Stopwatch(clock=clock)
        watch.start()
        clock.value += 5.0
        self.assertAlmostEqual(watch.elapsed(), 5.0)
        original = time.time
        try:
            time.time = lambda: original() + 3600  # an NTP step, or somebody in Settings
            self.assertAlmostEqual(watch.elapsed(), 5.0, msg="the stopwatch read wall time")
        finally:
            time.time = original
        clock.value += 2.5
        self.assertAlmostEqual(watch.elapsed(), 7.5)

    def test_laps_carry_both_the_split_and_the_total(self):
        clock = self.Clock()
        watch = Stopwatch(clock=clock)
        watch.start()
        clock.value += 12.0
        self.assertEqual(watch.lap(), (12.0, 12.0))
        clock.value += 7.5
        self.assertEqual(watch.lap(), (7.5, 19.5))
        clock.value += 3.0
        self.assertEqual(watch.lap(), (3.0, 22.5))
        self.assertEqual(len(watch.laps), 3)

    def test_stop_and_resume_keeps_the_elapsed_time(self):
        clock = self.Clock()
        watch = Stopwatch(clock=clock)
        watch.start()
        clock.value += 4.0
        watch.stop()
        clock.value += 100.0  # the app was closed, or the tab was elsewhere
        self.assertAlmostEqual(watch.elapsed(), 4.0)
        watch.start()
        clock.value += 1.0
        self.assertAlmostEqual(watch.elapsed(), 5.0)

    def test_countdown_is_monotonic_and_pauses(self):
        clock = self.Clock()
        timer = Countdown(300.0, clock=clock)
        timer.start()
        clock.value += 60.0
        self.assertAlmostEqual(timer.remaining(), 240.0)
        timer.pause()
        clock.value += 3600.0
        self.assertAlmostEqual(timer.remaining(), 240.0)
        timer.start()
        clock.value += 240.0
        self.assertTrue(timer.finished())
        self.assertAlmostEqual(timer.remaining(), 0.0)
        self.assertAlmostEqual(timer.fraction(), 0.0)

    def test_a_reopened_window_joins_a_running_timer_rather_than_restarting_it(self):
        clock = self.Clock()
        timer = Countdown(1500.0, clock=clock)   # the 25-minute preset
        timer.resume_with(412.0)                 # what the scheduled unit says is left
        self.assertTrue(timer.running)
        self.assertAlmostEqual(timer.remaining(), 412.0)
        self.assertAlmostEqual(timer.total, 1500.0)
        clock.value += 12.0
        self.assertAlmostEqual(timer.remaining(), 400.0)

    def test_readout_formats(self):
        self.assertEqual(format_stopwatch(0.0), "00:00.00")
        self.assertEqual(format_stopwatch(65.43), "01:05.43")
        self.assertEqual(format_countdown(300.0), "05:00")
        self.assertEqual(format_countdown(0.2), "00:01", "a timer reads 00:01 until it is done")
        self.assertEqual(format_countdown(0.0), "00:00")


class AlarmArithmeticTests(unittest.TestCase):
    def setUp(self):
        # Friday 11 September 2026, 08:00 local.
        self.friday = datetime(2026, 9, 11, 8, 0, tzinfo=UTC)

    def test_once_today_then_tomorrow(self):
        alarm = Alarm("a" * 32, "", 9, 30)
        self.assertEqual(next_occurrence(alarm, self.friday), datetime(2026, 9, 11, 9, 30, tzinfo=UTC))
        later = Alarm("a" * 32, "", 6, 30)
        self.assertEqual(next_occurrence(later, self.friday), datetime(2026, 9, 12, 6, 30, tzinfo=UTC))

    def test_weekday_alarm_skips_the_weekend(self):
        alarm = Alarm("a" * 32, "", 6, 30, WEEKDAYS)
        self.assertEqual(self.friday.weekday(), 4)
        self.assertEqual(next_occurrence(alarm, self.friday), datetime(2026, 9, 14, 6, 30, tzinfo=UTC))

    def test_weekend_alarm_from_a_friday(self):
        alarm = Alarm("a" * 32, "", 7, 15, WEEKENDS)
        self.assertEqual(next_occurrence(alarm, self.friday), datetime(2026, 9, 12, 7, 15, tzinfo=UTC))

    def test_a_weekly_alarm_resolves_a_full_week_out(self):
        alarm = Alarm("a" * 32, "", 6, 0, (4,))  # Fridays, and it is already 08:00
        self.assertEqual(next_occurrence(alarm, self.friday), datetime(2026, 9, 18, 6, 0, tzinfo=UTC))

    def test_repeat_text(self):
        uid = "a" * 32
        self.assertEqual(Alarm(uid, "", 7, 0).repeat_text(), "Once")
        self.assertEqual(Alarm(uid, "", 7, 0, EVERY_DAY).repeat_text(), "Every day")
        self.assertEqual(Alarm(uid, "", 7, 0, WEEKDAYS).repeat_text(), "Weekdays")
        self.assertEqual(Alarm(uid, "", 7, 0, WEEKENDS).repeat_text(), "Weekends")
        self.assertEqual(Alarm(uid, "", 7, 0, (0, 2, 4)).repeat_text(), "Mon Wed Fri")
        self.assertEqual(Alarm(uid, "Gym", 7, 0, WEEKDAYS).subtitle(), "Gym")
        self.assertEqual(Alarm(uid, "", 7, 0, WEEKDAYS).subtitle(), "Weekdays")

    def test_snooze_and_ramp(self):
        alarm = Alarm("a" * 32, "", 6, 30, snooze_minutes=9)
        self.assertEqual(snooze_until(alarm, self.friday), datetime(2026, 9, 11, 8, 9, tzinfo=UTC))
        self.assertAlmostEqual(volume_ramp(0.0), 0.15)
        self.assertAlmostEqual(volume_ramp(10.0, ramp_seconds=20.0), 0.575)
        self.assertAlmostEqual(volume_ramp(40.0, ramp_seconds=20.0), 1.0)


# Scheduling alarms and timers, and the move off the systemd units that used to
# do it, are tested in src/prairie-core/tests/clock_alarms_unit.py.


class StoreTests(unittest.TestCase):
    def test_alarm_toggle_preserves_unedited_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ClockStore(Path(temporary) / "clock.json")
            uid = "a" * 32
            store._write({"world": [], "alarms": [{"uid": uid, "hour": 7, "minute": 0,
                                                    "enabled": True, "future_field": "keep"}],
                          "timers": [], "future_section": {"keep": True}})
            self.assertTrue(store.update_alarm_enabled(uid, False))
            data = store._read()
            self.assertEqual(data["alarms"][0]["future_field"], "keep")
            self.assertFalse(data["alarms"][0]["enabled"])
            self.assertEqual(data["future_section"], {"keep": True})

    def test_v70_fixture_is_in_memory(self):
        source = REPO_ROOT / "tests/fixtures/clock-v70.json"
        before = source.read_bytes()
        store = FixtureClockStore.from_path(source)
        self.assertEqual(len(store.world_clocks()), 5)
        self.assertEqual(len(store.alarms()), 3)
        self.assertTrue(store.update_alarm_enabled("0" * 30 + "11", False))
        self.assertEqual(source.read_bytes(), before)

    def test_records_persist_privately_and_undo_can_restore_position(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "clock/state.json"
            store = ClockStore(path)
            first = store.add_world_clock("London", "Europe/London")
            second = store.add_world_clock("Tokyo", "Asia/Tokyo")
            store.add_world_clock("Sydney", "Australia/Sydney")
            store.remove_world_clock(second.uid)
            self.assertEqual([c.label for c in store.world_clocks()], ["London", "Sydney"])
            store.insert_world_clock(second, 1)
            self.assertEqual([c.label for c in store.world_clocks()], ["London", "Tokyo", "Sydney"])
            self.assertTrue(store.move_world_clock(second.uid, 1))
            self.assertEqual([c.label for c in store.world_clocks()], ["London", "Sydney", "Tokyo"])
            self.assertFalse(store.move_world_clock(second.uid, 1))
            self.assertTrue(store.move_world_clock(second.uid, -2))
            self.assertEqual([c.label for c in store.world_clocks()], ["Tokyo", "London", "Sydney"])

            alarm = Alarm("e" * 32, "Tea", 6, 30, WEEKDAYS)
            store.save_alarm(alarm)
            self.assertEqual(store.alarms()[0].days, WEEKDAYS)
            store.delete_alarm(alarm.uid)
            self.assertEqual(store.alarms(), ())
            store.save_alarm(alarm)  # Undo
            self.assertEqual(store.alarms()[0].label, "Tea")

            store.remove_world_clock(first.uid)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_zone_helpers(self):
        self.assertEqual(zone_city("America/Los_Angeles"), "Los Angeles")
        self.assertEqual(zone_city("UTC"), "UTC")
        self.assertEqual(
            zone_offset("Asia/Kathmandu", now=datetime(2026, 9, 11, 12, tzinfo=UTC), home="UTC"),
            timedelta(hours=5, minutes=45),
        )
        self.assertEqual(local_time("UTC", datetime.fromisoformat("2026-08-13T12:00:00-05:00")).hour, 17)


if __name__ == "__main__":
    unittest.main(verbosity=2)
