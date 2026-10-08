import json
import time
import unittest
from types import SimpleNamespace
from luma_continuity.relay_binding import RelayBinding, BoundRelayExchange


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.binding=RelayBinding('account','a'*64,'e'*32,
            '11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222','requester')
        self.bound=BoundRelayExchange('/unused',self.binding,SimpleNamespace(device_id=self.binding.device_id),account=lambda:'account')
        self.calls=[]
        self.bound.exchange=lambda request:self.calls.append(request) or {'state':'complete','result':{}}
        self.request=dict(version=1,epoch='e'*32,id='b'*32,account='account',capability='messages.read',expires=int(time.time())+60,payload={})

    def test_same_request_is_forwarded_without_rewrite(self):
        self.bound.call('a'*64,json.dumps(self.request))
        self.assertEqual(self.calls,[self.request])

    def test_untrusted_peer_account_epoch_capability_and_expiry_fail_before_network(self):
        for key,value in [('account','other'),('epoch','d'*32),('capability','calls.control'),('expires',0),('id','x'),('destination','https://attacker.test')]:
            request={**self.request,key:value}
            with self.subTest(key=key),self.assertRaises(PermissionError): self.bound.call('a'*64,json.dumps(request))
        with self.assertRaises(PermissionError):self.bound.call('c'*64,json.dumps(self.request))
        self.assertEqual(self.calls,[])

    def test_duplicate_fields_and_oversize_fail(self):
        for value in ['{"id":"a","id":"b"}', 'x'*(1024*1024+1)]:
            with self.assertRaises(ValueError):self.bound.call('a'*64,value)
        self.assertEqual(self.calls,[])

    def test_no_rebind_on_account_change(self):
        self.bound.account=lambda:'other'
        with self.assertRaises(PermissionError):self.bound.call('a'*64,json.dumps(self.request))
        self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
