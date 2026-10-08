# SPDX-License-Identifier: Apache-2.0
import json
import os
import tempfile
import unittest
from pathlib import Path

from ari import agent, audit, intents, models, policy  # noqa: E402
from ari.store import Store  # noqa: E402
from ari.tools import answers  # noqa: E402

MODELS = next(p for p in (Path(agent.__file__).resolve().parents[1] / "data/models.json",
                          Path("/usr/share/luma-ari/models.json")) if p.exists())


class Intents(unittest.TestCase):
    def test_common_requests_skip_the_model(self):
        cases = {
            "Move my dock to the top": ("set_dock_position", {"edge": "top"}),
            "dark mode": ("set_theme", {"theme": "dark"}),
            "Change my wallpaper to something random": ("set_wallpaper", {"wallpaper": "something random"}),
            "Open Discord": ("open_app", {"name": "Discord"}),
            "Lower my refresh rate": ("set_refresh_rate", {"hz": "lower", "display": ""}),
            "set my refresh rate to 60hz": ("set_refresh_rate", {"hz": "60", "display": ""}),
            "What's 17% of 3,200?": ("calculate", {"expression": "17% of 3,200"}),
            "what time is it": ("now", {}),
            "Dim my screen": ("set_brightness", {"level": "dimmer"}),
            "Which model are you?": ("_about", {}),
            "Press PLAY inside Tide": ("media_control", {"action": "play", "player": "Tide"}),
            "pause the music": ("media_control", {"action": "pause", "player": ""}),
            "Turn TILING off": ("set_tiling", {"state": "off"}),
            "Set my system timezone to eastern time": ("set_timezone", {"zone": "eastern time"}),
            "mute": ("set_volume", {"level": "mute"}),
            "set brightness to 40%": ("set_brightness", {"level": "40"}),
        }
        for text, (tool, arguments) in cases.items():
            intent = intents.match(text)
            self.assertIsNotNone(intent, text)
            self.assertEqual((intent.tool, intent.arguments), (tool, arguments), text)

    def test_questions_go_to_the_model(self):
        for text in ("What's a group of horses called?", "Who is the president?", "What's 2026?",
                     "open the dock settings and explain them"):
            intent = intents.match(text)
            self.assertTrue(intent is None or intent.tool == "open_app" and "dock" not in text, text)


class Answers(unittest.TestCase):
    def test_calculate_is_exact_and_refuses_code(self):
        self.assertEqual(answers.calculate({"expression": "17% of 3,200"})["summary"], "17% of 3,200 is 544")
        self.assertEqual(answers.calculate({"expression": "2^10"})["data"]["value"], 1024)
        with self.assertRaises(Exception):
            answers.calculate({"expression": "__import__('os').system('true')"})


class Policy(unittest.TestCase):
    def test_unknown_and_later_tier_tools_never_run(self):
        engine = policy.Policy()
        self.assertFalse(engine.check("run_shell", calls_this_turn=0, after_untrusted=False).allowed)
        self.assertTrue(engine.check("set_theme", calls_this_turn=0, after_untrusted=False).allowed)
        self.assertFalse(engine.check("set_theme", calls_this_turn=policy.MAX_CALLS_PER_TURN,
                                      after_untrusted=False).allowed)

    def test_approval_follows_the_chosen_mode(self):
        engine = policy.Policy()
        self.assertTrue(engine.needs_approval("set_timezone"))
        self.assertFalse(engine.needs_approval("set_dock_position"))
        self.assertFalse(engine.needs_approval("weather"))
        engine.approval_mode = "all"
        self.assertTrue(engine.needs_approval("set_dock_position"))
        engine.approval_mode = "never"
        self.assertFalse(engine.needs_approval("set_timezone"))

    def test_a_tier_the_person_turned_off_is_refused(self):
        engine = policy.Policy()
        engine.enabled.discard(policy.PERSONAL_SETTINGS)
        decision = engine.check("set_theme", calls_this_turn=0, after_untrusted=False)
        self.assertFalse(decision.allowed)
        self.assertIn("turned off", decision.reason)
        self.assertTrue(engine.check("weather", calls_this_turn=0, after_untrusted=False).allowed)

    def test_the_rate_limit_holds_across_turns(self):
        engine = policy.Policy()
        for _ in range(policy.MAX_CALLS_PER_MINUTE):
            self.assertTrue(engine.check("now", calls_this_turn=0, after_untrusted=False).allowed)
        self.assertFalse(engine.check("now", calls_this_turn=0, after_untrusted=False).allowed)


class Replies(unittest.TestCase):
    def turn(self, **values):
        turn = agent.Turn("c", "text", lambda _event: None)
        for key, value in values.items():
            setattr(turn, key, value)
        return turn

    def test_filler_and_markdown_are_removed(self):
        text = "I'll calculate 17% of 3,200 for you. 17% of 3,200 is **544**."
        self.assertEqual(agent._normalise(text, self.turn()), "17% of 3,200 is 544.")
        self.assertEqual(agent._normalise("I'll check.", self.turn()), "I'll check.")

    def test_a_claimed_change_without_a_step_is_corrected(self):
        reply = agent._normalise("Done, the accent is chartreuse now.", self.turn())
        self.assertTrue(reply.startswith("I haven't changed anything."), reply)
        reply = agent._normalise("I changed the theme to frost. Want anything else?", self.turn())
        self.assertEqual(reply, "I haven't changed anything. Want anything else?")

    def test_instructions_are_never_recited(self):
        from ari.persona import RULES
        reply = agent._normalise(" ".join(RULES[:3]), self.turn())
        self.assertNotIn("Warm, direct", reply)

    def test_a_short_yes_is_routed_with_what_came_before(self):
        recent = [{"role": "user", "content": "Can you change my time zone?"},
                  {"role": "assistant", "content": "Would you like me to set it?"},
                  {"role": "user", "content": "Yes"}]
        self.assertIn("time zone", agent._routing_text("Yes", recent))
        self.assertEqual(agent._routing_text("What's the weather like in Paris today?", recent),
                         "What's the weather like in Paris today?")

    def test_a_setting_value_must_come_from_the_person(self):
        self.assertFalse(agent._value_named("Set my accent colour to chartreuse", "set_accent", {"color": "yellow"}))
        self.assertTrue(agent._value_named("make the accent grey", "set_accent", {"color": "slate"}))
        self.assertTrue(agent._value_named("dock on the left please", "set_dock_position", {"edge": "left"}))
        self.assertTrue(agent._value_named("Dim my screen", "set_brightness", {"level": "dimmer"}))

    def test_recent_changes_say_what_was_undone(self):
        text = agent._recent_changes([{"tool": "set_dock_position", "summary": "Dock moved to the top",
                                       "state": "undone", "created": 0}])
        self.assertIn("Dock moved to the top (undone since)", text)
        self.assertIn("None.", agent._recent_changes([]))

    def test_ignored_instructions_are_mentioned(self):
        reply = agent._normalise("The page is about tomatoes.", self.turn(injection_seen=True))
        self.assertIn("I ignored them", reply)


class Capabilities(unittest.TestCase):
    def test_time_zones_by_the_names_people_use(self):
        from ari.tools import system
        if not system.ZONE_TABLE.exists():
            self.skipTest("no tzdata")
        self.assertEqual(system.resolve_zone("eastern time"), "America/New_York")
        self.assertEqual(system.resolve_zone("Tokyo"), "Asia/Tokyo")
        self.assertEqual(system.resolve_zone("pacific"), "America/Los_Angeles")
        self.assertIsNone(system.resolve_zone("Atlantis"))

    def test_the_settings_catalogue_leaves_out_security(self):
        from ari.tools import preferences
        self.assertTrue(preferences._allowed("org.gnome.desktop.interface"))
        self.assertFalse(preferences._allowed("org.gnome.desktop.screensaver"))
        self.assertFalse(preferences._allowed("org.gnome.desktop.privacy"))
        self.assertFalse(preferences._allowed("org.projectluma.Ari"))


class Records(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_the_activity_log_is_private_and_append_only(self):
        path = self.root / "activity.jsonl"
        audit.record({"tool": "now"}, path)
        audit.record({"tool": "calculate"}, path)
        self.assertEqual([e["tool"] for e in audit.recent(10, path)], ["now", "calculate"])
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_conversations_keep_messages_and_steps(self):
        store = Store(self.root / "ari.db")
        self.addCleanup(store.db.close)
        conversation = store.new_conversation()
        message = store.add_message(conversation, "user", "Move my dock to the top")
        step = store.add_step(conversation, message, "set_dock_position", "Dock moved to the top",
                              {"tool": "set_dock_position", "arguments": {"edge": "bottom"}})
        self.assertEqual(store.messages(conversation)[0]["content"], "Move my dock to the top")
        self.assertEqual(store.step(step)["tool"], "set_dock_position")
        self.assertEqual([s["summary"] for s in store.recent_steps(0)], ["Dock moved to the top"])
        store.delete_conversation(conversation)
        self.assertFalse(store.exists(conversation))


class Catalogue(unittest.TestCase):
    def test_removing_a_model_leaves_nothing_behind(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = models.catalogue(MODELS)[0]
            for path in (model.path(root), model.path(root).with_suffix(".verified")):
                path.write_text(model.sha256)
            self.assertEqual(models.installed([model], root), [model])
            models.remove(model, root)
            self.assertEqual(list(root.iterdir()), [])

    def test_suite_results_are_read_per_model(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "2026-09-13-qwen3-4b-q4.json").write_text(
                json.dumps({"model": "qwen3-4b-q4", "passed": 14, "total": 14, "date": "2026-09-13"}))
            self.assertEqual(models.suite_results(Path(directory))["qwen3-4b-q4"]["passed"], 14)


    def test_every_model_is_pinned_and_checksummed(self):
        catalogue = models.catalogue(MODELS)
        self.assertGreaterEqual(len(catalogue), 3)
        rows = json.loads(MODELS.read_text())["models"]
        for row in rows:
            self.assertRegex(row["url"], r"^https://huggingface\.co/.+/resolve/[0-9a-f]{40}/")
            self.assertRegex(row["sha256"], r"^[0-9a-f]{64}$")
        for tier in ("minimal", "standard"):
            self.assertIsNotNone(models.default_for_tier(tier, catalogue))



class Cloud(unittest.TestCase):
    CATALOGUE = {"data": [
        {"id": "cheap/fast", "name": "Fast", "context_length": 1000000, "supported_parameters": ["tools"],
         "pricing": {"prompt": "0.00000015", "completion": "0.0000006"}},
        {"id": "big/frontier", "name": "Frontier", "context_length": 1000000, "supported_parameters": ["tools"],
         "pricing": {"prompt": "0.00001", "completion": "0.00005"}},
        {"id": "mid/builder", "name": "Builder", "context_length": 1000000, "supported_parameters": ["tools"],
         "pricing": {"prompt": "0.000002", "completion": "0.00001"}},
        {"id": "no/tools", "name": "No tools", "supported_parameters": [],
         "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "big/frontier:batch", "name": "Batch", "supported_parameters": ["tools"],
         "pricing": {"prompt": "0.000005", "completion": "0.000025"}},
        {"id": "~alias/latest", "name": "Alias", "supported_parameters": ["tools"],
         "pricing": {"prompt": "0.000001", "completion": "0.000001"}},
        {"id": "router/auto", "name": "Auto", "supported_parameters": ["tools"],
         "pricing": {"prompt": "-1", "completion": "-1"}},
    ]}

    def test_only_models_that_can_use_tools_at_a_known_price(self):
        from ari import cloud
        found = {m.id for m in cloud.parse_catalogue(self.CATALOGUE)}
        self.assertEqual(found, {"cheap/fast", "big/frontier", "mid/builder"})

    def test_cost_is_told_per_question_and_expensive_models_are_marked(self):
        from ari import cloud
        models = {m.id: m for m in cloud.parse_catalogue(self.CATALOGUE)}
        self.assertEqual(models["cheap/fast"].cost_class, "low")
        self.assertEqual(models["mid/builder"].cost_class, "moderate")
        self.assertEqual(models["big/frontier"].cost_class, "high")
        # $5 per million input is expensive even though the string is 0.000005, not 5e-06 exactly.
        self.assertEqual(cloud.CloudModel("a/b", "B", 1, float("0.000005"), float("0.0000001"), "").cost_class, "high")
        self.assertEqual(cloud.cost_label(models["cheap/fast"]), "About 0.1¢ a question")
        self.assertEqual(cloud.cost_label(models["big/frontier"]), "About 10¢ a question")

    def test_recommendations_use_the_first_model_still_listed(self):
        from ari import cloud
        models = cloud.parse_catalogue(self.CATALOGUE)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roles.json"
            path.write_text(json.dumps({"schema_version": 1, "roles": [
                {"id": "everyday", "title": "Everyday", "why": "w", "candidates": ["gone/model", "cheap/fast"]},
                {"id": "nothing", "title": "None", "why": "w", "candidates": ["gone/too"]}]}))
            shown = cloud.recommendations(models, path)
        self.assertEqual([(r["role"], r["id"]) for r in shown], [("everyday", "cheap/fast")])

    def test_shipped_recommendations_are_valid(self):
        value = json.loads((MODELS.parent / "cloud-models.json").read_text())
        self.assertEqual(value["schema_version"], 1)
        self.assertEqual([r["id"] for r in value["roles"]], ["everyday", "builder", "frontier"])
        self.assertTrue(all(r["candidates"] and r["why"] for r in value["roles"]))

    def test_requests_carry_a_price_ceiling_and_refuse_training(self):
        from ari import cloud
        model = {m.id: m for m in cloud.parse_catalogue(self.CATALOGUE)}["mid/builder"]
        options = cloud.request_options(model, private=True)
        self.assertAlmostEqual(options["provider"]["max_price"]["prompt"], 2.5)
        self.assertAlmostEqual(options["provider"]["max_price"]["completion"], 12.5)
        self.assertEqual(options["provider"]["data_collection"], "deny")
        self.assertNotIn("data_collection", cloud.request_options(model, private=False)["provider"])

    def test_the_ledger_adds_up_by_month(self):
        from ari import cloud
        with tempfile.TemporaryDirectory() as directory:
            ledger = cloud.Ledger(Path(directory) / "spend.json")
            ledger.add(0.25)
            ledger.add(0.5)
            ledger.add(-3)
            self.assertAlmostEqual(ledger.spent(), 0.75)
            value = json.loads(ledger.path.read_text())
            value["month"] = "2000-01"
            ledger.path.write_text(json.dumps(value))
            self.assertEqual(ledger.spent(), 0.0)

    def test_a_reply_reports_its_cost_and_errors_read_plainly(self):
        import io
        import urllib.error
        from unittest import mock
        from ari.providers import OpenAICompatible
        stream = io.BytesIO(b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n'
                            b'data: {"choices":[],"usage":{"cost":0.0042}}\n'
                            b'data: [DONE]\n')
        with mock.patch("urllib.request.urlopen", return_value=stream):
            events = list(OpenAICompatible("https://example.invalid", "k", "m").chat([]))
        self.assertIn({"type": "cost", "cost": 0.0042}, events)
        error = urllib.error.HTTPError("https://example.invalid", 402, "Payment Required", {}, None)
        self.addCleanup(error.close)
        with mock.patch("urllib.request.urlopen", side_effect=error):
            events = list(OpenAICompatible("https://example.invalid", "k", "m").chat([]))
        self.assertIn("credit", events[0]["message"])


if __name__ == "__main__":
    unittest.main()
