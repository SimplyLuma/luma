# SPDX-License-Identifier: MPL-2.0
"""Registration, command resolution and generated units, on a temporary tree."""

import unittest
from datetime import datetime, timezone
from unittest import mock

from pathlib import Path

from luma_background import desktop, ids, units
from luma_background.categories import CATEGORIES
from luma_background.registry import Layout, Registry
from helpers import Tree

DECLARED = """
[background]
agent = "{app}.Agent"
exec = "{program} --agent"
category = "{category}"
wake = ["login", "network", "schedule"]
interval = "15m"
publishes = ["unread"]
"""


class RegistryFixture(unittest.TestCase):
    def setUp(self):
        self.tree = Tree()
        self.addCleanup(self.tree.cleanup)
        # Programs resolve from the tree's own bin directory.
        patcher = mock.patch.object(units, "NATIVE_SEARCH_PATH", (str(self.tree.bin),))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.executable = mock.patch.object(
            units, "_executable",
            lambda path, require_system: path.exists() and (not require_system or self.tree.is_system(path)))
        self.executable.start()
        self.addCleanup(self.executable.stop)

    def registry(self, layout=None):
        return Registry(layout or self.tree.layout, is_system_path=self.tree.is_system)

    def native(self, app="org.projectluma.Messages", category="communication", program="prairie-messages"):
        self.tree.system_paths.add(self.tree.program(program))
        self.tree.desktop(app, program)
        self.tree.declaration(app, DECLARED.format(app=app, program=program, category=category))


class RegistryTests(RegistryFixture):
    def test_signed_ui_role_keeps_native_agent_when_exported_ui_takes_precedence(self):
        app = 'org.projectluma.Messages'
        self.native()
        self.tree.desktop(app, f'/usr/bin/flatpak run --command=prairie-messages {app}', user=True)
        self.tree.desktop(app, f'/usr/bin/flatpak run --command=prairie-messages {app}', flatpak=True)
        with mock.patch('luma_background.registry.native_service_role', return_value=True):
            result = self.registry(self.tree.flatpak_layout()).scan()
        self.assertEqual(result.problems, [])
        record = result.agents[app]
        self.assertEqual(record.origin, 'native')
        self.assertFalse(record.command.sandboxed)
        self.assertEqual(record.command.argv, (str(self.tree.bin / 'prairie-messages'), '--agent'))
        self.assertTrue(record.trusted_install)

    def test_native_service_role_refuses_user_only_declaration_or_missing_host_entry(self):
        app = 'org.projectluma.Messages'
        self.native()
        (self.tree.system / 'applications' / f'{app}.desktop').unlink()
        self.tree.desktop(app, f'/usr/bin/flatpak run --command=other {app}', user=True)
        with mock.patch('luma_background.registry.native_service_role', return_value=True):
            result = self.registry().scan()
        self.assertEqual(result.agents, {})
        self.assertTrue(result.problems)

    def test_native_declaration_registers(self):
        self.native()
        result = self.registry().scan()
        record = result.agents["org.projectluma.Messages"]
        self.assertEqual(result.problems, [])
        self.assertEqual(record.command.argv, (str(self.tree.bin / "prairie-messages"), "--agent"))
        self.assertTrue(record.trusted_install)
        self.assertEqual(record.unit, "app-org.projectluma.Messages-agent.service")
        self.assertEqual(record.category.id, "communication")
        self.assertEqual(record.interval_seconds, 900)

    def test_no_desktop_entry_no_agent(self):
        self.tree.declaration("com.example.Ghost", DECLARED.format(app="com.example.Ghost", program="ghost", category="sync"))
        result = self.registry().scan()
        self.assertEqual(result.agents, {})
        self.assertIn(("com.example.Ghost", "no installed desktop entry"), result.problems)

    def test_exec_must_be_the_desktop_program(self):
        self.tree.program("usage-meter")
        self.tree.program("evil")
        self.tree.desktop("com.example.Usage", "usage-meter %U", user=True)
        self.tree.declaration("com.example.Usage", DECLARED.format(app="com.example.Usage", program="evil", category="sync"), user=True)
        result = self.registry().scan()
        self.assertEqual(result.agents, {})
        self.assertTrue(any("desktop entry's Exec" in problem for _app, problem in result.problems))

    def test_user_declarations_are_never_trusted(self):
        self.tree.program("usage-meter")
        self.tree.desktop("com.example.Usage", "usage-meter", user=True)
        self.tree.declaration("com.example.Usage", DECLARED.format(app="com.example.Usage", program="usage-meter", category="widget-data"), user=True)
        record = self.registry().scan().agents["com.example.Usage"]
        self.assertFalse(record.trusted_install)

    def test_system_helper_binary_allowed_only_for_system_installs(self):
        helper = self.tree.program("messages-agent")
        self.tree.program("prairie-messages")
        self.tree.desktop("org.projectluma.Messages", "prairie-messages")
        self.tree.declaration("org.projectluma.Messages", DECLARED.format(
            app="org.projectluma.Messages", program=str(helper), category="communication"))
        # The helper is not under /usr on this test tree, so it is refused
        # even though the declaration is system-owned.
        result = self.registry().scan()
        self.assertEqual(result.agents, {})

    def test_flatpak_declaration_runs_in_the_sandbox(self):
        app = "com.example.Chat"
        self.tree.desktop(app, f"/usr/bin/flatpak run --branch=stable --arch=x86_64 --command=chat --file-forwarding {app} @@u %U @@", flatpak=True)
        self.tree.declaration(app, DECLARED.format(app=app, program="chat", category="communication"), flatpak=True)
        record = self.registry(self.tree.flatpak_layout()).scan().agents[app]
        self.assertEqual(record.origin, "flatpak")
        self.assertEqual(record.command.argv, ("/usr/bin/flatpak", "run", "--die-with-parent", "--command=chat", app, "--agent"))
        self.assertTrue(record.command.sandboxed)

    def test_flatpak_declaration_cannot_change_command(self):
        app = "com.example.Chat"
        self.tree.desktop(app, f"/usr/bin/flatpak run --command=chat {app}", flatpak=True)
        self.tree.declaration(app, DECLARED.format(app=app, program="other", category="communication"), flatpak=True)
        self.assertEqual(self.registry(self.tree.flatpak_layout()).scan().agents, {})

    def test_portal_autostart_is_rebuilt(self):
        app = "com.example.Weatherish"
        self.tree.desktop(app, f"/usr/bin/flatpak run --command=weatherish {app}", flatpak=True)
        (self.tree.home / ".config/autostart" / f"{app}.desktop").write_text(
            f"[Desktop Entry]\nType=Application\nName=W\nX-XDP-Autostart={app}\nX-Flatpak={app}\n"
            f"Exec=flatpak run --command=weatherish {app} --background\n")
        record = self.registry(self.tree.flatpak_layout()).scan().agents[app]
        self.assertTrue(record.portal_autostart)
        self.assertEqual(record.wake, ("login",))
        self.assertEqual(record.category.id, "other")
        self.assertFalse(record.command.owns_bus_name)
        self.assertEqual(record.command.argv, ("/usr/bin/flatpak", "run", "--die-with-parent", "--command=weatherish", app, "--background"))

    def test_portal_autostart_for_another_app_is_refused(self):
        app = "com.example.Weatherish"
        self.tree.desktop(app, f"/usr/bin/flatpak run {app}", flatpak=True)
        (self.tree.home / ".config/autostart" / f"{app}.desktop").write_text(
            f"[Desktop Entry]\nType=Application\nX-XDP-Autostart={app}\nExec=flatpak run org.evil.App\n")
        result = self.registry(self.tree.flatpak_layout()).scan()
        self.assertEqual(result.agents, {})
        with self.assertRaises(units.CommandRefused):
            units.portal_autostart_command(app, ("/bin/sh", "-c", "x"))

    def test_native_apps_cannot_use_portal_autostart(self):
        self.tree.program("usage-meter")
        self.tree.desktop("com.example.Usage", "usage-meter")
        (self.tree.home / ".config/autostart/com.example.Usage.desktop").write_text(
            "[Desktop Entry]\nType=Application\nX-XDP-Autostart=com.example.Usage\nExec=usage-meter\n")
        self.assertEqual(self.registry().scan().agents, {})


class ReplacedAutostartTests(RegistryFixture):
    """Autostart entries that would start an app its agent already runs for."""

    MESSAGES_FALLBACK = ["Name=Messages", "Exec=prairie-messages --agent --autostart",
                         "X-Luma-Background-Agent=org.projectluma.Messages.Agent"]

    def test_an_apps_system_fallback_entry_naming_its_agent_is_replaced(self):
        self.native()
        self.tree.autostart("org.projectluma.Messages.Agent", self.MESSAGES_FALLBACK)
        result = self.registry().scan()
        self.assertEqual(result.agents["org.projectluma.Messages"].autostart_units,
                         ("app-org.projectluma.Messages.Agent@autostart.service",))
        self.assertEqual(result.problems, [])

    def test_a_native_apps_portal_entry_is_replaced_by_its_declared_agent(self):
        # Clock: a host app the portal (or Clock itself) wrote an autostart entry for.
        self.native(app="org.projectluma.Clock", category="alarms", program="prairie-clock")
        self.tree.autostart("org.projectluma.Clock", [
            "Name=Clock", "Exec=/usr/bin/prairie-clock --gapplication-service", "NoDisplay=true",
            "X-XDP-Autostart=org.projectluma.Clock"], system=False)
        record = self.registry().scan().agents["org.projectluma.Clock"]
        self.assertEqual(record.autostart_units, ("app-org.projectluma.Clock@autostart.service",))
        self.assertEqual(record.command.argv, (str(self.tree.bin / "prairie-clock"), "--agent"))

    def test_the_persons_entry_of_the_same_name_decides(self):
        self.native()
        self.tree.autostart("org.projectluma.Messages.Agent", self.MESSAGES_FALLBACK)
        self.tree.autostart("org.projectluma.Messages.Agent", ["Name=Messages", "Exec=true", "Hidden=true"],
                            system=False)
        self.assertEqual(self.registry().scan().agents["org.projectluma.Messages"].autostart_units, ())

    def test_other_entries_are_left_alone(self):
        self.native()
        self.tree.autostart("com.example.Tool", ["Name=Tool", "Exec=tool"])
        self.tree.autostart("com.example.Other.Agent", ["Name=Other", "Exec=other --agent",
                                                        "X-Luma-Background-Agent=com.example.Other.Agent"])
        # An entry cannot claim a registered app's agent from under another name's portal key.
        self.tree.autostart("com.example.Sneaky", ["Name=S", "Exec=s", "X-XDP-Autostart=org.projectluma.Messages"])
        result = self.registry().scan()
        self.assertEqual(result.agents["org.projectluma.Messages"].autostart_units, ())
        self.assertNotIn("com.example.Other", result.agents)

    def test_unregistered_apps_keep_their_autostart(self):
        self.tree.program("prairie-messages")
        self.tree.desktop("org.projectluma.Messages", "prairie-messages")
        self.tree.autostart("org.projectluma.Messages.Agent", self.MESSAGES_FALLBACK)
        self.assertEqual(self.registry().scan().agents, {})

    def test_names_that_cannot_be_masked_are_reported(self):
        self.native()
        self.tree.autostart("messages agent", self.MESSAGES_FALLBACK)
        result = self.registry().scan()
        self.assertEqual(result.agents["org.projectluma.Messages"].autostart_units, ())
        self.assertTrue(any("cannot be masked" in problem for _path, problem in result.problems))

    def test_unit_names_match_the_generator(self):
        self.assertEqual(ids.autostart_file_unit("org.projectluma.Charlie.Agent"),
                         "app-org.projectluma.Charlie.Agent@autostart.service")
        self.assertEqual(ids.autostart_file_unit("mail-agent"), "app-mail\\x2dagent@autostart.service")
        for bad in ("../evil", "a/b", ".hidden", "x y", ""):
            with self.assertRaises(ids.InvalidIdentifier):
                ids.autostart_file_unit(bad)

    def test_layout_reads_xdg_config_dirs(self):
        layout = Layout.from_environment({"HOME": "/home/p", "XDG_CONFIG_DIRS": "/etc/luma/xdg:relative:/etc/xdg"})
        self.assertEqual(layout.config_dirs, (Path("/etc/luma/xdg"), Path("/etc/xdg")))
        self.assertIn(Path("/etc/luma/xdg/autostart"), layout.watch_directories())
        self.assertEqual(Layout.from_environment({"HOME": "/home/p"}).config_dirs, (Path("/etc/xdg"),))


class UnitTests(unittest.TestCase):
    def spec(self, category="communication", argv=("/usr/bin/prairie-messages", "--agent"), owns=True):
        return units.AgentUnitSpec("org.projectluma.Messages", "Messages", "org.projectluma.Messages.Agent",
                                   units.AgentCommand(argv, False, owns), CATEGORIES[category])

    def test_service_limits_and_identity(self):
        text = units.agent_service(self.spec())
        for line in ("Type=dbus", "BusName=org.projectluma.Messages.Agent",
                     'ExecStart="/usr/bin/prairie-messages" "--agent"',
                     "Slice=luma-background-essential.slice", "MemoryHigh=67108864", "MemoryMax=134217728",
                     "CPUWeight=20", "IOWeight=20", "Restart=on-failure", "RestartSteps=6",
                     "RestartMaxDelaySec=5min", "NoNewPrivileges=yes", "LogExtraFields=LUMA_APP_ID=org.projectluma.Messages",
                     "PartOf=graphical-session.target"):
            self.assertIn(line, text.splitlines(), line)

    def test_mail_gets_more_memory_and_pauses(self):
        text = units.agent_service(self.spec("mail"))
        self.assertIn("MemoryMax=268435456", text)
        self.assertIn("Slice=luma-background-deferrable.slice", text)

    def test_non_agent_program_is_exec_type(self):
        text = units.agent_service(self.spec(owns=False))
        self.assertIn("Type=exec", text)
        self.assertNotIn("BusName", text)

    def test_exec_quoting_neutralises_specifiers(self):
        self.assertEqual(units.quote_exec(("/usr/bin/x", "%h", "$HOME", 'a"b', "c\\d")),
                         '"/usr/bin/x" "%%h" "$$HOME" "a\\"b" "c\\\\d"')
        with self.assertRaises(units.CommandRefused):
            units.quote_exec(("/usr/bin/x", "a\nExecStartPost=/bin/evil"))

    def test_description_cannot_inject_lines(self):
        spec = units.AgentUnitSpec("org.projectluma.Messages", "Mess\nExecStartPre=/bin/evil %h",
                                   "org.projectluma.Messages.Agent",
                                   units.AgentCommand(("/usr/bin/x",), False, True), CATEGORIES["sync"])
        text = units.agent_service(spec)
        self.assertNotIn("\nExecStartPre", text)
        self.assertIn("%%h", text)

    def test_schedule_units(self):
        at = int(datetime(2026, 9, 16, 7, 30, tzinfo=timezone.utc).timestamp())
        files = units.schedule_units("org.projectluma.Clock", "Clock", "alarm-1", at=at, accuracy=1)
        timer = files["app-org.projectluma.Clock-agent-alarm-1.timer"]
        service = files["app-org.projectluma.Clock-agent-alarm-1.service"]
        self.assertIn("OnCalendar=2026-09-16 07:30:00 UTC", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("AccuracySec=1s", timer)
        self.assertIn('"Wake" "ss" "org.projectluma.Clock" "schedule:alarm-1"', service)
        repeating = units.schedule_units("com.example.Usage", "Usage", "interval", every=900)
        self.assertIn("OnUnitActiveSec=900s", repeating["app-com.example.Usage-agent-interval.timer"])
        self.assertIn("RandomizedDelaySec=90s", repeating["app-com.example.Usage-agent-interval.timer"])
        with self.assertRaises(ValueError):
            units.schedule_units("com.example.Usage", "Usage", "fast", every=10)

    def test_flatpak_scope_names(self):
        self.assertTrue(units.is_flatpak_scope_for("app-flatpak-com.example.Chat-123456.scope", "com.example.Chat"))
        self.assertFalse(units.is_flatpak_scope_for("app-flatpak-com.example.ChatX-1.scope", "com.example.Chat"))
        self.assertFalse(units.is_flatpak_scope_for("app-gnome-com.example.Chat-1.scope", "com.example.Chat"))

    def test_desktop_exec_parsing(self):
        self.assertEqual(desktop.parse_exec('"/opt/My App/run" --x "a \\"b\\"" %U'),
                         ("/opt/My App/run", "--x", 'a "b"'))


if __name__ == "__main__":
    unittest.main()
