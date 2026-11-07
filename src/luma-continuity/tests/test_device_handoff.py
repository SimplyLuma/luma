"""Connect signed in at first login from the first-boot setup's device sign-in."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from luma_continuity.account import AccountConfig, AccountError, AccountModel

AVAILABLE = importlib.util.find_spec('authlib') is not None
ISSUER = 'https://id.example.test/realms/luma-connect'
SETTINGS = {'issuer': ISSUER, 'api_origin': 'https://hub.example.test',
            'client_id': 'luma-device', 'browser_client_id': 'luma-connect-app'}
SHIPPED = Path(__file__).resolve().parents[1]/'data'/'client.json'


class SettingsTests(unittest.TestCase):
    def test_device_and_browser_clients(self):
        config = AccountConfig.from_settings(SETTINGS)
        self.assertEqual((config.client_id, config.device_client_id), ('luma-connect-app', 'luma-device'))
        self.assertEqual(config.token_client({}), 'luma-connect-app')
        self.assertEqual(config.token_client({'_client_id': 'luma-device'}), 'luma-device')
        with self.assertRaises(AccountError): config.token_client({'_client_id': 'someone-else'})

    def test_one_client_serves_both(self):
        config = AccountConfig.from_settings({k: v for k, v in SETTINGS.items() if k != 'browser_client_id'})
        self.assertEqual((config.client_id, config.device_client_id), ('luma-device', 'luma-device'))

    def test_rejects_unknown_keys_and_plain_http(self):
        for bad in (dict(SETTINGS, extra='x'), dict(SETTINGS, issuer='http://id.example.test/realms/luma-connect'),
                    dict(SETTINGS, client_id=7), {k: v for k, v in SETTINGS.items() if k != 'issuer'}):
            with self.assertRaises(ValueError): AccountConfig.from_settings(bad)

    def test_shipped_configuration(self):
        data = json.loads(SHIPPED.read_text())
        config = AccountConfig.from_settings(data)
        self.assertEqual(config.device_client_id, 'luma-device')
        self.assertEqual(urlsplit(config.issuer).path.rsplit('/', 1)[-1], 'luma-connect')
        # luma-setup's device grant reads exactly these two keys.
        self.assertTrue(data['issuer'] and data['client_id'] == 'luma-device')


@unittest.skipUnless(AVAILABLE, 'Authlib/joserfc protocol-test environment required')
class HandoffTests(unittest.TestCase):
    def setUp(self):
        from mock_device_idp import MockDeviceIdP, setup_device_sign_in
        from unittest import mock
        import luma_continuity.login as login
        from luma_continuity.login import DeviceHandoff
        idp_transport = lambda: self.idp.transport
        class Login(login.BrowserLogin):  # renewal inside AccountModel reaches the mock too
            def __init__(self, *args, http_transport=None):
                super().__init__(*args, http_transport=http_transport or idp_transport())
        patch = mock.patch.object(login, 'BrowserLogin', Login); patch.start(); self.addCleanup(patch.stop)
        self.config = AccountConfig.from_settings(SETTINGS)
        self.idp = MockDeviceIdP(ISSUER, 'luma-device')
        home = tempfile.TemporaryDirectory(); self.addCleanup(home.cleanup)
        self.seal = Path(home.name)/'.local/state/luma-setup/connect.cred'
        self.seal.parent.mkdir(parents=True)
        # luma-setup at Finish: the token response, sealed (systemd-creds stands in here).
        self.user_code, response = setup_device_sign_in(self.idp, self.config.device_client_id)
        self.seal.write_text('sealed:' + response)
        self.sealed_refresh = json.loads(response)['refresh_token']
        self.unsealed = []
        def unseal(path):
            self.unsealed.append(path)
            raw = Path(path).read_text()
            if not raw.startswith('sealed:'): raise RuntimeError('systemd-creds: not sealed for this user')
            return raw.removeprefix('sealed:')
        test = self
        self.stored = None
        class Tokens:
            def get(self): return dict(test.stored) if test.stored else None
            def set(self, token): test.stored = dict(token)
            def clear(self): test.stored = None
        class API:
            config = self.config
            subject = None
            def request(self, method, path, token, body=None):
                held = test.idp.access.get(token)
                if held is None: raise AccountError('signed_out', 401)
                if path == '/v1/me':
                    return {'issuer': ISSUER, 'subject': self.subject or held['sub'],
                            'account_id': 'account-1', 'session_id': held['sid']}
                if path.endswith('/sync'): return {'items': []}
                return {}
        self.api = API()
        self.handoff = DeviceHandoff(self.config, self.api, Tokens(), path=self.seal, unseal=unseal,
                                     http_transport=self.idp.transport)
        self.model = AccountModel(self.api, Tokens(), handoff=self.handoff)

    def token_requests(self):
        return [form for path, form in self.idp.requests if path.endswith('/token') and
                form.get('grant_type') == 'refresh_token']

    def test_setup_sign_in_is_adopted_at_first_login(self):
        self.assertTrue(set(self.user_code.replace('-', '')) <= set('BCDFGHJKLMNPQRSTVWXZ'))
        self.assertTrue(self.handoff.pending())
        state = self.model.refresh()
        self.assertEqual(state['account_status'], 'signed_in')
        self.assertEqual(state['identity']['subject'], 'fixture-subject')
        self.assertFalse(self.seal.exists())
        self.assertFalse(self.handoff.pending())
        self.assertEqual(self.stored['_client_id'], 'luma-device')
        self.assertEqual(self.stored['_identity'], {'issuer': ISSUER, 'subject': 'fixture-subject',
                                                    'session_id': 'fixture-session'})
        self.assertNotIn('_pending_verification', self.stored)
        # The sealed refresh token was rotated with the device client and is retired.
        rotation, = self.token_requests()
        self.assertEqual((rotation['client_id'], rotation['refresh_token']), ('luma-device', self.sealed_refresh))
        self.assertNotIn(self.sealed_refresh, self.idp.refresh)
        self.assertNotEqual(self.stored['refresh_token'], self.sealed_refresh)
        # Later refreshes leave the adopted credential alone.
        self.model.refresh()
        self.assertEqual(len(self.token_requests()), 1)

    def test_renewal_keeps_the_device_client(self):
        self.model.refresh()
        self.stored['expires_at'] = 0
        state = self.model.refresh()
        self.assertEqual(state['account_status'], 'signed_in')
        self.assertEqual([form['client_id'] for form in self.token_requests()], ['luma-device', 'luma-device'])
        self.assertEqual(self.stored['_client_id'], 'luma-device')

    def test_unusable_seal_is_discarded_and_stays_signed_out(self):
        for spoil in ('revoked', 'audience', 'foreign'):
            with self.subTest(spoil):
                if not self.seal.exists(): self.setUp()
                if spoil == 'revoked': self.idp.revoke_all()
                if spoil == 'audience': self.idp.audience = 'luma-connect-app'
                if spoil == 'foreign': self.seal.write_text('another machine')
                state = self.model.refresh()
                self.assertEqual(state['account_status'], 'signed_out')
                self.assertFalse(self.seal.exists())
                self.assertIsNone(self.stored)

    def test_unreachable_provider_keeps_the_seal(self):
        self.idp.down = True
        state = self.model.refresh()
        self.assertNotEqual(state['account_status'], 'signed_in')
        self.assertEqual(state['account_failure']['stage'], 'device_handoff')
        self.assertTrue(self.seal.exists())
        self.assertIsNone(self.stored)
        self.idp.down = False
        self.assertEqual(self.model.refresh()['account_status'], 'signed_in')
        self.assertFalse(self.seal.exists())

    def test_account_disagreement_never_signs_in(self):
        self.api.subject = 'somebody-else'
        state = self.model.refresh()
        self.assertEqual(state['account_status'], 'reauth_required')
        self.assertFalse(self.seal.exists())
        self.assertTrue(self.stored['_pending_verification'])

    def test_existing_sign_in_wins(self):
        self.stored = {'access_token': 'browser', '_identity': {'issuer': ISSUER, 'subject': 'fixture-subject'}}
        self.model.refresh()
        self.assertFalse(self.seal.exists())
        self.assertEqual(self.unsealed, [])
        self.assertEqual(self.token_requests(), [])
        self.assertEqual(self.stored['access_token'], 'browser')

    def test_browser_sign_in_uses_the_browser_client(self):
        from luma_continuity.login import BrowserLogin
        flow = BrowserLogin(self.config, self.api, None, http_transport=self.idp.transport)
        try:
            query = parse_qs(urlsplit(flow.prepare()).query)
        finally:
            flow.close()
        self.assertEqual(query['client_id'], ['luma-connect-app'])
        self.assertEqual(query['code_challenge_method'], ['S256'])


if __name__ == '__main__':
    unittest.main()
