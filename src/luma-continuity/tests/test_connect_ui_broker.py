# SPDX-License-Identifier: Apache-2.0
import json
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from luma_continuity.connect_ui_broker import APP, ConnectUIBroker, command_plan, require_connect_if_sandbox
from native_sender_fixture import NativeSender

class Invocation:
    def __init__(self): self.ready=threading.Event();self.error=None;self.value=None
    def return_dbus_error(self,name,message): self.error=(name,message);self.ready.set()
    def return_value(self,value): self.value=value;self.ready.set()
class GLib:
    @staticmethod
    def idle_add(callback): return callback()
    @staticmethod
    def Variant(kind,value): return value

class ConnectUI(unittest.TestCase):
    def test_fixed_commands_and_no_arbitrary_paths_options_hub_or_fields(self):
        self.assertEqual(command_plan({'operation':'status','values':{}}),(['status','--json'],45,None))
        self.assertEqual(command_plan({'operation':'service','values':{'id':'notes','enabled':False}})[0],['service','notes','off'])
        plan=command_plan({'operation':'profile','values':{'name':'Owned name','discoverable_by_phone':True}})
        self.assertEqual(plan[0],['profile','set','--stdin','--json']); self.assertEqual(json.loads(plan[2])['name'],'Owned name')
        plan=command_plan({'operation':'connect','values':{'code':'ABCD-1234','name':'Laptop'}})
        self.assertEqual(plan[0][2],'https://hub.simplyluma.com')
        for invalid in ({'operation':'status','values':{'path':'/etc/passwd'}},
            {'operation':'service','values':{'id':'secret','enabled':True}},
            {'operation':'service','values':{'id':'notes','enabled':1}},
            {'operation':'profile','values':{'token':'secret'}},
            {'operation':'profile','values':{'phone':'x'*257}},
            {'operation':'connect','values':{'code':'CODE','name':'Laptop','hub':'https://evil.invalid'}},
            {'operation':'shell','values':{}}, {'operation':'sign-out','values':{'device':'x\n--force'}}):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):command_plan(invalid)

    def test_auth_before_work_bounded_queue_and_reply_never_exposes_failure_detail(self):
        ran=[]
        def runner(args,timeout,body,cancelled):
            ran.append(args);return 0,'{"signed_in":false}',''
        broker=ConnectUIBroker(authenticate=lambda *_:APP,runner=runner);self.addCleanup(broker.close)
        values=[json.dumps({'operation':'status','values':{}})]
        request=Invocation();broker.dispatch(None,':1.2',values,request,GLib)
        self.assertTrue(request.ready.wait(2));self.assertIsNone(request.error)
        self.assertEqual(json.loads(request.value[0])['code'],0);self.assertEqual(ran,[['status','--json']])
        for auth in (lambda *_:'org.projectluma.Notes',lambda *_:(_ for _ in ()).throw(PermissionError('token path'))):
            broker.authenticate=auth;request=Invocation();broker.dispatch(None,':1.2',values,request,GLib)
            self.assertEqual(request.error[1],'Connect access is unavailable.')
        self.assertEqual(len(ran),1)
        broker.authenticate=lambda *_:APP
        broker.slots.acquire();broker.slots.acquire()
        try:
            request=Invocation();broker.dispatch(None,':1.2',values,request,GLib);self.assertIsNotNone(request.error)
        finally:broker.slots.release();broker.slots.release()
        self.assertEqual(len(ran),1)

    def test_native_real_pid_absence_and_foreign_uid_inspection_failure_closed(self):
        require_connect_if_sandbox(NativeSender(),':native','GetState')
        class Foreign(NativeSender):
            def call_sync(self,*args):return SimpleNamespace(unpack=lambda:(os.getuid()+1,))
        with self.assertRaises(PermissionError):require_connect_if_sandbox(Foreign(),':foreign','GetState')
        with patch('pathlib.Path.open',side_effect=PermissionError('inspection refused')):
            with self.assertRaises(PermissionError):require_connect_if_sandbox(NativeSender(),':native','GetState')
        with patch('pathlib.Path.open',side_effect=OSError('proc unavailable')):
            with self.assertRaises(OSError):require_connect_if_sandbox(NativeSender(),':native','GetState')

if __name__=='__main__':unittest.main()
