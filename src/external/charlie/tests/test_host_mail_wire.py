# SPDX-License-Identifier: Apache-2.0
import base64
from dataclasses import asdict
import json
import unittest
from charlie_luma.host_mail_wire import encode, decode, validate, MAX_WIRE, MAX_ATTACHMENTS
from charlie_luma.model import Account, ServerConfig, Draft

class MailWireTests(unittest.TestCase):
    def test_account_credentials_never_become_account_fields(self):
        account = asdict(Account("mail-123", "Alice", "alice@example.org", "custom"))
        config = asdict(ServerConfig("imap.example.org", smtp_host="smtp.example.org"))
        result = validate("SaveAccount", {"account": account, "config": config, "password": "private-password"})
        self.assertEqual(result["password"], "private-password")
        self.assertNotIn("private-password", encode(asdict(result["account"])))
        account["oauth-token"] = "cannot-import-secret"
        with self.assertRaises(ValueError): validate("SaveAccount", {"account": account, "config": config, "password": ""})

    def test_fixed_operations_reject_exec_sql_and_credential_lookup(self):
        for operation in ("Spawn", "LookupSecret", "GetToken", "SQL", "OpenPath"):
            with self.assertRaises(ValueError): validate(operation, {})
        with self.assertRaises(ValueError): validate("Sync", {"account_id": "mail-123", "command": "rm"})

    def test_attachment_bytes_are_roundtripped_without_paths(self):
        draft = asdict(Draft("mail-123", to=["bob@example.org"], body="Hello"))
        draft["attachments"] = [{"filename": "song.flac", "data": base64.b64encode(b"native-audio-bytes").decode()}]
        result = validate("Send", decode(encode({"draft": draft})))
        self.assertEqual(result["attachments"], [("song.flac", b"native-audio-bytes")])
        self.assertEqual(result["draft"].attachments, [])

    def test_attachment_traversal_and_host_path_are_rejected(self):
        for attachment in ({"filename": "../enrollment.key", "data": ""}, {"filename": "/etc/passwd", "data": ""}, {"filename": "..", "data": ""}, {"filename": "..\\key", "data": ""}, {"path": "/etc/passwd"}):
            value = asdict(Draft("mail-123")); value["attachments"] = [attachment]
            with self.assertRaises(ValueError): validate("Send", {"draft": value})

    def test_invalid_attachment_encoding_and_aggregate_size_are_rejected(self):
        value = asdict(Draft("mail-123")); value["attachments"] = [{"filename": "a", "data": "not-base64!"}]
        with self.assertRaises(ValueError): validate("Send", {"draft": value})
        data = base64.b64encode(b"x" * (MAX_ATTACHMENTS // 2 + 1)).decode()
        value["attachments"] = [{"filename": "a", "data": data}, {"filename": "b", "data": data}]
        with self.assertRaises(ValueError): validate("Send", {"draft": value})

    def test_server_addresses_ports_and_tls_are_typed(self):
        account = asdict(Account("mail-123", "A", "a@example.org")); good = asdict(ServerConfig("imap.example.org", smtp_host="smtp.example.org"))
        for key, value in (("imap_host", "https://example.org/path"), ("smtp_host", "a@b"), ("imap_port", True), ("smtp_port", 70000), ("use_starttls", "yes")):
            config = dict(good); config[key] = value
            with self.assertRaises(ValueError): validate("SaveAccount", {"account": account, "config": config, "password": "p"})

    def test_flags_and_item_counts_are_bounded(self):
        with self.assertRaises(ValueError): validate("MarkRead", {"message_ids": ["a"], "read": 1})
        with self.assertRaises(ValueError): validate("MarkRead", {"message_ids": ["a"] * 257, "read": True})
        self.assertEqual(validate("MarkRead", {"message_ids": ["a"], "read": True})["message_ids"], ("a",))

    def test_json_budget_and_nonfinite_values_fail(self):
        with self.assertRaises(ValueError): encode({"body": "x" * MAX_WIRE})
        with self.assertRaises(ValueError): decode("x" * (MAX_WIRE + 1))
        with self.assertRaises(ValueError): decode('{"value": NaN}')
        with self.assertRaises(ValueError): decode("[]")

    def test_all_existing_provider_presets_admit_their_account_types(self):
        from charlie_luma.accounts import PROVIDERS
        for provider in PROVIDERS:
            a = asdict(Account("mail-123", "A", "a@example.org", provider.key)); c = asdict(ServerConfig("imap.example.org", smtp_host="smtp.example.org"))
            self.assertEqual(validate("SaveAccount", {"account": a, "config": c, "password": "p"})["account"].provider, provider.key)
