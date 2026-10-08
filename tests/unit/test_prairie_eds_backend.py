#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/prairie-core"))
from datetime import datetime, timezone

from prairie_apps.eds_backend import (
    _component_record,
    _contact_vcard,
    _escape_vcard,
    _birthday,
    _categories,
    _ical_timestamp,
    _short_address,
    inspect_eds_inventory,
    load_contacts,
)


class EdsBackendTests(unittest.TestCase):
    def test_test_gate_never_touches_session_bus(self):
        with mock.patch.dict(os.environ, {"PRAIRIE_EDS_MODE": "disabled"}):
            inventory = inspect_eds_inventory()
            self.assertFalse(inventory.available)
            self.assertEqual(load_contacts(), ())

    @mock.patch("prairie_apps.eds_backend._modules", side_effect=ImportError("missing"))
    def test_missing_eds_is_a_truthful_empty_state(self, _modules):
        inventory = inspect_eds_inventory()
        self.assertFalse(inventory.available)
        self.assertIn("unavailable", inventory.reason)

    def test_contact_vcard_escapes_user_fields(self):
        vcard = _contact_vcard("Pat; Example", "+1 555 0100", "pat@example.test")
        self.assertIn("FN:Pat\\; Example", vcard)
        self.assertIn("TEL;TYPE=CELL:+1 555 0100", vcard)
        self.assertIn("EMAIL:pat@example.test", vcard)

    def test_calendar_timestamp_is_utc_ical(self):
        value = datetime(2026, 8, 13, 10, 30, tzinfo=timezone.utc)
        self.assertEqual(_ical_timestamp(value), "20260813T103000Z")

    def test_task_description_text_is_ical_escaped(self):
        self.assertEqual(_escape_vcard("Line one\nLine two, later"),"Line one\\nLine two\\, later")

    def test_calendar_record_uses_current_eds_description_api(self):
        description = mock.Mock()
        description.get_value.return_value = "Release preparation"
        component = mock.Mock()
        component.get_summary.return_value.get_value.return_value = "Design review"
        component.get_dtstart.return_value = None
        component.get_due.return_value = None
        component.get_descriptions.return_value = [description]
        component.get_percent_complete.return_value = 0
        component.get_uid.return_value = "event-1"

        record = _component_record(component, "events")

        self.assertEqual(record.description, "Release preparation")



class _Card:
    def __init__(self, lines):
        self.lines = lines

    def get_attributes_by_name(self, name):
        return [_Attribute(v) for n, v in self.lines if n == name]


class _Attribute:
    def __init__(self, values):
        self.values = values

    def get_values(self):
        return self.values


class ContactReadingTests(unittest.TestCase):
    def test_categories_split_and_deduplicate(self):
        card = _Card([("CATEGORIES", ["Family,Launch team"]), ("CATEGORIES", ["Family"])])
        self.assertEqual(_categories(card), ("Family", "Launch team"))

    def test_birthday_reads_as_a_day_of_the_year(self):
        self.assertEqual(_birthday("1990-03-14"), "March 14, 1990")
        self.assertEqual(_birthday("1604-03-14"), "March 14")
        self.assertEqual(_birthday("--0314"), "March 14")
        self.assertEqual(_birthday("19901102"), "November 2, 1990")
        self.assertEqual(_birthday("sometime"), "sometime")

    def test_address_is_street_town_region(self):
        card = _Card([("ADR", ["", "", "1 Main St", "Oakland", "California", "94607", "USA"])])
        self.assertEqual(_short_address(card), "1 Main St, Oakland, California")


if __name__ == "__main__": unittest.main()
