# SPDX-License-Identifier: Apache-2.0
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock
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

    def test_windows_control_socket_reconnects_without_an_open_tcp_port(self):
        api = MagicMock();api.connect.return_value = 0
        stream = MagicMock();stream.fileno.return_value = 123
        with patch.object(host, 'windows', return_value=True), patch.object(host.socket, 'socket', return_value=stream) as create, patch('ctypes.WinDLL', create=True, return_value=api):
            for _ in range(2):
                self.assertIs(host.local_control_socket('C:/private/qmp', 7), stream)
        self.assertEqual(create.call_count, 2)
        create.assert_called_with(1, host.socket.SOCK_STREAM)
        self.assertEqual(api.connect.call_count, 2)
        address = api.connect.call_args.args[1]._obj
        self.assertEqual(address.family, 1)
        self.assertEqual(address.path, b'C:/private/qmp')
        stream.settimeout.assert_called_with(7)

    def test_windows_control_socket_closes_on_connect_failure(self):
        api = MagicMock();api.connect.return_value = -1;api.WSAGetLastError.return_value = 10061
        stream = MagicMock()
        with patch.object(host, 'windows', return_value=True), patch.object(host.socket, 'socket', return_value=stream), patch('ctypes.WinDLL', create=True, return_value=api):
            with self.assertRaises(OSError):host.local_control_socket('C:/private/qmp')
        stream.close.assert_called_once()

    def test_windows_control_socket_rejects_unrepresentable_paths(self):
        with patch.object(host, 'windows', return_value=True), patch.object(host.socket, 'socket') as create:
            for value in ['x'*108, 'private\0qmp']:
                with self.assertRaises(ValueError):host.local_control_socket(value)
        create.assert_not_called()
