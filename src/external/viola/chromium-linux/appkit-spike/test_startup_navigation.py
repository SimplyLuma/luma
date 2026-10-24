# SPDX-License-Identifier: GPL-3.0-only
import unittest
from startup_navigation import primary_startup_url

class StartupNavigation(unittest.TestCase):
    def choose(self,**changes):
        args=dict(start_url=None,had_profile=False,urls=[],interactive=True,origin='http://127.0.0.1:1234',measure_presentation=False)
        args.update(changes);return primary_startup_url(**args)
    def test_explicit_initial_uri_does_not_first_load_unrelated_google_tab(self):
        self.assertIsNone(self.choose(urls=['https://requested.invalid/path']))
    def test_existing_profile_is_not_replaced_by_default_navigation(self):
        self.assertIsNone(self.choose(had_profile=True))
    def test_explicit_start_url_retains_its_priority(self):
        self.assertEqual(self.choose(start_url='https://explicit.invalid',urls=['https://other.invalid']), 'https://explicit.invalid')
    def test_fresh_regular_launch_retains_default(self):
        self.assertEqual(self.choose(),'https://www.google.com/')
    def test_qa_defaults_preserved(self):
        self.assertEqual(self.choose(interactive=False),'http://127.0.0.1:1234/native-window')
        self.assertEqual(self.choose(interactive=False,measure_presentation=True),'http://127.0.0.1:1234/cadence')

if __name__=='__main__':unittest.main()
