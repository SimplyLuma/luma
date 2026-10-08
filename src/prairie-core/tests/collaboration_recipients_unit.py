#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Recipient hints are resolved into current accounts, never used as identities.

Controlled directory/cache fixtures qualify selection policy only. Real owned
private-provider/native UI qualification is recorded separately.
"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from prairie_apps.collaboration_ui import NativeCollaborationShare

class Cache:
    def __init__(self, frequent): self.people = frequent
    def mappings(self, client, kind): return []
    def remember(self, client, snapshot, local): pass
    def frequent(self, client): return list(self.people)
    def close(self): pass

class Directory:
    def __init__(self, friends, answers): self.known = friends; self.answers = answers; self.calls = []
    def friends(self): return list(self.known)
    def lookup(self, handle):
        self.calls.append(handle)
        return dict(self.answers[handle])

def person(handle, account): return {'handle': handle, 'account': account, 'display_name': handle.title()}

class RecipientPolicy(unittest.TestCase):
    def prepare(self, favorites, frequent=(), friends=(), answers=None):
        directory = Directory(friends, answers or {})
        bridge = NativeCollaborationShare.__new__(NativeCollaborationShare)
        bridge.kind, bridge.local_id, bridge.content = 'note', 'owned-local-note', {'title': 'Owned note', 'body': '', 'runs': []}
        snapshot = {'id': 'owned-document', 'kind': 'note', 'account': 'owned-current-account'}
        client = SimpleNamespace(create=lambda *_: snapshot)
        with patch.dict(os.environ, {}, clear=True), \
             patch('prairie_apps.collaboration_ui.CollaborationClient', return_value=client), \
             patch('prairie_apps.collaboration_ui.PeopleDirectory', return_value=directory), \
             patch('prairie_apps.collaboration_ui.CollaborationCache', return_value=Cache(frequent)), \
             patch('prairie_apps.eds_backend.load_contacts', return_value=[SimpleNamespace(favourite=True, handle=h) for h in favorites]):
            _, result = bridge._prepare()
        return result, directory.calls

    def test_favorite_outside_friends_and_history_is_resolved_and_offered(self):
        bob = person('owned.bob', 'bob-account')
        result, calls = self.prepare(['owned.bob'], answers={'owned.bob': bob})
        self.assertEqual(result, [bob]); self.assertEqual(calls, ['owned.bob'])

    def test_recent_reassigned_username_cannot_select_old_account(self):
        old = person('reassigned', 'former-account'); current = person('reassigned', 'new-account')
        result, calls = self.prepare([], frequent=[old], answers={'reassigned': current})
        self.assertEqual(result, []); self.assertEqual(calls, ['reassigned'])

    def test_favorites_rank_before_frequent_and_friends_without_duplicates_or_self(self):
        bob, carol, dave = [person(name, name+'-account') for name in ('bob', 'carol', 'dave')]
        result, calls = self.prepare(['bob', 'me'], frequent=[carol, bob], friends=[dave, bob],
                                    answers={'carol': carol, 'me': person('me', 'owned-current-account')})
        self.assertEqual([p['handle'] for p in result], ['bob', 'carol', 'dave'])
        self.assertEqual(set(calls), {'carol', 'me'})

    def test_membership_completion_releases_pending_operation_after_closed_window_or_callback_failure(self):
        bridge = NativeCollaborationShare.__new__(NativeCollaborationShare)
        bridge.window = SimpleNamespace(closed=True)
        bridge.busy = True
        self.assertFalse(bridge._finished({}, None, bridge._membership_done))
        self.assertFalse(bridge.busy)
        bridge.window = SimpleNamespace(closed=False, layer_host=object())
        bridge.busy = True
        with patch('prairie_apps.collaboration_ui.Toast.show', side_effect=RuntimeError('actual feedback failure')):
            with self.assertRaisesRegex(RuntimeError, 'actual feedback failure'):
                bridge._finished(None, ValueError('provider refused'), bridge._membership_done)
        self.assertFalse(bridge.busy, 'feedback failure cannot lock every later permission command')

if __name__ == '__main__': unittest.main(verbosity=2)
