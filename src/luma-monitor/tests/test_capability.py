# SPDX-License-Identifier: Apache-2.0
"""The report is the only source of these words, and of this order."""
import json
import tempfile
import unittest
from pathlib import Path

from luma_monitor import capability


def entry(identifier, result, **rest):
    row = {'id': identifier, 'title': identifier.title(), 'promise': f'{identifier} works.',
           'remedy': f'Fix {identifier}.', 'severity': 'required', 'tier': 'hardware',
           'result': result, 'detail': f'{identifier} detail', 'evidence': {}}
    row.update(rest)
    return row


def report(entries, **rest):
    data = {'version': 1, 'written_at': 1_000_000.0, 'hostname': 'machine',
            'capabilities': entries}
    data.update(rest)
    return capability.Report(path='<fixture>', data=data)


class States(unittest.TestCase):
    def test_every_result_has_the_words_the_terminal_uses(self):
        self.assertEqual(capability.STATES['pass'], 'Working')
        self.assertEqual(capability.STATES['fail'], 'Not working')
        self.assertEqual(capability.STATES['unproven'], 'Not checked here')
        self.assertEqual(capability.STATES['not-applicable'], 'Not on this machine')
        self.assertEqual(capability.STATES['skip'], 'Not checked here')

    def test_not_checked_is_its_own_state_and_never_reads_like_working(self):
        # The whole reason this view exists: a capability nobody could prove
        # must not be quietly counted as fine.
        for result in ('unproven', 'skip'):
            self.assertEqual(capability.STATES[result], capability.NOT_CHECKED)
            self.assertNotEqual(capability.STATES[result], capability.WORKING)
        self.assertNotEqual(capability.NOT_CHECKED, capability.NOT_PRESENT)

    def test_absent_is_not_a_failure(self):
        row = report([entry('camera', 'not-applicable')]).capabilities[0]
        self.assertEqual(row.state, capability.NOT_PRESENT)
        self.assertEqual(row.tone, 'absent')
        self.assertEqual(row.note, '')

    def test_an_unknown_result_is_not_silently_working(self):
        row = report([entry('future', 'something-new')]).capabilities[0]
        self.assertEqual(row.state, capability.NOT_CHECKED)


class Words(unittest.TestCase):
    def test_title_promise_and_remedy_are_verbatim(self):
        row = report([entry('video-decode-hardware', 'fail',
                            title='Hardware video decode',
                            promise='Video plays without the processor decoding it.',
                            remedy='The graphics driver for video is installed but nothing '
                                   'is using it; video is being decoded on the processor.')
                      ]).capabilities[0]
        self.assertEqual(row.title, 'Hardware video decode')
        self.assertEqual(row.promise, 'Video plays without the processor decoding it.')
        self.assertEqual(row.note, 'The graphics driver for video is installed but nothing '
                                   'is using it; video is being decoded on the processor.')

    def test_an_unproven_row_says_why_from_detail(self):
        row = report([entry('vulkan', 'unproven', detail='no render node in this session')
                      ]).capabilities[0]
        self.assertEqual(row.state, capability.NOT_CHECKED)
        self.assertEqual(row.note, 'no render node in this session')

    def test_a_broken_row_without_a_remedy_falls_back_to_the_detail(self):
        row = report([entry('wifi', 'fail', remedy='', detail='no wireless device')
                      ]).capabilities[0]
        self.assertEqual(row.note, 'no wireless device')

    def test_a_working_row_has_nothing_to_act_on(self):
        self.assertEqual(report([entry('opengl', 'pass')]).capabilities[0].note, '')


class Order(unittest.TestCase):
    def test_rows_keep_the_manifests_order(self):
        rows = report([entry('video-decode-hardware', 'pass'),
                       entry('browser-video-decode', 'fail'),
                       entry('opengl', 'pass')]).capabilities
        self.assertEqual([row.id for row in rows],
                         ['video-decode-hardware', 'browser-video-decode', 'opengl'])

    def test_a_capability_checked_at_two_tiers_is_one_row(self):
        # The report carries one entry per tier; the view shows one promise.
        rows = report([entry('wifi', 'pass', tier='session'),
                       entry('wifi', 'fail', tier='hardware')]).capabilities
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].state, capability.NOT_WORKING)

    def test_the_worse_tier_wins_and_absent_never_hides_a_proof(self):
        rows = report([entry('camera', 'not-applicable'), entry('camera', 'pass')]).capabilities
        self.assertEqual(rows[0].state, capability.WORKING)
        rows = report([entry('bluetooth', 'pass'), entry('bluetooth', 'unproven')]).capabilities
        self.assertEqual(rows[0].state, capability.NOT_CHECKED)

    def test_entries_without_an_id_are_ignored(self):
        self.assertEqual(report([{'result': 'pass'}, entry('opengl', 'pass')]).capabilities[0].id,
                         'opengl')


class Age(unittest.TestCase):
    def test_the_age_is_worded_as_the_terminal_words_it(self):
        self.assertEqual(capability.age_text(1000, now=1000), 'checked just now')
        self.assertEqual(capability.age_text(1000, now=1000 + 3599), 'checked just now')
        self.assertEqual(capability.age_text(1000, now=1000 + 3 * 3600), 'checked 3 hours ago')
        self.assertEqual(capability.age_text(1000, now=1000 + 26 * 3600), 'checked 26 hours ago')

    def test_a_report_with_no_time_has_no_age(self):
        self.assertIsNone(capability.age_text(0))
        self.assertIsNone(capability.age_text('nonsense'))

    def test_the_summary_names_the_machine_and_the_age(self):
        summary = report([entry('opengl', 'pass')]).summary()
        self.assertTrue(summary.startswith('machine · checked '))


class Missing(unittest.TestCase):
    def test_a_machine_with_no_report_says_so(self):
        with tempfile.TemporaryDirectory() as directory:
            absent = capability.Report(path=str(Path(directory) / 'nothing.json'))
        self.assertFalse(absent.present)
        self.assertEqual(absent.summary(), 'This machine has not been checked yet')
        self.assertEqual(absent.capabilities, [])

    def test_a_damaged_report_is_treated_as_no_report_rather_than_as_good_news(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'capability-report.json'
            path.write_text('{ this is not json')
            damaged = capability.Report(path=str(path))
        self.assertFalse(damaged.present)
        self.assertEqual(damaged.summary(), capability.NOT_CHECKED_YET)

    def test_a_real_report_on_disk_is_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'capability-report.json'
            path.write_text(json.dumps({'written_at': 1.0, 'hostname': 'laptop',
                                        'capabilities': [entry('opengl', 'pass')]}))
            loaded = capability.Report(path=str(path))
        self.assertTrue(loaded.present)
        self.assertEqual(loaded.capabilities[0].state, capability.WORKING)

    def test_the_command_offered_is_the_one_that_works(self):
        self.assertEqual(capability.CHECK_COMMAND, 'sudo luma-capability --check')


class Counts(unittest.TestCase):
    def test_counts_cover_every_state(self):
        tally = report([entry('a', 'pass'), entry('b', 'fail'), entry('c', 'unproven'),
                        entry('d', 'not-applicable'), entry('e', 'skip')]).counts()
        self.assertEqual(tally[capability.WORKING], 1)
        self.assertEqual(tally[capability.NOT_WORKING], 1)
        self.assertEqual(tally[capability.NOT_CHECKED], 2)
        self.assertEqual(tally[capability.NOT_PRESENT], 1)


if __name__ == '__main__':
    unittest.main()
