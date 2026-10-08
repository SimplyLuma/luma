# SPDX-License-Identifier: Apache-2.0
"""Checks on exact fixture content, filtering and isolated simulated actions."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "src/luma-ari/ari_ui/fixture.py"
if not MODULE.is_file():
    # The package archive puts ari_ui beside tests rather than under src.
    ROOT = Path(__file__).resolve().parents[1]
    MODULE = ROOT / "ari_ui/fixture.py"
SCENARIO = ROOT / "tools/lumaui-conform/scenarios/ari.json"
if not SCENARIO.is_file():
    SCENARIO = ROOT / "tests/scenarios/ari.json"
spec = importlib.util.spec_from_file_location("ari_v70_fixture", MODULE)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
DATA = json.loads((ROOT / "tests/fixtures/ari-v70.json").read_text())


class V70FixtureTests(unittest.TestCase):
    def test_every_scenario_state_has_phone_coverage(self):
        scenario = json.loads(SCENARIO.read_text())
        self.assertEqual({state["name"] for state in scenario["states"]}, set(scenario["phone_states"]))

    def test_existing_audit_dates(self):
        self.assertEqual(fixture.audit_timestamp("2026-09-26T04:00:00+0000"), 1790395200)
        self.assertEqual(fixture.audit_timestamp(0), 0)
        self.assertIsNone(fixture.audit_timestamp("not a date"))
        self.assertIsNone(fixture.audit_timestamp(None))

    def test_fixture_download_completes_without_changing_the_original(self):
        model = next(model for model in self.source.data["local_models"] if model["id"] == "qwen14")
        self.assertFalse(self.source.advance_download("qwen14"))
        self.assertEqual(model["dl"], 5)
        self.assertTrue(self.source.advance_download("qwen14", 100))
        self.assertTrue(model["have"])
        self.assertNotIn("dl", model)
        original = next(model for model in DATA["local_models"] if model["id"] == "qwen14")
        self.assertNotIn("dl", original)
        self.assertFalse(original.get("have", False))

    def setUp(self):
        self.source = fixture.FixtureSource(DATA)

    def test_opening_data(self):
        self.assertEqual(DATA["state"]["cur"], "budget")
        self.assertEqual(DATA["state"]["model"], "auto")
        self.assertEqual(len(DATA["conversations"]), 5)
        self.assertEqual(len(DATA["activity"]), 6)
        self.assertEqual(len(DATA["local_models"]), 10)
        self.assertEqual(fixture.pending_count(DATA["activity"]), 1)
        self.assertEqual(DATA["activity"]["budget"]["ask"]["subj"], "Budget for September")

    def test_chat_search_and_order(self):
        groups = fixture.chat_groups(DATA["conversations"])
        self.assertEqual([day for day, _ in groups], ["Today", "Yesterday", "Monday"])
        self.assertEqual([chat["id"] for chat in groups[0][1]], ["budget", "tips"])
        self.assertEqual(fixture.chat_groups(DATA["conversations"], "BUDGET")[0][1][0]["id"], "budget")
        self.assertEqual(fixture.chat_groups(DATA["conversations"], "no such chat"), [])

    def test_activity_filters(self):
        self.assertEqual([key for key, _ in fixture.activity_items(DATA["activity"], "Files")],
                         ["budget", "shots", "photos"])
        self.assertEqual([key for key, _ in fixture.activity_items(DATA["activity"], "People")],
                         ["budget", "quiet", "dad"])
        self.assertEqual(len(fixture.activity_items(DATA["activity"])), 6)
        self.assertEqual(fixture.activity_items(DATA["activity"], "missing"), [])

    def test_trace_status_precedence(self):
        receipt = copy.deepcopy(DATA["activity"]["budget"])
        self.assertEqual(fixture.trace_state(receipt), ("ask", "Needs your OK"))
        receipt["nodes"][0]["st"] = "run"
        self.assertEqual(fixture.trace_state(receipt)[0], "ask")
        receipt["nodes"][-1]["st"] = "hide"
        self.assertEqual(fixture.trace_state(receipt), ("run", "Working"))
        receipt["nodes"][0]["st"] = "done"
        self.assertEqual(fixture.trace_state(receipt), ("done", "4 steps"))
        receipt["undo"] = "undone"
        self.assertEqual(fixture.trace_state(receipt), ("undone", "Undone"))

    def test_model_fit_boundaries(self):
        computer = DATA["computer"]
        for memory, expected in ((12, "good"), (12.1, "ok"), (24, "ok"), (24.1, "no")):
            with self.subTest(memory=memory):
                self.assertEqual(fixture.model_fit({"mem": memory}, computer)[0], expected)

    def test_model_catalog(self):
        catalog = fixture.model_catalog(DATA["local_models"])
        self.assertEqual([model["id"] for model in catalog["local"]], ["qwen8", "qwen30"])
        self.assertEqual(catalog["recommended"]["id"], "qwen14")
        self.assertEqual(len(catalog["store"]), 7)
        catalog = fixture.model_catalog(DATA["local_models"], "Images")
        self.assertIsNone(catalog["recommended"])
        self.assertEqual([model["id"] for model in catalog["store"]], ["gemma12", "qvl7"])
        self.assertEqual(fixture.model_catalog(DATA["local_models"], query=" MISTRAL ")["store"][0]["id"], "mistral24")
        self.assertEqual(fixture.model_catalog(DATA["local_models"], query="missing")["store"], [])

    def test_downloading_moves_out_of_store(self):
        models = copy.deepcopy(DATA["local_models"])
        models[3]["dl"] = 0
        catalog = fixture.model_catalog(models)
        self.assertIn(models[3], catalog["local"])
        self.assertNotIn(models[3], catalog["store"])

    def test_declining_changes_only_owned_memory(self):
        before = copy.deepcopy(DATA)
        self.source.answer("budget", False)
        node = self.source.data["activity"]["budget"]["nodes"][-1]
        self.assertEqual((node["st"], node["m"]), ("undone", "Not sent"))
        self.assertEqual(fixture.pending_count(self.source.data["activity"]), 0)
        self.assertEqual(DATA, before)
        self.assertEqual(fixture.FixtureSource(DATA).data, before)

    def test_sent_email_survives_undo(self):
        self.source.answer("budget", True)
        self.source.undo("budget")
        nodes = self.source.data["activity"]["budget"]["nodes"]
        self.assertEqual([node["st"] for node in nodes], ["done", "undone", "undone", "done"])
        self.assertEqual(fixture.trace_state(self.source.data["activity"]["budget"])[0], "undone")

    def test_invalid_actions_fail(self):
        with self.assertRaises(ValueError):
            self.source.answer("shots", True)
        with self.assertRaises(ValueError):
            self.source.undo("quiet")
        with self.assertRaises(KeyError):
            self.source.undo("unknown")
        with self.assertRaises(ValueError):
            fixture.FixtureSource({})

    def test_environment_is_explicit_and_file_stays_unchanged(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(fixture.source_from_environment())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.json"
            raw = json.dumps(DATA)
            path.write_text(raw)
            with patch.dict(os.environ, {"LUMA_ARI_FIXTURE": str(path)}):
                source = fixture.source_from_environment()
                source.answer("budget", True)
                source.undo("budget")
            self.assertEqual(path.read_text(), raw)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_scripted_suggestions_create_fixture_receipts_only(self):
        source = fixture.FixtureSource(DATA)
        chat = {"id": "owned", "msgs": []}
        messages = fixture.scripted_reply(source, chat, "Put my tax documents in one folder", "qwen8")
        self.assertEqual(messages[0]["s"], 2.4)
        receipt = source.data["activity"][messages[1]["ch"]]
        self.assertEqual(receipt["nodes"][1]["n"], "Taxes 2026")
        self.assertEqual(DATA, json.loads((ROOT / "tests/fixtures/ari-v70.json").read_text()))
        response = fixture.scripted_reply(source, chat, "Explain local models in two sentences", "qwen8")
        self.assertTrue(response[0]["mem"])


if __name__ == "__main__":
    unittest.main()
