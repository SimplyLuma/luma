# SPDX-License-Identifier: Apache-2.0
import json,os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from prairie_apps.collaboration_ownership import profile_ready

class Ownership(unittest.TestCase):
 def test_host_defers_without_creating_profile_then_accepts_only_matching_completed_receipt(self):
  with tempfile.TemporaryDirectory() as root:
   home=Path(root);env={'HOME':root};data=home/'.var/app/org.projectluma.Notes/data'
   with patch('prairie_apps.app_installs.resolve',return_value=SimpleNamespace(data_home=data)):
    self.assertFalse(profile_ready('org.projectluma.Notes',env));self.assertFalse((home/'.var').exists())
    p=home/'.local/state/luma/app-migration/org.projectluma.Notes.ready.json';p.parent.mkdir(parents=True)
    doc={'schema':'org.projectluma.app-data-ready/v1','app_id':'org.projectluma.Notes','result':'pending','receipt_sha256':'a'*64}
    p.write_text(json.dumps(doc));self.assertFalse(profile_ready('org.projectluma.Notes',env))
    doc['result']='PASS';doc['app_id']='org.projectluma.Tasks';p.write_text(json.dumps(doc));self.assertFalse(profile_ready('org.projectluma.Notes',env))
    doc['app_id']='org.projectluma.Notes';p.write_text(json.dumps(doc));self.assertTrue(profile_ready('org.projectluma.Notes',env))
    doc['receipt_sha256']='not a digest';p.write_text(json.dumps(doc));self.assertFalse(profile_ready('org.projectluma.Notes',env))
    doc['receipt_sha256']='a'*64;p.write_text(json.dumps(doc))
    p.write_text(json.dumps(doc)+' '*65536);self.assertFalse(profile_ready('org.projectluma.Notes',env));p.write_text(json.dumps(doc))
    # A large full receipt is not loaded: the broker publishes a bounded final marker.
    p.with_name('org.projectluma.Notes.json').write_text(' '*70000);self.assertTrue(profile_ready('org.projectluma.Notes',env))
    p.chmod(0o666);self.assertFalse(profile_ready('org.projectluma.Notes',env));p.chmod(0o600)
    other=home/'other';p.rename(other);p.symlink_to(other);self.assertFalse(profile_ready('org.projectluma.Notes',env))
 def test_native_owner_and_started_sandbox_do_not_need_host_profile_creation(self):
  with tempfile.TemporaryDirectory() as root:
   env={'HOME':root}
   with patch('prairie_apps.app_installs.resolve',return_value=SimpleNamespace(data_home=Path(root)/'.local/share')):
    self.assertTrue(profile_ready('org.projectluma.Notes',env))
   self.assertTrue(profile_ready('org.projectluma.Notes',env|{'FLATPAK_ID':'org.projectluma.Notes'}))
   self.assertFalse((Path(root)/'.var').exists())
 def test_both_real_sync_adapters_defer_before_credentials_cache_or_provider_creation(self):
  from prairie_apps.collaboration import sync_notes_collaboration
  from prairie_apps.tasks_collaboration import sync_tasks_collaboration
  with tempfile.TemporaryDirectory() as root:
   env={'HOME':root}
   def independent(app_id, _env):return SimpleNamespace(data_home=Path(root)/'.var/app'/app_id/'data')
   with patch('prairie_apps.app_installs.resolve',side_effect=independent), \
        patch('prairie_apps.collaboration.CollaborationClient',side_effect=AssertionError('must defer before credentials/network')), \
        patch('prairie_apps.tasks_collaboration.CollaborationClient',side_effect=AssertionError('must defer before provider/cache')):
    self.assertTrue(sync_notes_collaboration(environment=env)['deferred_until_first_launch'])
    self.assertEqual(sync_tasks_collaboration(environment=env),0)
   self.assertEqual(list(Path(root).iterdir()),[], 'sync must leave both original and sandbox ownership unmodified')

 def test_notes_sync_filters_pending_task_lists_and_removes_prior_kind_leakage(self):
  from prairie_apps.collaboration import CollaborationCache, sync_notes_collaboration
  with tempfile.TemporaryDirectory() as root:
   env={'HOME':root,'XDG_DATA_HOME':root+'/data'}
   client=SimpleNamespace(address='https://private.invalid',identity=SimpleNamespace(device_id='owned-device'),documents=lambda:[
    {'id':'pending-note','kind':'note','accepted':False,'owner':'owned-peer'},
    {'id':'pending-list','kind':'list','accepted':False,'owner':'owned-peer'},
    {'id':'accepted-list','kind':'list','accepted':True,'owner':'owned-peer'}])
   cache=CollaborationCache(env)
   with cache.db:
    cache.db.execute('INSERT INTO invitations VALUES(?,?,?,?,?)',('old-leaked-list',client.address,'owned-device','list','owned-peer'))
    cache.db.execute('INSERT INTO invitations VALUES(?,?,?,?,?)',('other-context-note',client.address,'another-device','note','owned-peer'))
   cache.close()
   with patch('prairie_apps.collaboration_ownership.profile_ready',return_value=True), \
        patch('prairie_apps.collaboration.CollaborationClient',return_value=client), \
        patch('prairie_apps.app_installs.app_environment',side_effect=lambda _app,environment:environment):
    result=sync_notes_collaboration(environment=env,store_path=Path(root)/'notes.sqlite3')
   self.assertEqual(result,{'updated':0,'conflicts':0})
   cache=CollaborationCache(env)
   try:
    rows=[tuple(row) for row in cache.db.execute('SELECT id,kind,device FROM invitations ORDER BY id')]
    self.assertEqual(rows,[('other-context-note','note','another-device'),('pending-note','note','owned-device')])
   finally:cache.close()

if __name__=='__main__':unittest.main(verbosity=2)
