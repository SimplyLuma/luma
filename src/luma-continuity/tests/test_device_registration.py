from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from luma_continuity.bootstrap import create_identity
from luma_continuity.device_registration import DeviceRegistration
from luma_continuity.account import AccountError
from luma_continuity.policy import Journal


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)/'device';self.pin=create_identity(self.directory)
        self.identity=dict(issuer='https://identity.example/realms/luma',subject='subject',account_id='account',session_id='session')
        self.calls=[];self.clock=1000;self.enabled=True;self.fail=False
        def request(method,path,token,body):
            self.calls.append((method,path,body))
            if self.fail:raise OSError('synthetic lost response')
            return dict(id='12345678-1234-1234-1234-123456789abc',certificate_sha256=self.pin,revoked=False)
        self.model=SimpleNamespace(snapshot=lambda:dict(identity=dict(self.identity)),authority_token=lambda:'synthetic',api=SimpleNamespace(request=request))
        self.new=lambda:DeviceRegistration(self.directory,self.model,enabled=lambda:self.enabled,now=lambda:self.clock)
        self.registry=self.new()

    def test_real_pin_and_restart_reuses_receipt_without_another_post(self):
        first=self.registry.ensure('Synthetic device')
        self.assertEqual(first,self.new().ensure('Synthetic device'))
        self.assertEqual(self.calls,[('POST','/v1/devices',{'name':'Synthetic device','certificate_sha256':self.pin})])
        self.assertEqual(self.registry.path.stat().st_mode&0o777,0o600)

    def test_changed_session_requires_recovery_not_duplicate_registration(self):
        self.registry.ensure('Synthetic device');self.identity['session_id']='new-session'
        with self.assertRaises(AccountError):self.new().ensure('Synthetic device')
        self.assertEqual(len(self.calls),1)

    def test_wrong_local_pair_account_never_registers(self):
        journal=Journal(self.directory/'continuity.db')
        journal.approve('a'*64,'b'*32,['messages.read'],account='another-account');journal.close()
        with self.assertRaises(AccountError):self.registry.ensure('Synthetic device')
        self.assertEqual(self.calls,[])

    def test_lost_reply_has_persistent_bounded_retry_and_disabled_no_post(self):
        self.fail=True
        with self.assertRaises(OSError):self.registry.ensure('Synthetic device')
        self.fail=False
        self.assertEqual(self.new().ensure('Synthetic device')['status'],'retry_later')
        self.enabled=False;self.clock+=901
        self.assertEqual(self.new().ensure('Synthetic device')['status'],'disabled')
        self.assertEqual(len(self.calls),1)
        self.enabled=True
        self.assertEqual(self.new().ensure('Synthetic device')['status'],'registered')
        self.assertEqual(len(self.calls),2)
