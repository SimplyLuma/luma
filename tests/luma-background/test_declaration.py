# SPDX-License-Identifier: MPL-2.0
"""Identifiers and the [background] contract, including the shared lint corpus."""

import json
import tomllib
import unittest
from pathlib import Path

from luma_background import ids
from luma_background.declaration import (
    DeclarationError, command_errors, from_mapping, parse_interval, validate,
)

CORPUS = Path(__file__).with_name("corpus")


def base(**background):
    table = {
        "agent": "com.example.Usage.Agent",
        "exec": "usage-meter --agent",
        "category": "widget-data",
        "wake": ["login", "network"],
    }
    table.update(background)
    return {"application": {"id": "com.example.Usage", "name": "Usage"}, "background": table}


class Identifiers(unittest.TestCase):
    def test_app_ids(self):
        for good in ("org.projectluma.Messages", "com.example.Usage", "io.github.a_b.My-App"):
            self.assertTrue(ids.is_app_id(good), good)
        for bad in ("messages", "org.example", "org.9x.App", "org.example.App.desktop",
                    "org.exa-mple.App", "org.example.App/../x", "org.example.Äpp", "", None,
                    "org.example." + "a" * 300):
            self.assertFalse(ids.is_app_id(bad), bad)

    def test_agent_name_must_live_under_the_app(self):
        self.assertTrue(ids.is_agent_name("com.example.Usage.Agent", "com.example.Usage"))
        self.assertFalse(ids.is_agent_name("com.example.UsageAgent", "com.example.Usage"))
        self.assertFalse(ids.is_agent_name("com.example.Usage", "com.example.Usage"))
        self.assertFalse(ids.is_agent_name("org.projectluma.Messages.Agent", "com.example.Usage"))

    def test_units_are_derived_and_escaped(self):
        self.assertEqual(ids.agent_unit("org.projectluma.Messages"), "app-org.projectluma.Messages-agent.service")
        self.assertEqual(ids.agent_unit("io.github.x.My-App"), "app-io.github.x.My\\x2dApp-agent.service")
        self.assertEqual(ids.parse_agent_unit("app-io.github.x.My\\x2dApp-agent.service"), "io.github.x.My-App")
        self.assertEqual(ids.schedule_timer("com.example.Usage", "alarm-7"), "app-com.example.Usage-agent-alarm-7.timer")
        self.assertEqual(ids.parse_schedule_unit("app-com.example.Usage-agent-alarm-7.service"),
                         ("com.example.Usage", "alarm-7"))
        self.assertIsNone(ids.parse_agent_unit("app-gnome-com.example.Usage-123.scope"))
        with self.assertRaises(ids.InvalidIdentifier):
            ids.agent_unit("com.example/../../x")
        with self.assertRaises(ids.InvalidIdentifier):
            ids.schedule_timer("com.example.Usage", "Bad Name")


class Contract(unittest.TestCase):
    def test_a_complete_declaration(self):
        declaration = from_mapping(base(wake=["login", "schedule"], interval="15m",
                                        publishes=["remaining", "live-extension:com.example.Usage.Remaining"]))
        self.assertEqual(declaration.argv, ("usage-meter", "--agent"))
        self.assertEqual(declaration.interval_seconds, 900)
        self.assertEqual(declaration.wake, ("login", "schedule"))

    def test_commands_are_plain_words(self):
        for bad in ("usage-meter %U", "sh -c 'rm -rf ~'", "usage-meter; reboot", "FOO=1 usage-meter",
                    "usage-meter $HOME", "usage-meter `id`", "usage-meter > /tmp/x", "-usage",
                    "usage\nmeter", "", "   ", 'usage "--agent"'):
            self.assertTrue(command_errors(bad), bad)
        self.assertEqual(command_errors("/usr/libexec/usage-agent --agent --quiet"), [])

    def test_rules(self):
        cases = {
            "agent": base(agent="org.other.App.Agent"),
            "category": base(category="games"),
            "wake event": base(wake=["boot"]),
            "empty wake": base(wake=[]),
            "interval without schedule": base(interval="15m"),
            "interval too short": base(wake=["schedule"], interval="1m"),
            "unknown key": base(priority="high"),
            "publishes": base(publishes=["Remaining!"]),
        }
        for label, data in cases.items():
            with self.subTest(label):
                self.assertTrue(validate(data))
                with self.assertRaises(DeclarationError):
                    from_mapping(data)

    def test_file_name_must_match(self):
        self.assertTrue(validate(base(), expected_app_id="com.example.Other"))

    def test_intervals(self):
        self.assertEqual(parse_interval("5m"), 300)
        self.assertEqual(parse_interval("6h"), 21600)
        self.assertEqual(parse_interval("7d"), 604800)
        for bad in ("4m", "8d", "15", "15s", "1.5h", 15, "0m"):
            with self.assertRaises(ValueError):
                parse_interval(bad)


class SharedCorpus(unittest.TestCase):
    """The same fixtures `luma lint` is tested against, so the rules agree."""

    def test_corpus(self):
        expectations = json.loads((CORPUS / "expectations.json").read_text())
        self.assertGreater(len(expectations), 8)
        for name, valid in expectations.items():
            with self.subTest(name):
                data = tomllib.loads((CORPUS / name).read_text())
                self.assertEqual(not validate(data), valid, validate(data))


if __name__ == "__main__":
    unittest.main()
