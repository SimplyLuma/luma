#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""A completed send follows its own canonical thread without stealing selection."""
import ast
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prairie_apps.messages_accounts import AccountStore

# Exercise the maintained callback without constructing a display. The store
# below is the real SQLite account store and its actual identity migration.
source = Path(__file__).resolve().parents[1] / 'prairie_apps/messages.py'
tree = ast.parse(source.read_text())
owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MessagesWindow')
method = next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == '_finish_transmit')
method.returns = None
for argument in method.args.args:
    argument.annotation = None
namespace = {'GLib': SimpleNamespace(SOURCE_REMOVE=False)}
exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(source), 'exec'), namespace)
finish = namespace['_finish_transmit']


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        store = AccountStore(Path(self.temp.name) / 'messages.db')
        self.addCleanup(store.close)
        self.service = SimpleNamespace(store=store)
        self.record = store.add('@carol', 'Owned self-send', direction='outgoing')
        store.move_conversation('@carol', 'u:acct-carol')
        store.update_state(self.record.uid, 'sent')
        self.opened = []
        def opened(thread, *, service):
            self.opened.append((thread.address, service))
            self.window.current_address = thread.address
        self.window = SimpleNamespace(closed=False, service=self.service, current_address='@carol',
                                      _resolve_sent_conversation=opened, _render_messages=lambda: None,
                                      _reload_threads=lambda: None, _notice=lambda _: None)

    def complete(self, service=None, uid=None, original='@carol'):
        return finish(self.window, service or self.service, uid or self.record.uid, 'sent', '', original)

    def test_first_send_keeps_the_delivered_message_visible(self):
        self.complete()
        self.assertEqual(self.window.current_address, 'u:acct-carol')
        self.assertEqual(self.service.store.thread(self.window.current_address)[0].uid, self.record.uid)
        self.assertEqual(len(self.opened), 1)

    def test_pending_next_draft_and_attachment_follow_the_identity(self):
        store = self.service.store
        store.set_draft('@carol', 'The next message, typed while the first send finishes')
        source = Path(self.temp.name) / 'owned-next-attachment.txt'
        source.write_text('Owned next draft attachment')
        attachment = store.attach_file('@carol', source)
        self.complete()
        self.assertEqual(store.draft('u:acct-carol'), 'The next message, typed while the first send finishes')
        self.assertEqual(store.draft('@carol'), '')
        self.assertEqual(store.draft_attachments('u:acct-carol')[0].uid, attachment.uid)
        self.assertEqual(store.attachment_path(attachment).read_text(), source.read_text())
        self.assertEqual(store.draft_attachments('@carol'), ())

    def test_reply_already_migrated_by_provider_is_preserved(self):
        store = self.service.store
        store.set_draft_reply('u:acct-carol', self.record.uid)
        store.set_draft('@carol', 'Pending next message')
        self.complete()
        self.assertEqual(store.draft_reply('u:acct-carol').message_uid, self.record.uid)

    def test_newer_canonical_draft_cannot_be_overwritten(self):
        store = self.service.store
        store.set_draft('@carol', 'Older alias draft')
        store.set_draft('u:acct-carol', 'Newer canonical draft')
        store._connection.execute('UPDATE drafts SET updated=updated+10 WHERE address=?', ('u:acct-carol',))
        store._connection.commit()
        self.complete()
        self.assertEqual(store.draft('u:acct-carol'), 'Newer canonical draft')

    def test_unrelated_alias_and_other_account_uid_are_refused(self):
        with self.assertRaises(ValueError):
            self.service.store.resolve_sent_conversation(self.record.uid, '@unrelated')
        other = AccountStore(Path(self.temp.name) / 'other-account.db')
        try:
            with self.assertRaises(KeyError):
                other.resolve_sent_conversation(self.record.uid, '@carol')
        finally:
            other.close()

    def test_incoming_record_cannot_authorize_identity_resolution(self):
        store = self.service.store
        incoming = store.add('u:acct-carol', 'Received message', direction='incoming')
        with self.assertRaises(ValueError):
            store.resolve_sent_conversation(incoming.uid, '@carol')

    def test_later_selection_is_never_replaced(self):
        self.window.current_address = '@another-person'
        self.complete()
        self.assertEqual(self.window.current_address, '@another-person')
        self.assertEqual(self.opened, [])

    def test_same_address_on_another_account_is_never_replaced(self):
        self.window.service = SimpleNamespace(store=self.service.store)
        self.complete()
        self.assertEqual(self.window.current_address, '@carol')
        self.assertEqual(self.opened, [])

    def test_deleted_message_and_legacy_callback_cannot_redirect(self):
        self.complete(uid='removed-message')
        self.complete(original=None)
        self.assertEqual(self.window.current_address, '@carol')
        self.assertEqual(self.opened, [])

    def test_closed_window_stays_closed(self):
        self.window.closed = True
        self.complete()
        self.assertEqual(self.opened, [])


class SurfaceMigrationTest(unittest.TestCase):
    def test_pending_rich_draft_reply_and_selection_are_kept(self):
        class Base:
            def _resolve_sent_conversation(self, thread, *, service):
                self.current_address = thread.address
        path = source.with_name('messages_app_port.py')
        tree = ast.parse(path.read_text())
        owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'LumaUIMessagesWindow')
        method = next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == '_resolve_sent_conversation')
        owner.bases = [ast.Name(id='Base', ctx=ast.Load())]
        owner.body = [method]
        namespace = {'Base': Base}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[owner], type_ignores=[])), str(path), 'exec'), namespace)
        window = namespace['LumaUIMessagesWindow']()
        old = {'id': 'account:test|@carol', 'address': '@carol'}
        new = {'id': 'account:test|u:acct-carol', 'address': 'u:acct-carol'}
        reply, selected = {'id': 'reply-original'}, {'id': 'selected-original'}
        seen = []
        surface = SimpleNamespace(current=old, pending=old, reply=reply, selected_message=selected,
                                  _drafts={old['id']: 'Next typed draft'},
                                  _rich_drafts={old['id']: ('Next typed draft', [('message-bold', 0, 4)])},
                                  _remember_draft=lambda: None, _entries=lambda: [new],
                                  _present_entry=lambda entry: seen.append(entry),
                                  render_details=lambda: None)
        surface._composer = lambda: seen.append((surface._drafts[surface.current['id']], surface.reply))
        window.surface = surface
        service = SimpleNamespace(key=lambda address: 'account:test|' + address)
        window._resolve_sent_conversation(SimpleNamespace(address='u:acct-carol'), service=service)
        self.assertIs(surface.current, new)
        self.assertIs(surface.pending, new)
        self.assertIs(surface.reply, reply)
        self.assertIs(surface.selected_message, selected)
        self.assertEqual(surface._drafts, {new['id']: 'Next typed draft'})
        self.assertEqual(surface._rich_drafts[new['id']][1], [('message-bold', 0, 4)])
        self.assertEqual(seen[-1], ('Next typed draft', reply))


if __name__ == '__main__':
    unittest.main()
