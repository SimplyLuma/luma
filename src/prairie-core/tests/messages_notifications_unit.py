#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Muted Messages conversations do not post network notifications."""

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from prairie_apps.messages_agent import MessagesAgent
from prairie_apps.messages_preferences import ConversationPreferences


class MutedNotificationTest(unittest.TestCase):
    def test_background_agent_keeps_count_but_does_not_notify(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
                os.environ, {"XDG_DATA_HOME": directory}):
            preferences = ConversationPreferences()
            preferences.set_flag("account:one|@priya", "muted", True)
            sent = []
            counted = []
            agent = MessagesAgent.__new__(MessagesAgent)
            agent.services = {"one": SimpleNamespace()}
            agent.notifier = SimpleNamespace(notify=lambda *args, **kwargs: sent.append((args, kwargs)))
            agent.viewing = {}
            agent._schedule_count = lambda: counted.append(True)
            agent._arrived("one", "@priya", "Priya", "Hello")
            self.assertEqual(sent, [])
            self.assertEqual(counted, [True])
            self.assertTrue(Path(preferences.path).is_file())


if __name__ == "__main__":
    unittest.main()
