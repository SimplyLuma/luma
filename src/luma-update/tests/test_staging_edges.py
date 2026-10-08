# SPDX-License-Identifier: Apache-2.0
"""Staging edge cases from the independent review (finding 11 a-c)."""

import json
import unittest

import fakes
from fakes import commit, graph_doc, release
from luma_update.engine import classify_error
from luma_update.errors import TransactionError


def older_refusal(target, booted):
    return TransactionError(
        f"While pulling luma/1/x86_64/stable: Upgrade target revision '{target}' with timestamp "
        f"'Tue Sep 15 09:00:00 2026' is chronologically older than current revision '{booted}' with timestamp "
        f"'Tue Sep 15 10:00:00 2026'; use --allow-downgrade to permit")


class PromotedTimestamps(fakes.FakeRpmOstree):
    """rpm-ostree whose timestamp check refuses commits promoted before the booted one."""

    def __init__(self, older=()):
        super().__init__()
        self.older = set(older)

    def update_deployment(self, *, revision, refspec, allow_downgrade, **kwargs):
        if revision in self.older and not allow_downgrade:
            self.calls.append(("update", revision, refspec, allow_downgrade))
            raise older_refusal(revision, commit(1))
        super().update_deployment(revision=revision, refspec=refspec, allow_downgrade=allow_downgrade, **kwargs)


class Edges(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()

    def tearDown(self):
        self.rig.close()

    def state(self):
        return json.loads(self.rig.paths.state_file.read_text())

    def events(self):
        return [p for (_m, url, p, _h) in self.rig.http.posts if url.endswith("/events") and "result" in (p or {})]

    def test_a_version_newer_commit_with_an_older_timestamp_is_staged_pinned(self):
        self.rig.backend = PromotedTimestamps(older={commit(2)})
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))
        engine = self.rig.engine()
        engine.automatic()
        self.assertEqual(self.rig.backend.calls, [("update", commit(2), None, False), ("update", commit(2), None, True)])
        status = engine.status()
        self.assertEqual((status.state, status.staged_commit, status.last_error), ("staged", commit(2), ""))
        self.assertEqual([r["result"] for r in self.events()], ["staged"])

    def test_the_older_timestamp_retry_never_applies_to_another_commit_or_an_older_version(self):
        # rpm-ostree names a different commit than the one asked for: not retried.
        backend = fakes.FakeRpmOstree()
        backend.fail_with = older_refusal(commit(9), commit(1))
        self.rig.backend = backend
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))
        engine = self.rig.engine()
        engine.automatic()
        self.assertEqual(backend.calls, [("update", commit(2), None, False)])
        self.assertEqual(engine.status().last_error_class, "transaction")

    def test_the_older_timestamp_retry_is_not_used_for_a_booted_version_that_is_newer(self):
        # Booted 1.0.2 (e.g. from a channel switch), graph target 1.0.1 by "Switch now" goes through
        # the rollback path with allow-downgrade already; an update of an older version never retries.
        self.rig.backend = PromotedTimestamps(older={commit(2)})
        self.rig.backend.list[0].version = "1.0.2"
        self.rig.publish(graph_doc([release("1.0.2", 1), release("1.0.3", 2)]))
        engine = self.rig.engine()
        engine.check()
        self.rig.backend.list[0].version = "1.0.4"  # booted became newer than the chosen release since
        self.assertFalse(engine.download(user_initiated=True))
        self.assertEqual(self.rig.backend.calls, [("update", commit(2), None, False)])

    def test_b_min_free_space_is_a_disk_space_error(self):
        for text in ("error: Writing content object: min-free-space-percent '3%' would be exceeded, "
                     "at least 13.2 MB requested",
                     "min-free-space-size 500MB would be exceeded",
                     "min-free-space-percent '3%' would be exceeded"):
            self.assertEqual(classify_error(TransactionError(text)), "disk-space", text)
        self.assertEqual(classify_error(TransactionError("the quota would be exceeded")), "transaction")
        self.rig.backend.fail_with = TransactionError("min-free-space-size 500MB would be exceeded")
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))
        engine = self.rig.engine()
        engine.automatic()
        self.assertEqual(engine.status().last_error_class, "disk-space")
        self.assertEqual(self.events()[-1]["error_class"], "disk-space")

    def test_c_a_wrong_staged_commit_is_removed_before_raising(self):
        class WrongCommit(fakes.FakeRpmOstree):
            def update_deployment(self, *, revision, **kwargs):
                super().update_deployment(revision=commit(66), **kwargs)
        self.rig.backend = WrongCommit()
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))
        engine = self.rig.engine()
        engine.automatic()
        self.assertIn(("cleanup",), self.rig.backend.calls)
        self.assertFalse(any(d.staged for d in self.rig.backend.list))
        status = engine.status()
        self.assertEqual((status.state, status.last_error_class, status.staged_commit),
                         ("available", "transaction", ""))
        self.assertIsNone(self.state()["pending"])


if __name__ == "__main__":
    unittest.main()
