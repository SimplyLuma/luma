# SPDX-License-Identifier: Apache-2.0
"""First-boot provisioning (ADR-028, section 14): the state machine."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from luma_installer import depot_provision as p
from luma_installer.depot_catalog import Catalog, CatalogEntry, Collection


def entry(identifier):
    return CatalogEntry(identifier, identifier.title(), 'flatpak', f'org.example.{identifier.title()}',
                        'luma', 'publisher', ('x86_64',), 'https://simplyluma.com/apps/' + identifier,
                        'admitted', 'Reviewed', tier='luma')


CATALOG = Catalog('2026-09-30', tuple(entry(i) for i in ('write', 'grid', 'stage', 'canvas', 'reel')),
                  schema_version=4, collections=(
                      Collection('office', 'Office', '', ('write', 'grid', 'stage')),
                      Collection('creative', 'Creative', '', ('canvas', 'reel', 'darkroom'))))


class Plans(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / 'first-boot-apps.json'

    def write(self, value):
        self.path.write_text(json.dumps(value) if not isinstance(value, str) else value)
        return p.read_plan(self.path)

    def test_a_valid_plan(self):
        plan = self.write({'collections': ['office', 'office'], 'applications': ['canvas'], 'extra': 1})
        self.assertEqual((plan.collections, plan.applications), (('office',), ('canvas',)))
        self.assertEqual(len(plan.digest), 64)

    def test_invalid_plans(self):
        for value in ('[]', '{', {'collections': 'office'}, {'applications': ['../x']},
                      {'applications': [1]}, {'collections': ['a'] * 201}):
            with self.subTest(value=value), self.assertRaises(p.PlanError):
                self.write(value)
        with self.assertRaises(p.PlanError):
            p.read_plan(Path(self.dir.name) / 'absent.json')
        self.path.write_bytes(b' ' * (p.PLAN_MAX_BYTES + 1))
        with self.assertRaises(p.PlanError):
            p.read_plan(self.path)

    def test_resolution_expands_collections_in_order_once(self):
        plan = self.write({'collections': ['office', 'creative', 'games'], 'applications': ['grid', 'reel']})
        ordered, missing = p.resolve(plan, CATALOG)
        self.assertEqual(ordered, ('write', 'grid', 'stage', 'canvas', 'reel'))
        self.assertEqual(missing, ('collection:games', 'darkroom'))


class Machine(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.env = {'XDG_STATE_HOME': self.dir.name}
        self.now = 1000.0
        plan_path = Path(self.dir.name) / 'plan.json'
        plan_path.write_text(json.dumps({'collections': ['office']}))
        self.plan = p.read_plan(plan_path)
        self.installed = set()

    def machine(self, catalog=CATALOG, plan=None):
        return p.Provisioner(plan or self.plan, catalog, environment=self.env, clock=lambda: self.now,
                             is_installed=lambda app_id: app_id in self.installed)

    def test_installs_each_in_order_then_marks_done(self):
        machine = self.machine()
        seen = []
        while True:
            action, value = machine.next_action()
            if action != 'install':
                break
            seen.append(value)
            machine.started(value)
            machine.succeeded(value)
        self.assertEqual(seen, ['write', 'grid', 'stage'])
        self.assertEqual(action, 'finished')
        self.assertTrue(machine.finish())
        self.assertTrue(p.already_done(self.env))

    def test_failures_back_off_then_give_up_for_this_session(self):
        machine = self.machine()
        delays = []
        for _attempt in range(p.MAX_ATTEMPTS):
            action, value = machine.next_action()
            self.assertEqual((action, value), ('install', 'write'))
            machine.started('write')
            machine.failed('write', 'network down')
            record = machine.record('write')
            if record['state'] == p.PENDING:
                delays.append(record['next_attempt'] - self.now)
                # Other apps are not held up behind the one that is failing.
                while (step := machine.next_action())[0] == 'install':
                    self.assertNotEqual(step[1], 'write')
                    machine.started(step[1])
                    machine.succeeded(step[1])
                self.assertEqual(step, ('wait', delays[-1]))
                self.now = record['next_attempt']
        self.assertEqual(delays, [30, 60, 120, 300, 600, 1800, 1800])
        self.assertEqual(machine.record('write')['state'], p.FAILED)
        self.assertEqual(machine.next_action()[0], 'finished')
        self.assertFalse(machine.finish())
        self.assertFalse(p.already_done(self.env))

    def test_waits_when_everything_left_is_backing_off(self):
        machine = self.machine()
        for app_id in ('write', 'grid', 'stage'):
            machine.started(app_id)
            machine.failed(app_id, 'offline')
        self.assertEqual(machine.next_action(), ('wait', 30))

    def test_an_interrupted_session_resumes_without_reinstalling(self):
        machine = self.machine()
        machine.started('write')
        machine.succeeded('write')
        machine.started('grid')  # the session ends here
        resumed = self.machine()
        self.assertEqual(resumed.record('write')['state'], p.INSTALLED)
        self.assertEqual(resumed.record('grid')['state'], p.PENDING)
        self.assertEqual(resumed.next_action(), ('install', 'grid'))

    def test_something_installed_by_hand_is_not_installed_again(self):
        self.installed = {'write', 'grid'}
        machine = self.machine()
        self.assertEqual(machine.next_action(), ('install', 'stage'))
        self.assertEqual(machine.record('write')['state'], p.INSTALLED)

    def test_apps_not_public_yet_keep_provisioning_open_until_they_are(self):
        plan_path = Path(self.dir.name) / 'creative.json'
        plan_path.write_text(json.dumps({'collections': ['creative']}))
        plan = p.read_plan(plan_path)
        machine = self.machine(plan=plan)
        for app_id in ('canvas', 'reel'):
            self.assertEqual(machine.next_action(), ('install', app_id))
            machine.started(app_id)
            machine.succeeded(app_id)
        self.assertEqual(machine.next_action()[0], 'finished')
        self.assertEqual(machine.summary()[p.UNAVAILABLE], ['darkroom'])
        self.assertFalse(machine.finish())
        later = Catalog('2026-10-10', CATALOG.applications + (entry('darkroom'),), 4,
                        collections=CATALOG.collections)
        again = self.machine(catalog=later, plan=plan)
        self.assertEqual(again.next_action(), ('install', 'darkroom'))

    def test_a_new_session_retries_what_failed_but_not_forever(self):
        machine = self.machine()
        for app_id in ('write', 'grid', 'stage'):
            machine.started(app_id)
            machine.failed(app_id, 'refused', permanent=True)
        self.assertFalse(machine.finish())
        for run in range(2, p.MAX_RUNS + 1):
            machine = self.machine()
            self.assertEqual(machine.state['runs'], run)
            self.assertEqual(machine.next_action(), ('install', 'write'))
            for app_id in ('write', 'grid', 'stage'):
                machine.started(app_id)
                machine.failed(app_id, 'refused', permanent=True)
            done = machine.finish()
            self.assertEqual(done, run >= p.MAX_RUNS)

    def test_a_changed_plan_starts_over(self):
        machine = self.machine()
        machine.started('write')
        machine.succeeded('write')
        plan_path = Path(self.dir.name) / 'other.json'
        plan_path.write_text(json.dumps({'applications': ['write', 'canvas']}))
        other = self.machine(plan=p.read_plan(plan_path))
        self.assertEqual(other.record('write')['state'], p.PENDING)
        self.assertEqual(other.state['order'], ['write', 'canvas'])


if __name__ == '__main__':
    unittest.main()
