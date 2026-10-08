#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Editing a contact changes only what was edited (read-modify-write).

Real-shaped cards, parsed and written by the real EBookContacts (in memory: no
address book is opened). Every property the edit did not touch must come back
byte for byte, the sync tag included, which stays the address book's to manage.
Skipped where EBookContacts is not installed.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/prairie-core"))

try:
    import gi

    gi.require_version("EBookContacts", "1.2")
    from gi.repository import EBookContacts
except (ImportError, ValueError):
    EBookContacts = None

from prairie_apps.eds_backend import _parse_birthday, apply_contact_changes  # noqa: E402

CARD = "\r\n".join((
    "BEGIN:VCARD",
    "VERSION:3.0",
    "UID:4f1c-real-shaped",
    "FN:Priya Raman",
    "N:Raman;Priya;K.;Dr.;",
    "X-EVOLUTION-FILE-AS:Raman\\, Priya",
    "ORG:Luma;Design",
    "TITLE:Designer",
    "TEL;TYPE=PREF;X-EVOLUTION-E164=+1,4155550199:+1 (415) 555-0199",
    "TEL;TYPE=WORK:+1 510 555 0133",
    "EMAIL;TYPE=HOME:priya@example.com",
    "EMAIL;TYPE=WORK:priya@studio.example",
    "BDAY:1990-03-14",
    "ADR;TYPE=HOME:;;1 Main St;Oakland;California;94607;USA",
    "NOTE:Oat milk\\, no sugar",
    "X-ABLABEL:Studio",
    "X-SOCIALPROFILE;TYPE=mastodon:https://example.social/@priya",
    "PHOTO;ENCODING=b;TYPE=JPEG:/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8U",
    "X-EVOLUTION-WEBDAV-ETAG:2025-06-10T11:57:35.868-07:00",
    "REV:2025-06-10T18:57:35Z",
    "END:VCARD",
    "",
))


def lines(contact) -> list[str]:
    """The card as the address book writes it, one unfolded property per line."""
    text = contact.to_string().replace("\r\n ", "")
    return [line for line in text.split("\r\n") if line]


def serialised(text: str):
    """A card the address book has re-serialised, as it does after any change.

    An untouched card hands back the text it was read from; the first change
    makes EBookContacts write every property in its own form (parameter order,
    quoting), so the comparison is between two cards written the same way.
    """
    contact = EBookContacts.Contact.new_from_vcard(text)
    marker = EBookContacts.VCardAttribute.new(None, "X-R1-MARKER")
    contact.append_attribute(marker)
    contact.remove_attribute(contact.get_attributes_by_name("X-R1-MARKER")[0])
    return contact


@unittest.skipIf(EBookContacts is None, "EBookContacts is not installed")
class ReadModifyWriteTests(unittest.TestCase):
    def edited(self, changes: dict[str, str]) -> tuple[list[str], list[str]]:
        before = serialised(CARD)
        after = serialised(CARD)
        apply_contact_changes(after, changes, EBookContacts)
        return lines(before), lines(after)

    def assert_only(self, changes: dict[str, str], touched: set[str]) -> list[str]:
        before, after = self.edited(changes)
        kept = [line for line in before if line.split(":")[0].split(";")[0] not in touched]
        for line in kept:
            self.assertIn(line, after, f"{line!r} must survive an edit of {sorted(changes)}")
        return after

    def test_nothing_changed_is_byte_for_byte(self):
        before, after = self.edited({})
        self.assertEqual(before, after)

    def test_phone_edit_keeps_everything_else(self):
        after = self.assert_only({"phone": "+1 415 555 0100"}, {"TEL"})
        self.assertIn("TEL;TYPE=PREF:+1 415 555 0100", after)   # its type stays; the stale E164 goes
        self.assertNotIn("4155550199", "\n".join(after))
        self.assertIn("TEL;TYPE=WORK:+1 510 555 0133", after)   # the second number is untouched
        self.assertIn("X-EVOLUTION-WEBDAV-ETAG:2025-06-10T11:57:35.868-07:00", after)

    def test_email_edit_touches_only_the_first_address(self):
        after = self.assert_only({"email": "priya@simplyluma.com"}, {"EMAIL"})
        self.assertIn("EMAIL;TYPE=HOME:priya@simplyluma.com", after)
        self.assertIn("EMAIL;TYPE=WORK:priya@studio.example", after)

    def test_name_edit_updates_the_structured_name(self):
        after = self.assert_only({"name": "Priya Raman-Okafor"}, {"FN", "N", "X-EVOLUTION-FILE-AS"})
        self.assertIn("FN:Priya Raman-Okafor", after)
        self.assertIn("N:Raman-Okafor;Priya;K.;Dr.;", after)   # middle name and prefix stay
        self.assertIn("X-EVOLUTION-FILE-AS:Raman-Okafor\\, Priya", after)

    def test_birthday_address_and_work(self):
        after = self.assert_only({"birthday": "June 2"}, {"BDAY"})
        self.assertIn("BDAY:--0602", after)
        after = self.assert_only({"address": "2 Oak Ave, Berkeley, California"}, {"ADR"})
        self.assertIn("ADR;TYPE=HOME:;;2 Oak Ave;Berkeley;California;94607;USA", after)   # code and country stay
        after = self.assert_only({"work": "Luma · Lead designer"}, {"ORG", "TITLE"})
        self.assertIn("ORG:Luma;Design", after)
        self.assertIn("TITLE:Lead designer", after)

    def test_the_private_note(self):
        after = self.assert_only({"note": "Oat milk, two sugars"}, {"NOTE"})
        self.assertIn("NOTE:Oat milk\\, two sugars", after)
        after = self.assert_only({"note": ""}, {"NOTE"})
        self.assertFalse([line for line in after if line.startswith("NOTE")])

    def test_clearing_a_field_removes_only_its_own_property(self):
        after = self.assert_only({"phone": ""}, {"TEL"})
        self.assertNotIn("+1 (415) 555-0199", "\n".join(after))
        self.assertIn("TEL;TYPE=WORK:+1 510 555 0133", after)

    def test_a_new_number_on_a_card_without_one(self):
        contact = EBookContacts.Contact.new_from_vcard("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Sam\r\nEND:VCARD\r\n")
        apply_contact_changes(contact, {"phone": "555 0101"}, EBookContacts)
        self.assertIn("TEL;TYPE=CELL:555 0101", lines(contact))

    def test_refusals(self):
        contact = EBookContacts.Contact.new_from_vcard(CARD)
        with self.assertRaises(ValueError):
            apply_contact_changes(contact, {"name": "  "}, EBookContacts)
        with self.assertRaises(ValueError):
            apply_contact_changes(contact, {"unsupported_field": "x"}, EBookContacts)

    def test_luma_username_persists_without_changing_other_properties(self):
        after = self.assert_only({"handle": "@Priya.Raman"}, {"X-LUMA-USERNAME"})
        self.assertIn("X-LUMA-USERNAME:priya.raman", after)
        contact = EBookContacts.Contact.new_from_vcard("\r\n".join(after) + "\r\n")
        apply_contact_changes(contact, {"handle": ""}, EBookContacts)
        self.assertFalse(contact.get_attributes_by_name("X-LUMA-USERNAME"))

    def test_invalid_luma_username_does_not_change_the_card(self):
        contact = serialised(CARD)
        before = lines(contact)
        with self.assertRaises(ValueError):
            apply_contact_changes(contact, {"name": "Changed", "handle": "person@example.com"}, EBookContacts)
        self.assertEqual(before, lines(contact))


class BirthdayTests(unittest.TestCase):
    def test_forms_people_write(self):
        self.assertEqual(_parse_birthday("March 14"), "--0314")
        self.assertEqual(_parse_birthday("14 Mar"), "--0314")
        self.assertEqual(_parse_birthday("March 14, 1990"), "1990-03-14")
        self.assertEqual(_parse_birthday("1990-03-14"), "1990-03-14")
        for bad in ("soon", "13/14", "Feb 40", "March"):
            with self.assertRaises(ValueError, msg=bad):
                _parse_birthday(bad)


if __name__ == "__main__":
    unittest.main()
