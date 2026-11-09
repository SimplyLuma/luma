# SPDX-License-Identifier: GPL-3.0-only
"""A private display start waits while Mutter progresses and fails clearly."""
from unittest.mock import patch
import unittest
import engine_display
from engine_display import wait_started


class Process:
    def __init__(self, pid=1, code=None):
        self.pid, self.code = pid, code

    def poll(self):
        return self.code


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class StartupWait(unittest.TestCase):
    def wait(self, ready, activity, processes=None, **limits):
        clock = Clock()
        processes = processes or (('Mutter', Process()),)
        with patch.object(engine_display, '_activity', lambda pid: activity(clock.now)):
            wait_started(ready(clock), processes, 'The private engine display',
                         clock=clock, sleep=clock.sleep, **limits)
        return clock.now

    def test_slow_progressing_start_outlasts_the_old_deadline(self):
        elapsed = self.wait(lambda clock: lambda: clock.now >= 25, lambda now: int(now))
        self.assertGreaterEqual(elapsed, 25)

    def test_no_progress_fails_after_the_stall_limit(self):
        with self.assertRaisesRegex(TimeoutError, 'Mutter made no progress for 20 s'):
            self.wait(lambda clock: lambda: False, lambda now: 7)

    def test_progress_is_bounded_by_the_startup_limit(self):
        with self.assertRaisesRegex(TimeoutError, 'did not become ready within 120 s'):
            self.wait(lambda clock: lambda: False, lambda now: now)

    def test_dead_compositor_fails_at_once_with_its_status(self):
        with self.assertRaisesRegex(RuntimeError, 'Mutter exited with status 1'):
            self.wait(lambda clock: lambda: False, lambda now: now,
                      processes=(('Mutter', Process(code=1)),))

    def test_activity_reads_this_process(self):
        import os
        self.assertEqual(len(engine_display._activity(os.getpid())), 4)
        self.assertIsNone(engine_display._activity(2 ** 30))


if __name__ == '__main__':
    unittest.main()
