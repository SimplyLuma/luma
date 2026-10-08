# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from luma_installer.collaboration_migration import SCHEMA, export_collaboration_cache

class Export(unittest.TestCase):
 def setUp(self):
  self.root=tempfile.TemporaryDirectory();self.addCleanup(self.root.cleanup);self.base=Path(self.root.name)
  self.source=self.base/'snapshot.sqlite3';self.db=sqlite3.connect(self.source);self.addCleanup(self.db.close)
  self.db.execute('PRAGMA journal_mode=WAL')
  for sql in SCHEMA:self.db.execute(sql)
  self.db.execute('CREATE TABLE device_tokens(token TEXT)');self.db.execute("INSERT INTO device_tokens VALUES('must never copy')")
  for kind in ('note','task','list'):
   self.db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)',(kind,'owned-hub','owned-device',kind,kind+'-local',7,'comment','{"body":"unsent ancestor"}'))
   self.db.execute('INSERT INTO snapshots VALUES(?,?)',(kind,json.dumps({'kind':kind,'comments':[{'body':kind+' comment'}]})))
   self.db.execute('INSERT INTO invitations VALUES(?,?,?,?,?)',('pending-'+kind,'owned-hub','owned-device',kind,'owned-owner'))
  for i in range(20):
   self.db.execute('INSERT INTO recipients VALUES(?,?,?,?,?,?)',('owned-hub','owned-device','peer'+str(i),json.dumps({'handle':'peer'+str(i),'account':'peer'+str(i),'token':'never-export'}),i,i))
  self.db.commit()
 def test_filtered_wal_permissions_mapping_and_bounded_history(self):
  for app,kinds in [('org.projectluma.Notes',{'note'}),('org.projectluma.Tasks',{'task','list'})]:
   target=self.base/(app+'.db');result=export_collaboration_cache(self.source,target,app)
   with sqlite3.connect(target) as db:
    self.assertEqual({row[0]for row in db.execute('SELECT kind FROM documents')},kinds)
    self.assertEqual({row[0]for row in db.execute('SELECT id FROM snapshots')},kinds)
    self.assertEqual({row[0]for row in db.execute('SELECT kind FROM invitations')},kinds)
    self.assertEqual(db.execute('SELECT revision,role,base,local_id FROM documents ORDER BY kind').fetchone()[:3],(7,'comment','{"body":"unsent ancestor"}'))
    self.assertEqual(result['recipients'],8)
    self.assertNotIn('never-export',str(db.execute('SELECT person FROM recipients').fetchall()))
    self.assertEqual({r[0]for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")},{'documents','snapshots','invitations','recipients'})
   self.assertEqual(target.stat().st_mode&0o777,0o600)
  self.assertEqual(self.db.execute('SELECT count(*) FROM documents').fetchone()[0],3)
  self.assertEqual(self.db.execute('SELECT token FROM device_tokens').fetchone()[0],'must never copy')
 def test_existing_destination_cancel_mismatch_and_link_refuse_without_replacing_data(self):
  target=self.base/'destination';target.write_bytes(b'keep existing')
  with self.assertRaises(FileExistsError):export_collaboration_cache(self.source,target,'org.projectluma.Notes')
  self.assertEqual(target.read_bytes(),b'keep existing');target.unlink()
  with self.assertRaisesRegex(ValueError,'cancelled'):export_collaboration_cache(self.source,target,'org.projectluma.Notes',cancelled=lambda:True)
  self.assertFalse(target.exists())
  self.db.execute("UPDATE snapshots SET snapshot=? WHERE id='note'",(json.dumps({'kind':'list'}),));self.db.commit()
  with self.assertRaisesRegex(ValueError,'different document kind'):export_collaboration_cache(self.source,target,'org.projectluma.Notes')
  self.assertFalse(target.exists());link=self.base/'link';link.symlink_to(self.source)
  with self.assertRaisesRegex(ValueError,'regular file'):export_collaboration_cache(link,target,'org.projectluma.Notes')
 def test_unknown_app_or_schema_fail_before_destination_created(self):
  target=self.base/'destination'
  with self.assertRaises(ValueError):export_collaboration_cache(self.source,target,'org.projectluma.Contacts')
  self.db.execute('ALTER TABLE documents ADD COLUMN unreviewed TEXT');self.db.commit()
  with self.assertRaisesRegex(ValueError,'schema'):export_collaboration_cache(self.source,target,'org.projectluma.Notes')
  self.assertFalse(target.exists())

if __name__=='__main__':unittest.main(verbosity=2)
