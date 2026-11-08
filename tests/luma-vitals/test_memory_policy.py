# SPDX-License-Identifier: Apache-2.0
"""Luma's memory policy: warn, contain, close the right app, and say so once by name.

Each scenario is modelled on a real machine: a 32 GB laptop with zram swap
running a dozen browser-engine apps (2026-11-07), where systemd-oomd stopped
the app holding the most swap instead of the one that was running away.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / 'src/luma-vitals'
if SOURCE.exists():
    sys.path.insert(0, str(SOURCE))

from luma_vitals import names  # noqa: E402
from luma_vitals.detectors import Event  # noqa: E402
from luma_vitals.memory_feedback import MemoryFeedback  # noqa: E402
from luma_vitals.memory_policy import GIB, MIB, MemoryPolicy, WATCH_INTERVAL  # noqa: E402
from luma_vitals.sampler import KNOWN_CONTAINERS, MachineSample, UnitSample, container_unit  # noqa: E402

TOTAL = 32 * GIB
SWAP = 16 * GIB


class Bus:
    def __init__(self, focused=frozenset()):
        self.focused = set(focused)
        self.sent = []
        self.withdrawn = []

    def focused_apps(self):
        return None if self.focused is None else set(self.focused)

    def notify(self, summary, body, *, icon='', actions=(), replaces=0):
        identity = replaces or len(self.sent) + 1
        self.sent.append(dict(id=identity, summary=summary, body=body, icon=icon, actions=tuple(actions),
                              replaces=replaces))
        return identity

    def withdraw(self, identity):
        self.withdrawn.append(identity)


class Systemd:
    def __init__(self):
        self.stopped = []
        self.properties = []

    def stop(self, unit):
        self.stopped.append(unit)
        return True

    def set_property(self, unit, **properties):
        self.properties.append((unit, properties))
        return True


class App:
    """One app's unit: RAM, swap and CPU time that the scenario moves along."""

    def __init__(self, unit, memory, swap=0, cpu_percent=0.0, systemd_unit=None):
        self.unit, self.memory, self.swap, self.cpu_percent = unit, memory, swap, cpu_percent
        self.systemd_unit = systemd_unit or unit
        self.cpu_usec = 0

    def advance(self, seconds):
        self.cpu_usec += int(self.cpu_percent / 100 * seconds * 1e6)

    def sample(self):
        return UnitSample(self.unit, self.cpu_usec, self.memory, self.memory, self.swap, 0, 0, 'app.slice',
                          self.systemd_unit)


def machine(t, apps, available, swap_used, full=0.0):
    return MachineSample(time=t, memory_total=TOTAL, memory_available=available, swap_total=SWAP,
                         swap_free=SWAP - swap_used, pressure={'memory': {'some': 0.0, 'full': full}},
                         units=[app.sample() for app in apps])


def describe(unit):
    label = unit.split('-')[2] if unit.startswith('app-gnome-') else unit
    return names.AppName(label.capitalize(), app_id=label)


class Scenario(unittest.TestCase):
    def setUp(self):
        self.bus, self.systemd = Bus(), Systemd()
        self.policy = MemoryPolicy(self.bus, self.systemd, describe)
        self.t = 1_000_000.0
        self.events = []

    def step(self, apps, available, swap_used, seconds=15, full=0.0):
        self.t += seconds
        for app in apps:
            app.advance(seconds)
        events = self.policy.observe(machine(self.t, apps, available, swap_used, full))
        self.events += events
        return events

    def kinds(self, kind):
        return [event for event in self.events if event.kind == kind]

    def assert_told_before_closing(self, unit, name):
        """The last warning names the app with both buttons, and nothing is closed yet."""
        last = [event for event in self.kinds('memory-warning') if event.details.get('last')]
        self.assertEqual([event.unit for event in last], [unit])
        self.assertEqual(self.systemd.stopped, [])
        notice = self.bus.sent[-1]
        self.assertEqual(notice['summary'], f'{name} will be closed to free memory')
        self.assertEqual(notice['actions'], ('close', f'Close {name}', 'keep', 'Keep Open'))
        return notice


class LeakingBackgroundApp(Scenario):
    def test_runaway_is_warned_then_contained_then_closed_and_focused_app_survives(self):
        editor = App('app-gnome-editor-1.scope', 6 * GIB, cpu_percent=30)
        chat = App('app-gnome-chat-2.scope', 2 * GIB, cpu_percent=2)
        self.bus.focused = {'editor'}
        apps = [editor, chat]
        # Ten minutes of chat leaking 400 MB a minute while the person writes.
        for _ in range(40):
            chat.memory += 100 * MIB
            self.step(apps, available=12 * GIB, swap_used=4 * GIB)
        warnings = self.kinds('memory-warning')
        self.assertEqual({event.unit for event in warnings}, {'app-gnome-chat-2.scope'})
        # Growing on, it updates the one notice in place.
        self.assertEqual({sent['id'] for sent in self.bus.sent}, {self.bus.sent[0]['id']})
        notice = self.bus.sent[-1]
        self.assertEqual(notice['summary'], 'Chat is using a lot of memory')
        self.assertIn('still growing', notice['body'])
        self.assertEqual(notice['actions'], ('close', 'Close Chat', 'keep', 'Keep Open'))
        contained = self.kinds('memory-contained')
        self.assertEqual([event.unit for event in contained], ['app-gnome-chat-2.scope'])
        ceiling = contained[0].details['memory_high']
        self.assertGreaterEqual(ceiling, int(TOTAL * 0.10))
        self.assertIn(('app-gnome-chat-2.scope', {'MemoryHigh': str(ceiling)}), self.systemd.properties)
        # The focused editor is protected from systemd-oomd too, and never contained.
        self.assertIn(('app-gnome-editor-1.scope', {'ManagedOOMPreference': 'avoid'}), self.systemd.properties)
        self.assertFalse(any(unit == 'app-gnome-editor-1.scope' and 'MemoryHigh' in props
                             for unit, props in self.systemd.properties))
        self.assertEqual(self.systemd.stopped, [])
        # Held back, it fills swap instead, until memory and swap are both nearly full.
        for _ in range(3):
            chat.swap += 2 * GIB
            self.step(apps, available=2 * GIB, swap_used=14 * GIB, seconds=WATCH_INTERVAL)
        self.assertEqual(self.systemd.stopped, ['app-gnome-chat-2.scope'])
        closed = self.bus.sent[-1]
        self.assertEqual(closed['summary'], 'Chat was closed to free memory')
        # It replaces the warning: one notification for the one app.
        self.assertEqual(closed['replaces'], notice['id'])
        self.assertEqual([event.unit for event in self.kinds('memory-reclaimed')], ['app-gnome-chat-2.scope'])
        # Gone from the next sample, its notice stays.
        apps.remove(chat)
        self.step(apps, available=10 * GIB, swap_used=4 * GIB)
        self.assertEqual(self.bus.withdrawn, [])

    def test_focused_app_is_never_closed_even_when_it_is_the_largest(self):
        game = App('app-gnome-game-1.scope', 14 * GIB, swap=6 * GIB, cpu_percent=90)
        self.bus.focused = {'game'}
        for _ in range(6):
            self.step([game], available=1 * GIB, swap_used=15 * GIB, seconds=WATCH_INTERVAL)
        self.assertEqual(self.systemd.stopped, [])
        # Told once, never contained: it is the app in use.
        self.assertEqual(len(self.kinds('memory-warning')), 1)
        self.assertEqual(self.kinds('memory-contained'), [])

    def test_recently_used_app_is_protected_for_ten_minutes(self):
        browser = App('app-gnome-browser-1.scope', 5 * GIB, swap=3 * GIB)
        notes = App('app-gnome-notes-2.scope', 1 * GIB)
        self.bus.focused = {'browser'}
        self.step([browser, notes], available=8 * GIB, swap_used=6 * GIB)
        self.bus.focused = {'notes'}
        for _ in range(4):
            self.step([browser, notes], available=1 * GIB, swap_used=15 * GIB, seconds=WATCH_INTERVAL)
        self.assertEqual(self.systemd.stopped, [])
        self.t += 11 * 60
        self.step([browser, notes], available=1 * GIB, swap_used=15 * GIB, seconds=WATCH_INTERVAL)
        # Its earlier warning is stale: it is told again, with time to keep it.
        warning = self.assert_told_before_closing('app-gnome-browser-1.scope', 'Browser')
        for _ in range(3):
            self.step([browser, notes], available=1 * GIB, swap_used=15 * GIB, seconds=WATCH_INTERVAL)
        self.assertEqual(self.systemd.stopped, ['app-gnome-browser-1.scope'])
        self.assertEqual(self.bus.sent[-1]['replaces'], warning['id'])
        self.assertIn(('app-gnome-browser-1.scope', {'ManagedOOMPreference': 'none'}), self.systemd.properties)


class ManyAppsAtOnce(Scenario):
    def test_no_close_and_no_warning_while_there_is_headroom(self):
        apps = [App(f'app-gnome-app{n}-{n}.scope', 1536 * MIB, swap=400 * MIB, cpu_percent=n % 3)
                for n in range(14)]
        self.bus.focused = {'app0'}
        for _ in range(60):
            self.step(apps, available=7 * GIB, swap_used=6 * GIB)
        # Swap past 80% alone, with RAM to spare, is not a reason to close anything either.
        for _ in range(10):
            self.step(apps, available=9 * GIB, swap_used=int(15.5 * GIB), seconds=WATCH_INTERVAL)
        self.assertEqual(self.systemd.stopped, [])
        self.assertEqual(self.bus.sent, [])
        self.assertEqual([event for event in self.events if event.kind != 'memory-contained'], [])

    def test_a_large_app_is_told_once_not_every_hour(self):
        browser = App('app-gnome-browser-1.scope', 9 * GIB, cpu_percent=10)
        self.bus.focused = {'browser'}
        for _ in range(4 * 240):
            self.step([browser], available=12 * GIB, swap_used=2 * GIB)
        self.assertEqual(len(self.bus.sent), 1)

    def test_keep_open_on_the_last_warning_spares_it(self):
        store = App('app-gnome-store-2.scope', 2 * GIB, swap=1 * GIB)
        for _ in range(24):
            self.step([store], available=6 * GIB, swap_used=8 * GIB)
        for _ in range(2):
            self.step([store], available=2 * GIB, swap_used=14 * GIB, seconds=WATCH_INTERVAL)
        notice = self.assert_told_before_closing('app-gnome-store-2.scope', 'Store')
        self.policy.answer(notice['id'], 'keep')
        for _ in range(6):
            self.step([store], available=2 * GIB, swap_used=14 * GIB, seconds=WATCH_INTERVAL)
        self.assertEqual(self.systemd.stopped, [])

    def test_one_tight_sample_is_not_enough(self):
        apps = [App('app-gnome-a-1.scope', 3 * GIB), App('app-gnome-b-2.scope', 3 * GIB)]
        self.step(apps, available=8 * GIB, swap_used=4 * GIB)
        self.step(apps, available=1 * GIB, swap_used=15 * GIB, seconds=WATCH_INTERVAL)
        self.step(apps, available=6 * GIB, swap_used=10 * GIB, seconds=WATCH_INTERVAL)
        self.step(apps, available=1 * GIB, swap_used=15 * GIB, seconds=WATCH_INTERVAL)
        self.assertEqual(self.systemd.stopped, [])

    def test_samples_faster_when_memory_runs_short(self):
        self.assertEqual(self.policy.interval(machine(0, [], 12 * GIB, 2 * GIB), 15.0), 15.0)
        self.assertEqual(self.policy.interval(machine(0, [], 5 * GIB, 2 * GIB), 15.0), WATCH_INTERVAL)
        self.assertEqual(self.policy.interval(machine(0, [], 12 * GIB, 11 * GIB), 15.0), WATCH_INTERVAL)


class SwapHeavyIdleApps(Scenario):
    def test_idle_apps_are_reclaimed_first_one_at_a_time(self):
        busy = App('app-gnome-builder-1.scope', 2560 * MIB, cpu_percent=40)
        idle_large = App('app-gnome-store-2.scope', 512 * MIB, swap=1536 * MIB)
        idle_small = App('app-gnome-photos-3.scope', 256 * MIB, swap=1024 * MIB)
        focused = App('app-gnome-browser-4.scope', 8 * GIB, swap=2 * GIB, cpu_percent=20)
        self.bus.focused = {'browser'}
        apps = [busy, idle_large, idle_small, focused]
        for _ in range(24):
            self.step(apps, available=6 * GIB, swap_used=8 * GIB)
        for _ in range(2):
            self.step(apps, available=2 * GIB, swap_used=14 * GIB, seconds=WATCH_INTERVAL)
        self.assert_told_before_closing('app-gnome-store-2.scope', 'Store')
        for _ in range(3):
            self.step(apps, available=2 * GIB, swap_used=14 * GIB, seconds=WATCH_INTERVAL)
        self.assertEqual(self.systemd.stopped, ['app-gnome-store-2.scope'])
        apps.remove(idle_large)
        # Still tight after the cooldown: the next idle one, never the busy one first.
        for _ in range(12):
            self.step(apps, available=2 * GIB, swap_used=14 * GIB, seconds=WATCH_INTERVAL)
        self.assertEqual(self.systemd.stopped[:2], ['app-gnome-store-2.scope', 'app-gnome-photos-3.scope'])
        self.assertNotIn('app-gnome-browser-4.scope', self.systemd.stopped)

    def test_the_real_hog_goes_before_a_small_idle_app(self):
        sync = App('app-gnome-sync-1.scope', 3 * GIB, swap=4 * GIB, cpu_percent=15)
        clock = App('app-gnome-clock-2.scope', 300 * MIB)
        browser = App('app-gnome-browser-3.scope', 6 * GIB, cpu_percent=20)
        self.bus.focused = {'browser'}
        apps = [sync, clock, browser]
        for _ in range(24):
            self.step(apps, available=6 * GIB, swap_used=8 * GIB)
        for _ in range(5):
            self.step(apps, available=2 * GIB, swap_used=14 * GIB, seconds=WATCH_INTERVAL)
        self.assertEqual(self.systemd.stopped, ['app-gnome-sync-1.scope'])

    def test_without_focus_only_quiet_apps_are_candidates(self):
        self.bus.focused = None
        busy = App('app-gnome-builder-1.scope', 6 * GIB, cpu_percent=40)
        quiet = App('app-gnome-store-2.scope', 1 * GIB, swap=1 * GIB)
        for _ in range(24):
            self.step([busy, quiet], available=6 * GIB, swap_used=8 * GIB)
        for _ in range(5):
            self.step([busy, quiet], available=2 * GIB, swap_used=14 * GIB, seconds=WATCH_INTERVAL)
        self.assertEqual(self.systemd.stopped, ['app-gnome-store-2.scope'])
        # Without focus nothing is contained: the growing app may be the one in use.
        self.assertEqual(self.kinds('memory-contained'), [])

    def test_a_memory_stall_counts_even_with_swap_left(self):
        idle = App('app-gnome-store-2.scope', 2 * GIB, swap=1 * GIB)
        for _ in range(24):
            self.step([idle], available=6 * GIB, swap_used=4 * GIB)
        for _ in range(5):
            self.step([idle], available=6 * GIB, swap_used=4 * GIB, seconds=WATCH_INTERVAL, full=35.0)
        self.assertEqual(self.systemd.stopped, ['app-gnome-store-2.scope'])


class PersonAnswers(Scenario):
    def leak(self):
        chat = App('app-gnome-chat-2.scope', 4 * GIB)
        for _ in range(40):
            chat.memory += 100 * MIB
            self.step([chat], available=12 * GIB, swap_used=4 * GIB)
        return chat

    def test_keep_open_lifts_the_ceiling_and_protects_it(self):
        chat = self.leak()
        notice = self.bus.sent[0]['id']
        self.assertEqual([event.kind for event in self.policy.answer(notice, 'keep')], ['memory-kept'])
        self.assertIn(('app-gnome-chat-2.scope', {'MemoryHigh': '60%'}), self.systemd.properties)
        for _ in range(4):
            chat.memory += 100 * MIB
            self.step([chat], available=1 * GIB, swap_used=15 * GIB, seconds=WATCH_INTERVAL)
        self.assertEqual(self.systemd.stopped, [])
        self.assertEqual(len(self.bus.sent), 1)

    def test_close_stops_it_now(self):
        self.leak()
        notice = self.bus.sent[0]['id']
        self.assertEqual([event.kind for event in self.policy.answer(notice, 'close')], ['memory-closed-by-person'])
        self.assertEqual(self.systemd.stopped, ['app-gnome-chat-2.scope'])
        self.assertEqual(self.policy.answer(999, 'close'), [])


class SingleNotificationByName(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        applications = root / 'share/applications'
        applications.mkdir(parents=True)
        for desktop_id, name in [('com.example.Chat', 'Chat Pro'),
                                 ('org.projectluma.Installed.deb-helper-desk-0123456789ab', 'Helper Desk'),
                                 ('org.example.Two-Words', 'Two Words')]:
            (applications / f'{desktop_id}.desktop').write_text(
                f'[Desktop Entry]\nType=Application\nName={name}\nIcon={desktop_id}\nExec=true\n')
        self.environment = {key: os.environ.get(key) for key in ('XDG_DATA_DIRS', 'XDG_DATA_HOME')}
        os.environ['XDG_DATA_DIRS'] = str(root / 'share')
        os.environ['XDG_DATA_HOME'] = str(root / 'home')
        self.addCleanup(self.restore)

    def restore(self):
        for key, value in self.environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.directory.cleanup()

    def test_every_unit_form_is_named_by_its_app(self):
        cases = {
            'app-flatpak-com.example.Chat-1601334635.scope': 'Chat Pro',
            'app-gnome-com.example.Chat-4242.scope': 'Chat Pro',
            'app-com.example.Chat@a1b2c3.service': 'Chat Pro',
            'dbus-:1.2-com.example.Chat@0.service': 'Chat Pro',
            'app-gnome-org.example.Two\\x2dWords-77.scope': 'Two Words',
            'luma-run-deb-helper-desk-0123456789ab.container': 'Helper Desk',
            # No desktop entry: the id still says what it was, never the raw id.
            'app-flatpak-com.rhyme.viola-2549595592.scope': 'Viola',
            'luma-run-deb-claude-desktop-b251a0224a86.container': 'Claude Desktop',
            'libpod-2d7301e254233db25445b9e96579a92bf604b03ba25e8762d615233b3a5101d0.scope': 'An app',
            'luma-dev-env-20260922192102.service': 'A background task',
        }
        for unit, expected in cases.items():
            with self.subTest(unit=unit):
                self.assertEqual(names.display_name(unit), expected)
        described = names.describe('app-flatpak-com.example.Chat-1.scope')
        self.assertEqual((described.icon, described.desktop_id), ('com.example.Chat', 'com.example.Chat.desktop'))

    def test_a_stopped_container_is_named_by_what_it_ran(self):
        KNOWN_CONTAINERS['2d7301e25423'] = 'luma-run-deb-helper-desk-0123456789ab'
        self.addCleanup(KNOWN_CONTAINERS.pop, '2d7301e25423', None)
        unit = 'libpod-2d7301e254233db25445b9e96579a92bf604b03ba25e8762d615233b3a5101d0.scope'
        self.assertEqual(names.display_name(container_unit(unit)), 'Helper Desk')
        bus = Bus()
        MemoryFeedback(notifier=bus, clock=lambda: 100.0).observe(
            [Event('out-of-memory', unit, 'x', {})])
        self.assertEqual(len(bus.sent), 1)
        self.assertEqual(bus.sent[0]['summary'], 'Helper Desk closed because memory ran out')
        self.assertNotIn('libpod', bus.sent[0]['body'])

    def test_oomd_kill_says_the_name_once(self):
        bus = Bus()
        feedback = MemoryFeedback(notifier=bus, clock=lambda: 100.0)
        event = Event('out-of-memory', 'app-flatpak-com.rhyme.viola-2549595592.scope', 'x', {})
        feedback.observe([event])
        feedback.observe([event])
        self.assertEqual([sent['summary'] for sent in bus.sent], ['Viola closed because memory ran out'])

    def test_oomd_kill_is_left_to_housekeeping_when_it_runs(self):
        # The image's housekeeping plugin names an oomd kill from Monitor too
        # (gnome-settings-daemon patch 0002); two notices for one app is the bug.
        bus = Bus()
        feedback = MemoryFeedback(notifier=bus, clock=lambda: 100.0, kill_notices_elsewhere=lambda: True)
        feedback.observe([Event('out-of-memory', 'app-flatpak-com.rhyme.viola-2549595592.scope', 'x', {})])
        feedback.observe([Event('memory-pressure', 'machine', 'x', {})])
        self.assertEqual(bus.sent, [])

    def test_no_pressure_notice_on_top_of_the_policy_notice(self):
        bus = Bus()
        feedback = MemoryFeedback(notifier=bus, clock=lambda: 1000.0)
        feedback.observe([Event('memory-pressure', 'machine', 'x', {}),
                          Event('memory-reclaimed', 'app-gnome-chat-2.scope', 'x', {})])
        self.assertEqual(bus.sent, [])

    def test_policy_notice_uses_the_display_name_and_icon(self):
        bus, systemd = Bus(), Systemd()
        policy = MemoryPolicy(bus, systemd)
        app = App('app-flatpak-com.example.Chat-77.scope', 9 * GIB)
        policy.observe(machine(1.0, [app], 12 * GIB, 2 * GIB))
        self.assertEqual(len(bus.sent), 1)
        self.assertEqual(bus.sent[0]['summary'], 'Chat Pro is using a lot of memory')
        self.assertEqual(bus.sent[0]['icon'], 'com.example.Chat')
        self.assertNotIn('com.example', bus.sent[0]['body'])


if __name__ == '__main__':
    unittest.main()
