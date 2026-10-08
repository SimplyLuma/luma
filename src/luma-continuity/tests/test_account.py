import tempfile
from pathlib import Path
import unittest
from luma_continuity.account import AccountModel, AccountConfig, AccountError, authorization_current

class AccountStateTests(unittest.TestCase):
    def setUp(self):
        self.calls=[]; self.fail=False; self.revoked=False; self.pauses=[]
        self.token={'access_token':'synthetic-only'}
        test=self
        class Tokens:
            def get(self): return test.token
            def clear(self): test.token=None
        class API:
            config=AccountConfig('https://identity.example.test/realms/test','https://api.example.test','native-test')
            def request(self,method,path,token,body=None):
                test.calls.append((method,path))
                if test.revoked: raise AccountError('signed_out',401)
                if test.fail: raise AccountError('offline')
                if path=='/v1/me': return {'issuer':self.config.issuer,'subject':'fixture','account_id':'fixture-account','session_id':'here'}
                if path.endswith('/profile'): return {'name':'Synthetic','email':'fixture@example.test'}
                if path.endswith('/usage'): return {'status':'unavailable','used_bytes':None,'quota_bytes':None,'selected_sync_bytes':None}
                if path.endswith('/sync'): return {'items':[{'id':'photos','enabled':False,'available':False},{'id':'clipboard','enabled':False,'available':False}]}
                if path.endswith('/sessions'): return {'items':[{'session_id':'here','current':True,'can_revoke_here':False},{'session_id':'there','current':False,'can_revoke_here':True}]}
                if path.endswith('/security'): return {'encryption':{'toggle_available':False}}
                if method in {'POST','DELETE'}: return {'revoked_session_ids':['fixture-session'],'local_data_action':'none'}
                raise AssertionError(path)
        self.model=AccountModel(API(),Tokens(),pause_account=lambda:self.pauses.append(True),now=lambda:100)

    def test_unknown_storage_is_not_zero_and_outage_preserves_stale(self):
        state=self.model.refresh()
        self.assertEqual(state['account_status'],'signed_in')
        self.assertIsNone(state['usage']['used_bytes'])
        self.assertFalse(state['sync']['items'][0]['available'])
        self.assertFalse(state['sync']['items'][1]['visible'])
        self.fail=True
        stale=self.model.refresh()
        self.assertTrue(stale['stale'])
        self.assertEqual(stale['profile'],state['profile'])
        self.assertEqual(stale['observed_at'],100)

    def test_auth_failure_reports_sanitized_stage_and_clears_after_recovery(self):
        get=self.model.tokens.get
        def rejected():raise AccountError('sign_in_required',401)
        self.model.tokens.get=rejected
        state=self.model.refresh()
        self.assertEqual(state['account_failure'],{'stage':'credential_read','code':'sign_in_required'})
        self.assertEqual(state['account_status'],'reauth_required')
        self.model.tokens.get=get
        self.assertIsNone(self.model.refresh()['account_failure'])

    def test_disable_unsupported_never_looks_successful(self):
        self.model.refresh()
        with self.assertRaises(AccountError): self.model.set_sync('photos',False)

    def test_locked_credentials_preserve_identity_and_recover_without_login(self):
        original=self.model.refresh()
        getter=self.model.tokens.get
        def locked():raise AccountError('keyring_locked')
        self.model.tokens.get=locked
        state=self.model.refresh()
        self.assertEqual(state['account_status'],'locked')
        self.assertTrue(state['stale'])
        self.assertEqual(state['identity'],original['identity'])
        self.assertEqual(state['profile'],original['profile'])
        self.assertIsNotNone(self.token)
        self.model.tokens.get=getter
        restored=self.model.refresh()
        self.assertEqual(restored['account_status'],'signed_in')
        self.assertFalse(restored['stale'])

    def test_unlock_during_outage_keeps_automatic_recovery_armed(self):
        from luma_continuity.lifecycle import Recovery
        getter=self.model.tokens.get
        def locked():raise AccountError('keyring_locked')
        self.model.tokens.get=locked
        self.assertEqual(self.model.refresh()['account_status'],'locked')
        self.model.tokens.get=getter;self.fail=True
        state=self.model.refresh()
        self.assertNotEqual(state['account_status'],'locked')
        self.assertTrue(state['stale'])
        self.assertFalse(authorization_current(state))
        callbacks=[]
        recovery=Recovery(self.model.refresh,later=lambda delay,cb:callbacks.append(cb),cancel=lambda _:None)
        recovery.completed(state)
        self.assertEqual(len(callbacks),1)
        self.fail=False;callbacks.pop()()
        self.assertEqual(self.model.snapshot()['account_status'],'signed_in')
        recovery.close()

    def test_cold_locked_credentials_are_not_signed_out(self):
        def locked():raise AccountError('keyring_locked')
        self.model.tokens.get=locked
        state=self.model.refresh()
        self.assertEqual(state['account_status'],'locked')
        self.assertIsNone(state['identity'])
        self.assertIsNotNone(self.token)

    def test_monotonic_authorization_bound_survives_wallclock_rollback(self):
        self.model.monotonic=lambda:100
        state=self.model.refresh()
        self.assertTrue(authorization_current(state,now=100,monotonic=100))
        self.assertFalse(authorization_current(state,now=-1000,monotonic=116))
        self.assertFalse(authorization_current(state,now=116,monotonic=100))

    def test_signout_current_revokes_before_keyring_clear_preserves_file(self):
        self.model.refresh()
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'local-work';file.write_text('fixture unsynced data')
            self.model.sign_out('current')
            self.assertEqual(self.calls[-1],('POST','/v1/account/signout/current'))
            self.assertIsNone(self.token)
            self.assertEqual(file.read_text(),'fixture unsynced data')
            self.assertEqual(self.model.snapshot()['account_status'],'signed_out')

    def test_failed_revocation_retains_credentials_and_account(self):
        self.model.refresh();self.fail=True
        with self.assertRaises(AccountError): self.model.sign_out('current')
        self.assertIsNotNone(self.token)
        self.assertEqual(self.model.snapshot()['account_status'],'signed_in')

    def test_remote_revocation_pauses_without_data_deletion(self):
        self.model.refresh();self.revoked=True
        state=self.model.refresh()
        self.assertEqual(state['account_status'],'signed_in')
        self.assertTrue(state['stale']);self.assertEqual(state['credential_status'],'rejected')
        self.assertTrue(self.pauses);self.assertIsNotNone(self.token)

    def test_current_cannot_be_revoked_via_other_device_route(self):
        self.model.refresh()
        with self.assertRaises(AccountError): self.model.sign_out('here')
        self.model.sign_out('there')
        self.assertIn(('DELETE','/v1/account/sessions/there'),self.calls)

    def test_unconfigured_has_no_fake_account(self):
        state=AccountModel().refresh()
        self.assertEqual(state['account_status'],'signed_out')
        self.assertIsNone(state['usage']);self.assertIsNone(state['sessions'])

    def test_management_link_is_exact_trusted_identity_surface(self):
        self.model.refresh()
        expected=self.model.api.config.issuer+'/account/'
        self.model.state['security']={'password_change':{'mode':'identity_provider_browser','url':expected}}
        self.assertEqual(self.model.management_url(),expected)
        self.model.state['security']['password_change']['url']='https://elsewhere.example.test/'
        with self.assertRaises(AccountError):self.model.management_url()

class CredentialRetentionTests(unittest.TestCase):
    setUp=AccountStateTests.setUp
    def test_pending_rotation_between_state_and_store_read_has_no_authority(self):
        self.model.refresh()
        saved=self.token.copy()
        def rotating_get():
            return dict(saved,_pending_verification=True)
        self.model.tokens.get=rotating_get
        calls=list(self.calls)
        with self.assertRaises(AccountError):self.model.authority_token()
        with self.assertRaises(AccountError):self.model.sign_out('current')
        self.assertEqual(self.calls,calls)
        self.assertIsNotNone(self.token)

    def test_state_invalidation_during_store_read_blocks_authority(self):
        self.model.refresh()
        saved=self.token.copy()
        def invalidating_get():
            self.model.state.update(stale=True,service_status='unavailable')
            return saved
        self.model.tokens.get=invalidating_get
        with self.assertRaises(AccountError):self.model.authority_token()

    def test_mismatched_or_expired_record_never_becomes_authority(self):
        self.model.refresh()
        saved=self.token.copy()
        for extra in ({'_identity':{'subject':'different'}},{'expires_at':99}):
            self.model.tokens.get=lambda extra=extra:dict(saved,**extra)
            with self.assertRaises(AccountError):self.model.authority_token()

    def test_explicit_invalid_grant_requires_login_without_erasing_identity(self):
        original=self.model.refresh()
        def rejected(*_args,**_kwargs):raise AccountError('sign_in_required',401)
        self.model.api.request=rejected
        state=self.model.refresh()
        self.assertEqual(state['account_status'],'reauth_required')
        self.assertTrue(state['stale']);self.assertIsNotNone(self.token)
        self.assertEqual(state['identity'],original['identity'])
        self.assertFalse(authorization_current(state,now=100,monotonic=100))

    def test_missing_credential_after_ready_preserves_pair_identity(self):
        original=self.model.refresh();self.token=None
        state=self.model.refresh()
        self.assertEqual(state['account_status'],'reauth_required')
        self.assertEqual(state['identity'],original['identity'])
        self.assertEqual(state['credential_status'],'missing')

    def rotation(self,renew):
        from unittest.mock import patch
        self.model.monotonic=lambda:100
        self.token={'access_token':'synthetic-only','refresh_token':'r','expires_at':200,'_identity':{'subject':'fixture'}}
        self.assertTrue(authorization_current(self.model.refresh(),now=100,monotonic=100))
        self.token['expires_at']=120  # inside the 30-second rotation window, still valid
        observed=[]
        class Flow:
            def __init__(self,*args):pass
            def renew(flow,previous):
                observed.append(authorization_current(self.model.snapshot(),now=100,monotonic=100))
                return renew(previous)
        with patch('luma_continuity.login.BrowserLogin',Flow):state=self.model.refresh()
        return observed,state

    def test_routine_rotation_keeps_verified_lease_while_issuer_round_trip_runs(self):
        observed,state=self.rotation(lambda previous:dict(previous,access_token='rotated',expires_at=400))
        # Connect43 marked the account stale before this network call, so a live
        # call's authorization predicate failed for the whole rotation.
        self.assertEqual(observed,[True])
        self.assertTrue(authorization_current(state,now=100,monotonic=100))
        self.assertEqual(self.pauses,[])

    def test_failed_rotation_still_revokes_authority(self):
        def rejected(previous):raise AccountError('sign_in_required',401)
        observed,state=self.rotation(rejected)
        self.assertEqual(observed,[True])
        self.assertEqual(state['account_status'],'reauth_required')
        self.assertFalse(authorization_current(state,now=100,monotonic=100))
        self.assertEqual(self.pauses,[True])

class AccountDeadlineTests(unittest.TestCase):
    def test_slow_stream_cannot_keep_worker_alive_indefinitely(self):
        import httpx
        from unittest.mock import patch
        from luma_continuity.account import AccountAPI
        clock=[0]
        class Stream(httpx.SyncByteStream):
            def __iter__(self):
                yield b'{'
                clock[0]=11
                yield b'}'
        client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,stream=Stream())))
        self.addCleanup(client.close)
        api=AccountAPI(AccountConfig('https://identity.example','https://api.example','native'),client)
        with patch('luma_continuity.account.time.monotonic',side_effect=lambda:clock[0]):
            with self.assertRaises(AccountError) as caught:api.request('GET','/v1/me','synthetic')
        self.assertEqual(caught.exception.code,'service_unavailable')
