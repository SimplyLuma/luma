# SPDX-License-Identifier: Apache-2.0
"""The standard `badge` value: an agent's unread badge on its dock icon."""

import unittest

from luma_appkit.background import BADGE_DOT, BADGE_VALUE, Agent


class Usage(Agent):
    app_id = "com.example.Usage"
    agent_id = "com.example.Usage.Agent"


class BadgeTests(unittest.TestCase):
    def setUp(self):
        self.agent = Usage()

    def published(self):
        return self.agent._values.get(BADGE_VALUE)

    def test_the_name_is_standard(self):
        self.assertEqual(BADGE_VALUE, "badge")
        self.assertEqual(BADGE_DOT, "dot")

    def test_a_count_is_unsigned(self):
        self.agent.set_badge(3)
        self.assertEqual(self.published().get_type_string(), "u")
        self.assertEqual(self.agent.values[BADGE_VALUE], 3)
        self.agent.set_badge(2**40)
        self.assertEqual(self.agent.values[BADGE_VALUE], 2**32 - 1)

    def test_zero_keeps_the_value_and_shows_nothing(self):
        self.agent.set_badge(0)
        self.assertEqual(self.agent.values[BADGE_VALUE], 0)

    def test_dot(self):
        self.agent.set_badge(BADGE_DOT)
        self.assertEqual(self.published().get_type_string(), "s")
        self.assertEqual(self.agent.values[BADGE_VALUE], "dot")

    def test_none_unpublishes(self):
        self.agent.set_badge(4)
        self.agent.set_badge(None)
        self.assertNotIn(BADGE_VALUE, self.agent.values)

    def test_anything_else_is_refused(self):
        for bad in (-1, True, 1.5, "3", "running", ["dot"]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.agent.set_badge(bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
