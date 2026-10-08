#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Notes sync, change by change: versions, conflicts, pictures, pacing.

A fake sync host implements the incremental notes protocol the way the Hub
does (etag per note, conflicts returned with the host's copy, pictures named
by content), so every rule the device follows is checked without a network.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from prairie_apps import connect_notes, connect_sync
from prairie_apps.connect_notes import CONFLICT_SUFFIX, NotesDeltaSync, local_etag, note_etag
from prairie_apps.connect_sync import HubResponseError
from prairie_apps.notes_attachments import attachments_directory, store_picture
from prairie_apps.notes_backend import NotesStore

PNG = b"\x89PNG\r\n\x1a\n" + b"picture bytes" * 20


class FakeHost:
    """The notes side of a sync host, with the Hub's rules, in memory."""

    def __init__(self, *, delta: bool = True) -> None:
        self.delta = delta
        self.notes: dict[str, dict] = {}
        self.blobs: dict[str, bytes] = {}
        self.revision = 1
        self.requests: list[tuple[str, str]] = []
        self.refuse: HubResponseError | None = None
        # Picture storage, as the Hub counts it for the account.
        self.quota = 10 ** 9
        self.refuse_blob: HubResponseError | None = None

    def _etag(self, item: dict) -> str:
        return note_etag(item["title"], item["body"], item.get("folder_id"), item.get("favorite", False),
                         item.get("attachments", []))

    def get_json(self, url: str, *, token: str = "", timeout=None) -> dict:
        self.requests.append(("GET", url))
        if self.refuse is not None:
            raise self.refuse
        if url.endswith("/capabilities"):
            if not self.delta:
                raise HubResponseError(401, "Unauthorized")
            return {"notes_delta": 1, "notes_blobs": 1, "note_blob_quota_bytes": self.quota,
                    "note_blob_used_bytes": self.used}
        if url.endswith("/api/hub/sync/notes"):
            return {"items": [dict(note, origin_device_id=note["device"]) for note in self.notes.values()
                              if not note.get("deleted")]}
        if "/events" in url:
            return {"revision": self.revision}
        return {"revision": self.revision, "items": []}

    def post_json(self, url: str, payload: dict, *, token: str = "") -> dict:
        self.requests.append(("POST", url))
        if self.refuse is not None:
            raise self.refuse
        assert url.endswith("/notes/changes"), url
        accepted, deleted, conflicts, missing = [], [], [], []
        changed = False
        for item in payload["upserts"]:
            current = self.notes.get(item["id"])
            etag = self._etag(item)
            # Like the Hub: a page another device owns is never overwritten from here.
            owned = current is not None and current.get("device", payload["device_id"]) != payload["device_id"]
            if (current is None or current.get("deleted")
                    or (not owned and (item["base_etag"] == current["etag"] or etag == current["etag"]))):
                changed = changed or current is None or current["etag"] != etag or bool(current.get("deleted"))
                self.notes[item["id"]] = dict(item, etag=etag, device=payload["device_id"], deleted=False)
                accepted.append({"id": item["id"], "etag": etag})
                missing.extend(sha for sha in item.get("attachments", []) if sha not in self.blobs)
            else:
                conflicts.append({key: current[key] for key in ("id", "etag", "title", "body", "folder_id",
                                                                 "favorite", "deleted")})
        for item in payload["deletes"]:
            current = self.notes.get(item["id"])
            owned = current is not None and current.get("device", payload["device_id"]) != payload["device_id"]
            if current is None or current.get("deleted") or (not owned and item["base_etag"] == current["etag"]):
                if current is not None and not current.get("deleted"):
                    current["deleted"] = True
                    changed = True
                deleted.append(item["id"])
            else:
                conflicts.append({key: current[key] for key in ("id", "etag", "title", "body", "folder_id",
                                                                 "favorite", "deleted")})
        if changed:
            self.revision += 1
        return {"revision": self.revision, "accepted": accepted, "deleted": deleted,
                "conflicts": conflicts, "missing_blobs": sorted(set(missing))}

    def put_bytes(self, url: str, data: bytes, content_type: str, *, token: str = "") -> dict:
        self.requests.append(("PUT", url))
        sha = url.rsplit("/", 1)[1]
        assert hashlib.sha256(data).hexdigest() == sha
        assert content_type in ("image/png", "image/jpeg")
        if self.refuse_blob is not None:
            raise self.refuse_blob
        if self.used + len(data) > self.quota:
            raise HubResponseError(507, "Insufficient Storage", "Note images have used all storage.",
                                   code="note_storage_full")
        self.blobs[sha] = data
        return {"stored": True}

    @property
    def used(self) -> int:
        return sum(len(data) for data in self.blobs.values())

    def sweep(self, sha: str) -> None:
        """The Hub removes pictures of pages deleted for more than a week."""
        self.blobs.pop(sha, None)

    def edit_elsewhere(self, note_id: str, body: str) -> None:
        note = self.notes[note_id]
        note["body"] = body
        note["etag"] = self._etag(note)
        self.revision += 1


class DeltaCase(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="connect-notes-"))
        self.env = {"HOME": str(self.home), "XDG_DATA_HOME": str(self.home / "data")}
        self.patch = mock.patch.dict(os.environ, self.env)
        self.patch.start()
        self.store_path = self.home / "data" / "luma" / "notes" / "notes.sqlite3"
        self.store = NotesStore(self.store_path)
        self.host = FakeHost()
        self.state: dict = {}
        self.now = 1_000_000.0

    def tearDown(self) -> None:
        self.store.close()
        self.patch.stop()

    def sync(self) -> str:
        delta = NotesDeltaSync(address="https://host.example", token="t", device_id="dev-1", http=self.host,
                               state=self.state, scope="https://host.example|dev-1", store_path=self.store_path,
                               observed_at=1, clock=lambda: self.now)
        self.assertTrue(delta.supported())
        return delta.run()

    def puts(self) -> list[str]:
        return [url.rsplit("/", 1)[1] for method, url in self.host.requests if method == "PUT"]

    def capability_asks(self) -> int:
        return sum(1 for _method, url in self.host.requests if url.endswith("/capabilities"))

    def page_with(self, *pictures: bytes, title: str = "Page"):
        names = [store_picture(data) for data in pictures]
        runs = tuple({"start": index, "end": index + 1, "style": "image", "src": name}
                     for index, name in enumerate(names))
        note = self.store.create_note(title=title)
        self.store.update_note(note.id, title=title, body="￼" * len(names), runs=runs)
        return note, names

    def status(self) -> dict:
        from prairie_apps.notes_attachments import read_sync_status
        return read_sync_status(self.store_path.parent)

    def posts(self) -> int:
        return sum(1 for method, url in self.host.requests if method == "POST")


class ChangeTests(DeltaCase):
    def test_only_what_changed_is_sent_and_nothing_when_nothing_changed(self) -> None:
        notes = [self.store.create_note(title=f"Note {i}") for i in range(30)]
        self.assertIn("sent 30", self.sync())
        self.assertEqual(self.sync(), "notes: unchanged")
        self.assertEqual(self.posts(), 1, "an unchanged library sends nothing")
        self.store.update_note(notes[4].id, title="Note 4", body="edited")
        self.host.requests.clear()
        with mock.patch.object(self.host, "post_json", wraps=self.host.post_json) as post:
            self.assertIn("sent 1", self.sync())
        payload = post.call_args.args[1]
        self.assertEqual([item["id"] for item in payload["upserts"]], [notes[4].id])
        self.assertEqual(payload["upserts"][0]["base_etag"], self.host._etag(dict(
            title="Note 4", body="", folder_id=None, favorite=False, attachments=[])))
        self.assertEqual(self.host.notes[notes[4].id]["body"], "edited")

    def test_the_etag_matches_the_hub_recipe(self) -> None:
        expected = hashlib.sha256('{"attachments":["a","b"],"body":"Día\\n","favorite":true,"folder_id":null,"title":"T"}'
                                  .encode("utf-8")).hexdigest()
        self.assertEqual(note_etag("T", "Día\n", None, True, ["b", "a"]), expected)

    def test_a_folder_rename_is_sent_although_the_version_is_unchanged(self) -> None:
        folder = self.store.create_folder("Work")
        self.store.create_note(folder_id=folder.id, title="Plan")
        self.sync()
        self.store.rename_folder(folder.id, "Office")
        self.assertIn("sent 1", self.sync())
        self.assertEqual(next(iter(self.host.notes.values()))["folder_name"], "Office")

    def test_a_retry_after_a_lost_answer_is_accepted_not_a_conflict(self) -> None:
        note = self.store.create_note(title="Plan")
        self.sync()
        self.store.update_note(note.id, title="Plan", body="v2")
        # The host applied it, but the answer never arrived.
        self.host.post_json("x/notes/changes", {"device_id": "dev-1", "upserts": [
            dict(id=note.id, title="Plan", body="v2", folder_id=None, folder_name="", favorite=False,
                 created_at="", modified_at="", attachments=[],
                 base_etag=self.host.notes[note.id]["etag"])], "deletes": []})
        result = self.sync()
        # The pull sees the host already holds this version: nothing to copy.
        self.assertNotIn("other copy", result)
        self.assertEqual(self.host.notes[note.id]["body"], "v2")
        self.assertEqual(self.sync(), "notes: unchanged")


class ConflictTests(DeltaCase):
    def test_a_page_changed_on_both_sides_keeps_both_versions(self) -> None:
        note = self.store.create_note(title="Plan")
        self.store.update_note(note.id, title="Plan", body="original")
        self.sync()
        self.host.edit_elsewhere(note.id, "edited on the web")
        self.store.update_note(note.id, title="Plan", body="edited here")
        result = self.sync()
        self.assertIn("kept 1 other copy", result)
        titles = {n.title: n.body for n in self.store.list_notes()}
        self.assertEqual(titles["Plan"], "edited here", "this device's version is untouched")
        self.assertEqual(titles[f"Plan{CONFLICT_SUFFIX}"], "edited on the web", "the other version is kept")
        self.assertEqual(self.host.notes[note.id]["body"], "edited here", "ours went up straight after")
        copy = next(n for n in self.store.list_notes() if n.title.endswith(CONFLICT_SUFFIX))
        self.assertIn(copy.id, self.host.notes, "and the kept copy is synced too")
        self.assertEqual(self.sync(), "notes: unchanged")

    def test_deleted_here_but_changed_elsewhere_comes_back(self) -> None:
        note = self.store.create_note(title="Plan")
        self.sync()
        self.host.edit_elsewhere(note.id, "someone kept writing")
        self.store.soft_delete_note(note.id)
        self.sync()
        self.assertFalse(self.host.notes[note.id].get("deleted"), "the host's newer page was not deleted")
        bodies = [n.body for n in self.store.list_notes()]
        self.assertIn("someone kept writing", bodies, "and it is back on this device")

    def test_a_deleted_page_is_deleted_there_with_its_version(self) -> None:
        note = self.store.create_note(title="Old")
        self.sync()
        self.store.soft_delete_note(note.id)
        self.assertIn("deleted 1", self.sync())
        self.assertTrue(self.host.notes[note.id]["deleted"])

    def test_a_missing_library_is_never_taken_as_deletions(self) -> None:
        for index in range(5):
            self.store.create_note(title=f"N{index}")
        self.sync()
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.store_path) + suffix).unlink(missing_ok=True)
        self.store = NotesStore(self.store_path)
        self.sync()
        self.assertFalse(any(note.get("deleted") for note in self.host.notes.values()))


class OwnedElsewhereTests(DeltaCase):
    """A page the host keeps for another device refuses every change from
    this one, whatever version it names. That is not a conflict between
    versions, and must not make a copy on every pass (2026-09-21: 46 copies
    of two pages in twenty minutes on a laptop whose library came from an
    earlier enrolment)."""

    def owned_elsewhere(self, title: str, here: str, there: str):
        note = self.store.create_note(title=title)
        self.store.update_note(note.id, title=title, body=here)
        item = dict(id=note.id, title=title, body=there, folder_id=None, favorite=False, attachments=[])
        self.host.notes[note.id] = dict(item, etag=self.host._etag(item), device="dev-old", deleted=False)
        return note

    def copies(self) -> list[str]:
        return [n.title for n in self.store.list_notes() if n.title.endswith(CONFLICT_SUFFIX)]

    def test_one_copy_per_real_conflict_then_it_settles(self) -> None:
        self.owned_elsewhere("Tide", "edited here", "as the other device left it")
        for _ in range(6):
            self.sync()
        self.assertEqual(self.copies(), [f"Tide{CONFLICT_SUFFIX}"], "one copy, not one per pass")
        posts = self.posts()
        self.assertEqual(self.sync(), "notes: unchanged")
        self.assertEqual(self.posts(), posts, "a refused page is not resent until it changes here")

    def test_the_state_the_laptop_was_left_in_makes_no_more_copies(self) -> None:
        # What luma-connect-sync had recorded on the laptop: the host's version,
        # nothing acknowledged as sent.
        note = self.owned_elsewhere("Tide", "edited here", "as the other device left it")
        self.state.setdefault("https://host.example|dev-1|notes-delta", {
            "store": str(self.store_path), "bootstrapped": True, "blobs": {},
            "notes": {note.id: {"etag": self.host.notes[note.id]["etag"], "sent": ""}}})
        for _ in range(5):
            self.sync()
        self.assertEqual(self.copies(), [])

    def test_a_change_there_is_kept_once_and_a_change_here_is_not_copied_again(self) -> None:
        note = self.owned_elsewhere("Tide", "edited here", "v1")
        self.sync()
        self.host.edit_elsewhere(note.id, "v2 there")
        self.store.update_note(note.id, title="Tide", body="edited here again")
        for _ in range(4):
            self.sync()
        self.assertEqual(len(self.copies()), 2, "one copy for each version the other device wrote")
        self.assertEqual(self.host.notes[note.id]["body"], "v2 there", "the owner's page is not overwritten")

    def test_a_refused_deletion_is_not_retried_and_makes_no_copy(self) -> None:
        note = self.owned_elsewhere("Tide", "same", "same")
        self.sync()
        self.store.soft_delete_note(note.id)
        for _ in range(4):
            self.sync()
        self.assertEqual(self.copies(), [])
        self.assertFalse(self.host.notes[note.id].get("deleted"))

    def test_the_same_version_is_never_kept_twice(self) -> None:
        note = self.owned_elsewhere("Tide", "here", "there")
        conflict = {key: self.host.notes[note.id][key] for key in ("id", "etag", "title", "body", "folder_id",
                                                                   "favorite", "deleted")}
        delta = NotesDeltaSync(address="https://host.example", token="t", device_id="dev-1", http=self.host,
                               state=self.state, scope="https://host.example|dev-1", store_path=self.store_path,
                               observed_at=1, clock=lambda: self.now)
        live = {n.id: n for n in self.store.list_notes()}
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            first = delta._keep_conflicts([conflict], live, {}, {})
            second = delta._keep_conflicts([conflict], live, {}, {})
        self.assertEqual((first, second), (1, 0))
        self.assertEqual(len(self.copies()), 1)
        self.assertIn("already kept", err.getvalue(), "the refusal is logged")


class PictureSyncTests(DeltaCase):
    def test_pictures_are_uploaded_once_by_content(self) -> None:
        name = store_picture(PNG)
        runs = ({"start": 0, "end": 1, "style": "image", "src": name},)
        first = self.store.create_note(title="A")
        self.store.update_note(first.id, title="A", body="￼", runs=runs)
        second = self.store.create_note(title="B")
        self.store.update_note(second.id, title="B", body="￼", runs=runs)
        self.assertIn("uploaded 1 picture", self.sync())
        puts = [url for method, url in self.host.requests if method == "PUT"]
        self.assertEqual(len(puts), 1, "the same picture in two pages goes up once")
        self.assertTrue(puts[0].endswith(hashlib.sha256(PNG).hexdigest()))
        self.store.update_note(first.id, title="A2", body="￼", runs=runs)
        self.sync()
        self.assertEqual(len([1 for method, _ in self.host.requests if method == "PUT"]), 1,
                         "an edit to a page with a picture does not send the picture again")
        self.assertEqual(self.host.notes[first.id]["attachments"], [hashlib.sha256(PNG).hexdigest()])


class FallbackAndPacingTests(DeltaCase):
    def test_a_host_without_the_protocol_gets_snapshots_and_is_asked_once(self) -> None:
        self.host = FakeHost(delta=False)
        delta = NotesDeltaSync(address="https://h", token="t", device_id="d", http=self.host, state=self.state,
                               scope="s", store_path=self.store_path, observed_at=1)
        self.assertFalse(delta.supported(), "a 401 on the question means snapshots, not re-enrol")
        self.assertFalse(delta.supported())
        self.assertEqual(sum(1 for _m, url in self.host.requests if url.endswith("/capabilities")), 1)

    def test_a_rate_limit_is_remembered_and_nothing_is_sent_until_it_passes(self) -> None:
        state: dict = {}
        error = HubResponseError(429, "Too Many Requests", retry_after=120)
        connect_sync._note_backoff(state, "scope", error, self.env)
        self.assertGreater(state["scope|backoff-until"], time.time() + 100)

    def test_own_pushes_do_not_make_every_service_pull(self) -> None:
        state = {"s|revision": 10, "s|pulled-at": time.time()}
        self.assertFalse(connect_sync._remote_changed(state, "s", 11, own_bumps=1), "the bump was ours")
        self.assertFalse(connect_sync._remote_changed(state, "s", 10, own_bumps=0))
        self.assertTrue(connect_sync._remote_changed(state, "s", 12, own_bumps=1), "someone else too")
        self.assertTrue(connect_sync._remote_changed(state, "s", 11, own_bumps=0))
        state["s|pulled-at"] = time.time() - 3600
        self.assertTrue(connect_sync._remote_changed(state, "s", 10, own_bumps=0), "a full pull every 15 min")

    def test_the_watcher_skips_changes_this_device_already_has(self) -> None:
        home = self.home / "watch"
        env = {"HOME": str(home), "XDG_DATA_HOME": str(home / "data")}
        identity = connect_sync.DeviceIdentity("11111111-2222-4333-8444-555555555555", "tok",
                                               "https://hub.example", "Laptop", "now")
        connect_sync.save_identity(identity, env)
        scope = f"https://hub.example|{identity.device_id}"
        pushes = []

        class Events:
            revision = 7

            def get_json(self, url, *, token="", timeout=None):
                return {"revision": self.revision}

        def fake_push(**keywords):
            pushes.append(1)
            connect_sync._save_state({f"{scope}|revision": 7}, env)
            return 0

        with mock.patch.object(connect_sync, "push", fake_push):
            connect_sync.watch(environment=env, http=Events(), out=io.StringIO(), sleep=lambda s: False, rounds=4)
        self.assertEqual(len(pushes), 1, "the first sync only; revision 7 was already this device's")


def picture(seed: int, size: int = 400) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + bytes([seed]) * size


class PictureRefusalTests(DeltaCase):
    def test_full_storage_keeps_text_syncing_and_waits_quietly(self) -> None:
        self.host.quota = 500
        self.host.blobs["x" * 64] = b"0" * 450          # other pictures already use most of it
        note, names = self.page_with(picture(1), picture(2))
        self.sync()
        self.assertIn(note.id, self.host.notes, "the text went up although the pictures did not")
        shas = [name.split(".")[0] for name in names]
        self.assertEqual(self.status()["pictures"], {sha: "note_storage_full" for sha in shas})
        self.assertEqual(len(self.puts()), 1, "one refusal is enough: the second picture is not tried")
        self.store.update_note(note.id, title="Page, edited", body="￼￼", runs=self.store.get_note(note.id).runs)
        self.sync()
        self.sync()
        self.assertEqual(len(self.puts()), 1, "no automatic retry while storage is full")
        self.assertEqual(self.host.notes[note.id]["title"], "Page, edited", "text keeps syncing")

    def test_storage_freed_on_the_host_is_noticed_and_the_pictures_go_up(self) -> None:
        self.host.quota = 1000
        self.host.blobs["x" * 64] = b"0" * 900
        note, names = self.page_with(picture(1), picture(2))
        self.sync()
        asks = self.capability_asks()
        self.sync()
        self.assertEqual(self.capability_asks(), asks + 1, "usage is asked while pictures wait")
        self.sync()
        self.assertEqual(self.capability_asks(), asks + 1, "but not more than every few minutes")
        self.host.sweep("x" * 64)                        # removed on another device
        self.now += 601
        self.assertIn("uploaded 2 picture", self.sync())
        self.assertEqual(self.status()["pictures"], {})
        self.assertEqual(self.status()["used_bytes"], 0)

    def test_removing_a_picture_here_retries_at_once(self) -> None:
        self.host.quota = 1000
        self.host.blobs["x" * 64] = b"0" * 700
        note, names = self.page_with(picture(1), picture(2))
        self.sync()
        self.assertEqual(len(self.puts()), 1)
        self.host.quota = 1300                           # the plan grew; the host was not asked
        keep = self.store.get_note(note.id).runs[:1]
        self.store.update_note(note.id, title="Page", body="￼", runs=keep)
        asks = self.capability_asks()
        self.assertIn("uploaded 1 picture", self.sync())
        self.assertEqual(self.capability_asks(), asks, "a picture removed here is reason enough to retry")
        self.assertEqual(self.puts()[-1], names[0].split(".")[0])
        self.assertEqual(self.status()["pictures"], {})

    def test_a_picture_too_large_is_not_retried_and_a_smaller_copy_goes_up(self) -> None:
        self.host.refuse_blob = HubResponseError(413, "Payload Too Large", code="note_image_too_large")
        note, names = self.page_with(picture(3))
        sha = names[0].split(".")[0]
        self.sync()
        self.assertEqual(self.status()["pictures"], {sha: "note_image_too_large"})
        self.store.update_note(note.id, title="Edited", body="￼", runs=self.store.get_note(note.id).runs)
        self.sync()
        self.assertEqual(self.puts(), [sha], "never retried")
        # "Shrink and attach" puts a different picture in its place.
        self.host.refuse_blob = None
        smaller = store_picture(picture(4, 100))
        self.store.update_note(note.id, title="Edited", body="￼",
                               runs=({"start": 0, "end": 1, "style": "image", "src": smaller},))
        self.sync()
        self.assertEqual(self.puts()[-1], smaller.split(".")[0])
        self.assertEqual(self.status()["pictures"], {}, "the refused picture is no longer on any page")

    def test_a_rate_limited_picture_goes_up_later_without_resending_the_page(self) -> None:
        self.host.refuse_blob = HubResponseError(429, "Too Many Requests", retry_after=30)
        note, names = self.page_with(picture(5))
        with self.assertRaises(HubResponseError):
            self.sync()
        self.assertEqual(self.status()["pictures"], {}, "a rate limit is not shown as a problem")
        self.host.refuse_blob = None
        posts = self.posts()
        self.assertIn("uploaded 1 picture", self.sync())
        self.assertEqual(self.posts(), posts, "the page itself was already taken")

    def test_a_page_restored_from_recently_deleted_brings_back_swept_pictures(self) -> None:
        note, names = self.page_with(picture(6))
        sha = names[0].split(".")[0]
        self.sync()
        self.store.soft_delete_note(note.id)
        self.sync()
        self.assertTrue(self.host.notes[note.id]["deleted"])
        self.host.sweep(sha)                             # deleted for more than seven days
        self.store.restore_note(note.id)
        self.assertIn("uploaded 1 picture", self.sync())
        self.assertEqual(self.puts().count(sha), 2)
        self.assertIn(sha, self.host.blobs)

    def test_the_hub_code_is_read_from_a_refusal(self) -> None:
        from urllib.error import HTTPError
        body = io.BytesIO(json.dumps({"error": "Storage is full.", "code": "note_storage_full"}).encode())
        error = HTTPError("https://h/x", 507, "Insufficient Storage", {}, body)
        refused = connect_sync._response_error(error)
        self.assertEqual((refused.status, refused.code, refused.detail), (507, "note_storage_full", "Storage is full."))
        odd = connect_sync._response_error(HTTPError("https://h/x", 400, "Bad", {}, io.BytesIO(b'{"code": "a b<"}')))
        self.assertEqual(odd.code, "")


class AccountHub(FakeHost):
    """The Hub as it is now (REVISION 20260922T021950Z): pages belong to the
    account, so any device of it may change any page, held only to the
    version check; the page list is what the Hub's mirror route returns;
    pictures are read back by content address."""

    FIELDS = ("id", "etag", "title", "body", "folder_id", "favorite", "modified_at", "deleted", "attachments")

    def post_json(self, url: str, payload: dict, *, token: str = "") -> dict:
        self.requests.append(("POST", url))
        assert url.endswith("/notes/changes"), url
        accepted, deleted, conflicts, referenced = [], [], [], set()
        changed = False
        for item in payload["upserts"]:
            row, etag = self.notes.get(item["id"]), self._etag(item)
            if row is not None and not row["deleted"] and item["base_etag"] != row["etag"] and etag != row["etag"]:
                conflicts.append({key: row[key] for key in self.FIELDS})
                continue
            fresh = dict(item, etag=etag, device=payload["device_id"], deleted=False)
            fresh.pop("base_etag", None)
            if row != fresh:
                changed = True
                self.notes[item["id"]] = fresh
            accepted.append({"id": item["id"], "etag": etag})
            referenced.update(item.get("attachments", []))
        for item in payload["deletes"]:
            row = self.notes.get(item["id"])
            if row is None or row["deleted"]:
                deleted.append(item["id"])
            elif item["base_etag"] != row["etag"]:
                conflicts.append({key: row[key] for key in self.FIELDS})
            else:
                row.update(deleted=True, device=payload["device_id"])
                deleted.append(item["id"])
                changed = True
        if changed:
            self.revision += 1
        return {"revision": self.revision, "accepted": accepted, "deleted": deleted, "conflicts": conflicts,
                "missing_blobs": sorted(sha for sha in referenced if sha not in self.blobs)}

    def get_json(self, url: str, *, token: str = "", timeout=None) -> dict:
        if url.endswith("/api/hub/sync/notes"):
            self.requests.append(("GET", url))
            keys = ("id", "title", "body", "folder_id", "folder_name", "favorite", "created_at", "modified_at",
                    "etag", "attachments")
            return {"device_id": "x", "items": [
                dict({key: row.get(key) for key in keys}, updated_at=0, origin_device_id=row["device"])
                for row in sorted(self.notes.values(), key=lambda row: row["id"]) if not row["deleted"]]}
        return super().get_json(url, token=token, timeout=timeout)

    def download(self, url: str, destination: Path, *, token: str = "") -> None:
        self.requests.append(("GET", url))
        sha = url.rsplit("/", 1)[1]
        if sha not in self.blobs:
            raise HubResponseError(404, "Not Found")
        Path(destination).write_bytes(self.blobs[sha])

    def web_delete(self, note_id: str) -> None:
        """Deleted on the Luma Connect web page (or any other device)."""
        self.notes[note_id]["deleted"] = True
        self.revision += 1

    def lists(self) -> int:
        return sum(1 for method, url in self.requests if method == "GET" and url.endswith("/sync/notes"))


class Device:
    """One Luma device: its own library, pictures and sync state."""

    def __init__(self, root: Path, name: str, hub: AccountHub) -> None:
        self.name, self.hub, self.state = name, hub, {}
        self.store_path = root / name / "notes.sqlite3"
        self.attachments = root / name / "attachments"
        self.store = NotesStore(self.store_path)

    def sync(self) -> str:
        delta = NotesDeltaSync(address="https://hub.example", token=self.name, device_id=self.name,
                               http=self.hub, state=self.state, scope=f"hub|{self.name}",
                               store_path=self.store_path, observed_at=1, attachments=self.attachments)
        self.assertion = delta.supported()
        return delta.run()

    def write(self, title: str, body: str, note_id: str | None = None, picture: bytes | None = None):
        if note_id is None:
            note_id = self.store.create_note(title=title).id
        runs = ()
        if picture is not None:
            name = store_picture(picture, self.attachments)
            runs = ({"start": len(body), "end": len(body) + 1, "style": "image", "src": name},)
            body += "\ufffc"
        return self.store.update_note(note_id, title=title, body=body, runs=runs)

    def pages(self) -> dict[str, str]:
        return {note.title: note.body for note in self.store.list_notes()}

    def copies(self) -> list[str]:
        return [note.title for note in self.store.list_notes() if note.title.endswith(CONFLICT_SUFFIX)]


class PullTests(unittest.TestCase):
    """Changes made elsewhere reach this device (Notes sync both ways)."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="connect-notes-pull-"))
        self.patch = mock.patch.dict(os.environ, {"HOME": str(self.root), "XDG_DATA_HOME": str(self.root / "xdg")})
        self.patch.start()
        self.hub = AccountHub()
        self.laptop = Device(self.root, "laptop", self.hub)
        self.phone = Device(self.root, "phone", self.hub)

    def tearDown(self) -> None:
        self.laptop.store.close()
        self.phone.store.close()
        self.patch.stop()

    def posts(self) -> int:
        return sum(1 for method, _url in self.hub.requests if method == "POST")

    def test_a_page_made_changed_and_deleted_elsewhere_arrives(self) -> None:
        note = self.phone.write("Groceries", "milk ", picture=PNG)
        self.phone.sync()
        self.assertIn("received 1", self.laptop.sync())
        here = self.laptop.store.get_note(note.id)
        self.assertEqual((here.title, here.body), ("Groceries", "milk \ufffc"), "same page, same id")
        self.assertEqual(local_etag(here), self.hub.notes[note.id]["etag"], "exactly the host's version")
        self.assertEqual([run["style"] for run in here.runs], ["image"], "with its picture in place")
        self.assertTrue((self.laptop.attachments / here.runs[0]["src"]).read_bytes() == PNG)
        posts = self.posts()
        self.assertEqual(self.laptop.sync(), "notes: unchanged", "a page taken is not sent back")
        self.assertEqual(self.posts(), posts)
        self.phone.write("Groceries", "milk, eggs ", note.id, picture=PNG)
        self.phone.sync()
        self.laptop.sync()
        self.assertEqual(self.laptop.store.get_note(note.id).body, "milk, eggs \ufffc", "an edit arrives")
        self.phone.store.soft_delete_note(note.id)
        self.phone.sync()
        self.assertIn("removed 1", self.laptop.sync())
        self.assertEqual(self.laptop.pages(), {}, "a deletion arrives")
        self.assertEqual([n.id for n in self.laptop.store.list_notes(deleted=True)], [note.id],
                         "into Recently Deleted, where it can still be restored")
        self.assertTrue(self.hub.notes[note.id]["deleted"], "and it is not brought back on the host")
        self.assertEqual(self.laptop.sync(), "notes: unchanged")

    def test_a_page_changed_on_both_devices_keeps_one_copy_then_settles(self) -> None:
        note = self.laptop.write("Plan", "original")
        self.laptop.sync()
        self.phone.sync()
        self.assertIn(note.id, {n.id for n in self.phone.store.list_notes()}, "the phone has the page")
        self.phone.write("Plan", "edited on the phone", note.id)
        self.phone.sync()
        self.laptop.write("Plan", "edited on the laptop", note.id)
        self.assertIn("kept 1 other copy", self.laptop.sync())
        self.phone.sync()
        for device in (self.laptop, self.phone):
            self.assertEqual(device.pages(), {"Plan": "edited on the laptop",
                                              f"Plan{CONFLICT_SUFFIX}": "edited on the phone"}, device.name)
        for _ in range(5):
            self.laptop.sync()
            self.phone.sync()
        for device in (self.laptop, self.phone):
            self.assertEqual(device.copies(), [f"Plan{CONFLICT_SUFFIX}"], f"one copy on the {device.name}")
        self.assertEqual(sum(1 for row in self.hub.notes.values() if not row["deleted"]), 2)

    def test_a_page_deleted_elsewhere_and_unchanged_here_is_removed_here(self) -> None:
        note = self.laptop.write("Old list", "done")
        self.laptop.sync()
        self.hub.web_delete(note.id)
        self.assertIn("removed 1", self.laptop.sync())
        self.assertEqual(self.laptop.pages(), {})
        self.assertTrue(self.hub.notes[note.id]["deleted"], "never brought back")
        self.assertEqual(self.laptop.sync(), "notes: unchanged")
        self.assertTrue(self.hub.notes[note.id]["deleted"])

    def test_a_page_deleted_elsewhere_but_changed_here_since_is_kept(self) -> None:
        note = self.laptop.write("Draft", "v1")
        self.laptop.sync()
        self.hub.web_delete(note.id)
        self.laptop.write("Draft", "v2 written here", note.id)
        self.laptop.sync()
        self.assertEqual(self.laptop.pages(), {"Draft": "v2 written here"})
        self.assertFalse(self.hub.notes[note.id]["deleted"], "the change here goes up again")
        self.assertEqual(self.laptop.sync(), "notes: unchanged")

    def test_a_first_pull_merges_an_existing_library_by_id(self) -> None:
        # Both devices' pages are on the host; the laptop then loses its sync
        # state (the ThinkPad case: 13 pages here, 16 on the host).
        mine = [self.laptop.write(f"Mine {index}", "text").id for index in range(3)]
        self.laptop.sync()
        theirs = [self.phone.write(f"Theirs {index}", "text").id for index in range(2)]
        self.phone.sync()
        self.laptop.state.clear()
        result = self.laptop.sync()
        self.assertIn("received 2", result)
        self.assertEqual(sorted(n.id for n in self.laptop.store.list_notes()), sorted(mine + theirs),
                         "nothing duplicated, nothing copied")
        self.assertEqual(self.laptop.copies(), [])

    def test_a_page_saved_here_while_a_pull_takes_it_is_not_overwritten(self) -> None:
        # Notes saves the page between the pull reading it and writing the
        # host's version: the pull must not write over what was just saved.
        note = self.laptop.write("Plan", "original")
        self.laptop.sync()
        self.phone.sync()
        self.phone.write("Plan", "edited on the phone", note.id)
        self.phone.sync()
        taking = NotesDeltaSync._runs_for
        saved = []

        def saved_meanwhile(delta, *arguments, **keywords):
            if not saved:
                saved.append(self.laptop.write("Plan", "typed on the laptop", note.id))
            return taking(delta, *arguments, **keywords)

        with mock.patch.object(NotesDeltaSync, "_runs_for", saved_meanwhile):
            self.laptop.sync()
        self.assertEqual(len(saved), 1, "the save happened during the pull, as arranged")
        self.assertEqual(self.laptop.store.get_note(note.id).body, "typed on the laptop",
                         "what was saved here is not overwritten")
        for _ in range(3):
            self.laptop.sync()
            self.phone.sync()
        for device in (self.laptop, self.phone):
            self.assertEqual(device.pages(), {"Plan": "typed on the laptop",
                                              f"Plan{CONFLICT_SUFFIX}": "edited on the phone"}, device.name)

    def test_notes_and_sync_share_one_guard(self) -> None:
        # Notes kept the host's version as a copy while the page was being
        # edited; sync then meets the same version as a conflict: no second copy.
        note = self.laptop.write("Plan", "original")
        self.laptop.sync()
        theirs = self.laptop.write("Plan", "the phone's version", note.id)
        self.assertIsNotNone(connect_notes.keep_other_copy(self.laptop.store, theirs), "Notes keeps it once")
        self.assertIsNone(connect_notes.keep_other_copy(self.laptop.store, theirs), "and never twice")
        self.assertEqual(self.laptop.copies(), [f"Plan{CONFLICT_SUFFIX}"])
        delta = NotesDeltaSync(address="https://hub.example", token="laptop", device_id="laptop",
                               http=self.hub, state=self.laptop.state, scope="hub|laptop",
                               store_path=self.laptop.store_path, observed_at=1,
                               attachments=self.laptop.attachments)
        conflict = {"id": note.id, "etag": local_etag(theirs), "title": "Plan", "body": "the phone's version"}
        self.laptop.write("Plan", "typed on the laptop", note.id)
        self.assertEqual(delta._keep_conflicts([conflict], {n.id: n for n in self.laptop.store.list_notes()}, {}),
                         0, "sync sees the version Notes kept and keeps no second copy")
        self.assertEqual(self.laptop.copies(), [f"Plan{CONFLICT_SUFFIX}"])

    def test_no_loop_over_many_passes(self) -> None:
        shared = self.laptop.write("Shared", "one", picture=PNG)
        gone = self.laptop.write("Gone", "x")
        self.laptop.sync()
        self.phone.sync()
        self.assertIn(shared.id, {n.id for n in self.phone.store.list_notes()}, "the phone has the page")
        self.phone.write("Shared", "phone", shared.id, picture=PNG)
        self.phone.write("New", "from the phone")
        self.phone.store.soft_delete_note(gone.id)
        self.phone.sync()
        self.laptop.write("Shared", "laptop", shared.id, picture=PNG)
        for _ in range(2):
            self.laptop.sync()
            self.phone.sync()
        settled = (self.posts(), self.hub.lists(), self.hub.revision, len(self.hub.notes))
        for _ in range(10):
            self.assertEqual(self.laptop.sync(), "notes: unchanged")
            self.assertEqual(self.phone.sync(), "notes: unchanged")
        self.assertEqual((self.posts(), self.hub.lists(), self.hub.revision, len(self.hub.notes)), settled,
                         "no sends, no page lists, no revisions once settled")
        for device in (self.laptop, self.phone):
            self.assertEqual(device.pages(), {"Shared": "laptop\ufffc", f"Shared{CONFLICT_SUFFIX}": "phone\ufffc",
                                              "New": "from the phone"}, device.name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
