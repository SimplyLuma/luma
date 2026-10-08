# SPDX-License-Identifier: Apache-2.0
import unittest
from luma_tide.deferred import DeferredRemovals


class RemovalTests(unittest.TestCase):
    def setUp(self):
        self.callbacks, self.cancelled, self.committed = [], [], []
        def schedule(_delay, callback):
            self.callbacks.append(callback)
            return len(self.callbacks)
        self.removals = DeferredRemovals(schedule, self.cancelled.append, self.committed.append)

    def test_second_removal_keeps_the_first_undo_window_independent(self):
        self.removals.add('first')
        self.removals.add('second')
        self.removals.undo('second')
        self.callbacks[0]()
        self.callbacks[1]()
        self.assertEqual(self.committed, ['first'])
        self.assertEqual(self.cancelled, [2])
        self.assertFalse(self.removals.pending)

    def test_stale_timer_cannot_commit_a_later_removal(self):
        self.removals.add('same')
        self.removals.undo('same')
        self.removals.add('same')
        self.callbacks[0]()
        self.assertFalse(self.committed)
        self.assertIn('same', self.removals.pending)
        self.callbacks[1]()
        self.callbacks[1]()
        self.assertEqual(self.committed, ['same'])

    def test_close_commits_each_confirmed_removal_once_and_cancels_timers(self):
        self.removals.add('first')
        self.removals.add('first')
        self.removals.add('second')
        self.removals.flush()
        self.removals.flush()
        for callback in self.callbacks:
            callback()
        self.assertEqual(self.committed, ['first', 'second'])
        self.assertEqual(self.cancelled, [1, 2])
