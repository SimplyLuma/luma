import unittest
import test_call_relay_api as api_fixture
from luma_continuity.call_relay_directory import CallRelayDirectory


class CallDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.f=api_fixture.CallRelayAPITests();self.f.setUp()
        self.d=CallRelayDirectory(self.f.device,[self.f.pair],self.f.origin,now=lambda:1000)

    def test_same_generation_lease_extension_preserves_channel(self):
        self.d.apply('session_offered','first',self.f.metadata)
        extended=dict(self.f.metadata,expires=1700)
        self.assertEqual(self.d.apply('session_offered','renewed',extended),[])
        self.assertEqual(self.d.sessions[(self.f.pair,'call-control')].expires,1700)

    def test_fresh_snapshot_absence_closes_old_channel(self):
        self.d.apply('session_offered','first',self.f.metadata)
        obsolete=self.d.apply('snapshot','fresh',dict(device_id=self.f.device,sessions=[],reset=True))
        self.assertEqual(len(obsolete),1);self.assertEqual(self.d.sessions,{})

    def test_new_generation_replaces_and_wrong_purpose_rejected(self):
        self.d.apply('session_offered','first',self.f.metadata)
        changed=dict(self.f.metadata,session_id=self.f.device,generation=self.f.device,
            url='wss://api.example/v1/call-relay/call-control/'+self.f.device)
        self.assertEqual(len(self.d.apply('session_offered','second',changed)),1)
        with self.assertRaises(ValueError):self.d.apply('session_offered','bad',dict(changed,purpose='messages'))
        self.assertEqual(self.d.cursor,'second')
