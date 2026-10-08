#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the Connect exporter against a disposable XDG home and a stub hub.

No real Evolution Data Server, no real Notes library, and no network: the
transport is a callable that records what it was handed, and any accidental
real request would fail the "nothing was sent" assertions.
"""
import io
import types
import json
import os
from pathlib import Path
import tempfile

with tempfile.TemporaryDirectory(prefix="luma-connect-sync-test-") as directory:
    root = Path(directory)
    os.environ["XDG_DATA_HOME"] = str(root / "data")
    os.environ["PRAIRIE_EDS_MODE"] = "disabled"
    os.environ["HOME"] = str(root / "home")

    from prairie_apps.connect_sync import (
        CONTACT_FIELDS, ConnectError, DeviceIdentity, HubResponseError, NOTE_FIELDS,
        connect_data_directory, contacts_snapshot, device_file, enrol, hub_url,
        load_identity, notes_snapshot, push, save_identity,
    )
    from prairie_apps.eds_backend import ContactRecord
    from prairie_apps.notes_backend import NotesStore

    assert Path(os.environ["XDG_DATA_HOME"]).is_relative_to(root)
    assert connect_data_directory().is_relative_to(root), "the test must never touch a real token"

    # --- hub URL policy -----------------------------------------------------
    assert hub_url("https://hub.example/") == "https://hub.example"
    assert hub_url("http://127.0.0.1:8787") == "http://127.0.0.1:8787"
    assert hub_url("http://localhost:8787/base") == "http://localhost:8787/base"
    for rejected in ("http://hub.example", "http://192.168.1.10:8787", "http://[::1]:8787"):
        try:
            hub_url(rejected)
        except ConnectError as error:
            assert "https" in str(error), error
        else:
            raise AssertionError(f"plain http accepted for {rejected}")
    for malformed in ("", "hub.example", "ftp://hub.example", "https://user:pw@hub.example"):
        try:
            hub_url(malformed)
        except ConnectError:
            pass
        else:
            raise AssertionError(f"malformed hub URL accepted: {malformed!r}")

    # --- notes snapshot from a real temporary store --------------------------
    notes_path = root / "notes" / "notes.sqlite3"
    notes_path.parent.mkdir(parents=True)
    store = NotesStore(notes_path)
    folder = store.create_folder("Trip planning")
    kept = store.create_note(folder_id=folder.id, title="Packing")
    store.update_note(kept.id, title="Packing", body="socks\nadaptor")
    loose = store.create_note(title="Loose page")
    store.update_note(loose.id, title="Loose page", body="no folder")
    gone = store.create_note(folder_id=folder.id, title="Cancelled")
    store.update_note(gone.id, title="Cancelled", body="dropped")
    store.soft_delete_note(gone.id)
    store.close()

    snapshot = notes_snapshot("device-uuid", observed_at=1_700_000_000, path=notes_path)
    assert snapshot["device_id"] == "device-uuid"
    assert snapshot["observed_at"] == 1_700_000_000
    by_id = {item["id"]: item for item in snapshot["items"]}
    assert set(by_id) == {kept.id, loose.id}, "a soft-deleted note must be absent, not tombstoned here"
    assert by_id[kept.id]["folder_name"] == "Trip planning"
    assert by_id[kept.id]["folder_id"] == folder.id
    assert by_id[loose.id]["folder_id"] is None and by_id[loose.id]["folder_name"] == ""
    assert tuple(by_id[kept.id]) == NOTE_FIELDS, "payload keys are fixed by the contract"
    assert "runs" not in by_id[kept.id], "rich-text runs are not synced in v1"
    assert [f["name"] for f in snapshot["folders"]] == ["Trip planning"]
    assert notes_snapshot("device-uuid", path=root / "absent.sqlite3")["items"] == []
    assert not (root / "absent.sqlite3").exists(), "a missing store must not be created"

    # --- contacts snapshot from a stubbed loader -----------------------------
    stub_contacts = (
        ContactRecord("uid-1", "Ada Example", "+12025550100", "ada@example.test", "Example Co", "Engineer"),
        ContactRecord("uid-2", "Bo Example"),
    )
    contacts = contacts_snapshot("device-uuid", observed_at=1_700_000_000,
                                 loader=lambda: stub_contacts)
    assert [item["uid"] for item in contacts["items"]] == ["uid-1", "uid-2"]
    assert tuple(contacts["items"][0]) == CONTACT_FIELDS
    assert contacts["items"][1]["phone"] == "" and contacts["items"][1]["role"] == ""

    # --- token file permissions ---------------------------------------------
    identity = DeviceIdentity("device-uuid", "s3cret-token", "https://hub.example", "laptop", "now")
    path = save_identity(identity)
    assert path == device_file() and path.is_relative_to(root)
    assert path.stat().st_mode & 0o777 == 0o600, oct(path.stat().st_mode)
    assert path.parent.stat().st_mode & 0o777 == 0o700, oct(path.parent.stat().st_mode)
    assert json.loads(path.read_text(encoding="utf-8"))["token"] == "s3cret-token"
    assert load_identity().token == "s3cret-token"
    path.chmod(0o644)
    try:
        load_identity()
    except ConnectError as error:
        assert "chmod 600" in str(error), error
    else:
        raise AssertionError("a world-readable token file was accepted")
    path.chmod(0o600)

    # --- dry run sends nothing and prints no personal data -------------------
    def refuse(*_args, **_kwargs):
        raise AssertionError("--dry-run must not contact the hub")

    report = io.StringIO()
    assert push(dry_run=True, transport=refuse, notes_path=notes_path,
                contacts_loader=lambda: stub_contacts, out=report) == 0
    text = report.getvalue()
    assert "nothing was sent" in text
    assert "contacts: 2 record(s)" in text and "notes: 2 record(s)" in text
    assert "folders: 1" in text
    for field in CONTACT_FIELDS + NOTE_FIELDS:
        assert field in text, f"--dry-run must name the field {field}"
    for secret in ("Ada Example", "ada@example.test", "+12025550100", "Bo Example",
                   "socks", "adaptor", "no folder", "Packing", "Trip planning",
                   "Loose page", "s3cret-token"):
        assert secret not in text, f"--dry-run leaked {secret!r}"

    # --- a real push posts both snapshots with the bearer token --------------
    sent = []

    def record(url, payload, *, token="", timeout=None):
        sent.append((url, payload, token))
        return {"accepted": len(payload["items"]), "deleted": 0, "last_success_at": 1_700_000_001}

    report = io.StringIO()
    assert push(transport=record, notes_path=notes_path,
                contacts_loader=lambda: stub_contacts, out=report) == 0
    assert [url for url, _payload, _token in sent] == [
        "https://hub.example/api/hub/sync/contacts",
        "https://hub.example/api/hub/sync/notes",
    ]
    assert all(token == "s3cret-token" for _url, _payload, token in sent)
    assert all(payload["device_id"] == "device-uuid" for _url, payload, _token in sent)
    assert "accepted 2" in report.getvalue()

    # --hub overrides the enrolled address and is held to the same policy.
    sent.clear()
    assert push(hub="http://127.0.0.1:8787", transport=record, notes_path=notes_path,
                contacts_loader=lambda: stub_contacts, out=io.StringIO()) == 0
    assert sent[0][0] == "http://127.0.0.1:8787/api/hub/sync/contacts"
    try:
        push(hub="http://hub.example", transport=refuse, notes_path=notes_path,
             contacts_loader=lambda: stub_contacts, out=io.StringIO())
    except ConnectError as error:
        assert "plain http" in str(error), error
    else:
        raise AssertionError("push accepted a remote plain-http hub")

    # --- unchanged snapshots are not sent again; changed ones are ------------
    sent.clear()
    report = io.StringIO()
    assert push(transport=record, notes_path=notes_path,
                contacts_loader=lambda: stub_contacts, out=report) == 0
    assert sent == [] and report.getvalue().count("unchanged") == 2, report.getvalue()
    changed = stub_contacts + (ContactRecord("uid-3", "Cy Example"),)
    assert push(transport=record, notes_path=notes_path,
                contacts_loader=lambda: changed, out=io.StringIO()) == 0
    assert [url.rsplit("/", 1)[1] for url, _p, _t in sent] == ["contacts"], "only the changed service is sent"
    sent.clear()
    assert push(transport=record, notes_path=notes_path, force=True,
                contacts_loader=lambda: changed, out=io.StringIO()) == 0
    assert len(sent) == 2, "--force sends everything"
    assert (connect_data_directory() / "sync-state.json").stat().st_mode & 0o777 == 0o600

    # --- a vanished Notes library never empties the hub ----------------------
    sent.clear()
    try:
        push(transport=record, notes_path=root / "vanished.sqlite3",
             contacts_loader=lambda: changed, out=io.StringIO())
    except ConnectError as error:
        assert "Notes library" in str(error), error
    else:
        raise AssertionError("a missing Notes library was synced as empty")
    assert not any(url.endswith("/notes") for url, _p, _t in sent)

    # --- edits during a push make it go round again, a bounded number of times
    sent.clear()
    stamps = iter([1, 2, 2, 2])
    naps = []
    growing = [changed + (ContactRecord("uid-4", "Di Example"),),
               changed + (ContactRecord("uid-4", "Di Example"), ContactRecord("uid-5", "Ed Example"))]
    assert push(transport=record, notes_path=notes_path, settle=5, sleep=naps.append,
                stamp=lambda: next(stamps), contacts_loader=lambda: growing[min(len(naps), 2) - 1],
                out=io.StringIO()) == 0
    assert naps == [5, 5] and [len(p["items"]) for u, p, _t in sent if u.endswith("contacts")] == [4, 5]

    # --- 401 tells the operator to re-enrol ----------------------------------
    def reject(*_args, **_kwargs):
        raise HubResponseError(401, "Unauthorized")

    try:
        push(transport=reject, notes_path=notes_path, contacts_loader=lambda: stub_contacts,
             out=io.StringIO(), force=True)
    except ConnectError as error:
        assert "401" in str(error) and "enrol" in str(error) and "--force" in str(error), error
    else:
        raise AssertionError("a rejected token did not produce the re-enrol message")

    def unreachable(*_args, **_kwargs):
        raise ConnectError("The hub at https://hub.example could not be reached (refused).")

    try:
        push(transport=unreachable, notes_path=notes_path,
             contacts_loader=lambda: stub_contacts, out=io.StringIO(), force=True)
    except ConnectError as error:
        assert "could not be reached" in str(error)
    else:
        raise AssertionError("an unreachable hub was reported as success")

    # --- enrolment is once, and reuses the stored registration ---------------
    report = io.StringIO()
    again = enrol(code="ABCD2345", hub="https://hub.example", transport=refuse, out=report)
    assert again.token == "s3cret-token" and "already enrolled" in report.getvalue()

    device_file().unlink()
    try:
        push(transport=refuse, notes_path=notes_path, contacts_loader=lambda: stub_contacts,
             out=io.StringIO())
    except ConnectError as error:
        assert "not enrolled" in str(error) and "luma-connect-sync enrol" in str(error), error
    else:
        raise AssertionError("push ran without a registration")

    enrolments = []

    def issue(url, payload, *, token="", timeout=None):
        enrolments.append((url, payload, token))
        return {"device_id": payload["device_id"], "token": "fresh-token"}

    report = io.StringIO()
    fresh = enrol(code=" ABCD2345 ", hub="https://hub.example/", name="thinkpad",
                  transport=issue, out=report)
    assert enrolments[0][0] == "https://hub.example/api/hub/connect/devices"
    assert enrolments[0][1]["code"] == "ABCD2345" and enrolments[0][1]["name"] == "thinkpad"
    assert enrolments[0][2] == "", "enrolment is unauthenticated"
    assert fresh.token == "fresh-token" and load_identity().token == "fresh-token"
    assert device_file().stat().st_mode & 0o777 == 0o600
    assert "fresh-token" not in report.getvalue(), "the token must never be printed"
    try:
        enrol(code="", hub="https://hub.example", transport=refuse, out=io.StringIO())
    except ConnectError as error:
        assert "code is required" in str(error)
    else:
        raise AssertionError("an empty enrolment code was accepted")

# An unreachable address book and an empty one both come back as (). Only the
# second may be pushed: a full snapshot of nothing deletes everything the hub
# holds for this device.
class _Inventory:
    def __init__(self, available, reason=""):
        self.available, self.reason = available, reason


try:
    contacts_snapshot("device-1", loader=lambda: (),
                      inventory_probe=lambda: _Inventory(False, "Evolution Data Server is not running"))
except ConnectError as error:
    assert "Refusing to sync contacts" in str(error)
    assert "Evolution Data Server is not running" in str(error)
else:
    raise AssertionError("an empty snapshot was accepted while the address book was unavailable")

empty = contacts_snapshot("device-1", loader=lambda: (),
                          inventory_probe=lambda: _Inventory(True))
assert empty["items"] == [], "a readable but empty address book must still sync"

populated = contacts_snapshot(
    "device-1",
    loader=lambda: (types.SimpleNamespace(uid="u1", name="A Person", phone="", email="",
                                          organization="", role=""),),
    inventory_probe=lambda: _Inventory(False, "unavailable"))
assert len(populated["items"]) == 1, "records present must sync regardless of the probe"

print("Connect exporter: snapshot shapes, folder resolution, tombstone omission, "
      "silent dry run, 0600 token, http refusal, 401 re-enrol guidance, and "
      "empty-address-book refusal PASS")
