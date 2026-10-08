from types import SimpleNamespace
import unittest
from luma_continuity.call_relay_api import CallRelayAPI,CallRelaySession
from luma_continuity.relay_control import RelaySession


class CallRelayAPITests(unittest.TestCase):
    def setUp(self):
        self.device='11111111-1111-4111-8111-111111111111'
        self.pair='22222222-2222-4222-8222-222222222222'
        self.attempt='33333333-3333-4333-8333-333333333333'
        self.sid='44444444-4444-4444-8444-444444444444'
        self.origin='https://api.example';self.calls=[];self.allowed=True
        self.metadata=dict(session_id=self.sid,generation=self.sid,pair_id=self.pair,expires=1600,
            url='wss://api.example/v1/call-relay/call-control/'+self.sid,max_frame_bytes=65536,
            max_total_bytes=10485760,end_to_end_tls_required=True,purpose='call-control',attempt_id=self.attempt)
        def request(method,path,token,body=None):
            self.calls.append((method,path,body))
            if path.endswith('attempts'):return dict(attempt_id=self.attempt,pair_id=self.pair,purpose='call-control',start_deadline=1120)
            return dict(self.metadata)
        api=SimpleNamespace(config=SimpleNamespace(api_origin=self.origin),request=request)
        self.api=CallRelayAPI(api,self.device,bearer=lambda:'synthetic',authorized=lambda:self.allowed,now=lambda:1000)

    def test_server_mint_ensure_and_same_generation_renew(self):
        attempt=self.api.mint(self.pair,'call-control')
        session=self.api.ensure_call(self.pair,attempt['attempt_id'],'call-control')
        self.assertEqual(self.api.renew(session),session)
        self.assertEqual(self.calls[-1][2],dict(device_id=self.device,generation=self.sid,purpose='call-control'))

    def test_messages_and_calls_metadata_cannot_be_interchanged(self):
        with self.assertRaises(ValueError):RelaySession.parse(self.metadata,self.origin)
        message=dict(self.metadata);message.pop('purpose');message.pop('attempt_id')
        message['url']='wss://api.example/v1/relay/'+self.sid
        with self.assertRaises((ValueError,TypeError)):CallRelaySession.parse(message,self.origin)
        wrong=dict(self.metadata,url='wss://api.example/v1/call-relay/audio-signaling/'+self.sid)
        with self.assertRaises(ValueError):CallRelaySession.parse(wrong,self.origin)

    def test_wrong_attempt_or_expired_metadata_rejected(self):
        with self.assertRaises(PermissionError):self.api.ensure_call(self.pair,self.device,'call-control')
        self.metadata['expires']=999
        with self.assertRaises(PermissionError):self.api.ensure_call(self.pair,self.attempt,'call-control')

    def test_disabled_authority_never_mints_or_renews(self):
        self.allowed=False
        with self.assertRaises(PermissionError):self.api.mint(self.pair,'call-control')
        self.assertEqual(self.calls,[])


    def test_recovery_admission_and_explicit_mint_binding(self):
        grant=dict(recovery_id=self.attempt,expires=1500,purposes=['call-control'])
        original=self.api.api.request
        def request(method,path,token,body=None):
            if method=='GET':
                self.assertEqual(path,'/v1/devices/'+self.device+'/call-relay-admission/'+self.pair)
                return {'recovery':grant}
            return original(method,path,token,body)
        self.api.api.request=request
        self.assertEqual(self.api.admission(self.pair),grant)
        self.api.mint(self.pair,'call-control',grant['recovery_id'])
        self.assertEqual(self.calls[-1][2]['recovery_id'],self.attempt)

    def test_recovery_schema_expiry_and_authority_are_strict(self):
        for grant in ({'recovery_id':self.attempt,'expires':999,'purposes':['call-control']},
                      {'recovery_id':self.attempt,'expires':1500,'purposes':['call-control','call-control']},
                      {'recovery_id':self.attempt,'expires':1500,'purposes':['messages']},
                      {'recovery_id':self.attempt,'expires':1500,'purposes':[],'extra':True}):
            self.api.api.request=lambda *args,grant=grant,**kw:{'recovery':grant}
            with self.assertRaises(PermissionError):self.api.admission(self.pair)
        self.allowed=False
        with self.assertRaises(PermissionError):self.api.admission(self.pair)

    def test_normal_capacity_status_is_explicit_and_legacy_is_unknown(self):
        self.api.api.request=lambda *args,**kw:{'recovery':None,'normal_capacity_available':True}
        self.assertEqual(self.api.admission_status(self.pair),dict(recovery=None,normal_capacity_available=True))
        self.api.api.request=lambda *args,**kw:{'recovery':None}
        self.assertIsNone(self.api.admission_status(self.pair)['normal_capacity_available'])
        for value in (1,'true',None,[]):
            self.api.api.request=lambda *args,value=value,**kw:{'recovery':None,'normal_capacity_available':value}
            with self.assertRaises(PermissionError):self.api.admission_status(self.pair)
