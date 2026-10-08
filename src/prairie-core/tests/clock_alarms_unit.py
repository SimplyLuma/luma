#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Clock's alarm service, without a display, a sound server or a portal.

The scheduling decisions (what is due, what was missed, what rings late), the
move off the old systemd units, the autostart entry, the wall-clock timer on a
real main loop, and the service end to end against a fake application, portal
and ringer with a clock the test moves.

    PYTHONPATH=src/prairie-core python3 src/prairie-core/tests/clock_alarms_unit.py
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = tempfile.TemporaryDirectory()
for key, sub in (("XDG_DATA_HOME", "data"), ("XDG_CONFIG_HOME", "config"), ("XDG_STATE_HOME", "state")):
    os.environ[key] = str(Path(ROOT.name) / sub)
os.environ["TZ"] = "America/Chicago"
time.tzset()

from gi.repository import GLib  # noqa: E402

from prairie_apps import clock_alarms  # noqa: E402
from prairie_apps.clock_alarms import (  # noqa: E402
    BACKGROUND_ALLOWED, BACKGROUND_DENIED, BACKGROUND_UNAVAILABLE, LATE_RING_LIMIT,
    AlarmService, Event, SchedulerState, WallClockTimer, hand_off, legacy_units,
    migrate_systemd_units, missed_events, rings_now, upcoming_events, write_autostart_entry,
)
from prairie_apps.clock_backend import (  # noqa: E402
    WEEKDAYS, Alarm, ClockStore, TimerRecord, next_occurrence, previous_occurrence,
)

CHICAGO = ZoneInfo("America/Chicago")
A, B, C = "a" * 32, "b" * 32, "c" * 32


def epoch(*parts) -> float:
    return datetime(*parts, tzinfo=CHICAGO).timestamp()


class RecurrenceTests(unittest.TestCase):
    def test_a_week_ahead_alarm_keeps_its_wall_time_across_the_dst_change(self):
        # US daylight saving ends on Sunday 1 November 2026. A fixed offset
        # taken on Friday would put Monday's 06:30 at 05:30.
        friday = datetime(2026, 10, 30, 8, 0).astimezone()
        monday = next_occurrence(Alarm(A, "", 6, 30, (0,)), friday)
        self.assertEqual((monday.date().isoformat(), monday.hour, monday.minute), ("2026-11-02", 6, 30))
        self.assertEqual(monday.utcoffset(), timedelta(hours=-6))

    def test_a_real_zone_is_kept(self):
        friday = datetime(2026, 10, 30, 8, 0, tzinfo=CHICAGO)
        self.assertEqual(next_occurrence(Alarm(A, "", 6, 30, (0,)), friday),
                         datetime(2026, 11, 2, 6, 30, tzinfo=CHICAGO))

    def test_previous_occurrence(self):
        friday = datetime(2026, 9, 11, 8, 0, tzinfo=CHICAGO)
        self.assertEqual(previous_occurrence(Alarm(A, "", 7, 0), friday), datetime(2026, 9, 11, 7, 0, tzinfo=CHICAGO))
        self.assertEqual(previous_occurrence(Alarm(A, "", 9, 0), friday), datetime(2026, 9, 10, 9, 0, tzinfo=CHICAGO))
        self.assertEqual(previous_occurrence(Alarm(A, "", 6, 30, WEEKDAYS), datetime(2026, 9, 13, 8, tzinfo=CHICAGO)),
                         datetime(2026, 9, 11, 6, 30, tzinfo=CHICAGO))


class DecisionTests(unittest.TestCase):
    def test_upcoming_is_every_kind_soonest_first(self):
        now = epoch(2026, 9, 11, 6, 0)
        events = upcoming_events(
            [Alarm(A, "", 7, 0), Alarm(B, "", 6, 30, enabled=False)],
            [TimerRecord(C, "Timer", int(now + 300), 300)],
            {B: now + 60}, now,
        )
        self.assertEqual([(e.kind, e.uid) for e in events], [("snooze", B), ("timer", C), ("alarm", A)])
        self.assertEqual(events[-1].at, epoch(2026, 9, 11, 7, 0))

    def test_missed_only_counts_what_fell_due_since_the_last_look(self):
        since, now = epoch(2026, 9, 11, 6, 0), epoch(2026, 9, 11, 9, 0)
        alarms = [Alarm(A, "", 7, 0), Alarm(B, "", 5, 0)]
        timers = [TimerRecord(C, "Timer", int(epoch(2026, 9, 10, 23, 0)), 60)]
        events = missed_events(alarms, timers, {}, since, now)
        # Alarm B's 05:00 was before the last look; the timer is unfinished
        # whenever it passed.
        self.assertEqual([(e.kind, e.uid) for e in events], [("timer", C), ("alarm", A)])

    def test_timer_sentence(self):
        self.assertEqual(clock_alarms.timer_sentence(300), "Your 5-minute timer finished.")
        self.assertEqual(clock_alarms.timer_sentence(20), "Your 20-second timer finished.")
        self.assertEqual(clock_alarms.timer_sentence(90), "Your 90-second timer finished.")
        self.assertEqual(clock_alarms.timer_sentence(0), "Your timer finished.")

    def test_ringing_late_has_a_limit(self):
        event = Event("alarm", A, 1000.0)
        self.assertTrue(rings_now(event, 1000.0 + LATE_RING_LIMIT))
        self.assertFalse(rings_now(event, 1001.0 + LATE_RING_LIMIT))


class StateTests(unittest.TestCase):
    def test_world_clock_order_survives_reopening_store(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "state.json"
            store = ClockStore(path)
            first = store.add_world_clock("London", "Europe/London")
            store.add_world_clock("Tokyo", "Asia/Tokyo")
            store.add_world_clock("Lisbon", "Europe/Lisbon")
            self.assertTrue(store.move_world_clock(first.uid, 2))
            self.assertEqual([city.label for city in ClockStore(path).world_clocks()],
                             ["Tokyo", "Lisbon", "London"])

    def test_round_trip_is_private_and_ignores_bad_ids(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "clock/scheduler.json"
            state = SchedulerState(path)
            state.checked_at, state.snoozes, state.autostart = 12.5, {A: 99.0}, True
            state.save()
            text = path.read_text()
            path.write_text(text.replace(A, "../../etc"))
            again = SchedulerState(path)
            self.assertEqual((again.checked_at, again.snoozes, again.autostart), (12.5, {}, True))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_store_rejects_ids_that_are_not_32_hex_digits(self):
        with tempfile.TemporaryDirectory() as root:
            store = ClockStore(Path(root) / "state.json")
            with self.assertRaises(ValueError):
                store.save_alarm(Alarm("../x", "", 6, 30))
            with self.assertRaises(ValueError):
                store.save_timer(TimerRecord("x" * 32, "", 1, 1))

    def test_alarm_toggle_preserves_other_record_fields(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "state.json"
            store = ClockStore(path)
            store.save_alarm(Alarm(A, "Wake", 6, 30))
            payload = store._read()
            payload["alarms"][0]["future_option"] = {"volume": 7}
            store._write(payload)

            self.assertTrue(store.update_alarm_enabled(A, False))
            changed = store._read()["alarms"][0]
            self.assertFalse(changed["enabled"])
            self.assertEqual(changed["future_option"], {"volume": 7})
            self.assertEqual((changed["label"], changed["hour"], changed["minute"]),
                             ("Wake", 6, 30))


class MigrationTests(unittest.TestCase):
    def runner(self):
        calls = []
        return calls, lambda arguments, **_kw: calls.append(arguments)

    def test_old_units_are_withdrawn_and_records_kept(self):
        calls, runner = self.runner()
        with tempfile.TemporaryDirectory() as root:
            units = Path(root)
            for name in (f"prairie-alarm-{A}", f"prairie-timer-{B}", "prairie-alarm-nothex", "other"):
                (units / f"{name}.timer").write_text("[Timer]\n")
                (units / f"{name}.service").write_text("[Service]\n")
            with mock.patch.object(clock_alarms, "is_sandboxed", return_value=False):
                moved = migrate_systemd_units(unit_dir=units, runner=runner)
            self.assertEqual(moved, [f"prairie-alarm-{A}", f"prairie-timer-{B}"])
            self.assertEqual(sorted(p.name for p in units.iterdir()),
                             ["other.service", "other.timer", "prairie-alarm-nothex.service", "prairie-alarm-nothex.timer"])
            self.assertEqual(legacy_units(units), [])
        self.assertIn(["systemctl", "--user", "disable", "--now", f"prairie-alarm-{A}.timer"], calls)
        self.assertIn(["systemctl", "--user", "stop", "prairie-snooze-*.timer"], calls)
        self.assertEqual(calls[-1], ["systemctl", "--user", "daemon-reload"])

    def test_nothing_to_move_runs_nothing_and_the_sandbox_never_tries(self):
        calls, runner = self.runner()
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(clock_alarms, "is_sandboxed", return_value=False):
                self.assertEqual(migrate_systemd_units(unit_dir=Path(root), runner=runner), [])
            (Path(root) / f"prairie-alarm-{A}.timer").write_text("")
            with mock.patch.object(clock_alarms, "is_sandboxed", return_value=True):
                self.assertEqual(migrate_systemd_units(unit_dir=Path(root), runner=runner), [])
        self.assertEqual(calls, [])


class AutostartEntryTests(unittest.TestCase):
    def test_entry_starts_the_service_and_is_removed_again(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "autostart/org.projectluma.Clock.desktop"
            self.assertTrue(write_autostart_entry(True, path=path, command=["/usr/bin/prairie-clock", "--gapplication-service"]))
            keyfile = GLib.KeyFile()
            keyfile.load_from_file(str(path), GLib.KeyFileFlags.NONE)
            self.assertEqual(keyfile.get_string("Desktop Entry", "Exec"), "/usr/bin/prairie-clock --gapplication-service")
            self.assertTrue(keyfile.get_boolean("Desktop Entry", "NoDisplay"))
            self.assertFalse(write_autostart_entry(False, path=path))
            self.assertFalse(path.exists())


class WallClockTimerTests(unittest.TestCase):
    def run_until(self, flag, seconds=3.0):
        context = GLib.MainContext.default()
        end = time.monotonic() + seconds
        while not flag and time.monotonic() < end:
            context.iteration(False)
            time.sleep(0.01)

    def test_fires_at_a_wall_clock_deadline_and_for_one_already_past(self):
        for delay in (0.3, -5.0):
            fired = []
            timer = WallClockTimer(lambda: fired.append(time.time()))
            deadline = time.time() + delay
            timer.arm(deadline)
            self.run_until(fired)
            self.assertTrue(fired, delay)
            self.assertGreaterEqual(fired[0], min(deadline, fired[0]))
            timer.cancel()

    def test_cancel_stops_it(self):
        fired = []
        timer = WallClockTimer(lambda: fired.append(1))
        timer.arm(time.time() + 0.2)
        timer.cancel()
        self.run_until(fired, 0.6)
        self.assertEqual(fired, [])


# -- the service, end to end ---------------------------------------------------


class FakeApplication:
    def __init__(self):
        self.actions, self.sent, self.withdrawn = {}, [], []
        self.holds = 0

    def add_action(self, action):
        self.actions[action.get_name()] = action

    def activate(self, name, uid):
        self.actions[name].activate(GLib.Variant("s", uid))

    def send_notification(self, identifier, notification):
        self.sent.append(identifier)

    def withdraw_notification(self, identifier):
        self.withdrawn.append(identifier)

    def hold(self):
        self.holds += 1

    def release(self):
        self.holds -= 1

    def get_dbus_connection(self):
        return None


class FakePortal:
    def __init__(self, answer=(0, {"background": True, "autostart": True}), error=None):
        self.requests, self.statuses, self.answer, self.error = [], [], answer, error

    def request(self, *, autostart, commandline, reason, callback):
        self.requests.append((autostart, tuple(commandline)))
        code, results = self.answer
        if autostart is False and code == 0:
            results = {"background": True, "autostart": False}
        callback(None if self.error else code, results, self.error)

    def set_status(self, message):
        self.statuses.append(message)


class FakeRinger:
    def __init__(self):
        self.playing = False
        self.started = []
        self.on_finished = None

    def start(self, sound, seconds, *, ramp=True):
        self.started.append((sound, seconds, ramp))
        self.playing = True
        return True

    def stop(self, *, expired=False):
        self.playing = False
        if expired and self.on_finished:
            self.on_finished()


class FakeInhibitor:
    def __init__(self):
        self.reason, self.reasons, self.releases = "", [], 0

    @property
    def held(self):
        return bool(self.reason)

    def hold(self, reason):
        if not self.reason:
            self.reason = reason
            self.reasons.append(reason)

    def release(self):
        if self.reason:
            self.reason = ""
            self.releases += 1


class FakeWake:
    def __init__(self):
        self.at, self.calls = None, []

    def set(self, at, *, wait=False):
        at = None if at is None else int(at)
        if at != self.at:
            self.at = at
            self.calls.append(at)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = ClockStore(Path(self.directory.name) / "clock/state.json")
        self.now = epoch(2026, 9, 11, 6, 59)
        self.app, self.portal, self.ringer = FakeApplication(), FakePortal(), FakeRinger()
        self.inhibitor, self.wake = FakeInhibitor(), FakeWake()
        self.patch = mock.patch.object(clock_alarms, "migrate_systemd_units", return_value=[])
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.directory.cleanup()

    def service(self, *, sandboxed=True, portal=None):
        service = AlarmService(self.app, store=self.store, clock=lambda: self.now,
                               portal=portal or self.portal, ringer=self.ringer, sandboxed=sandboxed,
                               inhibitor=self.inhibitor, wake=self.wake)
        service.timer = mock.Mock()
        return service

    def test_sleep_is_held_off_from_just_before_an_alarm_until_it_is_answered(self):
        self.store.save_alarm(Alarm(A, "Wake", 7, 0, WEEKDAYS))
        service = self.service(sandboxed=False)
        service.start()  # 06:59, one minute before
        self.assertEqual(self.inhibitor.reasons, ["An alarm is about to go off"])
        self.now = epoch(2026, 9, 11, 7, 0, 1)
        service._wake()
        self.assertEqual(service.ringing, f"alarm-{A}")
        self.assertTrue(self.inhibitor.held, "held while it rings")
        self.app.activate("clock-stop", A)
        self.assertFalse(self.inhibitor.held, "answered, and the next one is tomorrow")

    def test_an_alarm_nobody_answers_lets_go_of_the_hold(self):
        self.store.save_alarm(Alarm(A, "", 7, 0, WEEKDAYS))
        service = self.service(sandboxed=False)
        service.start()
        self.now = epoch(2026, 9, 11, 7, 0)
        service._wake()
        self.assertTrue(self.inhibitor.held)
        self.ringer.stop(expired=True)
        self.assertFalse(self.inhibitor.held)

    def test_nothing_due_soon_holds_nothing_and_the_timer_looks_again_in_time(self):
        self.store.save_alarm(Alarm(A, "", 8, 0))
        service = self.service(sandboxed=False)
        service.start()
        self.assertFalse(self.inhibitor.held)
        service.timer.arm.assert_called_with(epoch(2026, 9, 11, 8, 0) - clock_alarms.AWAKE_BEFORE_SECONDS)
        self.now = epoch(2026, 9, 11, 7, 58, 30)
        service._wake()
        self.assertTrue(self.inhibitor.held)
        service.timer.arm.assert_called_with(epoch(2026, 9, 11, 8, 0))

    def test_the_computer_is_woken_a_minute_before_whatever_is_next(self):
        self.store.save_alarm(Alarm(A, "", 8, 0))
        service = self.service(sandboxed=False)
        service.start()
        self.assertEqual(self.wake.calls, [epoch(2026, 9, 11, 7, 59)])
        self.store.save_timer(TimerRecord(C, "Tea", int(epoch(2026, 9, 11, 7, 30)), 1860))
        service.reschedule()
        self.assertEqual(self.wake.at, epoch(2026, 9, 11, 7, 29))
        self.store.clear_timer(C)
        self.store.delete_alarm(A)
        service.reschedule()
        self.assertIsNone(self.wake.at, "nothing scheduled, no wake-up")

    def test_a_process_that_leaves_ringing_to_the_agent_leaves_waking_to_it_too(self):
        self.store.save_alarm(Alarm(A, "", 8, 0))
        service = self.service(sandboxed=False)
        with mock.patch.object(service, "_agent_elsewhere", return_value=True):
            service.start()
            service.shutdown()
        self.assertEqual(self.wake.calls, [])
        self.assertFalse(self.inhibitor.held)

    def test_shutting_down_clears_the_wake_up(self):
        self.store.save_alarm(Alarm(A, "", 8, 0))
        service = self.service(sandboxed=False)
        service.start()
        service.shutdown()
        self.assertEqual(self.wake.calls, [epoch(2026, 9, 11, 7, 59), None])

    def test_the_sandbox_never_asks_for_a_system_wake_up(self):
        self.store.save_alarm(Alarm(A, "", 8, 0))
        service = AlarmService(self.app, store=self.store, clock=lambda: self.now, portal=self.portal,
                               ringer=self.ringer, sandboxed=True)
        service.timer = mock.Mock()
        service.start()
        self.assertIsNone(service.system_wake)
        self.assertIsNone(service.inhibitor)

    def test_with_luma_background_running_the_agent_clock_writes_no_autostart_of_its_own(self):
        self.store.save_alarm(Alarm(A, "", 8, 0))
        service = self.service(sandboxed=False)
        with mock.patch.object(service, "_background_managed", return_value=True):
            service.start()
        self.assertEqual(self.portal.requests, [])
        entry = Path(os.environ["XDG_CONFIG_HOME"]) / "autostart/org.projectluma.Clock.desktop"
        self.assertFalse(entry.exists())

    def test_a_once_alarm_rings_snoozes_rings_again_and_then_lets_go(self):
        self.store.save_alarm(Alarm(A, "Wake", 7, 0, snooze_minutes=9))
        service = self.service()
        service.start()
        self.assertEqual([e.kind for e in service.upcoming], ["alarm"])
        service.timer.arm.assert_called_with(epoch(2026, 9, 11, 7, 0))
        self.assertEqual(self.app.holds, 1)
        self.assertEqual(self.portal.requests, [(True, ("prairie-clock", "--gapplication-service"))])
        self.assertEqual(service.background, BACKGROUND_ALLOWED)
        self.assertTrue(self.portal.statuses and self.portal.statuses[-1].startswith("Next alarm"))

        self.now = epoch(2026, 9, 11, 7, 0, 1)
        service._wake()
        self.assertEqual(self.app.sent, [f"alarm-{A}"])
        self.assertEqual(self.ringer.started[-1][0], "Chime")
        self.assertEqual(service.ringing, f"alarm-{A}")
        self.assertFalse(self.store.alarms()[0].enabled, "a one-time alarm is spent once it rings")
        self.assertEqual(self.app.holds, 1, "ringing keeps the process alive")

        self.app.activate("clock-snooze", A)
        self.assertFalse(self.ringer.playing)
        self.assertIn(f"alarm-{A}", self.app.withdrawn)
        self.assertEqual([(e.kind, e.at) for e in service.upcoming], [("snooze", epoch(2026, 9, 11, 7, 9))])
        self.assertEqual(SchedulerState(clock_alarms.scheduler_path(self.store)).snoozes, {A: epoch(2026, 9, 11, 7, 9)})

        self.now = epoch(2026, 9, 11, 7, 9, 0)
        service._wake()
        self.assertEqual(self.app.sent.count(f"alarm-{A}"), 2)
        self.app.activate("clock-stop", A)
        self.assertEqual(service.upcoming, [])
        self.assertIsNone(service.ringing)
        self.assertEqual(self.app.holds, 0, "nothing scheduled, nothing held")
        self.assertEqual(self.portal.requests[-1], (False, ("prairie-clock", "--gapplication-service")))

    def test_an_unanswered_alarm_stops_and_is_reported_missed(self):
        self.store.save_alarm(Alarm(A, "", 7, 0, WEEKDAYS))
        service = self.service()
        service.start()
        self.now = epoch(2026, 9, 11, 7, 0)
        service._wake()
        self.ringer.stop(expired=True)
        self.assertIsNone(service.ringing)
        self.assertEqual(self.app.sent, [f"alarm-{A}", f"alarm-{A}"])
        self.assertTrue(self.store.alarms()[0].enabled, "a repeating alarm stays on")
        self.assertEqual(self.app.holds, 1)

    def test_at_start_a_recent_miss_rings_and_an_old_one_is_only_reported(self):
        self.store.save_alarm(Alarm(A, "", 7, 0, WEEKDAYS))
        state = SchedulerState(clock_alarms.scheduler_path(self.store))
        state.checked_at = epoch(2026, 9, 10, 22, 0)
        state.save()
        self.now = epoch(2026, 9, 11, 7, 5)
        self.service().start()
        self.assertEqual(len(self.ringer.started), 1)

        self.ringer.started.clear()
        self.app.sent.clear()
        state = SchedulerState(clock_alarms.scheduler_path(self.store))
        state.checked_at = epoch(2026, 9, 13, 22, 0)
        state.save()
        self.now = epoch(2026, 9, 14, 9, 0)  # Monday, two hours after 07:00
        service = self.service()
        service.start()
        self.assertEqual(self.ringer.started, [])
        self.assertEqual(self.app.sent, [f"alarm-{A}"])
        self.assertIsNone(service.ringing)

    def test_a_timer_finishes_with_the_window_closed(self):
        self.store.save_timer(TimerRecord(C, "Timer", int(self.now + 300), 300))
        service = self.service()
        service.start()
        self.assertEqual([e.kind for e in service.upcoming], ["timer"])
        self.now += 300
        service._wake()
        self.assertEqual(self.app.sent, [f"timer-{C}"])
        self.assertEqual(self.store.timers(), ())
        self.assertEqual(self.ringer.started[-1], ("Chime", clock_alarms.TIMER_CHIME_SECONDS, False))

    def test_an_edit_in_the_same_second_cannot_swallow_a_due_alarm(self):
        self.store.save_alarm(Alarm(A, "", 7, 0, WEEKDAYS))
        service = self.service()
        service.start()
        self.now = epoch(2026, 9, 11, 7, 0, 0)
        self.store.save_alarm(Alarm(B, "", 8, 0))
        service.reschedule()
        self.assertEqual(self.app.sent, [f"alarm-{A}"])

    def test_a_new_alarm_for_a_time_just_passed_waits_for_tomorrow(self):
        service = self.service()
        service.start()
        self.now = epoch(2026, 9, 11, 7, 0, 30)
        self.store.save_alarm(Alarm(A, "", 7, 0))
        service.reschedule()
        self.assertEqual(self.app.sent, [])
        self.assertEqual(service.upcoming[0].at, epoch(2026, 9, 12, 7, 0))

    def test_a_legacy_unit_hand_off_rings(self):
        self.store.save_alarm(Alarm(A, "", 7, 0, WEEKDAYS))
        state = SchedulerState(clock_alarms.scheduler_path(self.store))
        state.checked_at = epoch(2026, 9, 11, 7, 0, 30)  # the plan already passed it
        state.save()
        hand_off("alarm", A, store=self.store, now=epoch(2026, 9, 11, 7, 0, 0))
        self.now = epoch(2026, 9, 11, 7, 0, 3)
        self.service().start()
        self.assertEqual(self.app.sent, [f"alarm-{A}"])
        self.assertEqual(SchedulerState(clock_alarms.scheduler_path(self.store)).handoff, [])

    def test_a_refused_background_request_in_the_sandbox_is_reported(self):
        self.store.save_alarm(Alarm(A, "", 7, 0))
        service = self.service(portal=FakePortal(answer=(0, {"background": False, "autostart": False})))
        seen = []
        service.connect_background(seen.append)
        service.start()
        self.assertEqual(seen, [BACKGROUND_DENIED])
        self.assertEqual(self.app.holds, 1, "still rings while it runs")

    def test_without_a_portal_the_sandbox_says_so_and_the_host_writes_its_own_entry(self):
        self.store.save_alarm(Alarm(A, "", 7, 0))
        failure = GLib.Error.new_literal(GLib.quark_from_string("test"), "no portal", 1)
        service = self.service(portal=FakePortal(error=failure))
        service.start()
        self.assertEqual(service.background, BACKGROUND_UNAVAILABLE)

        entry = Path(os.environ["XDG_CONFIG_HOME"]) / "autostart/org.projectluma.Clock.desktop"
        entry.unlink(missing_ok=True)
        (clock_alarms.scheduler_path(self.store)).unlink()
        host = self.service(sandboxed=False, portal=FakePortal(error=failure))
        host.start()
        self.assertEqual(host.background, BACKGROUND_ALLOWED)
        self.assertIn("--gapplication-service", entry.read_text())
        self.store.delete_alarm(A)
        host.reschedule()
        self.assertFalse(entry.exists())


class SessionInhibitorBusTests(unittest.TestCase):
    """SessionInhibitor against stand-ins for logind and GNOME's session manager on a private bus."""

    def setUp(self):
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            self.skipTest("needs a session bus (dbus-run-session)")
        from gi.repository import Gio
        self.Gio = Gio
        self.record = Path(tempfile.mkdtemp()) / "holds.jsonl"
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def start_fake(self, *arguments):
        import subprocess
        fake = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent / "fake_session_holds.py"),
                                 str(self.record), *arguments], stdout=subprocess.PIPE, text=True)
        self.addCleanup(fake.wait, 10)
        self.addCleanup(fake.terminate)
        self.assertEqual(fake.stdout.readline().strip(), "ready")
        return fake

    def events(self):
        import json
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text().splitlines() if line.strip()]

    def wait_for(self, predicate, seconds=10):
        context = GLib.MainContext.default()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        self.fail(f"timed out; events: {self.events()}")

    def test_both_holds_are_taken_once_and_given_back(self):
        from prairie_apps.clock_alarms import SessionInhibitor
        self.start_fake()
        inhibitor = SessionInhibitor(system=self.connection, session=self.connection)
        inhibitor.hold("An alarm is ringing")
        inhibitor.hold("An alarm is about to go off")
        taken = self.wait_for(lambda: len([e for e in self.events() if e["event"].endswith("inhibit")]) == 2
                              and self.events())
        logind = next(e for e in taken if e["event"] == "logind-inhibit")
        self.assertEqual((logind["what"], logind["who"], logind["why"], logind["mode"]),
                         ("sleep:idle", "Clock", "An alarm is ringing", "block"))
        session = next(e for e in taken if e["event"] == "gsm-inhibit")
        self.assertEqual((session["app_id"], session["flags"]), ("org.projectluma.Clock", 12))
        self.assertTrue(inhibitor.held)
        inhibitor.release()
        released = self.wait_for(lambda: {e["event"] for e in self.events()} >= {"logind-released", "gsm-uninhibit"}
                                 and self.events())
        self.assertEqual(next(e for e in released if e["event"] == "gsm-uninhibit")["cookie"], session["cookie"])
        self.assertFalse(inhibitor.held)

    def test_a_logind_that_is_not_there_yet_is_held_the_moment_it_appears(self):
        from prairie_apps.clock_alarms import SessionInhibitor
        self.start_fake("--logind-after", "1.5")
        inhibitor = SessionInhibitor(system=self.connection, session=self.connection)
        inhibitor.hold("An alarm is ringing")
        self.assertFalse(any(e["event"] == "logind-inhibit" for e in self.events()))
        self.wait_for(lambda: any(e["event"] == "logind-inhibit" for e in self.events()))
        inhibitor.release()
        self.wait_for(lambda: any(e["event"] == "logind-released" for e in self.events()))


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        ROOT.cleanup()
