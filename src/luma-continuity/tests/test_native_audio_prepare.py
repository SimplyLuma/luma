from types import SimpleNamespace
import unittest
from luma_continuity.native import NativeCalls


class NativeAudioPrepareTests(unittest.TestCase):
    def setUp(self):
        self.prepared=[];self.now=1000
        self.call=SimpleNamespace(call_id='native-fixture',started_at=123,answered_at=125,
            phase=SimpleNamespace(value='active'),address='synthetic',direction='incoming')
        self.calls=[self.call]
        self.native=NativeCalls(SimpleNamespace(calls=lambda:self.calls),authorized=lambda:True,now=lambda:self.now,
            audio_prepare=lambda *args:self.prepared.append(args))
        self.row=self.native('calls.read',{'operation':'snapshot'})['calls'][0]
        self.payload=dict(operation='prepare',call=self.row['id'],generation=self.row['generation'],
            attempt_id='11111111-1111-4111-8111-111111111111')

    def test_fresh_exact_call_preparation_consumes_capability(self):
        self.assertEqual(self.native('calls.audio',self.payload),{'prepared':True})
        self.assertEqual(self.prepared,[(self.call,self.row['generation'],self.payload['attempt_id'])])
        with self.assertRaises(PermissionError):self.native('calls.audio',self.payload)

    def test_changed_generation_or_phase_never_prepares(self):
        self.call.phase.value='ended'
        with self.assertRaises(PermissionError):self.native('calls.audio',self.payload)
        self.assertEqual(self.prepared,[])

    def test_expired_capability_never_prepares(self):
        self.now=1016
        with self.assertRaises(PermissionError):self.native('calls.audio',self.payload)
        self.assertEqual(self.prepared,[])
