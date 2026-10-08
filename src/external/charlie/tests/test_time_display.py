# SPDX-License-Identifier: Apache-2.0
from datetime import datetime, timedelta, timezone
import unittest

from charlie_luma.time_display import display_time, local_datetime


class TimeDisplayTests(unittest.TestCase):
    def setUp(self):
        self.chicago_summer = timezone(timedelta(hours=-5))

    def test_utc_message_uses_local_wall_clock(self):
        message = datetime(2026, 9, 15, 2, 52, tzinfo=timezone.utc)
        self.assertEqual(local_datetime(message, self.chicago_summer).hour, 21)
        self.assertEqual(
            display_time(
                message,
                now=datetime(2026, 9, 15, 3, 0, tzinfo=timezone.utc),
                zone=self.chicago_summer,
            ),
            "9:52 PM",
        )

    def test_relative_day_is_decided_after_local_conversion(self):
        message = datetime(2026, 9, 15, 2, 52, tzinfo=timezone.utc)
        self.assertEqual(
            display_time(
                message,
                now=datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc),
                zone=self.chicago_summer,
            ),
            "Yesterday",
        )

    def test_legacy_naive_values_are_interpreted_as_utc(self):
        value = datetime(2026, 9, 15, 2, 52)
        self.assertEqual(local_datetime(value, self.chicago_summer).hour, 21)


if __name__ == "__main__":
    unittest.main()
