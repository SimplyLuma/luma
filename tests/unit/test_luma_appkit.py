#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/luma-platform/appkit"))

from luma_appkit import AppContext, Command, CommandGroup, CommandRegistry  # noqa: E402


class ApplicationKitTests(unittest.TestCase):
    def test_context_uses_capabilities_not_width_or_separate_binary(self) -> None:
        desktop = AppContext.from_environment({"LUMA_DEVICE_CLASS": "desktop"})
        handheld = AppContext.from_environment({"LUMA_DEVICE_CLASS": "handheld"})
        self.assertEqual(desktop.presentation.value, "windowed")
        self.assertEqual(handheld.presentation.value, "fullscreen-mobile")
        self.assertEqual(handheld.input_mode.value, "touch")

    def test_a_group_kept_off_the_menu_still_runs(self) -> None:
        ran = []
        registry = CommandRegistry((
            CommandGroup("Accounts", (Command("mail.all", "All inboxes", lambda: ran.append("all")),)),
            CommandGroup(None, (Command("mail.compose", "New email", lambda: ran.append("new"), shortcut=("Ctrl", "N")),),
                         in_menu=False),
        ))
        self.assertEqual([g.label for g in registry.visible_groups(menu=True)], ["Accounts"])
        self.assertEqual(len(registry.visible_groups()), 2, "shortcuts and actions still see every group")
        self.assertTrue(registry.invoke("mail.compose"))
        self.assertEqual(ran, ["new"])

    def test_command_registry_is_authoritative_and_gated(self) -> None:
        called: list[str] = []
        registry = CommandRegistry(
            (CommandGroup(None, (
                Command("notes.save", "Save", lambda: called.append("save")),
                Command("notes.locked", "Locked", lambda: called.append("bad"), enabled=lambda: False),
            )),)
        )
        self.assertTrue(registry.invoke("notes.save"))
        self.assertFalse(registry.invoke("notes.locked"))
        self.assertEqual(called, ["save"])


if __name__ == "__main__":
    unittest.main()
