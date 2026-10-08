#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Run on a private dbus-run-session whose XDG data/config homes are disposable.

The environment guard prevents this fixture from writing into a real book.
"""
import os
from pathlib import Path
import tempfile
from prairie_apps.eds_backend import import_contacts_vcard, load_contacts, delete_contact

root = Path(os.environ.get("LUMA_CONTACTS_IMPORT_TEST_ROOT", ""))
assert root.is_absolute() and root.name.startswith("luma-contacts-import-test-")
assert Path(os.environ["XDG_DATA_HOME"]).is_relative_to(root)
assert Path(os.environ["XDG_CONFIG_HOME"]).is_relative_to(root)
os.environ.pop("PRAIRIE_EDS_MODE", None)

card = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:same-source-uid\r\nFN:Import Test Person\r\nTEL:+12025550100\r\nEMAIL:import@example.test\r\nORG:Test Organization\r\nTITLE:Test Role\r\nEND:VCARD\r\n"
assert not load_contacts()
uids = import_contacts_vcard(card + card.replace("Test Person", "Other Person"))
assert len(set(uids)) == 2 and "same-source-uid" not in uids
records = {record.uid: record for record in load_contacts()}
assert set(records) == set(uids)
for record in records.values():
    assert record.phone == "+12025550100"
    assert record.email == "import@example.test"
    assert record.organization == "Test Organization"
    assert record.role == "Test Role"
try:
    import_contacts_vcard(card + "BEGIN:VCARD\nFN:Incomplete")
except ValueError:
    pass
else:
    raise AssertionError("Truncated batch accepted")
assert {record.uid for record in load_contacts()} == set(uids)
for uid in uids:
    delete_contact(uid)
assert not load_contacts()
print("PASS: real isolated EDS vCard batch, fresh UIDs, full field persistence, malformed batch no-write, deletion cleanup")
