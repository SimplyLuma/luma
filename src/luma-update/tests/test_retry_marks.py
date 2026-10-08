# SPDX-License-Identifier: Apache-2.0
"""Do-not-retry marks: only a failed health check is permanent, and a marked
barrier never leaves a computer "up to date" behind it.

Regression tests for the independent review's engine scenarios A, B and D:
two unclean shutdowns, one boot of the previous GRUB entry, or a person's own
rollback used to mark a barrier release for good, after which the device
reported "up to date" forever and never received anything newer, including
security releases.
"""

import json
import unittest

import fakes
from fakes import NOW, commit, graph_doc, release
from luma_update import state as statemod
from luma_update.engine import Engine
from luma_update.graph import select_target, parse_graph
from luma_update import versions

DAY = 86400


class Scenario(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.boot = "boot-1"
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2, barrier=True),
                                    release("1.0.2", 3, importance="security")]))

    def tearDown(self):
        self.rig.close()

    def engine(self):
        return Engine(self.rig.paths, self.rig.settings, self.rig.backend, self.rig.probes, self.rig.http,
                      clock=lambda: self.rig.now, arch="x86_64", boot_id=lambda: self.boot,
                      greenboot_installed=True)

    def marks(self):
        return json.loads(self.rig.paths.state_file.read_text())["do_not_retry"]

    def restart(self, boot_commit=None, *, lose_staged=False):
        if lose_staged:
            self.rig.backend.list = [d for d in self.rig.backend.list if not d.staged]
        else:
            self.rig.backend.reboot_into(boot_commit)
        self.boot = f"boot-{int(self.rig.now)}"

    def later(self, seconds):
        self.rig.now += seconds
        # A graph signed after the clock moved, as the pipeline re-signs it daily.
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2, barrier=True),
                                    release("1.0.2", 3, importance="security")], generated=self.rig.now - 60))

    def assert_blocked_on_barrier(self, engine):
        decision = engine.check()
        self.assertEqual((decision.action, decision.reason), ("none", "barrier-blocked"))
        status = engine.status()
        self.assertEqual((status.state, status.waiting_version, status.available_version),
                         ("barrier-blocked", "1.0.1", ""))

    def test_a_unclean_shutdowns_block_then_retry_the_barrier(self):
        for _ in range(2):
            engine = self.engine()
            engine.automatic()
            self.assertEqual(engine.status().staged_version, "1.0.1")
            self.restart(lose_staged=True)  # the battery died before the update finalized
            self.assertEqual(self.engine().reconcile_boot(None), "failed")
            self.later(7 * 3600)
        mark = self.marks()[-1]
        self.assertEqual((mark["reason"], mark["attempts"]), ("failed-to-finalize", 1))
        self.assertNotEqual(mark["expires_at"], 0)
        self.assert_blocked_on_barrier(self.engine())
        # One day after the mark the barrier is offered again; the device is never "up to date".
        self.later(DAY)
        engine = self.engine()
        engine.automatic()
        self.assertEqual((engine.status().state, engine.status().staged_version), ("staged", "1.0.1"))
        # It works this time: the mark is gone and the security release follows.
        self.restart(commit(2))
        self.assertEqual(self.engine().reconcile_boot("green"), "booted")
        self.assertEqual(self.marks(), [])
        self.later(7 * 3600)
        engine = self.engine()
        self.assertEqual(str(engine.check().release.version), "1.0.2")

    def test_b_previous_grub_entry_is_not_a_failed_update(self):
        engine = self.engine()
        engine.automatic()
        # The update finalized at shutdown and is the default, but the person picked
        # the old entry in the boot menu for this boot.
        self.rig.backend.list[0].staged = False
        self.boot = "boot-2"
        engine = self.engine()
        self.assertEqual(engine.reconcile_boot(None), "pending")
        self.assertEqual(self.marks(), [])
        status = engine.status()
        self.assertEqual((status.state, status.staged_version, status.rolled_back_version),
                         ("staged", "1.0.1", ""))
        # It is not downloaded again, and the next restart starts it.
        self.later(7 * 3600)
        calls = len(self.rig.backend.calls)
        engine = self.engine()
        engine.automatic()
        self.assertEqual(len(self.rig.backend.calls), calls)
        self.assertEqual(engine.status().state, "staged")
        self.restart(commit(2))
        self.assertEqual(self.engine().reconcile_boot("green"), "booted")

    def test_previous_version_default_without_a_failed_check_expires(self):
        engine = self.engine()
        engine.automatic()
        # The previous version is the default again and no health check failed on the
        # new one: it never reached its checks, or someone rolled back by other means.
        self.restart(commit(1))
        self.boot = "boot-2"
        engine = self.engine()
        self.assertEqual(engine.reconcile_boot(None), "booted_previous")
        status = engine.status()
        self.assertEqual((status.rolled_back_version, status.rolled_back_at), ("", 0))
        mark = self.marks()[-1]
        self.assertEqual(mark["reason"], "booted-previous-deployment")
        self.assertEqual(mark["expires_at"], int(self.rig.now) + statemod.RETRY_MARK_SECONDS)
        reports = json.loads(self.rig.paths.state_file.read_text())["reports"]
        self.assertEqual((reports[-1]["result"], reports[-1]["error_class"]), ("rolled_back", "unknown"))
        self.later(7 * 3600)
        self.assert_blocked_on_barrier(self.engine())
        self.later(DAY)
        self.assertEqual(str(self.engine().check().release.version), "1.0.1")

    def test_d_person_rollback_of_a_barrier_waits_a_week_then_retries(self):
        engine = self.engine()
        engine.automatic()
        self.restart(commit(2))
        self.rig.backend.list[0].version = "1.0.1"
        engine = self.engine()
        self.assertEqual(engine.reconcile_boot("green"), "booted")
        engine.rollback()
        self.restart(commit(1))
        self.later(7 * 3600)
        self.assert_blocked_on_barrier(self.engine())
        self.later(3 * DAY)
        self.assert_blocked_on_barrier(self.engine())
        self.later(4 * DAY)
        self.assertEqual(str(self.engine().check().release.version), "1.0.1")

    def test_health_check_failure_on_a_barrier_backs_off_and_retries(self):
        engine = self.engine()
        engine.automatic()
        self.restart(commit(2))
        self.assertEqual(self.engine().reconcile_boot("red"), "pending")
        self.restart(commit(1))
        self.assertEqual(self.engine().reconcile_boot(None), "rolled_back")
        self.assertEqual(self.marks()[-1]["expires_at"], 0)
        self.later(7 * 3600)
        self.assert_blocked_on_barrier(self.engine())
        self.later(DAY)
        engine = self.engine()
        engine.automatic()
        self.assertEqual(engine.status().staged_version, "1.0.1")
        # Fails again: the second backoff is three days, and the mark stays permanent.
        self.restart(commit(2))
        self.engine().reconcile_boot("red")
        self.restart(commit(1))
        self.assertEqual(self.engine().reconcile_boot(None), "rolled_back")
        mark = self.marks()[-1]
        self.assertEqual((mark["attempts"], mark["expires_at"]), (2, 0))
        self.later(2 * DAY)
        self.assert_blocked_on_barrier(self.engine())
        self.later(DAY + 3600)
        self.assertEqual(str(self.engine().check().release.version), "1.0.1")


class Marks(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()

    def tearDown(self):
        self.rig.close()

    def test_only_a_failed_health_check_is_permanent(self):
        store = statemod.StateStore(self.rig.paths.state_file)
        with store.locked():
            store.add_do_not_retry(commit(2), "1.0.1", "failed-to-finalize", NOW)
            store.add_do_not_retry(commit(3), "1.0.2", "rolled-back-by-person", NOW)
            store.add_do_not_retry(commit(4), "1.0.3", "booted-previous-deployment", NOW)
            store.add_do_not_retry(commit(5), "1.0.4", "rolled-back-after-failed-boot", NOW)
        self.assertEqual(store.do_not_retry(NOW), {commit(2), commit(3), commit(4), commit(5)})
        self.assertEqual(store.do_not_retry(NOW + 7 * DAY), {commit(5)})
        # A later expiring mark never shortens a permanent one.
        with store.locked():
            store.add_do_not_retry(commit(5), "1.0.4", "rolled-back-by-person", NOW + DAY)
        self.assertEqual(store.do_not_retry(NOW + 30 * DAY), {commit(5)})
        self.assertEqual(store.retry_mark(commit(5))["attempts"], 2)

    def test_marks_written_by_the_first_builds_keep_their_meaning(self):
        self.rig.paths.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.rig.paths.state_file.write_text(json.dumps({"do_not_retry": [
            {"commit": commit(2), "version": "1.0.1", "reason": "rolled-back-after-failed-boot", "at": int(NOW)},
            {"commit": commit(3), "version": "1.0.2", "reason": "failed-to-finalize", "at": int(NOW)},
        ]}))
        store = statemod.StateStore(self.rig.paths.state_file)
        self.assertEqual(store.do_not_retry(NOW + DAY), {commit(2), commit(3)})
        self.assertEqual(store.do_not_retry(NOW + 8 * DAY), {commit(2)})

    def test_a_mark_from_a_clock_that_ran_ahead_has_expired(self):
        store = statemod.StateStore(self.rig.paths.state_file)
        with store.locked():
            store.add_do_not_retry(commit(2), "1.0.1", "failed-to-finalize", NOW + 365 * DAY)
        self.assertEqual(store.do_not_retry(NOW), frozenset())

    def test_graph_selection_stops_at_a_marked_barrier(self):
        doc = graph_doc([release("1.0.0", 1), release("1.0.1", 2), release("1.0.2", 3, barrier=True),
                         release("1.0.3", 4)])
        graph = parse_graph(json.dumps(doc).encode())

        def select(booted, n, dnr):
            return select_target(graph, booted_version=versions.parse(booted), booted_commit=commit(n),
                                 wariness=0.5, now=NOW, do_not_retry=frozenset(dnr))
        # Below the marked barrier there is still something to install.
        decision = select("1.0.0", 1, {commit(3)})
        self.assertEqual((decision.action, str(decision.release.version), str(decision.waiting.version)),
                         ("update", "1.0.1", "1.0.2"))
        decision = select("1.0.1", 2, {commit(3)})
        self.assertEqual((decision.action, decision.reason, str(decision.waiting.version)),
                         ("none", "barrier-blocked", "1.0.2"))
        # A marked release that is not a barrier is simply skipped.
        decision = select("1.0.2", 3, {commit(4)})
        self.assertEqual((decision.action, decision.reason), ("none", "up-to-date"))


if __name__ == "__main__":
    unittest.main()
