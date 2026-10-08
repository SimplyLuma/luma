"""Luma Connect's daemon under the ADR-033 agent contract."""
import configparser
from pathlib import Path
import tempfile
import unittest

from luma_continuity import agent

DATA = Path(__file__).resolve().parents[1] / 'data'


def ini(path):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read_string(path.read_text())
    return parser


class ServiceFileTests(unittest.TestCase):
    def test_dbus_activation_goes_through_systemd(self):
        service = ini(DATA / 'org.projectluma.Connect1.service')['D-BUS Service']
        self.assertEqual(service['SystemdService'], 'luma-connect.service')
        unit = ini(DATA / 'luma-connect.service')['Service']
        self.assertEqual((unit['Type'], unit['BusName']), ('dbus', 'org.projectluma.Connect1'))
        self.assertEqual(unit['ExecStart'], '/usr/bin/luma-connect-daemon')

    def test_no_agent_unit_is_shipped(self):
        # luma-background generates the agent's unit for an allowed app.
        self.assertEqual(sorted(path.name for path in DATA.glob('app-*')), [])
        self.assertFalse((DATA / 'org.projectluma.Connect.Agent.service').exists())

    def test_declaration_and_autostart_agree(self):
        import tomllib
        declaration = tomllib.loads((DATA / 'org.projectluma.Connect.toml').read_text())
        background = declaration['background']
        self.assertEqual(declaration['application']['id'], 'org.projectluma.Connect')
        self.assertEqual(background['category'], 'communication')
        self.assertEqual(background['exec'], '/usr/bin/luma-connect-daemon --agent')
        entry = ini(DATA / 'org.projectluma.Connect.Agent.desktop')['Desktop Entry']
        self.assertEqual(entry['Exec'], 'luma-connect-daemon --agent --autostart')
        self.assertEqual(entry['X-Luma-Background-Agent'], background['agent'])


class WakeTests(unittest.TestCase):
    def test_resume_starts_hub_sync_and_restarts_its_watcher_for_an_enrolled_device(self):
        calls = []
        kick = agent.SyncKick(lambda method, unit: calls.append((method, unit)), enrolled=lambda: True)
        self.assertTrue(kick())
        self.assertEqual(calls, [('StartUnit', 'luma-connect-sync.service'),
                                 ('TryRestartUnit', 'luma-connect-sync-watch.service')])

    def test_nothing_to_sync_without_a_hub(self):
        calls = []
        kick = agent.SyncKick(lambda method, unit: calls.append(method), enrolled=lambda: False)
        self.assertFalse(kick())
        self.assertEqual(calls, [])

    def test_a_failing_unit_call_does_not_stop_the_other(self):
        calls = []

        def call(method, unit):
            calls.append(method)
            if method == 'StartUnit':
                raise RuntimeError('no such unit')
        agent.SyncKick(call, enrolled=lambda: True)()
        self.assertEqual(calls, ['StartUnit', 'TryRestartUnit'])

    def test_enrolment_is_the_hub_device_file(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertFalse(agent.enrolled_with_hub(home))
            path = Path(home) / '.local/share/luma/connect/device.json'
            path.parent.mkdir(parents=True)
            path.write_text('{}')
            self.assertTrue(agent.enrolled_with_hub(home))

    def test_only_the_agent_command_line_asks_for_the_agent(self):
        self.assertTrue(agent.agent_requested(['luma-connect-daemon', '--agent']))
        self.assertFalse(agent.agent_requested(['luma-connect-daemon']))

    def test_without_the_contract_the_daemon_is_left_as_it_was(self):
        class Daemon:
            latest = {}
        original = agent._contract
        agent._contract = lambda: None
        try:
            connect = agent.ConnectAgent(Daemon(), connection=None)
            connect.start(); connect.update(); connect.close()
            self.assertIsNone(connect.publisher)
            self.assertFalse(agent.autostart_steps_aside(['luma-connect-daemon', '--agent', '--autostart'], None))
        finally:
            agent._contract = original


if __name__ == '__main__':
    unittest.main()
