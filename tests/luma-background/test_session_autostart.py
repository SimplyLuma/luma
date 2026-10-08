# SPDX-License-Identifier: MPL-2.0
"""The hidden entries that keep GNOME's session from starting an agent's fallback."""

from __future__ import annotations

import configparser
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
# In the source tarball tests/ sits beside data/; in the repository it does not.
CANDIDATES = (HERE.parent / "data/session-autostart", HERE.parents[1] / "src/luma-background/data/session-autostart")
DIRECTORY = next((path for path in CANDIDATES if path.is_dir()), CANDIDATES[0])
AGENTS = {"Calendar", "Charlie", "Connect", "Messages", "Phone"}


def read(path: Path) -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None, comment_prefixes=("#",))
    parser.optionxform = str
    parser.read(path, encoding="utf-8")
    return dict(parser["Desktop Entry"])


class SessionAutostart(unittest.TestCase):
    def test_every_first_party_fallback_is_shadowed(self):
        names = {path.name for path in DIRECTORY.glob("*.desktop")}
        self.assertEqual(names, {f"org.projectluma.{app}.Agent.desktop" for app in AGENTS})

    def test_each_shadow_is_hidden_and_names_its_agent(self):
        for path in DIRECTORY.glob("*.desktop"):
            with self.subTest(path.name):
                values = read(path)
                self.assertEqual(values["Type"], "Application")
                self.assertEqual(values["Hidden"], "true")
                # The file name is what the session matches on, and it must
                # be the agent the fallback entry names.
                self.assertEqual(values["X-Luma-Background-Agent"] + ".desktop", path.name)
                self.assertTrue(values["Name"])


if __name__ == "__main__":
    unittest.main()
