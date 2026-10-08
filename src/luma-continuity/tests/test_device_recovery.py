import base64
import json
from pathlib import Path
import unittest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from luma_continuity.account import AccountError
from luma_continuity.device_recovery import recover
import test_device_registration as registration_tests


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        registration_tests.RegistrationTests.setUp(self)
        self.registry.ensure('Synthetic device')
        self.before=self.registry.path.read_bytes()
        self.identifier=self.registry._load()['device_id']
        self.identity['session_id']='new-session'
        self.mutate=lambda p:None;self.bad_receipt=False;self.effect=[]
        def request(method,path,token,body=None):
            if method=='GET':raise AccountError('service_unavailable',403)
            if path.endswith('/recovery-challenges'):
                payload=dict(version=1,purpose='luma-connect-device-recovery',challenge_id='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
                    device_id=self.identifier,account_id=self.identity['account_id'],issuer=self.identity['issuer'],
                    session_id=self.identity['session_id'],certificate_sha256=self.pin,nonce='a'*64,expires=1050)
                self.mutate(payload)
                self.raw=json.dumps(payload).encode()
                return dict(challenge_id='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',expires=payload['expires'],payload_b64=base64.b64encode(self.raw).decode())
            self.assertTrue(path.endswith('/recover'))
            cert=x509.load_der_x509_certificate(base64.b64decode(body['certificate_der_b64']))
            cert.public_key().verify(base64.b64decode(body['signature_b64']),self.raw,ec.ECDSA(hashes.SHA256()))
            self.effect.append(True)
            return dict(device_id=self.identifier,account_id=self.identity['account_id'],session_id='wrong' if self.bad_receipt else 'new-session',certificate_sha256=self.pin,recovered=True)
        self.model.api.request=request

    def test_signature_and_same_uuid_receipt_preserve_pairing_files(self):
        journal=self.directory/'continuity.db';before=journal.read_bytes()
        result=recover(self.registry,now=lambda:1000)
        self.assertEqual(result['device_id'],self.identifier)
        self.assertEqual(self.registry._load()['binding']['session_id'],'new-session')
        self.assertEqual(journal.read_bytes(),before)
        self.assertEqual(self.effect,[True])
        self.assertEqual(recover(self.registry,now=lambda:1000),result)
        self.assertEqual(self.effect,[True])

    def test_wrong_scope_never_signs_or_changes_registration(self):
        for key,value in [('purpose','other'),('device_id','other'),('account_id','other'),('issuer','other'),('session_id','old'),('certificate_sha256','f'*64),('version',True),('expires',999),('expires',1061)]:
            with self.subTest(key=key,value=value):
                self.mutate=lambda p,k=key,v=value:p.update({k:v})
                with self.assertRaises(AccountError):recover(self.registry,now=lambda:1000)
                self.assertEqual(self.registry.path.read_bytes(),self.before)
                self.assertEqual(self.effect,[])

    def test_account_switch_never_requests_challenge(self):
        self.identity['account_id']='other'
        with self.assertRaises(AccountError):recover(self.registry,now=lambda:1000)
        self.assertFalse(hasattr(self,'raw'))

    def test_bad_receipt_never_rebinds(self):
        self.bad_receipt=True
        with self.assertRaises(AccountError):recover(self.registry,now=lambda:1000)
        self.assertEqual(self.registry.path.read_bytes(),self.before)

    def test_disabled_and_symlink_key_never_sign(self):
        self.enabled=False
        with self.assertRaises(AccountError):recover(self.registry,now=lambda:1000)
        self.enabled=True
        key=self.directory/'device.key';key.rename(self.directory/'retained.key');key.symlink_to('retained.key')
        with self.assertRaises(OSError):recover(self.registry,now=lambda:1000)
        self.assertEqual(self.effect,[])

    def test_account_changes_after_challenge_never_signs(self):
        original=self.model.authority_token;count=0
        def token():
            nonlocal count
            count+=1
            if count==3:self.identity['session_id']='another'
            return original()
        self.model.authority_token=token
        with self.assertRaises(AccountError):recover(self.registry,now=lambda:1000)
        self.assertEqual(self.effect,[])

    def test_lost_success_reads_own_session_receipt_without_replaying_proof(self):
        original=self.model.api.request
        def request(method,path,token,body=None):
            if method=='GET' and self.effect:
                return dict(device_id=self.identifier,account_id=self.identity['account_id'],session_id='new-session',certificate_sha256=self.pin)
            result=original(method,path,token,body)
            if path.endswith('/recover'):raise OSError('synthetic lost receipt')
            return result
        self.model.api.request=request
        self.assertEqual(recover(self.registry,now=lambda:1000)['device_id'],self.identifier)
        self.assertEqual(self.effect,[True])
        self.assertEqual(self.registry._load()['binding']['session_id'],'new-session')

    def test_restart_after_lost_success_reads_receipt_without_new_challenge(self):
        def request(method,path,token,body=None):
            self.assertEqual(method,'GET')
            self.assertTrue(path.endswith('/registration'))
            return dict(device_id=self.identifier,account_id=self.identity['account_id'],session_id='new-session',certificate_sha256=self.pin)
        self.model.api.request=request
        self.assertEqual(recover(self.registry,now=lambda:1000)['device_id'],self.identifier)
        self.assertEqual(self.effect,[])
