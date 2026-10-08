# SPDX-License-Identifier: MPL-2.0
"""Open at Login: one answer per app from every autostart source, and the writes that change it."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from helpers import Tree

from luma_background import desktop, units
from luma_background.login import LoginItems, LoginItemError
from luma_background.registry import Registry

DECLARED = """
[background]
agent = "{app}.Agent"
exec = "{program} --agent"
category = "communication"
wake = ["login"]
"""


class LoginFixture(unittest.TestCase):
    def setUp(self):
        self.tree = Tree()
        self.addCleanup(self.tree.cleanup)
        self.agents = {}
        self.allowed = {}
        self.calls = []
        self.environ = {"XDG_CURRENT_DESKTOP": "GNOME", "PATH": str(self.tree.bin)}

    def items(self, layout=None) -> LoginItems:
        return LoginItems(layout or self.tree.layout, lambda: self.agents,
                          lambda app: self.allowed.get(app, False), dict(self.environ))

    def set(self, items, app, enabled):
        return items.set_enabled(app, enabled, set_agent_allowed=lambda a, b: self.calls.append((a, b)))

    def user_file(self, stem) -> Path:
        return self.tree.home / ".config/autostart" / f"{stem}.desktop"

    def values(self, path) -> dict:
        return desktop.read_group(path)


class ReadingTests(LoginFixture):
    def test_app_without_any_entry_is_off(self):
        self.tree.desktop("org.example.Notes", "notes")
        item = self.items().describe("org.example.Notes")
        self.assertFalse(item["enabled"])
        self.assertEqual(item["controlled-by"], "autostart")
        self.assertTrue(item["available"])

    def test_system_entry_is_found_by_program_under_another_name(self):
        self.tree.desktop("com.discordapp.Discord", "/usr/bin/discord %U")
        self.tree.autostart("discord", ["Name=Discord", "Exec=/usr/bin/discord --start-minimized"])
        item = self.items().describe("com.discordapp.Discord")
        self.assertTrue(item["enabled"])
        self.assertEqual(item["sources"], ["autostart-system"])

    def test_user_hidden_override_wins_over_system_entry(self):
        self.tree.desktop("org.example.Sync", "sync-app")
        self.tree.autostart("org.example.Sync", ["Exec=sync-app"])
        self.tree.autostart("org.example.Sync", ["Exec=sync-app", "Hidden=true"], system=False)
        self.assertFalse(self.items().describe("org.example.Sync")["enabled"])

    def test_generator_rules_decide_whether_an_entry_runs(self):
        self.tree.desktop("org.example.Kde", "kde-thing")
        self.tree.autostart("org.example.Kde", ["Exec=kde-thing", "OnlyShowIn=KDE;"])
        self.assertFalse(self.items().describe("org.example.Kde")["enabled"])
        self.tree.desktop("org.example.Off", "off-thing")
        self.tree.autostart("org.example.Off", ["Exec=off-thing", "X-GNOME-Autostart-enabled=false"])
        self.assertFalse(self.items().describe("org.example.Off")["enabled"])
        self.tree.desktop("org.example.Gone", "gone-thing")
        self.tree.autostart("org.example.Gone", ["Exec=gone-thing", "TryExec=not-installed-anywhere"])
        self.assertFalse(self.items().describe("org.example.Gone")["enabled"])

    def test_flatpak_entry_matches_its_app_not_another_flatpak(self):
        self.tree.desktop("org.example.Chat", "/usr/bin/flatpak run --branch=stable org.example.Chat", flatpak=True)
        layout = self.tree.flatpak_layout()
        self.tree.autostart("other", ["Exec=/usr/bin/flatpak run org.example.Other"], system=False)
        self.assertFalse(self.items(layout).describe("org.example.Chat")["enabled"])
        self.tree.autostart("chat-login", ["Exec=/usr/bin/flatpak run org.example.Chat --hidden"], system=False)
        self.assertTrue(self.items(layout).describe("org.example.Chat")["enabled"])

    def test_list_shows_every_app_that_starts_on_its_own(self):
        self.tree.desktop("org.example.A", "a-app")
        self.tree.autostart("org.example.A", ["Exec=a-app"])
        self.tree.autostart("org.example.B", ["Exec=b-app", "Hidden=true"])
        listed = [item["app-id"] for item in self.items().list()]
        self.assertEqual(listed, ["org.example.A"])

    def test_bad_names_are_refused(self):
        with self.assertRaises(LoginItemError):
            self.items().describe("../etc/passwd")


class PortalAndAgentTests(LoginFixture):
    def portal_app(self):
        self.tree.program("flatchat")
        self.tree.desktop("org.example.Flatchat", "/usr/bin/flatpak run org.example.Flatchat", flatpak=True)
        self.tree.autostart("org.example.Flatchat", [
            "X-XDP-Autostart=org.example.Flatchat", "X-Flatpak=org.example.Flatchat",
            "Exec=flatpak run --command=flatchat org.example.Flatchat --background"], system=False)
        layout = self.tree.flatpak_layout()
        scan = Registry(layout, is_system_path=self.tree.is_system).scan(["org.example.Flatchat"])
        self.agents = scan.agents
        return layout

    def test_portal_autostart_is_the_agent_and_its_decision_is_the_switch(self):
        layout = self.portal_app()
        self.assertIn("org.example.Flatchat", self.agents)
        item = self.items(layout).describe("org.example.Flatchat")
        self.assertEqual(item["controlled-by"], "background")
        self.assertFalse(item["enabled"])
        self.allowed["org.example.Flatchat"] = True
        self.assertTrue(self.items(layout).describe("org.example.Flatchat")["enabled"])
        self.set(self.items(layout), "org.example.Flatchat", False)
        self.assertEqual(self.calls, [("org.example.Flatchat", False)])
        # The portal's file is the portal's: the switch never rewrites it.
        self.assertNotIn("Hidden", self.values(self.user_file("org.example.Flatchat")))

    def native_agent(self, app, program):
        self.tree.system_paths.add(self.tree.program(program))
        self.tree.desktop(app, program)
        self.tree.declaration(app, DECLARED.format(app=app, program=program))

    def scan(self, *extra):
        with mock.patch.object(units, "NATIVE_SEARCH_PATH", (str(self.tree.bin),)), \
                mock.patch.object(units, "_executable",
                                  lambda path, require_system: path.exists()
                                  and (not require_system or self.tree.is_system(path))):
            return Registry(self.tree.layout, is_system_path=self.tree.is_system).scan(*extra).agents

    def test_agent_fallback_entry_is_not_a_second_login_item(self):
        self.native_agent("org.example.Chatter", "chatter")
        self.tree.autostart("org.example.Chatter.Agent", [
            "Exec=chatter --agent", "X-Luma-Background-Agent=org.example.Chatter.Agent"])
        self.agents = self.scan()
        self.assertTrue(self.agents["org.example.Chatter"].autostart_units)
        item = self.items().describe("org.example.Chatter")
        self.assertEqual(item["controlled-by"], "autostart")
        self.assertFalse(item["enabled"])

    def test_native_portal_entry_absorbed_by_its_agent_uses_the_agent_switch(self):
        # Clock: the portal wrote an autostart entry, and its declared agent runs instead.
        self.native_agent("org.example.Clock", "clock")
        self.tree.autostart("org.example.Clock", ["Exec=/usr/bin/clock --gapplication-service",
                                                  "X-XDP-Autostart=org.example.Clock"], system=False)
        self.agents = self.scan()
        self.allowed["org.example.Clock"] = True
        item = self.items().describe("org.example.Clock")
        self.assertEqual((item["controlled-by"], item["enabled"]), ("background", True))


class WritingTests(LoginFixture):
    def test_turning_on_an_app_with_no_entry_writes_one_from_its_desktop_entry(self):
        self.tree.desktop("org.example.Notes", "notes --new-note")
        item = self.set(self.items(), "org.example.Notes", True)
        self.assertTrue(item["enabled"])
        values = self.values(self.user_file("org.example.Notes"))
        self.assertEqual(values["Exec"], "notes --new-note")
        self.assertEqual(values["X-Luma-App"], "org.example.Notes")
        self.assertFalse(self.set(self.items(), "org.example.Notes", False)["enabled"])
        self.assertEqual(self.values(self.user_file("org.example.Notes"))["Hidden"], "true")
        self.assertTrue(self.set(self.items(), "org.example.Notes", True)["enabled"])

    def test_system_entry_is_overridden_then_restored(self):
        self.tree.desktop("org.example.Sync", "sync-app")
        system = self.tree.autostart("org.example.Sync", ["Name=Sync", "Exec=sync-app --tray", "[Desktop Action x]", "Name=X"])
        before = system.read_text()
        self.assertFalse(self.set(self.items(), "org.example.Sync", False)["enabled"])
        self.assertEqual(system.read_text(), before)
        override = self.values(self.user_file("org.example.Sync"))
        self.assertEqual((override["Hidden"], override["Exec"]), ("true", "sync-app --tray"))
        self.assertTrue(self.set(self.items(), "org.example.Sync", True)["enabled"])
        self.assertFalse(self.user_file("org.example.Sync").exists())

    def test_app_disabled_its_own_entry_is_turned_back_on_in_place(self):
        self.tree.desktop("org.example.Tray", "tray")
        self.tree.autostart("tray", ["Exec=tray", "X-GNOME-Autostart-enabled=false", "Comment=keep me"], system=False)
        self.assertTrue(self.set(self.items(), "org.example.Tray", True)["enabled"])
        values = self.values(self.user_file("tray"))
        self.assertEqual((values["X-GNOME-Autostart-enabled"], values["Comment"]), ("true", "keep me"))
        self.assertFalse((self.tree.home / ".config/autostart/org.example.Tray.desktop").exists())

    def test_a_launcher_shared_by_several_apps_matches_none_of_them(self):
        self.tree.desktop("org.example.One", "/usr/bin/gjs-app one")
        self.tree.desktop("org.example.Two", "/usr/bin/gjs-app two")
        self.tree.autostart("two-helper", ["Exec=/usr/bin/gjs-app two --hidden"])
        self.assertFalse(self.items().describe("org.example.One")["enabled"])
        self.assertFalse(self.items().describe("org.example.Two")["enabled"])

    def test_every_source_that_starts_it_is_turned_off(self):
        self.tree.desktop("com.discordapp.Discord", "/usr/bin/discord")
        self.tree.autostart("discord", ["Exec=/usr/bin/discord"], system=False)
        self.tree.autostart("com.discordapp.Discord", ["Exec=/usr/bin/discord --minimized"])
        self.assertFalse(self.set(self.items(), "com.discordapp.Discord", False)["enabled"])

    def test_an_app_that_is_not_installed_cannot_be_turned_on(self):
        with self.assertRaises(LoginItemError):
            self.set(self.items(), "org.example.Missing", True)


if __name__ == "__main__":
    unittest.main()
