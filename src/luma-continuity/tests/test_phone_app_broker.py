# SPDX-License-Identifier: Apache-2.0
from contextlib import closing
import json,os,sqlite3,shutil,subprocess,sys,tempfile,time,threading,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from luma_continuity.phone_app_broker import PhoneAppBroker
from luma_continuity.call_provider import CallProvider,_public_selection,_selected_provider

class Invocation:
 def __init__(self):self.event=threading.Event();self.error=None;self.value=None
 def return_dbus_error(self,name,message):self.error=(name,message);self.event.set()
 def return_value(self,value):self.value=value;self.event.set()
class GLib:
 @staticmethod
 def idle_add(callback):return callback()
 @staticmethod
 def Variant(kind,value):return value
class Provider:
 def __init__(self):self.rows=[];self.actions=[];self.allowed=True
 def authorized(self):return self.allowed
 def control_authorized(self):return self.allowed
 def calls(self):return self.rows
 def dial(self,number):self.actions.append(('dial',number))
 def accept(self,identifier):self.actions.append(('answer',identifier))
 def close(self):self.actions.append(('close',))
class Phone(unittest.TestCase):
 def setUp(self):
  self.selected=NS(peer='a'*64,epoch='b'*32,account=None,label='Owned phone',carrier='companion')
  self.broker=PhoneAppBroker(NS(),authenticate=lambda *_:'org.projectluma.Phone')
  self.addCleanup(self.broker.close)
 def request(self,capability,payload,**extra):
  return dict(version=1,account=None,epoch='b'*32,id=os.urandom(16).hex(),capability=capability,expires=int(time.time())+10,payload=payload,**extra)
 def test_context_is_endpoint_free_strict_and_invalid_selection_never_local_fallback(self):
  self.broker._selection=lambda:self.selected
  context=self.broker.context();self.assertEqual(vars(_public_selection(context)),vars(self.selected))
  self.assertNotIn('address',context);self.assertIsNone(_public_selection({'selected':False}))
  for bad in ({'selected':0},dict(context,peer='bad'),dict(context,epoch='x'*32),dict(context,account='wrong'),dict(context,address='127.0.0.1')):
   with self.assertRaises(ValueError):_public_selection(bad)
  self.broker._selection=lambda:None;self.assertEqual(self.broker.context(),{'selected':False})
 def test_exact_public_client_subset_imports_and_validates_without_host_owners(self):
  with tempfile.TemporaryDirectory() as root:
   package=Path(root)/'luma_continuity';package.mkdir()
   source=Path(__file__).resolve().parents[1]/'luma_continuity'
   for name in ('__init__.py','phone_contract.py','call_provider.py'):shutil.copyfile(source/name,package/name)
   code="""import sys,importlib.abc
sys.path.insert(0,sys.argv[1])
class RefuseHost(importlib.abc.MetaPathFinder):
 def find_spec(self,name,*args):
  if name in ('luma_continuity.daemon','luma_continuity.account','luma_continuity.policy','luma_continuity.transport') or name.startswith('cryptography'):
   raise AssertionError('Host owner imported: '+name)
sys.meta_path.insert(0,RefuseHost())
from luma_continuity.call_provider import CallProvider,_public_selection
assert _public_selection({'selected':False}) is None
p=CallProvider(lambda _:dict(state='complete',result=dict(calls=[],dial_token=None,voice_available=False)),account=None,epoch='a'*32,authorized=lambda:True,control_authorized=lambda:True,subscribe=lambda _:lambda:None,dispatch=lambda cb:cb())
try:assert p._snapshot()==dict(calls=[],dial_token=None,voice_available=False)
finally:p.close()
"""
   run=subprocess.run([sys.executable,'-I','-c',code,root],capture_output=True,text=True,timeout=10)
   self.assertEqual(run.returncode,0,run.stderr)
 def test_native_no_selection_does_not_require_connect_or_hide_local_modem(self):
  with tempfile.TemporaryDirectory() as home,patch.object(Path,'home',return_value=Path(home)),patch.dict(os.environ,{'FLATPAK_ID':''}),patch('gi.repository.Gio.bus_get_sync',side_effect=AssertionError('Connect must not be required')):
   self.assertIsNone(_selected_provider(dispatch=lambda cb:cb()))
 def test_refused_app_foreign_owner_failure_bad_arguments_and_bounded_queue_do_not_read_data(self):
  self.broker.history=lambda:(_ for _ in ()).throw(AssertionError('must not read'))
  for auth in (lambda *_:'org.projectluma.Notes',lambda *_:(_ for _ in ()).throw(PermissionError('secret key path'))):
   self.broker.authenticate=auth;i=Invocation();self.broker.dispatch(None,':1.2','GetPhoneHistory',[],i,GLib)
   self.assertTrue(i.event.wait(1));self.assertEqual(i.error[1],'Phone access is unavailable.')
  self.broker.authenticate=lambda *_:'org.projectluma.Phone'
  for method,values in [('Arbitrary',[]),('GetPhoneTogether',['x'*8193]),('GetPhoneHistory',['wrong'])]:
   i=Invocation();self.broker.dispatch(None,':1.2',method,values,i,GLib);self.assertIsNotNone(i.error)
  self.broker.slots.acquire();self.broker.slots.acquire()
  try:
   i=Invocation();self.broker.dispatch(None,':1.2','GetPhoneHistory',[],i,GLib);self.assertIsNotNone(i.error)
  finally:self.broker.slots.release();self.broker.slots.release()
 def test_actual_sqlite_queries_only_latest_matching_contact_and_live_bounded_calls(self):
  with tempfile.TemporaryDirectory() as root:
   home=Path(root);path=home/'.local/share/prairie/messages/messages.db';path.parent.mkdir(parents=True)
   with closing(sqlite3.connect(path)) as db:
    db.execute('CREATE TABLE messages(address TEXT,body TEXT,timestamp INTEGER,direction TEXT)')
    db.executemany('INSERT INTO messages VALUES(?,?,?,?)',[('+1 555','old',1,'incoming'),('+1555','A'*300,2,'outgoing'),('+1888','unrelated private body',3,'incoming')])
    db.commit()
   with patch.object(Path,'home',return_value=home),patch.dict(os.environ,{'XDG_DATA_HOME':str(home/'.local/share')}):
    result=self.broker.together('+1 (555)');self.assertEqual(result,{'message':{'body':'A'*45,'timestamp':2,'direction':'outgoing'}})
    self.assertEqual(self.broker.together('999'),{'message':None})
    for invalid in ('','1'*129,'1'*33):
     with self.assertRaises(ValueError):self.broker.together(invalid)
   history=home/'history.db'
   with closing(sqlite3.connect(history)) as db:
    db.execute('CREATE TABLE calls(id TEXT,device_id TEXT,direction TEXT,started_at INTEGER,duration_s INTEGER,answered INTEGER,number TEXT,contact_uid TEXT,deleted INTEGER)')
    db.executemany('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?)',[(str(i),'owned','in',i,10,1,'555','',0)for i in range(600)])
    db.execute("INSERT INTO calls VALUES('deleted','owned','in',1000,10,1,'secret','',1)")
    db.commit()
   with patch('prairie_apps.connect_messages.cloud_history_path',return_value=history):
    rows=self.broker.history()['calls'];self.assertEqual(len(rows),512);self.assertEqual(rows[0]['id'],'599');self.assertNotIn('secret',json.dumps(rows))
 def test_companion_generation_mapping_typed_snapshot_replay_epoch_and_one_shot_dial(self):
  provider=Provider();self.broker._provider=lambda:(provider,self.selected);self.broker._selection=lambda:self.selected
  snapshot=self.broker.companion_request(self.request('calls.read',{'operation':'snapshot'}))['result']
  request=self.request('calls.control',{'operation':'dial','token':snapshot['dial_token'],'address':'555'})
  self.assertEqual(self.broker.companion_request(request)['result'],{'accepted':True})
  with self.assertRaises(PermissionError):self.broker.companion_request(request)
  with self.assertRaises(PermissionError):self.broker.companion_request(self.request('calls.control',request['payload']))
  self.assertEqual(provider.actions,[('dial','555')])
  provider.rows=[NS(call_id='4:/org/pipewire/call/1',address='555',direction='incoming',phase=NS(value='incoming'),started_at=1,answered_at=0)]
  result=self.broker.companion_request(self.request('calls.read',{'operation':'snapshot'}))['result']
  self.assertEqual(len(result['calls'][0]['generation']),32)
  checker=CallProvider(lambda _:dict(state='complete',result=result),account=None,epoch='b'*32,authorized=lambda:True,control_authorized=lambda:True,subscribe=lambda _:lambda:None,dispatch=lambda cb:cb())
  self.addCleanup(checker.close);self.assertEqual(checker._snapshot()['calls'],result['calls'])
  self.broker.companion_request(self.request('calls.control',{'operation':'answer','call':result['calls'][0]['id']}))
  self.assertEqual(provider.actions[-1],('answer','4:/org/pipewire/call/1'))
  wrong=self.request('calls.read',{'operation':'snapshot'});wrong['epoch']='c'*32
  with self.assertRaises(PermissionError):self.broker.companion_request(wrong)
  provider.allowed=False
  with self.assertRaises(PermissionError):self.broker.companion_request(self.request('calls.read',{'operation':'snapshot'}))
 def test_disconnect_or_close_preserves_other_client_then_restores_companion_audio(self):
  connection=NS(signal_unsubscribe=lambda n:None);self.broker.daemon.connection=connection
  provider=Provider();self.broker.companion=provider;self.broker.senders={':1.2':1,':1.3':2}
  self.broker._forget_sender(connection,':1.2');self.assertEqual(provider.actions,[])
  self.broker._forget_sender(connection,':1.3');self.assertEqual(provider.actions,[('close',)]);self.assertIsNone(self.broker.companion)

if __name__=='__main__':unittest.main()
