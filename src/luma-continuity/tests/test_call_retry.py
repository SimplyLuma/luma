import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from luma_continuity.call_retry import CallRetry


class CallRetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.clock=1000
        self.binding=SimpleNamespace(pair_id='pair',account='account',device_id='device',peer='peer',epoch='epoch',role='requester')
        self.policy=self.make()

    def make(self):return CallRetry(self.root,[self.binding],now=lambda:self.clock,jitter=lambda a,b:1)

    def test_event_storm_cannot_reserve_more_than_one_attempt(self):
        self.assertTrue(self.policy.reserve('pair'))
        for _ in range(100):self.assertFalse(self.policy.reserve('pair'))
        self.assertEqual(self.make().rows['pair']['attempts'],1)

    def test_three_automatic_attempts_require_explicit_retry(self):
        for _ in range(3):
            self.assertTrue(self.policy.reserve('pair'));self.clock+=400
        self.assertEqual(self.policy.state('pair')['reason'],'retry_required')
        self.assertFalse(self.make().reserve('pair'))
        self.policy.retry('pair');self.assertTrue(self.policy.reserve('pair'))
        self.clock+=400
        self.assertFalse(self.policy.reserve('pair'))
        self.assertEqual(self.make().rows['pair']['attempts'],4)

    def test_full_server_cooldown_survives_reconstruction_and_explicit_retry(self):
        self.policy.reserve('pair');self.policy.rate_limited('pair',86400)
        other=self.make();other.retry('pair');self.clock+=3601
        self.assertFalse(other.reserve('pair'))
        self.assertEqual(other.state('pair'),dict(reason='rate_limited',retry_at=87400))
        self.clock=87400;self.assertTrue(other.reserve('pair'))

    def test_late_old_owner_cooldown_merges_with_new_owner(self):
        self.policy.reserve('pair');new=self.make()
        self.clock+=100;new.reserve('pair')
        self.policy.rate_limited('pair',86400)
        latest=self.make()
        self.assertEqual(latest.rows['pair']['attempts'],2)
        self.assertFalse(new.reserve('pair'))

    def test_only_authenticated_success_resets_attempts_not_server_cooldown(self):
        self.policy.reserve('pair');self.policy.rate_limited('pair',600)
        self.policy.authenticated('pair')
        latest=self.make();self.assertEqual(latest.rows['pair']['attempts'],0)
        self.assertEqual(latest.state('pair')['reason'],'rate_limited')
        self.clock+=600;self.assertTrue(latest.reserve('pair'))

    def test_corrupt_state_fails_closed(self):
        self.policy.reserve('pair');self.policy.path.write_text('{"version":1}')
        with self.assertRaises(ValueError):self.make()

    def test_pause_timer_cancellation_does_not_erase_persistent_bounds(self):
        self.policy.reserve('pair');self.policy.rate_limited('pair',60)
        self.assertFalse(self.make().reserve('pair'))
        self.clock+=60;self.assertTrue(self.make().reserve('pair'))

    def test_account_http_429_preserves_one_day_retry_after(self):
        import httpx
        from luma_continuity.account import AccountAPI,AccountConfig,AccountError
        with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(429,headers={'Retry-After':'86400'},json={'error':'call_attempt_limit'}))) as client:
            api=AccountAPI(AccountConfig('https://identity.example/realms/test','https://api.example','test'),client)
            with self.assertRaises(AccountError) as result:api.request('POST','/v1/call-relay-attempts','synthetic',{})
            self.assertEqual(result.exception.retry_after,86400)


    def test_invalid_document_shapes_and_symlink_fail_closed(self):
        import json
        for doc in ({'version':True,'identity':{},'rows':{}}, {'version':1,'identity':[],'rows':{}}, {'version':1,'identity':{},'rows':[]}):
            self.policy.path.write_text(json.dumps(doc));self.policy.path.chmod(0o600)
            with self.assertRaises(ValueError):self.make()
        self.policy.path.unlink();target=self.root/'other';target.write_text('{}')
        self.policy.path.symlink_to(target)
        with self.assertRaises(OSError):self.make()

    def test_server_retention_plus_start_deadline_is_not_truncated(self):
        self.policy.rate_limited('pair',86520)
        self.assertEqual(self.make().state('pair')['retry_at'],87520)

    def grant(self):return dict(recovery_id='55555555-5555-4555-8555-555555555555',expires=self.clock+600,purposes=['call-control','audio-signaling'])

    def test_recovery_once_per_purpose_preserves_capacity_cooldown(self):
        self.policy.rate_limited('pair',86400,'call_attempt_limit');grant=self.grant()
        self.assertTrue(self.policy.reserve_recovery('pair',grant,'call-control'))
        self.assertFalse(self.make().reserve_recovery('pair',grant,'call-control'))
        self.assertTrue(self.make().reserve_recovery('pair',grant,'audio-signaling'))
        self.assertFalse(self.make().reserve_recovery('pair',grant,'audio-signaling'))
        self.assertEqual(self.make().rows['pair']['capacity_until'],87400)
        self.assertFalse(self.make().reserve('pair'))

    def test_recovery_never_overrides_other_or_unknown_429(self):
        for code in (None,'rate_limited','unknown'):
            self.policy.rate_limited('pair',60,code)
            self.assertFalse(self.policy.reserve_recovery('pair',self.grant(),'call-control'))
        self.assertEqual(self.make().recoveries,{})

    def test_expired_or_wrong_purpose_grant_never_reserves(self):
        grant=self.grant();grant['expires']=self.clock
        self.assertFalse(self.policy.reserve_recovery('pair',grant,'call-control'))
        grant=self.grant();grant['purposes']=['call-control']
        self.assertFalse(self.policy.reserve_recovery('pair',grant,'audio-signaling'))

    def test_version_one_migration_preserves_unknown_cooldown(self):
        import json
        doc=dict(version=1,identity=self.policy.identity,rows={'pair':dict(attempts=2,next_try=1001,server_until=1600)})
        self.policy.path.write_text(json.dumps(doc));self.policy.path.chmod(0o600)
        new=self.make()
        self.assertFalse(new.reserve_recovery('pair',self.grant(),'call-control'))
        self.assertEqual(new.rows['pair']['server_until'],1600)

    def test_typed_capacity_error_rejects_unknown_duplicate_and_oversized_json(self):
        import httpx
        from luma_continuity.account import AccountAPI,AccountConfig,AccountError
        for body,expected in [(b'{"error":"call_attempt_limit"}','call_attempt_limit'),
                              (b'{"error":"unknown"}','rate_limited'),
                              (b'{"error":"rate_limited","error":"call_attempt_limit"}','rate_limited'),
                              (b'{"error":"call_attempt_limit"}'+b' '*1024,'rate_limited')]:
            with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(429,headers={'Retry-After':'86520','Content-Type':'application/json'},content=body))) as client:
                api=AccountAPI(AccountConfig('https://identity.example/realms/test','https://api.example','test'),client)
                with self.assertRaises(AccountError) as result:api.request('POST','/v1/call-relay-attempts','synthetic',{})
                self.assertEqual(result.exception.code,expected)
                self.assertEqual(result.exception.retry_after,86520)


    def test_authoritative_capacity_restore_preserves_all_other_bounds_and_consumption(self):
        self.policy.rate_limited('pair',86400,'call_attempt_limit')
        self.assertTrue(self.policy.reserve_recovery('pair',self.grant(),'call-control'))
        self.policy.rate_limited('pair',600,'rate_limited')
        before=dict(self.policy.rows['pair']);consumed=dict(self.policy.recoveries)
        self.policy.capacity_restored('pair');after=self.make()
        self.assertEqual(after.rows['pair']['capacity_until'],0)
        for key in ('attempts','next_try','server_until'):self.assertEqual(after.rows['pair'][key],before[key])
        self.assertEqual(after.recoveries,consumed)
        self.assertFalse(after.reserve('pair'))
        after.retry('pair');self.assertFalse(after.reserve('pair'))

    def test_capacity_restore_does_not_clear_legacy_unknown_server_cooldown(self):
        self.policy.rate_limited('pair',86400)
        self.policy.capacity_restored('pair')
        self.assertEqual(self.make().rows['pair']['server_until'],87400)
        self.assertFalse(self.make().reserve_recovery('pair',self.grant(),'call-control'))
