import unittest

from luma_continuity.connect_view import (device_icon, discoverable_subtitle, format_phone, profile_subtitles,
                                         service_subtitle, sign_in_subtitle, stable, when_text)


class ConnectViewTest(unittest.TestCase):
    def test_refresh_bookkeeping_is_not_a_visible_change(self):
        first = {'generation': 1, 'observed_at': 10, 'profile': {'name': 'A', 'observed_at': 10},
                 'sessions': {'items': [{'id': 's', 'observed_at': 3}]}, 'valid_until': 5.0}
        second = {'generation': 2, 'observed_at': 20, 'profile': {'name': 'A', 'observed_at': 20},
                  'sessions': {'items': [{'id': 's', 'observed_at': 9}]}, 'valid_until': 9.0}
        self.assertEqual(stable(first), stable(second))
        second['profile']['name'] = 'B'
        self.assertNotEqual(stable(first), stable(second))

    def test_service_subtitles_say_what_happened(self):
        self.assertEqual(service_subtitle({'id': 'notes', 'enabled': False}), 'Off')
        self.assertEqual(service_subtitle({'id': 'notes', 'enabled': True}), 'Waiting for the first sync')
        synced = service_subtitle({'id': 'photos', 'enabled': True, 'last_success_at': 1789435408, 'item_count': 90})
        self.assertTrue(synced.startswith('Synced ') and synced.endswith(' · 90 photos'), synced)
        # A count of nothing, and a calendar count, say nothing extra.
        self.assertNotIn('·', service_subtitle({'id': 'contacts', 'enabled': True, 'last_success_at': 1789435408, 'item_count': 0}))
        self.assertNotIn('·', service_subtitle({'id': 'calendar', 'enabled': True, 'last_success_at': 1789435408, 'item_count': 3}))

    def test_times_read_naturally(self):
        self.assertEqual(when_text(None), 'Not yet')
        self.assertEqual(when_text(True), 'Not yet')
        self.assertTrue(when_text(86400 * 3, prefix='Synced ').startswith('Synced '))

    def test_devices_get_a_phone_or_computer_glyph(self):
        self.assertEqual(device_icon('Fairphone 6'), 'luma-connect-phone-symbolic')
        self.assertEqual(device_icon('ThinkPad X1 Carbon'), 'luma-connect-laptop-symbolic')
        self.assertEqual(device_icon(''), 'luma-connect-laptop-symbolic')

    # The profile rows only ever repeat what the hub says is verified.
    GITHUB = {'provider': 'github', 'name': 'GitHub', 'manage_url': 'https://github.com/settings/security'}

    def profile(self, **overrides):
        base = {'name': 'nmcmil', 'name_source': 'sign_in', 'email': 'nick@example.com', 'email_source': 'sign_in',
                'email_verified': True, 'email_verified_by': 'GitHub', 'sign_in_email': 'nick@example.com',
                'phone': None, 'phone_verified': False, 'discoverable_by_phone': False, 'phone_lookup': 'off',
                'sign_in': self.GITHUB, 'verification': {'email': False, 'phone': False, 'phone_lookup': False}}
        base.update(overrides)
        return base

    def test_phone_numbers_read_the_way_people_write_them(self):
        self.assertEqual(format_phone('+14155550199'), '+1 415-555-0199')
        self.assertEqual(format_phone('+442079460958'), '+442079460958')
        self.assertEqual(format_phone(None), '')
        self.assertEqual(format_phone('+1415555019'), '+1415555019')

    def test_profile_rows_say_what_is_verified_and_by_whom(self):
        rows = profile_subtitles(self.profile())
        self.assertEqual(rows, {'name': 'nmcmil', 'email': 'nick@example.com · Verified by GitHub', 'phone': 'Not added'})
        typed = profile_subtitles(self.profile(email='n@luma.example', email_source='profile', email_verified=False,
                                               email_verified_by=None, phone='+14155550199'))
        self.assertEqual(typed['email'], 'n@luma.example · Not verified')
        self.assertEqual(typed['phone'], '+1 415-555-0199 · Not verified')
        self.assertEqual(profile_subtitles(None), {'name': 'No name', 'email': 'Not added', 'phone': 'Not added'})

    def test_the_discoverability_switch_never_promises_more_than_today(self):
        self.assertEqual(discoverable_subtitle(self.profile()), 'Add a phone number first.')
        with_phone = self.profile(phone='+14155550199')
        self.assertIn('once it’s verified', discoverable_subtitle(with_phone))
        waiting = discoverable_subtitle(self.profile(phone='+14155550199', discoverable_by_phone=True, phone_lookup='waiting_for_verification'))
        self.assertEqual(waiting, 'On. It starts once your number is verified, and Luma can’t verify numbers yet.')
        self.assertEqual(discoverable_subtitle(self.profile(phone='+14155550199', discoverable_by_phone=True, phone_verified=True,
                                                            phone_lookup='on', verification={'phone': True, 'phone_lookup': True})),
                         'People who have your number can find you in Messages.')

    def test_sign_in_names_where_the_password_lives(self):
        self.assertEqual(sign_in_subtitle(self.profile()), 'GitHub · Your password and two-step verification are managed by GitHub')
        self.assertEqual(sign_in_subtitle(self.profile(sign_in={'provider': 'luma-connect', 'name': 'Luma Connect', 'manage_url': None})), 'Luma Connect')


if __name__ == '__main__':
    unittest.main()
