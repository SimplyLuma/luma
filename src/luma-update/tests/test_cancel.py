# SPDX-License-Identifier: Apache-2.0
"""A download can be cancelled, and an automatic one stops when the policy no longer allows it.

Before, a download ran to the end once started: a connection that became
metered part-way (a phone hotspot) kept pulling hundreds of megabytes, and
neither Depot nor the command line could stop it.
"""

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

import fakes
from fakes import commit, graph_doc, release
from luma_update import cli
from luma_update.engine import NothingToDo


class Cancelling(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))
        self.engine = self.rig.engine()
        self.engine.policy_recheck_seconds = 0

    def tearDown(self):
        self.rig.close()

    def state(self):
        return json.loads(self.rig.paths.state_file.read_text())

    def assert_cancelled_cleanly(self):
        status = self.engine.status()
        self.assertEqual((status.state, status.staged_version, status.available_version, status.last_error,
                          status.progress), ("available", "", "1.0.1", "", 0.0))
        self.assertIsNone(self.state()["pending"])
        self.assertEqual([r for r in self.state()["reports"] if r.get("result") == "failed"], [])
        self.assertFalse(self.engine.download_running())

    def test_person_cancels_a_running_download(self):
        self.engine.check()
        seen = []

        def cancel_now():
            seen.append((self.engine.download_running(), self.engine.status().state))
            self.engine.cancel_download("asked over D-Bus")
        self.rig.backend.during_download = cancel_now
        self.assertFalse(self.engine.download(user_initiated=True))
        self.assertEqual(seen, [(True, "downloading")])
        self.assert_cancelled_cleanly()
        # Download again later: it completes.
        self.rig.backend.during_download = None
        self.assertTrue(self.engine.download(user_initiated=True))
        self.assertEqual(self.engine.status().state, "staged")

    def test_nothing_to_cancel(self):
        with self.assertRaises(NothingToDo):
            self.engine.cancel_download()
        self.assertFalse(self.engine.connection_changed())

    def test_automatic_download_stops_when_the_connection_becomes_metered(self):
        def hotspot():
            self.rig.probes.net.metered = True
        self.rig.backend.during_download = hotspot
        self.engine.automatic()
        self.assert_cancelled_cleanly()
        self.assertTrue(self.engine.status().metered)

    def test_automatic_download_stops_when_power_runs_low(self):
        def unplugged():
            self.rig.probes.pwr.on_battery = True
            self.rig.probes.pwr.percentage = 12.0
        self.rig.backend.during_download = unplugged
        self.engine.automatic()
        self.assert_cancelled_cleanly()

    def test_network_signal_cancels_an_automatic_download(self):
        self.engine.policy_recheck_seconds = 3600  # only the signal path

        def hotspot():
            self.rig.probes.net.metered = True
            self.assertTrue(self.engine.connection_changed())
        self.rig.backend.during_download = hotspot
        self.engine.automatic()
        self.assert_cancelled_cleanly()

    def test_a_download_the_person_asked_for_continues_on_a_metered_connection(self):
        self.engine.check()

        def hotspot():
            self.rig.probes.net.metered = True
            self.assertFalse(self.engine.connection_changed())
        self.rig.backend.during_download = hotspot
        self.assertTrue(self.engine.download(user_initiated=True))
        self.assertEqual(self.engine.status().staged_commit, commit(2))

    def test_cancel_arriving_after_staging_keeps_the_update(self):
        self.engine.check()
        original = self.rig.backend.update_deployment

        def finish_then_cancel(**kwargs):
            original(**kwargs)
            kwargs["cancellable"].cancel()
        self.rig.backend.update_deployment = finish_then_cancel
        self.assertTrue(self.engine.download(user_initiated=True))
        self.assertEqual(self.engine.status().state, "staged")


class CommandLine(unittest.TestCase):
    def test_cancel_calls_the_service(self):
        client = mock.Mock()
        with mock.patch.object(cli, "Client", return_value=client), mock.patch.object(cli, "_gio",
                                                                                         return_value=(mock.Mock(), mock.Mock())):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(cli.main(["cancel"]), cli.EXIT_OK)
        client.call.assert_called_once_with("Cancel")
        self.assertIn("download was stopped", out.getvalue())


try:
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    from luma_update import rpmostree
except (ImportError, ValueError):  # no GObject introspection here
    rpmostree = None


@unittest.skipIf(rpmostree is None, "PyGObject is not available")
class RpmOstreeCancellable(unittest.TestCase):
    def test_cancelled_before_start_never_asks_rpm_ostree(self):
        connection = mock.Mock()
        client = rpmostree.RpmOstree(connection)
        cancellable = client.new_cancellable()
        self.assertIsInstance(cancellable, Gio.Cancellable)
        cancellable.cancel()
        with self.assertRaises(rpmostree.TransactionError):
            client.update_deployment(revision=commit(2), refspec=None, allow_downgrade=False, cancellable=cancellable)
        connection.call_sync.assert_not_called()


if __name__ == "__main__":
    unittest.main()
