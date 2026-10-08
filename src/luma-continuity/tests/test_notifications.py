import json
import unittest
from luma_continuity.notifications import NativeNotifications

class NotificationAdapterTests(unittest.TestCase):
    def test_only_bounded_source_owned_records_and_opaque_actions(self):
        calls = []
        snapshot = {"generation": 1, "removed": [], "records": [{"id": "d"*32, "title": "Synthetic", "body": "Fixture", "actions": [{"id": "e"*32, "label": "Open"}]}]}
        def caller(method, signature, values):
            calls.append((method, signature, values))
            return json.dumps(snapshot) if method == 'Snapshot' else 'complete'
        adapter = NativeNotifications('a'*64, 'b'*32, authorized=lambda: True, caller=caller)
        self.assertEqual(adapter('notifications.read', {'operation': 'snapshot'}), snapshot)
        self.assertEqual(adapter('notifications.act', {'action':'e'*32, 'operation':'f'*32}), {'state':'complete'})
        self.assertEqual(calls[1], ('Invoke', '(ssss)', ('a'*64, 'b'*32, 'e'*32, 'f'*32)))
        snapshot['records'][0]['inlineReply'] = 'must-not-export'
        with self.assertRaises(ValueError): adapter('notifications.read', {'operation': 'snapshot'})
        with self.assertRaises(ValueError): adapter('notifications.act', {'action':'/local/path', 'operation':'f'*32})

    def test_revoke_does_not_call_owner(self):
        calls = []
        adapter = NativeNotifications('a'*64, 'b'*32, authorized=lambda: False, caller=lambda *args: calls.append(args))
        with self.assertRaises(PermissionError): adapter('notifications.read', {'operation': 'snapshot'})
        self.assertEqual(calls, [])
