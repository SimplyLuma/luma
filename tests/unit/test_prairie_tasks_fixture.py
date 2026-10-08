# SPDX-License-Identifier: Apache-2.0
from datetime import date
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/prairie-core"))
from prairie_apps.tasks_fixture import FixtureRepository, source_from_environment


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.path = ROOT / "tests/fixtures/tasks-v70.json"
        self.repository = FixtureRepository(self.path)

    def test_full_spec_data_is_retained(self):
        self.assertEqual(len(self.repository.data["tasks"]), 16)
        sources, tasks = self.repository.load()
        self.assertEqual([s.name for s in sources], ["Personal", "Launch", "Home", "Groceries"])
        deck = next(t for t in tasks if t.uid == "t100")
        self.assertEqual(deck.title, "Finish the walkthrough deck")
        self.assertEqual(deck.due.date(), date(2026, 9, 23))
        self.assertEqual((deck.due.hour, deck.due.minute), (17, 0))
        self.assertEqual(len([t for t in tasks if t.parent == deck.uid]), 5)
        self.assertEqual(len(deck.comments), 2)
        self.assertEqual(self.repository.data["tasks"][1]["who"], ["me", "PR", "NF"])
        self.assertTrue(all(not source.writable for source in sources))

    def test_fixture_never_constructs_real_repository(self):
        with patch.dict(os.environ, {"LUMA_TASKS_FIXTURE": str(self.path)}), \
             patch("prairie_apps.tasks_backend.TasksRepository", side_effect=AssertionError("Real store touched")):
            source = source_from_environment()
            self.assertIsInstance(source, FixtureRepository)
            self.assertEqual(len(source.load()[0]), 4)
            source.close()

    def test_missing_fixture_fails_closed(self):
        with patch.dict(os.environ, {"LUMA_TASKS_FIXTURE": str(self.path) + ".missing"}), \
             patch("prairie_apps.tasks_backend.TasksRepository", side_effect=AssertionError("Real store touched")):
            with self.assertRaises(FileNotFoundError):
                source_from_environment()

    def test_mutations_are_refused_and_fixture_unchanged(self):
        before = self.path.read_bytes()
        task = self.repository.load()[1][0]
        for method, args in (("edit", (task, {"title": "Changed"})),
                             ("add", ("personal", {"title": "New"})),
                             ("delete", (task,)), ("complete", (task, True)),
                             ("comment", (task, "Hello", "me")), ("undo", (None,)),
                             ("create_list", ("New",)), ("configure", ("launch", (), (), True))):
            with self.subTest(method=method), self.assertRaisesRegex(RuntimeError, "read-only"):
                getattr(self.repository, method)(*args)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.repository.load()[1][0].title, task.title)


if __name__ == "__main__":
    unittest.main()
