# SPDX-License-Identifier: Apache-2.0
"""Reading luma-updated's state (ADR-030, section 6) without inventing any of it."""

import json
import unittest

from luma_installer import depot_system_update as su


NIGHTLY = su.parse_os_release(
    'NAME=Luma\nVERSION="Prairie, Beta 0, Nightly 20260916"\nID=luma\nVERSION_ID=1\n'
    'VERSION_CODENAME=prairie\nPRETTY_NAME="Luma (Prairie, Beta 0, Nightly 20260916)"\n'
    'LUMA_RELEASE_CHANNEL=nightly\nLUMA_RELEASE_STAGE=beta\nLUMA_RELEASE_STAGE_NUMBER=0\n')


def booted_as(info):
    su._os_release_cache[su.OS_RELEASE] = info


class Reading(unittest.TestCase):
    def setUp(self):
        booted_as({})

    def tearDown(self):
        su._os_release_cache.clear()

    def test_dbus_property_names(self):
        state = su.from_values({
            'State': 'Staged', 'Channel': 'stable', 'BootedVersion': '1.0.0', 'BootedCommit': 'abc',
            'StagedVersion': '1.0.1', 'AvailableVersion': '1.0.1', 'AvailableSummary': 'Faster Alt+Tab.',
            'NotesUrl': 'https://simplyluma.com/releases/1.0.1', 'Importance': 'security',
            'DownloadBytes': 214000000, 'Progress': 1.0, 'LastCheck': 1790000000, 'LastError': '',
            'Metered': False, 'RollbackAvailable': True, 'PreviewEnrolled': False,
            'AvailableChannels': ['stable', 'beta', 'nightly', 'bogus'],
        }, 'dbus')
        self.assertTrue(state.service and state.staged and state.update_ready and state.security)
        self.assertFalse(state.available)
        self.assertEqual(state.available_channels, ('stable', 'beta', 'nightly'))
        self.assertTrue(state.nightly_allowed)
        # luma-update older than release names: Depot names versions by the same rules.
        self.assertEqual(su.display_version(state.staged_version), 'Luma (Version 1.0.1, Prairie)')
        self.assertEqual(state.staged_name, 'Luma (Version 1.0.1, Prairie)')
        self.assertEqual(su.display_version('Luma (Prairie, Beta 1)'), 'Luma (Prairie, Beta 1)')

    def test_cli_json_uses_snake_case(self):
        state = su.from_status_json(json.dumps({
            'state': 'downloading', 'channel': 'beta', 'booted_version': '1.0.0-beta.3',
            'available_version': '1.0.0-beta.4', 'progress': 42, 'download_bytes': 1000,
            'last_check': '2026-10-01T12:00:00Z', 'metered': True}))
        self.assertEqual(state.source, 'cli')
        self.assertTrue(state.downloading and state.available and state.metered)
        self.assertFalse(state.preview_enrolled)
        self.assertAlmostEqual(state.progress, 0.42)
        self.assertGreater(state.last_check, 0)
        self.assertFalse(state.nightly_allowed)

    def test_agent_contract_additions(self):
        state = su.from_status_json(json.dumps({
            'schema_version': 1, 'state': 'restart-required', 'managed': True, 'rolled_back_version': '1.0.2',
            'waiting_version': '1.0.3', 'booted_deadend_reason': 'Pulled', 'preview_enrolled': True,
            'channel': 'beta', 'future_key': 'ignored'}), 'file')
        self.assertTrue(state.restart_required and state.update_ready and state.preview_enrolled)
        self.assertEqual((state.rolled_back_version, state.waiting_version, state.source), ('1.0.2', '1.0.3', 'file'))
        unmanaged = su.from_values({'State': 'staged', 'StagedVersion': '9', 'Managed': False}, 'dbus')
        self.assertFalse(unmanaged.update_ready)
        self.assertFalse(su.from_status_json('{"schema_version": 2}').service)

    def test_barrier_blocked_is_never_up_to_date(self):
        state = su.from_values({'State': 'barrier-blocked', 'BootedVersion': '1.0.0', 'WaitingVersion': '1.0.1',
                                'Managed': True}, 'dbus')
        self.assertTrue(state.barrier_blocked)
        self.assertEqual(state.waiting_version, '1.0.1')
        self.assertFalse(state.available or state.staged or state.update_ready)

    def test_counting_copy_is_honest_about_early_updates(self):
        public = su.counting_privacy_text(False)
        self.assertNotIn('can be joined', public)
        self.assertIn('network address', public)
        self.assertNotIn('credential', public)
        early = su.counting_privacy_text(True)
        self.assertTrue(early.startswith(public))
        self.assertIn('credential tied to your Luma account', early)
        self.assertIn('by address and time', early)
        self.assertIn('No account is needed', su.EARLY_UPDATES_SUBTITLE)

    def test_nothing_is_made_up(self):
        state = su.from_values({'State': 'idle', 'NotesUrl': 'http://evil.example/notes', 'Progress': 'lots',
                                'DownloadBytes': -5, 'Importance': 'panic', 'Metered': 'yes'}, 'dbus')
        self.assertEqual((state.notes_url, state.progress, state.download_bytes, state.importance, state.metered),
                         ('', 0.0, 0, 'normal', False))
        self.assertFalse(state.update_ready)
        for text in ('', 'not json', '[1, 2]', None):
            self.assertFalse(su.from_status_json(text).service)
        self.assertFalse(su.NOT_INSTALLED.update_ready)

    def test_checked_ago(self):
        state = su.from_values({'LastCheck': 1000}, 'dbus')
        self.assertEqual(state.checked_ago(1030), 'just now')
        self.assertEqual(state.checked_ago(1000 + 600), '10 minutes ago')
        self.assertEqual(state.checked_ago(1000 + 5 * 3600), '5 hours ago')
        self.assertEqual(state.checked_ago(1000 + 86400 * 1.5), 'yesterday')
        self.assertEqual(su.from_values({}, 'dbus').checked_ago(5), '')


class ChoicesAndAudit(unittest.TestCase):
    """The optional, visible half of ADR-030 section 6: what a person chose, and
    what Depot can honestly say about where this system came from."""

    def test_the_choices_are_read_from_the_agent(self):
        state = su.from_values({
            'State': 'available', 'Channel': 'beta', 'BootedVersion': '1.0.0', 'AvailableVersion': '1.0.1',
            'AutomaticDownload': False, 'IgnoredVersion': '1.0.1',
            'RepositoryUrl': 'https://os.luma.example/repo', 'GraphUrl': 'https://os.luma.example/graph/beta.json',
            'SignatureVerified': True, 'SigningKeyId': 'A1B2C3D4E5F60718',
            'LastCheckReason': 'newest-eligible', 'LastCheckAttempt': 1790000001,
        }, 'dbus')
        self.assertFalse(state.automatic_download)
        self.assertEqual(state.ignored_version, '1.0.1')
        self.assertTrue(state.ignored)
        self.assertEqual(state.offered_version, '1.0.1')
        self.assertEqual(state.host, 'os.luma.example')
        self.assertTrue(state.signature_verified)
        self.assertEqual(state.signing_key_id, 'A1B2C3D4E5F60718')
        self.assertEqual(state.last_check_attempt, 1790000001)

    def test_an_ignored_update_stops_badging_but_is_still_offered(self):
        state = su.from_values({'State': 'staged', 'StagedVersion': '1.0.1', 'IgnoredVersion': '1.0.1',
                                'Managed': True}, 'dbus')
        self.assertTrue(state.staged)
        self.assertFalse(state.update_ready)
        newer = su.from_values({'State': 'staged', 'StagedVersion': '1.0.2', 'IgnoredVersion': '1.0.1',
                                'Managed': True}, 'dbus')
        self.assertTrue(newer.update_ready)

    def test_an_older_agent_reads_as_the_safe_answer(self):
        state = su.from_values({'State': 'idle', 'BootedVersion': '1.0.0'}, 'dbus')
        self.assertTrue(state.automatic_download)       # the package default
        self.assertEqual(state.ignored_version, '')
        self.assertFalse(state.signature_verified)      # never claimed without evidence
        self.assertEqual(state.repository_url, '')
        self.assertEqual(state.host, '')                # no host is invented

    def test_no_host_is_hard_coded_anywhere_in_the_reading(self):
        state = su.from_values({'RepositoryUrl': 'https://updates.r2.example.net/luma/repo'}, 'dbus')
        self.assertEqual(state.host, 'updates.r2.example.net')
        self.assertEqual(su.host_of('not a url'), '')
        self.assertEqual(su.host_of('http://insecure.example/repo'), 'insecure.example')

    def test_a_redacted_preview_url_still_reads_and_names_no_credential(self):
        state = su.from_values({'RepositoryUrl': 'https://dl.example/os/preview/<credential>/repo'}, 'dbus')
        self.assertEqual(state.host, 'dl.example')
        self.assertNotIn('preview/s', state.repository_url.split('<credential>')[0])

    def test_channels_are_named_for_people_not_for_the_graph(self):
        self.assertEqual(su.channel_name('stable'), 'Official')
        self.assertEqual(su.channel_name('beta'), 'Beta')
        self.assertEqual(su.channel_name('nightly'), 'Nightly')
        self.assertEqual(su.channel_name(''), '')
        self.assertEqual(set(su.CHANNEL_DETAILS), {'stable', 'beta', 'nightly'})

    def test_every_reason_the_agent_can_publish_has_plain_words(self):
        for reason in ('up-to-date', 'newest-eligible', 'barrier-blocked', 'rollout-not-reached',
                       'network', 'signature', 'stale-graph', 'disk-space'):
            self.assertTrue(su.check_reason_text(reason).endswith('.'), reason)
        self.assertEqual(su.check_reason_text('something-new-the-agent-invented'), '')

    def test_the_download_note_says_what_actually_happens(self):
        on = su.automatic_download_note(False, True)
        off = su.automatic_download_note(False, False)
        self.assertIn('metered', on)
        self.assertIn('30%', on)
        self.assertIn('until you press Download', off)
        for text in (on, off):
            self.assertIn('until you restart', text)


class NeverADeadEnd(unittest.TestCase):
    """A computer that follows no channel is still owed the whole page: what is
    true, what it would take, and what choosing a channel would do."""

    def test_every_unmanaged_reason_says_what_is_true_and_what_would_change_it(self):
        for reason in ('other-origin', 'no-remote', 'no-key', 'no-image-system', 'busy'):
            what, next_step = su.unmanaged_text(reason)
            self.assertTrue(what.endswith('.'), reason)
            self.assertTrue(next_step.endswith('.'), reason)
            self.assertNotIn('unavailable', (what + next_step).lower(), reason)

    def test_an_unknown_reason_still_answers_rather_than_going_blank(self):
        what, next_step = su.unmanaged_text('something-new')
        self.assertTrue(what and next_step)

    def test_the_consequence_says_the_same_four_things_every_time(self):
        for channel in ('stable', 'beta', 'nightly'):
            text = su.channel_consequence(channel, '', adopting=True)
            self.assertIn(su.channel_name(channel), text)
            self.assertIn('stay as they are', text)
            self.assertIn('until you restart', text)
            self.assertIn('go back to it', text)

    def test_changing_channel_on_a_managed_computer_says_what_would_happen(self):
        self.assertIn('already follows', su.channel_consequence('beta', 'beta'))
        self.assertIn('with the next update', su.channel_consequence('nightly', 'beta'))
        self.assertIn('waits until Official', su.channel_consequence('stable', 'beta'))

    def test_every_channel_has_a_pace_and_a_requirement(self):
        for channel in ('stable', 'beta', 'nightly'):
            self.assertEqual(len(su.CHANNEL_PACE[channel]), 2)
            self.assertIn(channel, su.CHANNEL_REQUIREMENT)
        self.assertEqual(su.CHANNEL_REQUIREMENT['stable'], '')
        self.assertIn('goes back', su.ROLLBACK_PROMISE)

    def test_adoptability_is_read_from_the_agent_and_never_assumed(self):
        state = su.from_values({'Managed': False, 'Adoptable': True,
                                'UnmanagedReason': 'other-origin'}, 'dbus')
        self.assertTrue(state.adoptable)
        self.assertEqual(state.unmanaged_reason, 'other-origin')
        older = su.from_values({'Managed': False}, 'dbus')
        self.assertFalse(older.adoptable)        # an older agent promises nothing
        self.assertEqual(older.unmanaged_reason, '')
        self.assertEqual(su.unmanaged_text(older.unmanaged_reason)[0][:4], 'This')


    def test_who_set_up_early_updates_is_said_plainly(self):
        media = su.from_values({'PreviewEnrolled': True, 'PreviewSource': 'staff-media', 'Channel': 'nightly',
                                'AvailableChannels': ['stable', 'beta', 'nightly']}, 'dbus')
        self.assertTrue(media.enrolled_by_staff)
        self.assertIn('Luma team', su.preview_source_text(media.preview_source))
        hub = su.from_values({'PreviewEnrolled': True, 'PreviewSource': 'hub'}, 'dbus')
        self.assertFalse(hub.enrolled_by_staff)
        self.assertIn('Luma account', su.preview_source_text('hub'))
        self.assertEqual(su.from_values({'PreviewEnrolled': False, 'PreviewSource': 'hub'}, 'dbus').preview_source, '')
        self.assertEqual(su.preview_source_text(su.from_values({'PreviewEnrolled': True}, 'dbus').preview_source), '')

    def test_adopting_says_what_happens_to_the_system(self):
        text = su.channel_consequence('nightly', '', adopting=True)
        for words in ('replaced', 'packages you added stay', 'until you restart', 'go back'):
            self.assertIn(words, text)

    def test_every_channel_needs_nothing(self):
        self.assertEqual(set(su.CHANNEL_REQUIREMENT.values()), {''})

    def test_channel_work_in_progress_is_named(self):
        self.assertIn('Nightly', su.channel_progress_text('EnrollPreview', 'nightly'))
        self.assertIn('Leaving', su.channel_progress_text('LeavePreview', ''))
        self.assertEqual(su.channel_progress_text('Check', 'nightly'), '')

    def test_counting_text_names_the_right_credential(self):
        self.assertIn('Luma team issued', su.counting_privacy_text(True, 'staff-media'))
        self.assertIn('Luma account', su.counting_privacy_text(True, 'hub'))


class ReleaseNames(unittest.TestCase):
    def tearDown(self):
        su._os_release_cache.clear()

    def test_published_names_are_shown(self):
        booted_as(NIGHTLY)
        state = su.from_values({
            'State': 'staged', 'Channel': 'nightly', 'BootedVersion': '1.0.0-nightly.20260917.5',
            'StagedVersion': '1.0.0-nightly.20260918.2', 'IgnoredVersion': '1.0.0-nightly.20260918.2',
            'BootedName': 'Luma (Prairie, Beta 0, Nightly 20260916)',
            'StagedName': 'Luma (Prairie, Beta 0, Nightly 20260917)',
            'IgnoredName': 'Luma (Prairie, Beta 0, Nightly 20260917)',
        }, 'dbus')
        self.assertEqual(state.booted_name, 'Luma (Prairie, Beta 0, Nightly 20260916)')
        self.assertEqual(state.offered_name, 'Luma (Prairie, Beta 0, Nightly 20260917)')
        self.assertEqual(state.offered_version, '1.0.0-nightly.20260918.2')
        self.assertTrue(state.ignored)

    def test_older_agent_is_named_by_the_same_rules(self):
        booted_as(NIGHTLY)
        state = su.from_status_json(json.dumps({
            'state': 'available', 'channel': 'nightly', 'booted_version': '1.0.0-nightly.20260917.5',
            'available_version': '1.0.0-nightly.20260918.2', 'waiting_version': '1.0.0-beta.1.1',
            'rolled_back_version': '1.0.0'}))
        self.assertEqual(state.booted_name, 'Luma (Prairie, Beta 0, Nightly 20260916)')
        self.assertEqual(state.available_name, 'Luma (Prairie, Beta 0, Nightly 20260918)')
        self.assertEqual(state.offered_name, 'Luma (Prairie, Beta 0, Nightly 20260918)')
        self.assertEqual(state.waiting_name, 'Luma (Prairie, Beta 1.1)')
        self.assertEqual(state.rolled_back_name, 'Luma (Version 1, Prairie)')
        self.assertEqual((state.staged_name, state.ignored_name), ('', ''))

    def test_an_image_from_before_release_names_is_never_luma_1_0(self):
        booted_as(su.parse_os_release('NAME=Luma\nID=luma\nVERSION_ID=1.0\nPRETTY_NAME="Luma 1.0"\n'))
        self.assertEqual(su.booted_display_name('1.0.0-nightly.20260916.9'),
                         'Luma (Prairie, Beta 0, Nightly 20260916)')
        booted_as({'ID': 'fedora', 'PRETTY_NAME': 'Fedora Linux 44 (Silverblue)'})
        self.assertEqual(su.booted_display_name(''), 'Fedora Linux 44 (Silverblue)')

    def test_names_are_validated(self):
        booted_as(NIGHTLY)
        state = su.from_values({'State': 'available', 'AvailableVersion': '1.0.0-beta.1',
                                'AvailableName': 'x' * 201, 'BootedVersion': '1.0.0', 'BootedName': 'a\nb'}, 'dbus')
        self.assertEqual(state.available_name, 'Luma (Prairie, Beta 1)')
        self.assertEqual(state.booted_name, 'Luma (Prairie, Beta 0, Nightly 20260916)')
        final = dict(NIGHTLY, LUMA_RELEASE_STAGE='final')
        self.assertEqual(su.derive_name('1.0.0-nightly.20261101.1', final), 'Luma (Version 1, Prairie, Nightly 20261101)')


if __name__ == '__main__':
    unittest.main()
