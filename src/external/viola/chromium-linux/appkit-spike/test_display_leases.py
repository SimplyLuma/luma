# SPDX-License-Identifier: GPL-3.0-only
import threading
import unittest
from display_leases import DisplayLeases


class Display:
    def __init__(self): self.closed = 0
    def close(self): self.closed += 1


class Leases(unittest.TestCase):
    def test_owner_release_cannot_release_another_browser(self):
        display = Display(); leases = DisplayLeases(lambda: display)
        handle = leases.reserve(':1.2'); leases.create(':1.2', handle)
        with self.assertRaises(PermissionError): leases.release(':1.3', handle)
        self.assertEqual(display.closed, 0)
        leases.release(':1.2', handle)
        self.assertEqual(display.closed, 1); self.assertTrue(leases.empty())

    def test_pending_capacity_and_factory_failure(self):
        leases = DisplayLeases(lambda: (_ for _ in ()).throw(RuntimeError('startup')))
        handles = [leases.reserve(':1.2') for _ in range(4)]
        with self.assertRaises(RuntimeError): leases.reserve(':1.3')
        with self.assertRaises(RuntimeError): leases.create(':1.2', handles[0])
        replacement = leases.reserve(':1.3')
        self.assertNotIn(replacement, handles)
        leases.disconnect(':1.2'); leases.disconnect(':1.3')
        for owner, handle in [(':1.2', value) for value in handles[1:]] + [(':1.3', replacement)]:
            with self.assertRaises(RuntimeError): leases.create(owner, handle)
        self.assertTrue(leases.empty())

    def test_loss_during_real_thread_startup_closes_without_reply(self):
        entered = threading.Event(); finish = threading.Event(); display = Display(); errors = []
        def factory(): entered.set(); self.assertTrue(finish.wait(2)); return display
        leases = DisplayLeases(factory); handle = leases.reserve(':1.2')
        def create():
            try: leases.create(':1.2', handle)
            except RuntimeError as error: errors.append(str(error))
        worker = threading.Thread(target=create); worker.start()
        self.assertTrue(entered.wait(2)); leases.disconnect(':1.2'); finish.set(); worker.join(2)
        self.assertFalse(worker.is_alive()); self.assertEqual(len(errors), 1)
        self.assertEqual(display.closed, 1); self.assertTrue(leases.empty())

    def test_loss_removes_only_original_sender(self):
        created = []
        def factory(): display = Display(); created.append(display); return display
        leases = DisplayLeases(factory)
        for owner in [':1.2', ':1.2', ':1.3']:
            handle = leases.reserve(owner); leases.create(owner, handle)
        leases.disconnect(':1.2'); self.assertEqual([d.closed for d in created], [1, 1, 0])
        leases.disconnect(':1.2'); self.assertEqual([d.closed for d in created], [1, 1, 0])
        leases.disconnect(':1.3'); self.assertTrue(leases.empty())

    def test_closing_and_cancelled_startup_keep_capacity(self):
        display = Display(); leases = DisplayLeases(lambda: display, capacity=1)
        handle = leases.reserve(':1.2'); leases.create(':1.2', handle)
        detached = leases.detach(':1.2')
        with self.assertRaises(RuntimeError): leases.reserve(':1.3')
        leases.close_detached(detached[0]); self.assertTrue(leases.empty())
        handle = leases.reserve(':1.3'); leases.detach(':1.3')
        with self.assertRaises(RuntimeError): leases.reserve(':1.4')
        with self.assertRaises(RuntimeError): leases.create(':1.3', handle)
        self.assertTrue(leases.empty())

    def test_release_during_startup_never_leaks_display(self):
        display = Display(); leases = None; handle = None
        def factory(): leases.release(':1.2', handle); return display
        leases = DisplayLeases(factory); handle = leases.reserve(':1.2')
        with self.assertRaises(RuntimeError): leases.create(':1.2', handle)
        self.assertEqual(display.closed, 1); self.assertTrue(leases.empty())


if __name__ == '__main__': unittest.main()
