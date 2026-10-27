# SPDX-License-Identifier: Apache-2.0
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from luma_tester import host
from luma_tester.core import Session, TesterError


class HostTests(unittest.TestCase):
    def test_windows_byte_lease_does_not_grow_on_each_lock(self):
        calls=[]
        api=SimpleNamespace(LK_NBLCK=2,locking=lambda *args:calls.append(args))
        with tempfile.TemporaryFile('a+b') as stream, patch.object(host,'windows',return_value=True), patch.dict('sys.modules',msvcrt=api):
            host.lock(stream);host.lock(stream)
            stream.seek(0,2)
            self.assertEqual(stream.tell(),1)
        self.assertEqual(len(calls),2)
        self.assertTrue(all(item[1:]==(2,1) for item in calls))

    def test_qmp_stream_skips_events_and_keeps_errors(self):
        class Duplex:
            def __init__(self, replies):self.replies=iter(replies);self.requests=[]
            def readline(self,*args):return json.dumps(next(self.replies)).encode()+b'\n'
            def write(self,value):self.requests.append(json.loads(value))
        stream=Duplex([{'QMP':{}},{'event':'RESET'},{'return':{}},{'return':{'status':'running'}}])
        self.assertEqual(Session.qmp_stream(stream,'query-status'),{'status':'running'})
        self.assertEqual([v['execute'] for v in stream.requests],['qmp_capabilities','query-status'])
        with self.assertRaises(TesterError):Session.qmp_stream(Duplex([{'QMP':{}},{'error':{'desc':'denied'}}]),'quit')

    def test_windows_disabled_hypervisor_is_not_reported_as_accelerated(self):
        with patch.object(host,'windows',return_value=True),patch('ctypes.WinDLL',create=True,side_effect=OSError('not installed')):
            self.assertFalse(host.whpx_available())
