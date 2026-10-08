import unittest
from prairie_apps.phone_backend import NativeCall,CallPhase
from luma_continuity.native import NativeCalls


class CallControlTests(unittest.TestCase):
    def setUp(self):
        self.calls=[];self.actions=[];self.allowed=True;self.control=True;self.clock=100
        owner=self
        class Phone:
            def calls(self):return tuple(owner.calls)
            def dial(self,address):owner.actions.append(('dial',address))
            def accept(self,uid):owner.actions.append(('answer',uid))
            def hangup(self,uid):owner.actions.append(('hangup',uid))
            def decline(self,uid):owner.actions.append(('decline',uid))
        self.adapter=NativeCalls(Phone(),authorized=lambda:self.allowed,
            control_authorized=lambda:self.control,now=lambda:self.clock)
    def snapshot(self):return self.adapter('calls.read',{'operation':'snapshot'})

    def test_dial_is_one_shot_and_separate_consent_is_required(self):
        token=self.snapshot()['dial_token'];request=dict(operation='dial',token=token,address='+12025550123')
        self.control=False
        with self.assertRaises(PermissionError):self.adapter('calls.control',request)
        self.assertEqual(self.actions,[])
        self.control=True;request['token']=self.snapshot()['dial_token']
        self.adapter('calls.control',request)
        with self.assertRaises(PermissionError):self.adapter('calls.control',request)
        self.assertEqual(self.actions,[('dial','+12025550123')])

    def test_expired_dial_or_new_busy_call_never_executes(self):
        request=dict(operation='dial',token=self.snapshot()['dial_token'],address='+12025550123')
        self.clock=116
        with self.assertRaises(PermissionError):self.adapter('calls.control',request)
        request['token']=self.snapshot()['dial_token']
        self.calls=[NativeCall('new','+12025550123','incoming',CallPhase.INCOMING,started_at=100)]
        with self.assertRaises(PermissionError):self.adapter('calls.control',request)
        self.assertEqual(self.actions,[])

    def test_native_voice_loss_withholds_token_and_rejects_queued_dial(self):
        available=[True];self.adapter.voice_available=lambda:available[0]
        token=self.snapshot()['dial_token']
        available[0]=False
        result=self.adapter('calls.control',dict(operation='dial',token=token,address='+12025550123'))
        self.assertEqual(result,{'accepted':False,'reason':'voice_unavailable'})
        state=self.snapshot();self.assertFalse(state['voice_available']);self.assertIsNone(state['dial_token'])
        self.assertEqual(self.actions,[])
        available[0]=True
        self.assertTrue(self.snapshot()['voice_available']);self.assertIsNotNone(self.snapshot()['dial_token'])

    def test_stable_generation_rotating_controls_and_decline(self):
        self.calls=[NativeCall('native','+12025550123','incoming',CallPhase.INCOMING,started_at=100)]
        old=self.snapshot()['calls'][0];new=self.snapshot()['calls'][0]
        self.assertEqual(old['generation'],new['generation']);self.assertNotEqual(old['id'],new['id'])
        with self.assertRaises(PermissionError):self.adapter('calls.control',dict(operation='decline',call=old['id']))
        self.adapter('calls.control',dict(operation='decline',call=new['id']))
        self.assertEqual(self.actions,[('decline','native')])
        self.calls=[NativeCall('native','+12025550123','incoming',CallPhase.INCOMING,started_at=200)]
        self.assertNotEqual(new['generation'],self.snapshot()['calls'][0]['generation'])

    def test_decline_cannot_hang_up_an_answered_call(self):
        self.calls=[NativeCall('native','+12025550123','incoming',CallPhase.ACTIVE,started_at=100)]
        token=self.snapshot()['calls'][0]['id']
        with self.assertRaises(PermissionError):self.adapter('calls.control',dict(operation='decline',call=token))
        self.assertEqual(self.actions,[])

    def test_native_decline_rejection_never_falls_back_to_hangup(self):
        self.calls=[NativeCall('native','+12025550123','incoming',CallPhase.INCOMING,started_at=100)]
        token=self.snapshot()['calls'][0]['id']
        def declined_after_answer(_uid):
            raise PermissionError('native call already answered')
        self.adapter.transport.decline=declined_after_answer
        with self.assertRaises(PermissionError):
            self.adapter('calls.control',dict(operation='decline',call=token))
        self.assertEqual(self.actions,[])


if __name__=='__main__':unittest.main()
