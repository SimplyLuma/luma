# SPDX-License-Identifier: Apache-2.0
import unittest
from dataclasses import replace
import fakes
from fakes import graph_doc, release

POLICY = 'early-test-nightly-20261009'

class InitialChannelPolicy(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.addCleanup(self.rig.close)
        self.rig.settings = replace(self.rig.settings, initial_channel_policy=POLICY)
        self.rig.backend.list[0] = replace(self.rig.backend.list[0], version='1.0.0-beta.1.1',
                                          origin='luma:luma/1/x86_64/beta')
        self.rig.publish(graph_doc([release('1.0.0-beta.1.1', 1)], channel='beta'), channel='beta')
        self.engine = self.rig.engine()

    def nightly(self, version='1.0.0-nightly.20261009.1', **kwargs):
        self.rig.publish(graph_doc([release(version, 2, **kwargs)], channel='nightly'), channel='nightly')

    def test_signed_ready_nightly_becomes_initial_default_once(self):
        self.nightly()
        decision = self.engine.check()
        self.assertEqual(str(decision.release.version), '1.0.0-nightly.20261009.1')
        self.assertEqual(self.engine.status().channel, 'nightly')
        self.assertEqual(self.engine.store.snapshot()['channel_default_policy'], POLICY)
        self.assertEqual(self.rig.backend.calls, [])  # Check never applies or restarts.

    def test_explicit_beta_choice_survives_restart_and_future_checks(self):
        self.nightly()
        self.engine.check()
        self.engine.set_channel('beta')
        self.engine = self.rig.engine()
        self.assertEqual(self.engine.check().action, "none")
        self.assertEqual(self.engine.status().channel, 'beta')
        self.assertTrue(self.engine.store.snapshot()['channel_chosen_by_person'])

    def test_choice_before_first_check_is_kept_even_when_same_as_origin(self):
        self.nightly()
        self.engine.set_channel('beta')
        self.assertEqual(self.rig.engine().check().action, "none")
        self.assertEqual(self.engine.store.snapshot()['requested_channel'], '')

    def test_missing_old_or_paused_nightly_keeps_working_beta_feed(self):
        for version, kwargs in ((None, {}), ('1.0.0-nightly.20260930.1', {}),
                                ('1.0.0-nightly.20261009.1', {'paused': True})):
            with self.subTest(version=version, kwargs=kwargs):
                if version: self.nightly(version, **kwargs)
                self.assertEqual(self.engine.check().action, "none")
                self.assertEqual(self.engine.status().channel, 'beta')
                self.assertEqual(self.engine.status().last_error_class, '')
                self.assertEqual(self.engine.store.snapshot()['channel_default_policy'], '')
        self.nightly()
        self.assertIsNotNone(self.engine.check())  # Later publication can complete migration.

    def test_bad_signature_cannot_switch_the_channel(self):
        self.nightly()
        url=self.rig.settings.graph_url.format(channel='nightly')
        self.rig.http.files[url + '.minisig'] = b'not a signature'
        self.assertEqual(self.engine.check().action, "none")
        self.assertEqual(self.engine.status().channel, 'beta')
        self.assertEqual(self.engine.store.snapshot()['channel_default_policy'], '')

    def test_disabled_vendor_policy_and_unmanaged_system_are_untouched(self):
        self.nightly()
        self.rig.settings=replace(self.rig.settings, initial_channel_policy='')
        engine=self.rig.engine()
        self.assertEqual(engine.check().action, "none")
        self.assertEqual(engine.status().channel, 'beta')
        self.rig.backend.list[0]=replace(self.rig.backend.list[0], origin='fedora:fedora/44/x86_64/silverblue')
        self.rig.settings=replace(self.rig.settings, initial_channel_policy=POLICY)
        engine=self.rig.engine()
        self.assertIsNone(engine.check())
        self.assertEqual(engine.status().last_error_class, 'unmanaged')
        self.assertEqual(self.rig.backend.calls, [])

if __name__ == '__main__': unittest.main()
