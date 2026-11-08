# SPDX-License-Identifier: Apache-2.0
"""A staged Luma update starts the download of the app updates that come with it."""
import unittest

import fakes
from fakes import commit, graph_doc, release
from luma_update import apps
from luma_update.engine import Engine
from luma_update.errors import TransactionError


class Staging(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))
        self.staged = []

    def tearDown(self):
        self.rig.close()

    def engine(self, on_staged):
        rig = self.rig
        return Engine(rig.paths, rig.settings, rig.backend, rig.probes, rig.http, clock=lambda: rig.now,
                      arch="x86_64", boot_id=lambda: "boot-1", greenboot_installed=True, on_staged=on_staged)

    def test_staging_starts_the_app_downloads_once(self):
        engine = self.engine(self.staged.append)
        engine.automatic()
        self.assertEqual(engine.status().state, "staged")
        self.assertEqual([(str(r.version), r.commit) for r in self.staged], [("1.0.1", commit(2))])

    def test_a_failing_app_download_never_costs_the_system_update(self):
        def broken(_release):
            raise RuntimeError("no systemd")
        engine = self.engine(broken)
        self.assertTrue(engine.download(user_initiated=True))
        self.assertEqual(engine.status().state, "staged")

    def test_nothing_staged_starts_nothing(self):
        self.rig.backend.fail_with = TransactionError("disk full")
        engine = self.engine(self.staged.append)
        self.assertFalse(engine.download(user_initiated=True))
        self.assertEqual(self.staged, [])


class Prefetch(unittest.TestCase):
    def test_starts_the_unit_without_waiting(self):
        calls = []

        class Connection:
            def call_sync(self, *args):
                calls.append(args)

        self.assertTrue(apps.start_prefetch(Connection()))
        name, path, interface, method, parameters = calls[0][:5]
        self.assertEqual((name, interface, method), ("org.freedesktop.systemd1", "org.freedesktop.systemd1.Manager",
                                                     "StartUnit"))
        self.assertEqual(parameters.unpack(), (apps.PREFETCH_UNIT, "replace"))

    def test_a_system_without_the_unit_carries_on(self):
        from gi.repository import GLib

        class Connection:
            def call_sync(self, *args):
                raise GLib.Error("Unit luma-system-app-updates-prefetch.service not found.")

        self.assertFalse(apps.start_prefetch(Connection()))


if __name__ == "__main__":
    unittest.main()
