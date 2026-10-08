# SPDX-License-Identifier: Apache-2.0
"""An error recorded on another system is not this one's.

Found on a Dell that restarted from nightly 20260915.9 (an agent that refused
nightly without a credential) into 20260920.4: the new agent came up still
saying "Early updates couldn't be set up." and skipped its first automatic
check because the old agent's failed attempt was less than an hour old."""

import json
import unittest

import fakes
from fakes import NOW, commit, graph_doc, release

OLD_ERROR = {"last_error": "Early updates couldn't be set up.", "last_error_class": "preview",
             "last_check_reason": "preview", "last_attempt": int(NOW) - 600}


class ErrorFromAnotherSystem(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.addCleanup(self.rig.close)
        self.rig.backend = fakes.FakeRpmOstree(booted_version="1.1.0-nightly.2", booted_commit=commit(8),
                                               origin="luma:luma/1/x86_64/nightly")
        self.rig.publish(graph_doc([release("1.1.0-nightly.2", 8)], channel="nightly"), channel="nightly")

    def start(self, state):
        self.rig.paths.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.rig.paths.state_file.write_text(json.dumps({"version": 1, **state}))
        engine = self.rig.engine()
        engine.reconcile_boot(None)  # what luma-updated does first on start
        engine.refresh()
        return engine

    def graph_fetches(self):
        return [url for url in self.rig.http.gets if url.endswith("nightly.json")]

    def test_an_older_agents_error_is_forgotten_and_the_first_check_runs(self):
        engine = self.start(OLD_ERROR)  # no attempt_booted: written by an agent that never recorded one
        status = engine.status()
        self.assertEqual((status.last_error, status.last_error_class, status.state == "error"), ("", "", False))
        engine.automatic()
        self.assertEqual(len(self.graph_fetches()), 1, "the first automatic check is not skipped")
        self.assertEqual(engine.status().last_error, "")

    def test_an_error_from_before_a_restart_into_another_deployment_is_forgotten(self):
        engine = self.start({**OLD_ERROR, "attempt_booted": commit(7)})
        self.assertEqual(engine.status().last_error, "")
        engine.automatic()
        self.assertEqual(len(self.graph_fetches()), 1)

    def test_this_systems_own_recent_error_stays_and_spaces_checks(self):
        engine = self.start({**OLD_ERROR, "attempt_booted": commit(8)})
        self.assertEqual(engine.status().last_error_class, "preview")
        engine.automatic()
        self.assertEqual(self.graph_fetches(), [], "a failure on this same system still spaces checks")


if __name__ == "__main__":
    unittest.main()
