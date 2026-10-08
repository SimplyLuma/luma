# SPDX-License-Identifier: Apache-2.0
"""`luma lint` rules for [background], against the corpus luma-background uses."""

from __future__ import annotations

import json
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/luma-platform/sdk"))

from luma_sdk.background import background_errors, background_project_errors  # noqa: E402

CORPUS = ROOT / "tests/luma-background/corpus"


class BackgroundLint(unittest.TestCase):
    def test_shared_corpus(self) -> None:
        expectations = json.loads((CORPUS / "expectations.json").read_text(encoding="utf-8"))
        for name, valid in expectations.items():
            with self.subTest(name):
                manifest = tomllib.loads((CORPUS / name).read_text(encoding="utf-8"))
                self.assertEqual(not background_errors(manifest), valid, background_errors(manifest))

    def test_no_table_no_rules(self) -> None:
        self.assertEqual(background_errors({"application": {"id": "org.example.App"}}), [])

    def test_project_rules(self) -> None:
        manifest = tomllib.loads((CORPUS / "valid-minimal.toml").read_text(encoding="utf-8"))
        manifest["distribution"] = {"portals": ["notifications"]}
        errors = background_project_errors(manifest, ROOT, "usage-meter %U", None)
        self.assertEqual(errors, ["distribution.portals must include 'background' when [background] is declared"])
        manifest["distribution"]["portals"].append("background")
        self.assertEqual(background_project_errors(manifest, ROOT, None, "usage-meter"), [])
        errors = background_project_errors(manifest, ROOT, None, "other-command")
        self.assertTrue(errors and "own command" in errors[0])


if __name__ == "__main__":
    unittest.main()
