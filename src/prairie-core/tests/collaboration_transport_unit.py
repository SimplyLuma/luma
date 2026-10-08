# SPDX-License-Identifier: Apache-2.0
"""Token-free metadata coalescing; real broker permission checks are not mocked as proof."""
import concurrent.futures
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from prairie_apps import collaboration_transport as transport
from prairie_apps.connect_sync import ConnectError, DeviceIdentity, HubResponseError

VALUE=DeviceIdentity('owned-device','','https://owned.invalid','Owned account','2026-10-07')

class Context(unittest.TestCase):
 def test_burst_is_coalesced_and_ttl_demands_new_success(self):
  now=[100.0];cache=transport._MetadataCache(lambda:now[0]);fetches=[]
  def fetch():fetches.append(True);return VALUE
  for _ in range(100):self.assertEqual(cache.get(fetch),VALUE)
  self.assertEqual(len(fetches),1);self.assertEqual(cache.value.token,'')
  now[0]=130.0;cache.get(fetch);self.assertEqual(len(fetches),2)
 def test_concurrent_readers_share_one_metadata_request(self):
  cache=transport._MetadataCache();fetches=[]
  def fetch():fetches.append(True);return VALUE
  with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
   values=list(pool.map(lambda _:cache.get(fetch),range(50)))
  self.assertEqual(values,[VALUE]*50);self.assertEqual(len(fetches),1)
 def test_signout_and_owner_signals_invalidate_cached_success(self):
  callbacks=[];subscriptions=[]
  connection=SimpleNamespace(signal_subscribe=lambda *args:(subscriptions.append(args),callbacks.append(args[-1]),len(callbacks))[2],signal_unsubscribe=lambda _:None)
  fake=SimpleNamespace(bus_get_sync=lambda *_:connection,BusType=SimpleNamespace(SESSION=0),DBusSignalFlags=SimpleNamespace(NONE=0))
  import sys
  cache=transport._MetadataCache();fetches=[]
  def fetch():fetches.append(True);return VALUE
  with patch.dict(sys.modules,{'gi':SimpleNamespace(repository=SimpleNamespace(Gio=fake)),'gi.repository':SimpleNamespace(Gio=fake)}),patch.object(transport,'_invalidation_connection',None),patch.object(transport,'_invalidation_subscriptions',[]),patch.object(transport,'_metadata',cache):
   transport._subscribe_invalidations();transport._subscribe_invalidations()
   self.assertEqual(len(callbacks),2)
   self.assertEqual(subscriptions[0][:5],(transport.BUS,transport.BUS,'StateChanged',transport.PATH,None))
   self.assertEqual(subscriptions[1][2:5],('NameOwnerChanged','/org/freedesktop/DBus',transport.BUS))
   cache.get(fetch)
   for callback in callbacks:callback();cache.get(fetch)
   self.assertEqual(len(fetches),3)
 def test_failed_refresh_removes_stale_identity_and_respects_retry_after(self):
  now=[0.0];cache=transport._MetadataCache(lambda:now[0]);cache.get(lambda:VALUE);now[0]=30.0
  error=HubResponseError(429,'Rate limit',retry_after=45)
  calls=[]
  def fail():calls.append(True);raise error
  for _ in range(10):
   with self.assertRaises(HubResponseError) as caught:cache.get(fail)
   self.assertEqual(caught.exception.status,429)
  self.assertEqual(len(calls),1);self.assertIsNone(cache.value)
  now[0]=75.0;self.assertEqual(cache.get(lambda:VALUE),VALUE)
 def test_lifecycle_failure_never_enables_cache_without_invalidation(self):
  cache=transport._MetadataCache();cache.get(lambda:VALUE)
  with patch.object(transport,'_metadata',cache),patch.object(transport,'_subscribe_invalidations',side_effect=ConnectError('no bus')),patch.object(transport,'_fresh_identity',return_value=VALUE) as fresh:
   for _ in range(3):self.assertEqual(transport.identity(),VALUE)
   self.assertEqual(fresh.call_count,3);self.assertIsNone(cache.value)
 def test_context_error_retains_true_status_and_no_bearer_is_returned(self):
  with patch.object(transport,'call',return_value={'error':{'status':403,'detail':'Denied','retry_after':0}}):
   with self.assertRaises(HubResponseError) as caught:transport._fresh_identity()
   self.assertEqual(caught.exception.status,403)
  values={'device_id':VALUE.device_id,'hub':VALUE.hub,'name':VALUE.name,'registered_at':VALUE.registered_at}
  with patch.object(transport,'call',return_value=values):self.assertEqual(transport._fresh_identity().token,'')
  with patch.object(transport,'call',return_value=values|{'token':'never-admit-a-bearer'}):
   with self.assertRaises(ConnectError):transport._fresh_identity()
 def test_actual_operations_are_not_served_from_metadata_cache(self):
  cache=transport._MetadataCache();cache.get(lambda:VALUE)
  with patch.object(transport,'_metadata',cache),patch.object(transport,'call',side_effect=[{'result':{'revision':1}},{'error':{'status':403,'detail':'Permission revoked'}}]) as method:
   http=transport.BrokerHTTP();self.assertEqual(http.post_json('https://owned.invalid/api/hub/sync/collaboration/documents',{}),{'revision':1})
   with self.assertRaises(HubResponseError):http.post_json('https://owned.invalid/api/hub/sync/collaboration/documents',{})
   self.assertEqual(method.call_count,2)

if __name__=='__main__':unittest.main(verbosity=2)
