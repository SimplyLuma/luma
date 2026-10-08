# SPDX-License-Identifier: Apache-2.0
"""The state store and the engine are safe with operations on several threads.

luma-updated runs each operation on a worker thread while its main loop answers
D-Bus calls. Regression test for the independent review's state_lock_race
proof: StateStore counted nesting in one attribute shared by every thread, so
a second thread entering locked() while the first was inside skipped the file
lock and the reload, and when the first left first the count went to -1 and no
later change was saved by that process.
"""

import json
import threading
import unittest

import fakes
from fakes import commit, graph_doc, release
from luma_update.state import StateStore


class StoreThreads(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.path = self.rig.paths.state_file

    def tearDown(self):
        self.rig.close()

    def on_disk(self):
        return json.loads(self.path.read_text())

    def test_overlapping_sections_from_two_threads_are_serialized_and_saved(self):
        store = StateStore(self.path)
        a_inside, b_started, a_left = threading.Event(), threading.Event(), threading.Event()
        order = []

        def thread_a():  # e.g. flush_reports' locked section
            with store.locked():
                store.data["reports"] = [{"a": 1}]
                order.append("a-in")
                a_inside.set()
                b_started.wait(5)
                # b must not be inside while a is.
                self.assertFalse(b_entered.is_set())
                order.append("a-out")
            a_left.set()

        b_entered = threading.Event()

        def thread_b():  # e.g. AcknowledgeRollback on another thread
            a_inside.wait(5)
            b_started.set()
            with store.locked():
                b_entered.set()
                order.append("b-in")
                store.data["rollback_notice"] = None
                store.data["last_error"] = "from b"

        threads = [threading.Thread(target=thread_a), threading.Thread(target=thread_b)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(order, ["a-in", "a-out", "b-in"])
        self.assertEqual(store._depth, 0)
        # b reloaded a's saved change instead of overwriting it.
        self.assertEqual(self.on_disk()["reports"], [{"a": 1}])
        self.assertEqual(self.on_disk()["last_error"], "from b")
        # And later changes from this process are still saved.
        with store.locked():
            store.data["pending"] = {"to_commit": "ab" * 32}
        self.assertIsNotNone(self.on_disk()["pending"])

    def test_no_update_is_lost_under_contention(self):
        store = StateStore(self.path)
        other = StateStore(self.path)  # another store in the same process, as the boot hook has

        def work(target):
            for _ in range(150):
                with target.locked():
                    with target.locked():  # nesting on the same thread still works
                        target.data["last_check"] = int(target.data.get("last_check", 0)) + 1

        threads = [threading.Thread(target=work, args=(store if i % 2 else other,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(self.on_disk()["last_check"], 900)
        self.assertEqual(store.snapshot()["last_check"], 900)

    def test_reload_inside_a_section_keeps_unsaved_changes(self):
        store = StateStore(self.path)
        with store.locked():
            store.data["last_error"] = "unsaved"
            store.reload()
            self.assertEqual(store.data["last_error"], "unsaved")
            copy = store.snapshot()
            copy["last_error"] = "changed copy"
            self.assertEqual(store.data["last_error"], "unsaved")
        self.assertEqual(self.on_disk()["last_error"], "unsaved")


class BlockingBackend(fakes.FakeRpmOstree):
    """rpm-ostree whose transaction waits until the test lets it finish."""

    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def update_deployment(self, **kwargs):
        self.entered.set()
        self.release.wait(10)
        super().update_deployment(**kwargs)


class EngineThreads(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.rig.backend = BlockingBackend()
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))
        self.engine = self.rig.engine()

    def tearDown(self):
        self.rig.backend.release.set()
        self.rig.close()

    def state(self):
        return json.loads(self.rig.paths.state_file.read_text())

    def test_reports_acknowledgement_and_refresh_beside_a_download(self):
        self.engine.check()
        with self.engine.store.locked():
            self.engine.store.data["rollback_notice"] = {"from_version": "0.9", "to_version": "1.0.0", "at": 1, "id": "x"}
            self.engine.store.queue_report({"channel": "stable", "from_version": "a", "to_version": "b",
                                            "arch": "x86_64", "result": "failed", "error_class": ""})
        download = threading.Thread(target=self.engine.download, kwargs={"user_initiated": True})
        download.start()
        self.assertTrue(self.rig.backend.entered.wait(5))

        # A report flush while the download runs leaves the queue alone.
        self.assertEqual(self.engine.flush_reports(), 0)
        self.assertEqual(len(self.state()["reports"]), 1)
        # A refresh from another thread publishes without disturbing the download.
        self.assertEqual(self.engine.refresh().state, "downloading")
        # A person's acknowledgement waits for the download, then applies.
        acknowledged = threading.Thread(target=self.engine.acknowledge_rollback_notice)
        acknowledged.start()
        acknowledged.join(0.3)
        self.assertTrue(acknowledged.is_alive())

        self.rig.backend.release.set()
        download.join(10)
        acknowledged.join(10)
        state = self.state()
        self.assertEqual(state["pending"]["to_commit"], commit(2))
        self.assertIsNone(state["rollback_notice"])
        self.assertEqual(self.engine.status().state, "staged")
        self.assertEqual(json.loads(self.rig.paths.status_file.read_text())["state"], "staged")
        self.assertGreaterEqual(self.engine.flush_reports(), 1)
        self.assertEqual(self.state()["reports"], [])


if __name__ == "__main__":
    unittest.main()
