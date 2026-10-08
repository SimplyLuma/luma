"""Focused checks for the complete variant budget."""

import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).with_name("variant-budget.py")
SPEC = importlib.util.spec_from_file_location("variant_budget", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class VariantBudgetTest(unittest.TestCase):
    def test_short_scenario_keeps_existing_budget(self):
        self.assertEqual(MODULE.budget({"states": [{"spec": [{"click": "#save"}]}]}), 900)

    def test_required_waits_in_each_state_and_before_are_counted(self):
        scenario = {"spec": {"before": [{"wait": 500}]},
                    "states": [{"spec": [{"wait": 10000}]}, {"spec": [{"wait": 10000}]}]}
        self.assertEqual(MODULE.budget(scenario), 921)

    def test_68_required_ten_second_states_fit_full_gate(self):
        scenario = {"states": [{"spec": [{"wait": 10000}]} for _ in range(68)]}
        self.assertEqual(MODULE.budget(scenario), 1580)

    def test_invalid_and_unbounded_waits_fail_closed(self):
        for wait in (-1, "1000", True, float("inf"), 3600000):
            with self.subTest(wait=wait), self.assertRaises(ValueError):
                MODULE.budget({"states": [{"spec": [{"wait": wait}]}]})


if __name__ == "__main__":
    unittest.main()
