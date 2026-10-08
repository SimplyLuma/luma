import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from luma_continuity.account import AccountError
from luma_continuity.bootstrap import create_identity
from luma_continuity.policy import Journal
from luma_continuity.relay_binding import load_bindings
from luma_continuity.relay_enrollment import RelayEnrollment,registered_relay_account


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.calls=[];self.pairs=[];self.enabled=True
        self.ids=['11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222']
        self.dirs=[self.root/'a',self.root/'b'];self.pins=[create_identity(p) for p in self.dirs]
        self.identities=[dict(issuer='https://identity.example',subject='subject',account_id='account',session_id='session'+str(i)) for i in range(2)]
        self.devices=[dict(id=self.ids[i],certificate_sha256=self.pins[i],revoked=False) for i in range(2)]
        self.owners=[];self.lost=False;self.mutate=None
        for i in range(2):
            journal=Journal(self.dirs[i]/'continuity.db')
            journal.approve(self.pins[1-i],'a'*32,['messages.read'] if i else [],account='account',outgoing_grants=[] if i else ['messages.read'])
            journal.close()
            saved=dict(binding=dict(self.identities[i],certificate_sha256=self.pins[i]),device_id=self.ids[i])
            model=SimpleNamespace(snapshot=lambda i=i:dict(identity=dict(self.identities[i])),authority_token=lambda i=i:'token'+str(i),
                api=SimpleNamespace(request=self.request))
            self.owners.append(RelayEnrollment(self.dirs[i],SimpleNamespace(_load=lambda s=saved:copy.deepcopy(s)),model,
                self.root/('config'+str(i))/'relay-bindings.json',enabled=lambda:self.enabled,now=lambda:1000))

    def request(self,method,path,token,body=None):
        self.calls.append((method,path,token,body))
        if self.mutate:self.mutate();self.mutate=None
        if path=='/v1/devices':return {'items':copy.deepcopy(self.devices)}
        if path=='/v1/pairings' and method=='GET':return {'items':copy.deepcopy(self.pairs)}
        if path=='/v1/pairings':
            pair=dict(id='33333333-3333-4333-8333-333333333333',left=body['device_id'],right=body['peer_device_id'],
                      left_approved=False,right_approved=False,revoked=False,expires=1300)
            self.pairs.append(pair)
            if self.lost:raise OSError('lost response')
            return dict(pair,fingerprints={d['id']:d['certificate_sha256'] for d in self.devices})
        pair=self.pairs[0];side='left' if body['device_id']==pair['left'] else 'right'
        self.assertEqual(token,'token'+str(self.ids.index(body['device_id'])))
        pair[side+'_approved']=True
        return {'id':pair['id'],'state':'paired'}

    def enroll(self,i):return self.owners[i].enroll(self.pins[1-i],self.ids[1-i],'receiver' if i else 'requester')

    def test_mutual_approval_preserves_native_journals_and_reuses_pair(self):
        before=[(p/'continuity.db').read_bytes() for p in self.dirs]
        self.assertEqual(self.enroll(0)['status'],'awaiting_peer')
        self.assertFalse(self.owners[0].path.exists())
        self.assertEqual(self.enroll(1)['status'],'enrolled')
        self.assertEqual(self.enroll(0)['status'],'enrolled')
        self.enroll(0)
        self.assertEqual(len([c for c in self.calls if c[:2]==('POST','/v1/pairings')]),1)
        for i in range(2):
            self.assertEqual((self.dirs[i]/'continuity.db').read_bytes(),before[i])
            self.assertEqual(load_bindings(self.owners[i].path)[0].peer,self.pins[1-i])
            self.assertEqual(self.owners[i].path.stat().st_mode&0o777,0o600)

    def test_server_pin_never_replaces_local_trust(self):
        self.devices[1]['certificate_sha256']='f'*64
        with self.assertRaises(AccountError):self.enroll(0)
        self.assertFalse(any(c[0]=='POST' for c in self.calls))

    def test_lost_create_response_recovers_existing_pair(self):
        self.lost=True
        with self.assertRaises(OSError):self.enroll(0)
        self.lost=False
        self.assertEqual(self.enroll(0)['status'],'awaiting_peer')
        self.assertEqual(len(self.pairs),1)

    def test_disabled_changed_session_and_wrong_direction_fail_closed(self):
        self.enabled=False
        with self.assertRaises(AccountError):self.enroll(0)
        self.enabled=True;self.identities[0]['session_id']='replacement'
        with self.assertRaises(AccountError):self.enroll(0)
        with self.assertRaises(AccountError):self.owners[1].enroll(self.pins[0],self.ids[0],'requester')
        self.assertEqual(self.calls,[])

    def test_identity_loss_during_request_never_approves(self):
        self.mutate=lambda:self.identities[0].update(session_id='replacement')
        with self.assertRaises(AccountError):self.enroll(0)
        self.assertFalse(any(c[0]=='POST' for c in self.calls))

    def test_ambiguous_matching_pairs_never_approves(self):
        self.enroll(0);self.pairs.append(dict(self.pairs[0],id='44444444-4444-4444-8444-444444444444'))
        self.calls.clear()
        with self.assertRaises(AccountError):self.enroll(1)
        self.assertFalse(any(c[0]=='POST' for c in self.calls))

    def test_runtime_requires_exact_registration_and_handles_corruption(self):
        self.enroll(0);self.enroll(1);self.enroll(0)
        owner=self.owners[0];bindings=load_bindings(owner.path)
        def check():return registered_relay_account(owner.directory,owner.registration,owner.model,bindings,'account')
        self.assertEqual(check(),'account')
        saved=owner.registration._load()
        for bad in (None,dict(saved,device_id=self.ids[1]),
                    dict(saved,binding=dict(saved['binding'],certificate_sha256='f'*64)),
                    dict(saved,binding=dict(saved['binding'],session_id='different'))):
            with self.subTest(receipt=bool(bad)):
                owner.registration._load=lambda:bad
                self.assertIsNone(check())
        def corrupted():raise ValueError('invalid JSON')
        owner.registration._load=corrupted
        self.assertIsNone(check())
