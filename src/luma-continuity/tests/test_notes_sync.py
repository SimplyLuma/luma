"""Real native NotesStore data, transactions, causal conflicts and replay."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from prairie_apps.notes_backend import NotesStore
from luma_continuity.notes_sync import NotesSync


class NotesSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.left=NotesStore(Path(self.tmp.name)/'left.db');self.right=NotesStore(Path(self.tmp.name)/'right.db')
        self.addCleanup(self.left.close);self.addCleanup(self.right.close)
        self.allowed=True
        self.a=NotesSync(self.left,authorized=lambda:self.allowed)
        self.b=NotesSync(self.right,authorized=lambda:self.allowed)
        self.cursors={}

    def transfer(self,source,target):
        after=self.cursors.get((source.origin,target.origin),0)
        while True:
            page=source.page(after)
            target.apply(source.origin,page)
            after=page['cursor'];self.cursors[(source.origin,target.origin)]=after
            if after==page['watermark']:break

    def converge(self):
        for _ in range(3):self.transfer(self.a,self.b);self.transfer(self.b,self.a)

    def test_initial_native_values_folder_richtext_and_no_echo(self):
        folder=self.left.create_folder('Work');note=self.left.create_note(folder_id=folder.id,title='Hello')
        self.left.update_note(note.id,title='Hello',body='bold text',runs=({'start':0,'end':4,'style':'bold'},))
        self.converge()
        self.assertEqual(self.left.get_note(note.id),self.right.get_note(note.id))
        self.assertEqual(self.left.get_folder(folder.id),self.right.get_folder(folder.id))
        before=[x.db.execute('SELECT count(*) FROM luma_notes_sync_ops').fetchone()[0] for x in (self.a,self.b)]
        self.converge()
        self.assertEqual(before,[x.db.execute('SELECT count(*) FROM luma_notes_sync_ops').fetchone()[0] for x in (self.a,self.b)])

    def test_lost_receipt_replay_and_changed_identity_rejected(self):
        note=self.left.create_note(title='One');page=self.a.page()
        self.b.apply(self.a.origin,page);self.b.apply(self.a.origin,page)
        self.assertEqual(len(self.right.list_notes()),1)
        changed=deepcopy(page);changed['items'][0]['value']['title']='changed under same ID'
        with self.assertRaises(ValueError):self.b.apply(self.a.origin,changed)
        self.assertEqual(self.right.get_note(note.id).title,'One')

    def test_offline_concurrent_edits_preserve_both_and_converge(self):
        note=self.left.create_note(title='Base');self.converge()
        self.left.update_note(note.id,title='Left',body='left contents')
        self.right.update_note(note.id,title='Right',body='right contents')
        self.converge()
        left={n.id:(n.title,n.body) for n in self.left.list_notes()}
        right={n.id:(n.title,n.body) for n in self.right.list_notes()}
        self.assertEqual(left,right)
        self.assertEqual({n.body for n in self.left.list_notes()},{'left contents','right contents'})
        self.assertEqual(len(left),2)

    def test_delete_versus_edit_preserves_edit_and_tombstone(self):
        note=self.left.create_note(title='Base');self.converge()
        self.left.soft_delete_note(note.id)
        self.right.update_note(note.id,title='Changed offline',body='Keep me')
        self.converge()
        self.assertIsNotNone(self.left.get_note(note.id).deleted_at)
        self.assertEqual([n.body for n in self.left.list_notes()],['Keep me'])
        self.assertEqual({n.id:n.body for n in self.left.list_notes()},{n.id:n.body for n in self.right.list_notes()})

    def test_atomic_apply_rollback_leaves_cursor_and_content_unchanged(self):
        note=self.left.create_note(title='Atomic');page=self.a.page()
        original=self.b._write
        def fail(*args):original(*args);raise RuntimeError('synthetic crash before receipt')
        self.b._write=fail
        with self.assertRaises(RuntimeError):self.b.apply(self.a.origin,page)
        self.assertEqual(self.right.list_notes(),())
        self.assertEqual(self.b.db.execute('SELECT count(*) FROM luma_notes_sync_cursors').fetchone()[0],0)
        self.b._write=original;self.b.apply(self.a.origin,page)
        self.assertEqual(self.right.get_note(note.id).title,'Atomic')

    def test_disable_retains_local_changes_and_blocks_apply(self):
        note=self.left.create_note(title='Offline');self.allowed=False
        with self.assertRaises(PermissionError):self.a.page()
        self.assertEqual(self.left.get_note(note.id).title,'Offline')
        self.allowed=True;self.converge();self.assertEqual(self.right.get_note(note.id).title,'Offline')

    def test_bad_folder_reference_and_vector_do_not_advance(self):
        self.left.create_note(title='Bad reference');page=self.a.page()
        bad=deepcopy(page);bad['items'][0]['value']['folder_id']='11111111-1111-4111-8111-111111111111'
        with self.assertRaises(ValueError):self.b.apply(self.a.origin,bad)
        bad=deepcopy(page);bad['items'][0]['clock']={}
        with self.assertRaises(ValueError):self.b.apply(self.a.origin,bad)
        self.assertEqual(self.right.list_notes(),())

    def test_folder_deletion_does_not_lose_concurrent_note_edit(self):
        folder=self.left.create_folder('Folder');note=self.left.create_note(folder_id=folder.id,title='Base');self.converge()
        self.left.delete_folder(folder.id);self.right.update_note(note.id,title='Edited',body='still here')
        self.converge()
        self.assertIn('still here',[n.body for n in self.left.list_notes()])
        self.assertEqual({n.id:n.body for n in self.left.list_notes()},{n.id:n.body for n in self.right.list_notes()})
