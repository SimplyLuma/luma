#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Phone must retain real Luma-only identities without treating them as carrier numbers."""
from types import SimpleNamespace
import unittest
from prairie_apps.phone_data import phone_contact
from prairie_apps.phone_fixture import matching_person, search_people, visible_people

def record(**changes):
    values=dict(uid="card-1",name="Bob",phone="",email="",handle="owned.bob",
                luma_account="account_123",on_luma=True,hue=None)
    values.update(changes)
    return SimpleNamespace(**values)

class PhoneLumaContactTests(unittest.TestCase):
    def test_verified_luma_card_is_present_without_carrier_number(self):
        person=phone_contact(record())
        self.assertIsNotNone(person)
        self.assertEqual((person.uid,person.phone,person.handle,person.luma_account,person.online),
                         ("card-1","","owned.bob","account_123",True))

    def test_telephone_contact_remains_even_without_luma_identity(self):
        person=phone_contact(record(phone="+15550101",handle="",luma_account="",on_luma=None))
        self.assertEqual(person.phone,"+15550101")
        self.assertFalse(person.online)
        self.assertEqual(person.luma_account,"")

    def test_unverified_username_does_not_create_luma_contact(self):
        for value in (False,None,1,"true"):
            with self.subTest(on_luma=value):
                self.assertIsNone(phone_contact(record(on_luma=value)))

    def test_missing_or_invalid_account_does_not_create_luma_contact(self):
        for value in ("","short","bad/account","x"*201,None):
            with self.subTest(account=value):
                self.assertIsNone(phone_contact(record(luma_account=value)))

    def test_missing_or_invalid_handle_does_not_create_luma_contact(self):
        for value in ("","bad/handle","bad handle","UPPER",None):
            with self.subTest(handle=value):
                self.assertIsNone(phone_contact(record(handle=value)))

    def test_unverified_carrier_card_does_not_gain_luma_actions(self):
        person=phone_contact(record(phone="+15550101",on_luma=False))
        self.assertEqual((person.handle,person.luma_account,person.online),("","",False))

    def test_empty_names_use_phone_email_then_username(self):
        for values,expected in ((dict(phone="+15550101",email="a@b"),"+15550101"),
                                (dict(email="a@b"),"a@b"),({},"@owned.bob")):
            with self.subTest(values=values):
                self.assertEqual(phone_contact(record(name="  ",**values)).name,expected)

    def test_verified_username_search_finds_named_card(self):
        people=(phone_contact(record()),)
        self.assertEqual(visible_people(people,"@owned.bob"),people)
        self.assertEqual(search_people(people,"@owned.bob"),people)
        self.assertEqual(visible_people(people,"@someone.else"),())

    def test_luma_only_identity_is_not_a_carrier_number_match(self):
        person=phone_contact(record())
        for value in ("","123","owned.bob"):
            with self.subTest(value=value):
                self.assertIsNone(matching_person((person,),value))

    def test_existing_carrier_number_matching_remains(self):
        person=phone_contact(record(phone="+15550101"))
        self.assertEqual(matching_person((person,),"555"),person)

if __name__=="__main__":
    unittest.main()
