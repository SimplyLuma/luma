"""A stalled package command can be stopped without touching a real app."""
from __future__ import annotations

import sys
import threading
import time
import unittest

from luma_installer import manager
from luma_installer.progress import Cancelled, Transaction, current


class RemovalCancellationTests(unittest.TestCase):
    def test_cancel_stops_a_running_package_command(self):
        transaction = Transaction()
        transaction.phase('Remove application', 0, cancellable=True)
        result = []
        started = threading.Event()

        def run():
            token = current.set(transaction)
            try:
                started.set()
                manager._run([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=35)
            except Exception as error:
                result.append(error)
            finally:
                current.reset(token)

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(started.wait(1))
        deadline = time.monotonic() + 3
        while transaction.cancel_callback is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(transaction.cancel_callback)
        self.assertTrue(transaction.cancel())
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], Cancelled)


if __name__ == '__main__':
    unittest.main()
