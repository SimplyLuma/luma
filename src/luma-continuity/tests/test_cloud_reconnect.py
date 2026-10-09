# SPDX-License-Identifier: Apache-2.0
"""Explicit code entry repairs a registration without discarding local data."""
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from luma_continuity.cloud_sync import CloudSync, DEFAULT_HUB, connect_problem
from luma_continuity.connect_cloud_contract import command_plan
from prairie_apps.connect_sync import (ConnectError, DeviceIdentity, HubResponseError,
    device_file, enrol, load_identity, save_identity)

class ExplicitReconnect(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)
        self.environment={'HOME':str(self.root/'home'),'XDG_DATA_HOME':str(self.root/'data')}
        self.identity=DeviceIdentity('prior-device','synthetic-revoked-token',DEFAULT_HUB,'Laptop','before')
        save_identity(self.identity,self.environment)
        self.local=self.root/'data/library.sqlite';self.local.write_bytes(b'owned notes and preferences fixture')
        self.service=self.root/'data/luma/connect/services.json';self.service.write_bytes(b'{"notes":false}')
        self.addCleanup(patch.stopall)
        self.system=patch('prairie_apps.connect_sync.subprocess.run').start()
    def submit(self,transport):
        args,timeout,body=command_plan({'operation':'connect','values':{'code':'ABCDEFGH','name':'Laptop'}})
        self.assertEqual(args[-1],'--force');self.assertEqual(timeout,60);self.assertIsNone(body)
        return enrol(code=args[4],hub=args[2],name=args[6],force=args[-1]=='--force',
            environment=self.environment,transport=transport,out=io.StringIO())
    def test_explicit_code_consumed_even_with_existing_revoked_registration(self):
        before=(self.local.read_bytes(),self.service.read_bytes());calls=[]
        def accept(url,payload,**kwargs):
            calls.append((url,payload));return {'device_id':'new-device','token':'synthetic-valid-token'}
        result=self.submit(accept)
        self.assertEqual(len(calls),1);self.assertEqual(calls[0][1]['code'],'ABCDEFGH')
        self.assertEqual(result.device_id,'new-device')
        self.assertEqual(load_identity(self.environment),result)
        self.assertNotEqual(result.token,self.identity.token)
        self.assertEqual((self.local.read_bytes(),self.service.read_bytes()),before)
        self.assertEqual(device_file(self.environment).stat().st_mode&0o777,0o600)
    def test_bad_code_network_failure_and_missing_token_preserve_identity_and_data(self):
        identity_bytes=device_file(self.environment).read_bytes()
        before=(self.local.read_bytes(),self.service.read_bytes())
        for failure in (HubResponseError(403,'Invalid code'),ConnectError('Network unavailable'),None):
            with self.subTest(failure=type(failure).__name__):
                def refuse(*args,**kwargs):
                    if failure is not None:raise failure
                    return {'device_id':'incomplete'}
                with self.assertRaises(ConnectError):self.submit(refuse)
                self.assertEqual(device_file(self.environment).read_bytes(),identity_bytes)
                self.assertEqual((self.local.read_bytes(),self.service.read_bytes()),before)
                self.assertEqual(load_identity(self.environment),self.identity)
        self.system.assert_not_called()
    def test_cli_without_explicit_force_remains_idempotent(self):
        def no_request(*args,**kwargs):raise AssertionError('Unexpected enrollment')
        self.assertEqual(enrol(code='ABCDEFGH',hub=DEFAULT_HUB,environment=self.environment,
            transport=no_request,out=io.StringIO()),self.identity)
    def test_native_code_entry_always_requests_replacement(self):
        calls=[];client=CloudSync();client._run=lambda *args,**kwargs:calls.append(args)
        client.connect('abcd efgh','Laptop',lambda problem:None)
        self.assertEqual(calls[0][0],['enrol','--hub',DEFAULT_HUB,'--code','ABCDEFGH','--name','Laptop','--force'])
    def test_typed_request_never_accepts_caller_selected_force_or_hub(self):
        for extra in ({'force':False},{'force':True},{'hub':'https://other.invalid'}):
            with self.assertRaises(ValueError):command_plan({'operation':'connect','values':{'code':'ABCDEFGH','name':'Laptop',**extra}})
    def test_older_host_wording_normalized_without_hiding_error(self):
        self.assertEqual(connect_problem('This computer needs to be connected to Luma Cloud again.'),
            'This computer needs to be connected to Luma Connect again.')
        self.assertEqual(connect_problem('Hub refused this code.'),'Hub refused this code.')
        self.assertIsNone(connect_problem(None))
    def test_sandbox_translates_existing_and_new_internal_argument_shapes(self):
        from gi.repository import Gio,GLib
        client=CloudSync();base=['enrol','--hub',DEFAULT_HUB,'--code','ABCDEFGH','--name','Laptop']
        with patch.dict(os.environ,{'FLATPAK_ID':'org.projectluma.Connect'}), \
             patch.object(Gio,'bus_get') as bus,patch.object(GLib,'idle_add',side_effect=lambda fn,*args:fn(*args)):
            errors=[]
            for args in (base,base+['--force']):client._remote(args,60,lambda *reply:errors.append(reply),None)
            self.assertEqual(bus.call_count,2);self.assertEqual(errors,[])
            client._remote(base+['--other'],60,lambda *reply:errors.append(reply),None)
            self.assertEqual(bus.call_count,2);self.assertIsNone(errors[0][0])

if __name__=='__main__':unittest.main()
