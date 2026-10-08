# SPDX-License-Identifier: Apache-2.0
import copy
import json
import unittest

from fakes import NOW, commit, graph_doc, iso, release
from luma_update import graph as g
from luma_update import versions


def parse(doc):
    return g.parse_graph(json.dumps(doc).encode())


def select(doc, booted="1.0.0", booted_n=1, wariness=0.5, now=NOW, dnr=frozenset()):
    return g.select_target(parse(doc), booted_version=versions.parse(booted), booted_commit=commit(booted_n),
                           wariness=wariness, now=now, do_not_retry=dnr)


class Versions(unittest.TestCase):
    def test_semver_precedence(self):
        ordered = ["1.0.0-beta.2", "1.0.0-beta.10", "1.0.0-nightly.20261001.1", "1.0.0-nightly.20261001.2",
                   "1.0.0", "1.0.1-beta.1", "1.0.1", "1.1.0", "10.0.0"]
        parsed = [versions.parse(v) for v in ordered]
        self.assertEqual(sorted(parsed), parsed)
        self.assertEqual(versions.parse("1.0.0"), versions.parse("1.0.0"))

    def test_rejects_non_luma_versions(self):
        for bad in ("1.0", "01.0.0", "1.0.0+build", "v1.0.0", "", "1.0.0-", None, "44.20260422.0.1"):
            with self.assertRaises(versions.InvalidVersion):
                versions.parse(bad)


class Parsing(unittest.TestCase):
    def test_adr_example_parses(self):
        doc = graph_doc([release("1.0.1", 2, start_percentage=0.05, duration=4320)])
        graph = parse(doc)
        self.assertEqual(graph.releases[0].rollout.start_percentage, 0.05)
        self.assertEqual(graph.channel, "stable")

    def test_strict_fields(self):
        base = graph_doc([release("1.0.1", 2)])
        mutations = [
            lambda d: d.update(schema_version=2),
            lambda d: d.update(channel="edge"),
            lambda d: d["releases"][0].update(commit="ABC"),
            lambda d: d["releases"][0].update(version="1.0"),
            lambda d: d["releases"][0].update(importance="critical"),
            lambda d: d["releases"][0].update(notes_url="http://example.com"),
            lambda d: d["releases"][0].update(paused="no"),
            lambda d: d["releases"][0]["rollout"].update(start_percentage=1.5),
            lambda d: d["releases"][0]["rollout"].update(duration_minutes=-1),
            lambda d: d["releases"][0].update(released_at="2026-10-05 12:00"),
            lambda d: d["releases"].append(release("1.0.1", 3)),
            lambda d: d["releases"].append(release("1.0.2", 2)),
            lambda d: d["releases"][0].update(rollback_to={"version": "1.0.0", "commit": commit(1)}),  # not deadend
        ]
        for mutate in mutations:
            doc = copy.deepcopy(base)
            mutate(doc)
            with self.assertRaises(g.GraphError):
                parse(doc)
        with self.assertRaises(g.GraphError):
            g.parse_graph(b"[]")
        with self.assertRaises(g.GraphError):
            g.parse_graph(b"x" * (g.MAX_GRAPH_BYTES + 1))

    def test_rollback_to_must_be_listed_and_older(self):
        doc = graph_doc([release("1.0.0", 1), release("1.0.1", 2, deadend=True,
                                                       rollback_to={"version": "1.0.0", "commit": commit(9)})])
        with self.assertRaises(g.GraphError):
            parse(doc)
        doc["releases"][1]["rollback_to"] = {"version": "1.0.2", "commit": commit(1)}
        with self.assertRaises(g.GraphError):
            parse(doc)


class Freshness(unittest.TestCase):
    def test_age_monotonic_future_and_identity(self):
        graph = parse(graph_doc([], generated=NOW - 3600))
        g.check_freshness(graph, channel="stable", arch="x86_64", now=NOW, last_generated_at=NOW - 7200)
        g.check_freshness(graph, channel="stable", arch="x86_64", now=NOW, last_generated_at=graph.generated_at)
        with self.assertRaises(g.StaleGraph):
            g.check_freshness(graph, channel="stable", arch="x86_64", now=NOW, last_generated_at=NOW - 60)
        old = parse(graph_doc([], generated=NOW - 15 * 86400))
        with self.assertRaises(g.StaleGraph):
            g.check_freshness(old, channel="stable", arch="x86_64", now=NOW, last_generated_at=None)
        # A replayed graph can hide newer releases for at most three days.
        recent = parse(graph_doc([], generated=NOW - 3 * 86400 + 60))
        g.check_freshness(recent, channel="stable", arch="x86_64", now=NOW, last_generated_at=None)
        frozen = parse(graph_doc([], generated=NOW - 3 * 86400 - 60))
        with self.assertRaises(g.StaleGraph) as caught:
            g.check_freshness(frozen, channel="stable", arch="x86_64", now=NOW, last_generated_at=None)
        self.assertIn("more than 3 days old", str(caught.exception))
        future = parse(graph_doc([], generated=NOW + 2 * 86400))
        with self.assertRaises(g.StaleGraph):
            g.check_freshness(future, channel="stable", arch="x86_64", now=NOW, last_generated_at=None)
        with self.assertRaises(g.GraphError):
            g.check_freshness(graph, channel="beta", arch="x86_64", now=NOW, last_generated_at=None)
        with self.assertRaises(g.GraphError):
            g.check_freshness(graph, channel="stable", arch="aarch64", now=NOW, last_generated_at=None)


class Selection(unittest.TestCase):
    def test_newest_eligible_and_no_downgrade(self):
        doc = graph_doc([release("0.9.0", 9), release("1.0.0", 1), release("1.0.1", 2), release("1.0.2", 3)])
        decision = select(doc)
        self.assertEqual((decision.action, str(decision.release.version)), ("update", "1.0.2"))
        self.assertEqual(select(doc, booted="1.0.2", booted_n=3).action, "none")
        self.assertEqual(select(doc, booted="1.1.0", booted_n=50).action, "none")

    def test_rollout_reach_formula_and_wariness(self):
        start = NOW - 1800 * 60  # half of a 3600-minute rollout
        doc = graph_doc([release("1.0.1", 2, start=start, start_percentage=0.1, duration=3600)])
        graph = parse(doc)
        reach = g.rollout_reach(graph.releases[0], NOW)
        self.assertAlmostEqual(reach, 0.1 + 0.9 * 0.5)
        self.assertEqual(select(doc, wariness=0.54).action, "update")
        decision = select(doc, wariness=0.56)
        self.assertEqual(decision.action, "none")
        self.assertEqual(decision.reason, "rollout-not-reached")
        self.assertEqual(str(decision.waiting.version), "1.0.1")
        self.assertEqual(g.rollout_reach(graph.releases[0], start - 1), 0.0)
        self.assertEqual(g.rollout_reach(graph.releases[0], start + 10 * 3600 * 60), 1.0)

    def test_not_started_rollout_is_not_eligible_even_for_eager_devices(self):
        doc = graph_doc([release("1.0.1", 2, start=NOW + 60, start_percentage=0.5, duration=60)])
        self.assertEqual(select(doc, wariness=0.0).action, "none")

    def test_security_ignores_percentage_but_not_start(self):
        doc = graph_doc([release("1.0.1", 2, start=NOW - 60, start_percentage=0.0, duration=100000,
                                 importance="security")])
        self.assertEqual(select(doc, wariness=0.999).release.importance, "security")
        doc["releases"][0]["rollout"]["start_at"] = iso(NOW + 60)
        self.assertEqual(select(doc, wariness=0.0).action, "none")

    def test_paused_and_deadend_are_skipped(self):
        doc = graph_doc([release("1.0.0", 1), release("1.0.1", 2), release("1.0.2", 3, paused=True),
                         release("1.0.3", 4, deadend=True, deadend_reason="breaks Wi-Fi")])
        decision = select(doc)
        self.assertEqual(str(decision.release.version), "1.0.1")

    def test_do_not_retry(self):
        doc = graph_doc([release("1.0.0", 1), release("1.0.1", 2), release("1.0.2", 3)])
        self.assertEqual(str(select(doc, dnr={commit(3)}).release.version), "1.0.1")
        self.assertEqual(select(doc, dnr={commit(2), commit(3)}).action, "none")

    def test_barrier_stops_at_first_newer_barrier(self):
        doc = graph_doc([release("1.0.0", 1), release("1.1.0", 2, barrier=True), release("1.2.0", 3),
                         release("1.3.0", 4, barrier=True), release("1.4.0", 5)])
        decision = select(doc)
        self.assertEqual(str(decision.release.version), "1.1.0")
        self.assertEqual(decision.reason, "barrier")
        self.assertEqual(str(select(doc, booted="1.1.0", booted_n=2).release.version), "1.3.0")
        self.assertEqual(str(select(doc, booted="1.3.0", booted_n=4).release.version), "1.4.0")

    def test_unreached_barrier_still_blocks_newer(self):
        doc = graph_doc([release("1.0.0", 1), release("1.0.5", 6),
                         release("1.1.0", 2, barrier=True, start=NOW + 3600),
                         release("1.2.0", 3)])
        decision = select(doc)
        self.assertEqual(str(decision.release.version), "1.0.5")
        doc["releases"][2]["paused"] = True
        self.assertEqual(str(select(doc).release.version), "1.0.5")

    def test_booted_deadend_moves_to_next_regardless_of_wariness(self):
        doc = graph_doc([release("1.0.0", 1), release("1.0.1", 2, deadend=True, deadend_reason="bad"),
                         release("1.0.2", 3, start=NOW - 60, start_percentage=0.0, duration=100000)])
        decision = select(doc, booted="1.0.1", booted_n=2, wariness=0.99)
        self.assertEqual((decision.action, str(decision.release.version), decision.reason),
                         ("update", "1.0.2", "booted-release-pulled"))

    def test_booted_deadend_signed_rollback_to(self):
        doc = graph_doc([release("1.0.0", 1), release("1.0.1", 2, deadend=True,
                                                       rollback_to={"version": "1.0.0", "commit": commit(1)})])
        decision = select(doc, booted="1.0.1", booted_n=2)
        self.assertEqual((decision.action, str(decision.release.version)), ("rollback", "1.0.0"))
        # Without the signed marker, no downgrade.
        del doc["releases"][1]["rollback_to"]
        self.assertEqual(select(doc, booted="1.0.1", booted_n=2).action, "none")
        # A rollback target that already failed here is not used.
        doc["releases"][1]["rollback_to"] = {"version": "1.0.0", "commit": commit(1)}
        self.assertEqual(select(doc, booted="1.0.1", booted_n=2, dnr={commit(1)}).action, "none")

    def test_deadend_on_other_device_does_not_downgrade(self):
        doc = graph_doc([release("1.0.0", 1), release("1.0.1", 2, deadend=True,
                                                       rollback_to={"version": "1.0.0", "commit": commit(1)})])
        self.assertEqual(select(doc, booted="1.0.0", booted_n=1).action, "none")

    def test_switch_now(self):
        doc = graph_doc([release("1.0.0", 1), release("1.0.1", 2), release("1.0.2", 3, paused=True)])
        decision = g.select_switch_now(parse(doc), wariness=0.5, now=NOW)
        self.assertEqual((decision.action, str(decision.release.version)), ("rollback", "1.0.1"))


if __name__ == "__main__":
    unittest.main()
