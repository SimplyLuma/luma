#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Reject malformed imports before EDS is changed; preserve complete cards."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/prairie-core"))
from prairie_apps.contacts_import import MAX_VCARD_BYTES, split_vcards
from prairie_apps.eds_backend import import_contacts_vcard

CARD = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:existing\r\nFN:Pat Example\r\nEMAIL:pat@example.test\r\nNOTE:Long\r\n  folded detail\r\nEND:VCARD\r\n"


class ContactsImportTests(unittest.TestCase):
    def test_complete_fields_and_folding_reach_native_parser(self):
        self.assertEqual(split_vcards(CARD + CARD), (CARD, CARD))

    def test_malformed_batch_never_initializes_eds(self):
        invalid = ("", CARD[:-12], CARD + "garbage", "END:VCARD", "BEGIN:VCARD\n" + CARD,
                   CARD + "\0", CARD * 1001, "x" * (MAX_VCARD_BYTES + 1))
        with patch("prairie_apps.eds_backend._modules") as modules:
            for text in invalid:
                with self.subTest(text=text[:25]), self.assertRaises(ValueError):
                    import_contacts_vcard(text)
            modules.assert_not_called()

    def modules(self, name="Pat Example", result=(True, ["new-1", "new-2"])):
        record = Mock()
        attribute = Mock()
        attribute.get_values.return_value = [name]
        record.get_attributes_by_name.return_value = [attribute]
        contact = Mock()
        contact.new_from_vcard_with_uid.return_value = record
        client = Mock()
        client.add_contacts_sync.return_value = result
        registry = Mock()
        data = SimpleNamespace(SourceRegistry=SimpleNamespace(new_sync=lambda _: registry))
        book = SimpleNamespace(BookClient=SimpleNamespace(connect_sync=lambda *_: client))
        contacts = SimpleNamespace(Contact=contact, BookOperationFlags=SimpleNamespace(CONFLICT_FAIL=1))
        return (data, book, contacts), contact, client

    def test_native_batch_receives_all_fields_with_fresh_uids(self):
        modules, contact, client = self.modules()
        with patch("prairie_apps.eds_backend._modules", return_value=modules):
            self.assertEqual(import_contacts_vcard(CARD + CARD), ("new-1", "new-2"))
        args = [call.args for call in contact.new_from_vcard_with_uid.call_args_list]
        self.assertEqual([args[0][0], args[1][0]], [CARD, CARD])
        self.assertNotEqual(args[0][1], args[1][1])
        self.assertNotIn("existing", (args[0][1], args[1][1]))
        self.assertEqual(len(client.add_contacts_sync.call_args.args[0]), 2)

    def test_invalid_contact_name_prevents_any_batch_write(self):
        modules, _, client = self.modules(name=" ")
        with patch("prairie_apps.eds_backend._modules", return_value=modules), self.assertRaises(ValueError):
            import_contacts_vcard(CARD)
        client.add_contacts_sync.assert_not_called()

    def test_failed_or_incomplete_batch_is_not_success(self):
        for result in ((False, []), (True, []), (True, ["only-one"])):
            modules, _, _ = self.modules(result=result)
            with patch("prairie_apps.eds_backend._modules", return_value=modules), self.assertRaises(RuntimeError):
                import_contacts_vcard(CARD + CARD)


if __name__ == "__main__":
    unittest.main()
