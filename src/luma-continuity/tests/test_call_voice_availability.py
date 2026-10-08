import unittest
from luma_continuity.call_provider import CallProvider


class VoiceAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.requests=[];self.available=False
        def exchange(request):
            self.requests.append(request['capability'])
            result={'calls':[],'dial_token':'a'*32,'voice_available':self.available}
            if request['capability']=='calls.control':result={'accepted':False,'reason':'voice_unavailable'}
            return {'state':'complete','result':result}
        self.provider=CallProvider(exchange,account='account',epoch='epoch',authorized=lambda:True,
                                   control_authorized=lambda:True,subscribe=lambda callback:lambda:None,
                                   dispatch=lambda callback:callback())
        self.addCleanup(self.provider.close)

    def test_unavailable_snapshot_does_not_send_dial(self):
        with self.assertRaisesRegex(PermissionError,'Your phone cannot make calls'):
            self.provider.dial('+12025550123')
        self.assertEqual(self.requests,['calls.read'])

    def test_registration_lost_after_snapshot_shows_safe_reason(self):
        self.available=True
        with self.assertRaisesRegex(PermissionError,'Your phone cannot make calls'):
            self.provider.dial('+12025550123')
        self.assertEqual(self.requests,['calls.read','calls.control'])
