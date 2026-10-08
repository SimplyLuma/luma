# SPDX-License-Identifier: Apache-2.0
"""Background app updates hold back anything that asks for more (ADR-030, section 7)."""

from tempfile import TemporaryDirectory
import unittest

from luma_installer import depot_autoupdate as au

CANVAS = au.Pending('catalog:canvas', 'Canvas', '0.1.1', widens=False, commit='a' * 64, installed_commit='d' * 64)
REEL = au.Pending('catalog:reel', 'Reel', '0.2.0', widens=True, commit='b' * 64, installed_commit='d' * 64)


class Planning(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.env = {'XDG_STATE_HOME': self.dir.name}

    def test_ordinary_updates_install_and_widening_ones_wait(self):
        plan = au.plan([CANVAS, REEL], au.load(self.env), enabled=True)
        self.assertEqual((plan.install, plan.held), (('catalog:canvas',), ('catalog:reel',)))
        self.assertEqual(plan.notify, (REEL,))

    def test_a_held_release_notifies_once(self):
        au.mark_notified([REEL], self.env)
        plan = au.plan([CANVAS, REEL], au.load(self.env), enabled=True)
        self.assertEqual((plan.held, plan.notify), (('catalog:reel',), ()))
        newer = au.Pending('catalog:reel', 'Reel', '0.3.0', widens=True, commit='b' * 64, installed_commit='d' * 64)
        self.assertEqual(au.plan([newer], au.load(self.env), enabled=True).notify, (newer,))

    def test_reviewing_a_release_releases_only_that_release(self):
        au.approve(REEL, self.env)
        state = au.load(self.env)
        self.assertEqual(au.plan([REEL], state, enabled=True).install, ('catalog:reel',))
        newer = au.Pending('catalog:reel', 'Reel', '0.3.0', widens=True, commit='b' * 64, installed_commit='d' * 64)
        self.assertEqual(au.plan([newer], state, enabled=True).held, ('catalog:reel',))

    def test_off_metered_or_power_saving_installs_nothing_but_still_reports_holds(self):
        for kwargs in ({'enabled': False}, {'enabled': True, 'metered': True},
                       {'enabled': True, 'power_saver': True}):
            with self.subTest(**kwargs):
                plan = au.plan([CANVAS, REEL], au.load(self.env), **kwargs)
                self.assertEqual(plan.install, ())
                self.assertEqual(plan.held, ('catalog:reel',))
                self.assertTrue(plan.skipped_reason)

    def test_approval_cannot_follow_another_commit_with_the_same_version(self):
        au.approve(REEL, self.env)
        replacement = au.Pending(REEL.app_id, REEL.name, REEL.release, True, 'c' * 64)
        self.assertEqual(au.plan([replacement], au.load(self.env), enabled=True).held, (REEL.app_id,))

    def test_missing_commit_and_legacy_label_approval_cannot_grant_permissions(self):
        legacy = {'approved': [f'{REEL.app_id}@{REEL.release}'], 'notified': []}
        self.assertTrue(au.is_held(REEL, legacy))
        unknown = au.Pending(REEL.app_id, REEL.name, REEL.release, True)
        with self.assertRaises(ValueError):
            au.approve(unknown, self.env)
        self.assertTrue(au.is_held(unknown, au.load(self.env)))

    def test_manual_updates_are_announced_once_per_actual_build(self):
        plan = au.plan([CANVAS, REEL], au.load(self.env), enabled=False)
        self.assertEqual(plan.available, (CANVAS,))
        au.mark_announced(plan.available, 'available', self.env)
        self.assertEqual(au.plan([CANVAS], au.load(self.env), enabled=False).available, ())
        replacement = au.Pending(CANVAS.app_id, CANVAS.name, CANVAS.release, False, 'c' * 64)
        self.assertEqual(au.plan([replacement], au.load(self.env), enabled=False).available, (replacement,))
        self.assertEqual(au.plan([replacement], au.load(self.env), enabled=True).available, ())

    def test_a_damaged_state_file_holds_everything_that_widens(self):
        path = au.state_path(self.env)
        path.parent.mkdir(parents=True)
        path.write_text('{"approved": "everything"}')
        self.assertEqual(au.plan([REEL], au.load(self.env), enabled=True).held, ('catalog:reel',))


    def test_approval_does_not_cover_another_installed_baseline(self):
        au.approve(REEL, self.env)
        replacement = au.Pending(REEL.app_id, REEL.name, REEL.release, True, REEL.commit, 'e' * 64)
        self.assertTrue(au.is_held(replacement, au.load(self.env)))

if __name__ == '__main__':
    unittest.main()
