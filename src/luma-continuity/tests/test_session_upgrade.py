from native_sender_fixture import NativeSender
"""Persistent sign-in upgrades preserve the existing account until verified."""
from types import SimpleNamespace
import time,unittest
from unittest.mock import patch
from luma_continuity.account import AccountConfig,AccountError
from luma_continuity.daemon import Daemon
from luma_continuity.login import BrowserLogin


class SessionUpgradeTests(unittest.TestCase):
    def flow(self,scope='openid profile email offline_access'):
        from joserfc import jwt
        from joserfc.jwk import RSAKey
        config=AccountConfig('https://identity.example/realm','https://api.example','native')
        key=RSAKey.generate_key(2048,parameters={'kid':'fixture'})
        identity=dict(issuer=config.issuer,subject='same-user',account_id='same-account',session_id='new-offline-session')
        claims=dict(iss=config.issuer,sub=identity['subject'],aud=config.client_id,sid=identity['session_id'],nonce='nonce',iat=int(time.time()),exp=int(time.time())+60)
        token=dict(access_token='new-synthetic',refresh_token='new-refresh',token_type='Bearer',scope=scope,
                   id_token=jwt.encode({'alg':'RS256','kid':'fixture'},claims,key))
        saved=[{'access_token':'old-synthetic'}]
        flow=BrowserLogin.__new__(BrowserLogin)
        flow.config=config;flow.api=SimpleNamespace(request=lambda *args:identity)
        flow.tokens=SimpleNamespace(set=lambda value:saved.append(value))
        flow.client=SimpleNamespace(fetch_token=lambda *args,**kwargs:token,close=lambda:None)
        flow.server=SimpleNamespace(server_close=lambda:None)
        flow.response='http://127.0.0.1/callback?code=synthetic';flow.deadline=time.monotonic()+10
        flow.callback_error=None;flow.verifier='verifier';flow.state='state';flow.nonce='nonce'
        flow.token_endpoint=config.issuer+'/token';flow.jwks_uri=config.issuer+'/certs'
        flow._json=lambda url:{'keys':[key.as_dict(private=False)]}
        return flow,saved,{k:identity[k] for k in ('issuer','subject','account_id')}

    def test_verified_new_sid_commits_only_same_account_offline_token(self):
        flow,saved,expected=self.flow()
        identity=flow.finish(expected_identity=expected)
        self.assertEqual(identity['session_id'],'new-offline-session')
        self.assertEqual(len(saved),2)
        self.assertEqual(saved[-1]['_identity']['subject'],'same-user')
        self.assertIn('offline_access',saved[-1]['scope'])

    def test_other_account_or_issuer_never_replaces_prior_credentials(self):
        for field in ('issuer','subject','account_id'):
            with self.subTest(field=field):
                flow,saved,expected=self.flow();expected[field]='different'
                with self.assertRaises(AccountError) as caught:flow.finish(expected_identity=expected)
                self.assertEqual(caught.exception.code,'identity_mismatch')
                self.assertEqual(saved,[{'access_token':'old-synthetic'}])

    def test_denied_offline_scope_never_replaces_prior_credentials(self):
        flow,saved,expected=self.flow(scope='openid profile email')
        with self.assertRaises(AccountError) as caught:flow.finish(expected_identity=expected)
        self.assertEqual(caught.exception.code,'persistent_login_unavailable')
        self.assertEqual(saved,[{'access_token':'old-synthetic'}])

    def test_cancelled_browser_preserves_prior_credentials(self):
        flow,saved,expected=self.flow();flow.response=None;flow.deadline=0
        with self.assertRaises(AccountError) as caught:flow.finish(expected_identity=expected)
        self.assertEqual(caught.exception.code,'login_expired')
        self.assertEqual(saved,[{'access_token':'old-synthetic'}])

    def test_signed_in_daemon_upgrade_refreshes_prior_authority_on_cancel(self):
        from gi.repository import GLib
        d=Daemon.__new__(Daemon);pending=[];refreshed=[];finished=[]
        d.config=AccountConfig('https://identity.example/realm','https://api.example','native')
        identity=dict(issuer=d.config.issuer,subject='same-user',account_id='same-account',session_id='old-online')
        d.latest=dict(account_status='signed_in',credential_status='ready',service_status='ready',stale=False,
                      valid_until=time.time()+60,lease_deadline_monotonic=time.monotonic()+60)
        d.model=SimpleNamespace(tokens=object(),api=object(),snapshot=lambda:dict(identity=identity),refresh=lambda:refreshed.append(True))
        d.GLib=SimpleNamespace(Variant=GLib.Variant,idle_add=lambda *args:None)
        d._schedule=lambda work:pending.append(work) or True
        invocation=SimpleNamespace(return_value=lambda value:None,return_dbus_error=lambda *args:self.fail(str(args)))
        class Flow:
            def __init__(self,*args):pass
            def prepare(self,email):return 'https://identity.example/realm/auth'
            def finish(self,expected_identity=None):
                finished.append(expected_identity);raise AccountError('login_cancelled')
        with patch('luma_continuity.login.BrowserLogin',Flow):
            d._call(NativeSender(),':native-test',None,None,'UpgradeSession',GLib.Variant('()',()),invocation)
            self.assertEqual(len(pending),1)
            with self.assertRaises(AccountError):pending[0]()
        self.assertEqual(finished,[{k:identity[k] for k in ('issuer','subject','account_id')}])
        self.assertEqual(refreshed,[True]);self.assertIsNone(d.login)
