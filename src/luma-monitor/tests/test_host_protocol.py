# SPDX-License-Identifier: Apache-2.0
import unittest
import json
from luma_monitor.model import Process, Identity
from luma_monitor.host_protocol import encode, decode, MAX_BYTES

class HostProtocolTests(unittest.TestCase):
    def sample(self):
        p=Process(321,123,1000,'editor','S',3,'/app-editor.scope',Identity('io.Editor','Editor'),12.5,3.0,2048,None,None,4.0,12,4096)
        return dict(processes=[p],rows=[dict(id='app:io.Editor',members=[p],background=False,name='Editor',cpu=12.5)],time=42,memory={'MemTotal':8192})
    def test_actual_identity_measurements_and_row_members_roundtrip(self):
        original=self.sample();restored=decode(encode(original,{1000:'Tester'}))
        self.assertEqual(restored['processes'],original['processes'])
        self.assertIs(restored['rows'][0]['members'][0],restored['processes'][0])
        self.assertEqual(restored['users'],{1000:'Tester'})
    def test_missing_or_duplicate_process_cannot_rebind_row(self):
        payload=json.loads(encode(self.sample(),{}));payload['processes']*=2
        with self.assertRaises(ValueError):decode(json.dumps(payload))
        payload=json.loads(encode(self.sample(),{}));payload['rows'][0]['members']=['pid:321:999']
        with self.assertRaises(KeyError):decode(json.dumps(payload))
        with self.assertRaises(ValueError):decode(' '*(MAX_BYTES+1))

if __name__=='__main__':unittest.main()
