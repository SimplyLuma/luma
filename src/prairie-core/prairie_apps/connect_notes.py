# SPDX-License-Identifier: Apache-2.0
"""Notes to a sync host one change at a time, with versions and pictures.

The protocol is plain HTTP and JSON, and nothing in it is particular to the Luma
Hub: any host that answers ``GET <host>/api/hub/sync/capabilities`` with
``notes_delta`` can hold a device's notes, so the source of truth can move (to
a person's own machine, say) without the device changing how it talks.

- Each note carries an etag: the SHA-256 of its canonical content. A change is
  sent with the etag this device last had acknowledged (``base_etag``); the
  host applies it only if its copy is still that version.
- When the host's copy moved on (edited somewhere else), the change is not
  applied and the host sends its copy back. Nothing is thrown away: the host's
  copy is kept on this device as a separate page ("… (other copy)"), and this
  device's version then goes up as the current one.
- A change refused although it named the version the host holds is not a
  conflict: the host keeps that page for another device. Nothing is copied;
  the change is not resent until the page changes here or there. The same
  version of a page is never kept here twice.
- A page deleted here is deleted there only if the host still holds the
  version this device last saw; otherwise the host's newer copy comes back here.
- Pictures are sent once. The host names the ones it lacks, by content
  address, after accepting the notes that use them; this device keeps a list
  of what it still owes, so a picture the host could not take yet (rate
  limit, a dropped connection) goes up later without the note being resent.
  A host may refuse a picture for good reasons with a stable code:
  ``note_storage_full`` (507) is not retried until the host reports less
  storage in use or a picture is removed from a page here; a picture refused
  as ``note_image_too_large`` (413) is never retried, and Notes offers to
  shrink it. Text keeps syncing either way. A page restored from Recently
  Deleted goes up again, and the host names any picture it has since swept.
- Only what changed since the last acknowledgement travels, in one request.
- Changes travel both ways. Before sending, the device asks whether the
  account moved since it last looked; if so it reads the host's pages and
  takes every page added or changed elsewhere, with its pictures. A page
  changed both here and there keeps both versions, once. A page deleted
  elsewhere goes to Recently Deleted here, unless it was changed here since
  it was last sent; then this device's version goes up again. A device with
  a library of its own merges by page id on its first pull: nothing is
  duplicated.

Hosts without the capability get the whole-library snapshot, as before.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Callable

from .notes_attachments import (
    MIME_TYPES, STORAGE_FULL, TOO_LARGE, PictureError, attachments_directory, picture_digest,
    referenced_pictures, store_picture, write_sync_status,
)
from .notes_backend import Note, NoteChangedError, NotesStore, _clean_note_title

CAPABILITY_TTL_SECONDS = 6 * 3600
MAX_BATCH_NOTES = 200
MAX_BATCH_BYTES = 4 * 1024 * 1024
CONFLICT_SUFFIX = " (other copy)"
# How many kept versions are remembered, so the same one is never kept twice.
MAX_KEPT_VERSIONS = 1000
# While pictures wait for storage, how often the host is asked how much of
# it is in use (a removed picture here retries at once, without asking).
USAGE_RECHECK_SECONDS = 600
# The host's pages are read again at least this often even when the account
# revision says nothing moved.
FULL_PULL_SECONDS = 900
# The host lists at most this many pages; a list this long may be cut short,
# so pages missing from it are not taken as deleted.
HOST_LIST_LIMIT = 5000
# An empty list from the host never empties a library of this many synced pages.
MASS_DELETE_FLOOR = 5
OBJECT = "\ufffc"


def note_attachments(note: Note) -> list[str]:
    return sorted({picture_digest(name) for name in referenced_pictures(note.runs)})


def note_etag(title: str, body: str, folder_id: str | None, favorite: bool, attachments) -> str:
    """The host's version recipe: SHA-256 of canonical JSON, sorted keys, no spaces."""
    canonical = json.dumps(
        {"attachments": sorted(set(attachments)), "body": body, "favorite": bool(favorite),
         "folder_id": folder_id, "title": title},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def local_etag(note: Note) -> str:
    return note_etag(note.title, note.body, note.folder_id, note.favorite, note_attachments(note))


def kept_version(note_id: str, etag: str) -> str:
    """The conflict-copy guard's key for one version of one page."""
    return f"{note_id}|{etag}"


def keep_other_copy(store: NotesStore, note: Note) -> Note | None:
    """Keep this version of a page as its own page, "… (other copy)", once.

    Notes calls this when a page changed elsewhere while it was being edited
    here: the person's text stays on the page and the other version is kept
    beside it. The guard is the one sync uses, so neither Notes nor sync keeps
    the same version twice. None when it was already kept.
    """
    title = note.title or "Untitled page"
    return store.keep_other_copy(kept_version(note.id, local_etag(note)), title=f"{title}{CONFLICT_SUFFIX}",
                                 body=note.body, runs=note.runs, folder_id=note.folder_id)


def _sent_hash(item: dict) -> str:
    """Everything sent for a note, so a folder rename is sent even though the
    note's version (which does not include the folder's name) is unchanged."""
    fields = {key: item[key] for key in sorted(item) if key != "base_etag"}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


class NotesDeltaSync:
    """One incremental exchange of this device's notes with a sync host."""

    def __init__(self, *, address: str, token: str, device_id: str, http, state: dict, scope: str,
                 store_path: Path, observed_at: int, clock: Callable[[], float] = time.time,
                 attachments: Path | None = None) -> None:
        self.address = address
        self.token = token
        self.device_id = device_id
        self.http = http
        self.state = state
        self.scope = scope
        self.store_path = store_path
        self.observed_at = observed_at
        self.clock = clock
        self.attachments = attachments or attachments_directory()
        self.revision: int | None = None

    # ------------------------------------------------------------ capability

    def supported(self) -> bool:
        """Whether the host speaks this protocol; asked once every few hours."""
        from .connect_sync import HubResponseError
        key = f"{self.scope}|capabilities"
        known = self.state.get(key, {})
        if known and self.clock() - float(known.get("checked", 0)) < CAPABILITY_TTL_SECONDS:
            return bool(known.get("notes_delta"))
        try:
            reply = self.http.get_json(f"{self.address}/api/hub/sync/capabilities", token=self.token)
            delta = int(reply.get("notes_delta", 0) or 0) >= 1
            blobs = int(reply.get("notes_blobs", 0) or 0) >= 1
            usage = _usage(reply)
        except HubResponseError as error:
            # A host that predates the question answers it however it answers
            # an unknown path: 404, 405, or the 401 of its browser routes. Any
            # of those means "snapshots"; a token that really was revoked is
            # still caught by the snapshot request that follows.
            if error.status == 429 or error.status >= 500:
                raise
            delta = blobs = False
            usage = {}
        self.state[key] = {"notes_delta": delta, "notes_blobs": blobs, "checked": self.clock(), **usage}
        return delta

    def _refresh_usage(self) -> None:
        """Ask the host how much picture storage is in use, at most every
        few minutes, and only while pictures wait for storage."""
        key = f"{self.scope}|capabilities"
        known = self.state.setdefault(key, {})
        if self.clock() - float(known.get("usage_checked", 0) or 0) < USAGE_RECHECK_SECONDS:
            return
        known["usage_checked"] = self.clock()
        reply = self.http.get_json(f"{self.address}/api/hub/sync/capabilities", token=self.token)
        known.update(_usage(reply))

    @property
    def usage(self) -> dict:
        known = self.state.get(f"{self.scope}|capabilities", {})
        return {"quota_bytes": known.get("quota_bytes"), "used_bytes": known.get("used_bytes")}

    # ----------------------------------------------------------------- state

    @property
    def record(self) -> dict:
        key = f"{self.scope}|notes-delta"
        record = self.state.get(key)
        if not isinstance(record, dict) or record.get("store") != str(self.store_path):
            record = {"store": str(self.store_path), "notes": {}, "bootstrapped": False}
            self.state[key] = record
        record.setdefault("notes", {})
        # Pictures the host named as missing and has not taken yet, by sha256:
        # {"reason": "" | code, "used": bytes in use then, "refs": pictures in use here then}.
        record.setdefault("blobs", {})
        # "<note id>|<etag>" of every host version kept here as an other copy.
        record.setdefault("kept", [])
        return record

    def _bootstrap(self) -> None:
        """A device moving from snapshots starts from what the host already
        holds for it, so its first incremental exchange is not all conflicts."""
        record = self.record
        if record.get("bootstrapped"):
            return
        reply = self.http.get_json(f"{self.address}/api/hub/sync/notes", token=self.token)
        for item in reply.get("items", []) or []:
            if (isinstance(item, dict) and item.get("origin_device_id") == self.device_id
                    and isinstance(item.get("id"), str) and isinstance(item.get("etag"), str)):
                record["notes"][item["id"]] = {"etag": item["etag"], "sent": ""}
        record["bootstrapped"] = True

    # ------------------------------------------------------------------ sync

    def run(self) -> str:
        """Send what changed; keep the host's copy of anything it would not
        take, and then send this device's version of it straight away."""
        self._bootstrap()
        totals = {"sent": 0, "deleted": 0, "kept": 0, "uploaded": 0}
        self._referenced: set[str] = set()
        try:
            pulled = self._pull()
            for _attempt in range(2):
                counts = self._exchange()
                if counts is None:
                    break
                for key in totals:
                    totals[key] += counts[key]
                if not counts["conflicts"]:
                    break
            totals["uploaded"] += self._upload_pending()
        finally:
            self._publish_status()
        if not any(totals.values()) and not any(pulled.values()):
            return "notes: unchanged"
        parts = [f"notes: sent {totals['sent']} change(s)"]
        if pulled["received"]:
            parts.append(f"received {pulled['received']}")
        if pulled["removed"]:
            parts.append(f"removed {pulled['removed']} deleted elsewhere")
        totals["kept"] += pulled["kept"]
        if totals["deleted"]:
            parts.append(f"deleted {totals['deleted']}")
        if totals["kept"]:
            parts.append(f"kept {totals['kept']} other copy(ies)")
        if totals["uploaded"]:
            parts.append(f"uploaded {totals['uploaded']} picture(s)")
        return ", ".join(parts)

    def _exchange(self) -> dict | None:
        from .connect_sync import ConnectError
        acked: dict[str, dict] = self.record["notes"]
        store = NotesStore(self.store_path)
        try:
            live = store.list_notes()
            trashed = {note.id: note for note in store.list_notes(deleted=True)}
            names = {folder.id: folder.name for folder in store.list_folders()}
        finally:
            store.close()
        self._referenced = {picture_digest(name) for note in live for name in note_attachments(note)}
        upserts, pending_sent, bases = [], {}, {}
        for note in live:
            item = _item(note, names)
            sent = _sent_hash(item)
            known = acked.get(note.id)
            if known and known.get("sent") == sent:
                continue
            item["base_etag"] = known.get("etag") if known else None
            bases[note.id] = item["base_etag"]
            upserts.append(item)
            pending_sent[note.id] = sent
        # Only pages moved to Recently Deleted here are deleted there. A page
        # missing from the library altogether (a replaced or damaged library)
        # is never taken as a deletion.
        deletes = [{"id": note_id, "base_etag": acked[note_id].get("etag")}
                   for note_id in sorted(acked) if note_id in trashed]
        bases.update((item["id"], item["base_etag"]) for item in deletes)
        if not upserts and not deletes:
            return None
        counts = {"sent": 0, "deleted": 0, "kept": 0, "uploaded": 0, "conflicts": 0}
        conflicts: list[dict] = []
        for batch_upserts, batch_deletes in self._batches(upserts, deletes):
            reply = self.http.post_json(
                f"{self.address}/api/hub/sync/notes/changes",
                {"device_id": self.device_id, "observed_at": self.observed_at,
                 "upserts": batch_upserts, "deletes": batch_deletes},
                token=self.token)
            if not isinstance(reply.get("accepted"), list):
                raise ConnectError("The sync host's answer to the note changes could not be read.")
            if isinstance(reply.get("revision"), int):
                self.revision = reply["revision"]
            for accepted in reply.get("accepted", []):
                note_id = accepted.get("id") if isinstance(accepted, dict) else None
                if note_id in pending_sent:
                    acked[note_id] = {"etag": str(accepted.get("etag", "")), "sent": pending_sent[note_id]}
                    counts["sent"] += 1
            for note_id in reply.get("deleted", []):
                if acked.pop(note_id, None) is not None:
                    counts["deleted"] += 1
            conflicts.extend(item for item in reply.get("conflicts", []) if isinstance(item, dict))
            self._owe(reply.get("missing_blobs", []))
        if conflicts:
            live_by_id = {note.id: note for note in live}
            refused = [item for item in conflicts if self._refused(item, bases, pending_sent, trashed)]
            conflicts = [item for item in conflicts if item not in refused]
            counts["conflicts"] = len(conflicts)
            counts["kept"] = self._keep_conflicts(conflicts, live_by_id, trashed, bases)
        return counts

    # ------------------------------------------------------------------ pull

    def _pull(self) -> dict:
        """Take what changed on the host since this device last looked.

        The account revision says whether anything moved; only then are the
        host's pages read. Pages are matched by id, so a library this device
        already had is merged, never duplicated.
        """
        from .connect_sync import ConnectError
        counts = {"received": 0, "removed": 0, "kept": 0}
        record = self.record
        acked: dict[str, dict] = record["notes"]
        if not record.get("pull_migrated"):
            # Before the host kept pages for the whole account, a change to a
            # page another device had made was refused and parked. Such a
            # change is sent again now; the host takes it.
            for entry in acked.values():
                if entry.pop("refused", None):
                    entry["sent"] = ""
            record["pull_migrated"] = True
        reply = self.http.get_json(f"{self.address}/api/hub/sync/events?after=0&wait=0", token=self.token)
        revision = reply.get("revision") if isinstance(reply, dict) else None
        if (isinstance(revision, int) and record.get("pulled_revision") == revision
                and self.clock() - float(record.get("pulled_at", 0) or 0) < FULL_PULL_SECONDS):
            return counts
        reply = self.http.get_json(f"{self.address}/api/hub/sync/notes", token=self.token)
        items = reply.get("items") if isinstance(reply, dict) else None
        if not isinstance(items, list):
            raise ConnectError("The sync host's list of notes could not be read.")
        remote = {item["id"]: item for item in items
                  if isinstance(item, dict) and isinstance(item.get("id"), str)
                  and isinstance(item.get("etag"), str) and isinstance(item.get("body"), str)}
        deferred = 0
        store = NotesStore(self.store_path)
        try:
            live = {note.id: note for note in store.list_notes()}
            trashed = {note.id: note for note in store.list_notes(deleted=True)}
            names = {folder.id: folder.name for folder in store.list_folders()}
            for note_id, item in remote.items():
                etag, known = item["etag"], acked.get(note_id)
                mine = live.get(note_id)
                if mine is not None:
                    sent = _sent_hash(_item(mine, names))
                    if local_etag(mine) == etag:
                        # Already the same here (a first pull, or both sides
                        # made the same change): nothing to take or to send.
                        if not known or known.get("etag") != etag or not known.get("sent"):
                            acked[note_id] = {"etag": etag, "sent": sent}
                        continue
                    if known and known.get("etag") == etag:
                        continue  # Unchanged there; the change here goes up.
                    if known and known.get("sent") == sent and not known.get("refused"):
                        if self._take(store, item, mine):
                            counts["received"] += 1
                        else:
                            deferred += 1
                        continue
                    # Changed here since it was last sent, and changed there:
                    # both versions stay, the host's as one copy.
                    counts["kept"] += self._keep_conflicts([dict(item, deleted=False)], live, trashed)
                    continue
                gone = trashed.get(note_id)
                if gone is not None:
                    if known and known.get("etag") == etag:
                        continue  # Deleted here; the deletion goes up.
                    if not known and local_etag(gone) == etag:
                        # Deleted here after this very version: delete it there.
                        acked[note_id] = {"etag": etag, "sent": _sent_hash(_item(gone, names))}
                        continue
                    _log(f"notes: page {note_id} was changed elsewhere after it was deleted here; "
                         "bringing it back")
                if self._take(store, item, gone):
                    counts["received"] += 1
                else:
                    deferred += 1
            live = {note.id: note for note in store.list_notes()}
            names = {folder.id: folder.name for folder in store.list_folders()}
            missing = [note_id for note_id in acked if note_id not in remote]
            if len(items) >= HOST_LIST_LIMIT:
                missing = []
            elif not remote and sum(1 for note_id in missing if note_id in live) >= MASS_DELETE_FLOOR:
                _log(f"notes: the host listed no pages but {len(missing)} were synced; "
                     "not removing any here")
                missing = []
            for note_id in missing:
                known = acked.pop(note_id)
                mine = live.get(note_id)
                if mine is None:
                    continue
                if known.get("sent") == _sent_hash(_item(mine, names)) and not known.get("refused"):
                    store.soft_delete_note(note_id)
                    counts["removed"] += 1
                else:
                    _log(f"notes: page {note_id} was deleted elsewhere but changed here since; "
                         "keeping it and sending it again")
        finally:
            store.close()
        if not deferred and isinstance(revision, int):
            record["pulled_revision"] = revision
            record["pulled_at"] = self.clock()
        return counts

    def _take(self, store: NotesStore, item: dict, mine: Note | None) -> bool:
        """Write the host's version of a page here, pictures first. False when
        it cannot be written exactly yet (a picture not fetched): it is tried
        again on the next pull and nothing is sent for it meanwhile."""
        note_id, etag = item["id"], item["etag"]
        attachments = [sha for sha in item.get("attachments", []) or [] if _is_sha(sha)]
        if not all(self._fetch_picture(sha) for sha in attachments):
            _log(f"notes: page {note_id} waits for a picture from the host")
            return False
        title, body = _clean_note_title(str(item.get("title", ""))), item["body"]
        folder = item.get("folder_id") if isinstance(item.get("folder_id"), str) else None
        favorite = bool(item.get("favorite"))
        runs = self._runs_for(body, attachments, mine)
        if note_etag(title, body, folder, favorite, [picture_digest(run["src"]) for run in runs
                                                      if run.get("style") == "image"]) != etag:
            _log(f"notes: page {note_id} from the host cannot be kept exactly here; left as it is")
            return False
        now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(self.clock()))
        try:
            # Only over the page as it was read: one saved in Notes meanwhile
            # is a change here, and the next pass keeps both versions.
            note = store.put_synced_note(
                note_id, title=title, body=body, runs=runs, folder_id=folder,
                folder_name=str(item.get("folder_name", "") or ""), favorite=favorite,
                created_at=str(item.get("created_at") or (mine.created_at if mine else now)),
                modified_at=str(item.get("modified_at") or now), expected=mine)
        except NoteChangedError:
            _log(f"notes: page {note_id} was saved here while it was being taken; not overwriting it")
            return False
        names = {entry.id: entry.name for entry in store.list_folders()}
        self.record["notes"][note_id] = {"etag": etag, "sent": _sent_hash(_item(note, names))}
        return True

    def _picture_name(self, sha: str) -> str | None:
        for found in sorted(self.attachments.glob(f"{sha}.*")):
            if found.suffix.lstrip(".") in MIME_TYPES:
                return found.name
        return None

    def _fetch_picture(self, sha: str) -> bool:
        """Have the picture here, fetching it from the host if need be."""
        from .connect_sync import ConnectError, HubResponseError
        if self._picture_name(sha):
            return True
        self.attachments.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.attachments / f".incoming-{sha}"
        try:
            self.http.download(f"{self.address}/api/hub/sync/notes/blobs/{sha}", temporary, token=self.token)
            data = temporary.read_bytes()
            if hashlib.sha256(data).hexdigest() != sha:
                _log(f"notes: the host's picture {sha[:12]} did not match its name; not kept")
                return False
            store_picture(data, self.attachments)
            return True
        except HubResponseError as error:
            if error.status == 429 or error.status >= 500:
                raise
            _log(f"notes: the host could not give picture {sha[:12]}: {error}")
            return False
        except (ConnectError, PictureError, OSError) as error:
            _log(f"notes: picture {sha[:12]} could not be fetched: {error}")
            return False
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass

    def _runs_for(self, body: str, attachments: list[str], mine: Note | None) -> tuple[dict, ...]:
        """Formatting for the host's text: the host keeps no formatting, so
        this device's own is kept where the text around it is unchanged, and
        each picture goes back on an object character, in the order this
        device had them, new ones after."""
        runs: list[dict] = []
        order: list[str] = []
        widths: dict[str, int] = {}
        if mine is not None:
            old = mine.body
            prefix = 0
            limit = min(len(old), len(body))
            while prefix < limit and old[prefix] == body[prefix]:
                prefix += 1
            suffix = 0
            while suffix < limit - prefix and old[-1 - suffix] == body[-1 - suffix]:
                suffix += 1
            shift = len(body) - len(old)
            for run in sorted(mine.runs, key=lambda run: (run.get("start", 0), run.get("end", 0))):
                if run.get("style") == "image":
                    sha = picture_digest(str(run.get("src", "")))
                    if sha in attachments and sha not in order:
                        order.append(sha)
                        if isinstance(run.get("width"), int):
                            widths[sha] = run["width"]
                    continue
                start, end = run.get("start"), run.get("end")
                if not isinstance(start, int) or not isinstance(end, int):
                    continue
                if end <= prefix:
                    runs.append(dict(run))
                elif start >= len(old) - suffix:
                    runs.append(dict(run, start=start + shift, end=end + shift))
        order += [sha for sha in sorted(set(attachments)) if sha not in order]
        local_files = {run['start'] for run in runs if run.get('style') == 'file'}
        slots = [index for index, char in enumerate(body) if char == OBJECT and index not in local_files]
        for index, sha in zip(slots, order):
            name = self._picture_name(sha)
            if name is None:
                continue
            run = {"start": index, "end": index + 1, "style": "image", "src": name}
            if sha in widths:
                run["width"] = widths[sha]
            runs.append(run)
        return tuple(sorted(runs, key=lambda run: (run["start"], run["end"])))

    def _refused(self, conflict: dict, bases: dict, pending_sent: dict, trashed) -> bool:
        """A change that named the very version the host holds, and was still
        not taken: the host keeps that page for another device. There is
        nothing to merge, so nothing is copied, and the change is not sent
        again until the page changes here (or there)."""
        note_id, etag = conflict.get("id"), conflict.get("etag")
        if not isinstance(note_id, str) or not etag or bases.get(note_id) != etag or conflict.get("deleted"):
            return False
        acked = self.record["notes"]
        if note_id in trashed:
            # Deleting it is refused for good: stop asking.
            acked.pop(note_id, None)
            _log(f"notes: the host keeps page {note_id} for another device; not deleting it there")
        else:
            acked[note_id] = {"etag": str(etag), "sent": pending_sent.get(note_id, ""), "refused": True}
            _log(f"notes: the host keeps page {note_id} for another device; "
                 "changes here stay on this device until it changes")
        return True

    def _batches(self, upserts: list[dict], deletes: list[dict]):
        batch: list[dict] = []
        size = 0
        for item in upserts:
            length = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
            if batch and (len(batch) >= MAX_BATCH_NOTES or size + length > MAX_BATCH_BYTES):
                yield batch, []
                batch, size = [], 0
            batch.append(item)
            size += length
        yield batch, deletes

    def _keep_conflicts(self, conflicts: list[dict], live: dict[str, Note], trashed: dict[str, Note],
                        bases: dict | None = None) -> int:
        """Keep the host's copy of every page it would not take, then send ours.

        The host's version is saved here as a page of its own, next to this
        device's version, so both survive; this device's version then becomes
        the current one on the host, based on the version the host reported.
        """
        acked = self.record["notes"]
        kept_versions: list = self.record["kept"]
        kept = 0
        store = NotesStore(self.store_path)
        try:
            for conflict in conflicts:
                note_id = conflict.get("id")
                if not isinstance(note_id, str):
                    continue
                theirs_etag = str(conflict.get("etag", ""))
                mine = live.get(note_id)
                same = mine is not None and local_etag(mine) == theirs_etag
                version = kept_version(note_id, theirs_etag)
                if (not same and not conflict.get("deleted")
                        and (version in kept_versions or store.has_kept_version(version))):
                    # Guard: this version is already kept here; a second copy
                    # would only be a duplicate.
                    _log(f"notes: version {theirs_etag[:12]} of page {note_id} is already kept here; "
                         f"not making another copy (sent against {str((bases or {}).get(note_id))[:12]})")
                elif not same and not conflict.get("deleted"):
                    title = str(conflict.get("title", "")) or "Untitled page"
                    folder = conflict.get("folder_id")
                    try:
                        store.get_folder(folder) if isinstance(folder, str) else None
                    except KeyError:
                        folder = None
                    copy = store.create_note(folder_id=folder if isinstance(folder, str) else None,
                                             title=f"{title}{CONFLICT_SUFFIX}")
                    body = str(conflict.get("body", ""))
                    pictures = [sha for sha in conflict.get("attachments", []) or [] if _is_sha(sha)]
                    runs = (self._runs_for(body, pictures, None)
                            if all(self._fetch_picture(sha) for sha in pictures) else ())
                    store.update_note(copy.id, title=copy.title, body=body, runs=runs)
                    store.remember_kept_version(version)
                    kept_versions.append(version)
                    del kept_versions[:-MAX_KEPT_VERSIONS]
                    kept += 1
                if note_id in trashed and not conflict.get("deleted"):
                    # Deleted here, changed there: the change wins the page back
                    # (as the copy just kept); this device stops deleting it.
                    acked.pop(note_id, None)
                    continue
                # Ours goes up next time on top of the version the host has.
                acked[note_id] = {"etag": theirs_etag, "sent": ""}
        finally:
            store.close()
        return kept

    def _owe(self, missing) -> None:
        """Note the pictures the host lacks; a refusal already recorded stays."""
        owed = self.record["blobs"]
        for sha in missing or []:
            if isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha):
                owed.setdefault(sha, {"reason": ""})

    def _upload_pending(self) -> int:
        """Send the pictures still owed, each once, by content address."""
        from .connect_sync import HubResponseError
        owed: dict[str, dict] = self.record["blobs"]
        referenced = self._referenced
        removed = False
        for sha in list(owed):
            if sha not in referenced:
                # No page here uses it any more: nothing to send.
                del owed[sha]
                removed = True
        waiting = [sha for sha, entry in owed.items() if entry.get("reason") == STORAGE_FULL]
        freed_here = removed or any(
            ref not in referenced for sha in waiting for ref in owed[sha].get("refs", ()))
        if waiting and not freed_here:
            try:
                self._refresh_usage()
            except HubResponseError as error:
                if error.status == 429 or error.status >= 500:
                    raise
        used_now = self.usage.get("used_bytes")
        count = 0
        for sha in sorted(owed):
            entry = owed[sha]
            reason = entry.get("reason", "")
            if reason == TOO_LARGE:
                continue
            if reason == STORAGE_FULL and not freed_here and not (
                    isinstance(used_now, int) and isinstance(entry.get("used"), int) and used_now < entry["used"]):
                continue
            found = next(iter(sorted(self.attachments.glob(f"{sha}.*"))), None)
            data = found.read_bytes() if found is not None else b""
            if found is None or hashlib.sha256(data).hexdigest() != sha:
                # Not kept here (or damaged): nothing this device can send.
                del owed[sha]
                continue
            extension = found.suffix.lstrip(".")
            try:
                self.http.put_bytes(f"{self.address}/api/hub/sync/notes/blobs/{sha}", data,
                                    MIME_TYPES.get(extension, "application/octet-stream"), token=self.token)
            except HubResponseError as error:
                if error.status == 429:
                    raise
                if error.status == 507 or error.code == STORAGE_FULL:
                    # Every picture still owed waits: none would fit either.
                    used = self.usage.get("used_bytes")
                    if not isinstance(used, int):
                        used = self.usage.get("quota_bytes")
                    for other in owed.values():
                        if other.get("reason") != TOO_LARGE:
                            other.update(reason=STORAGE_FULL, used=used, refs=sorted(referenced))
                    break
                if error.status == 413 or error.code == TOO_LARGE:
                    entry.update(reason=TOO_LARGE)
                    continue
                if error.status >= 500:
                    break
                continue
            del owed[sha]
            count += 1
        return count

    def _publish_status(self) -> None:
        """Tell Notes which pictures are not synced and why."""
        owed = self.record.get("blobs", {})
        pictures = {sha: entry["reason"] for sha, entry in owed.items()
                    if entry.get("reason") in (STORAGE_FULL, TOO_LARGE)}
        try:
            write_sync_status({"pictures": pictures, **self.usage}, self.store_path.parent)
        except OSError:
            pass


def _item(note: Note, names: dict) -> dict:
    """What is sent for a page."""
    return {
        "id": note.id, "title": note.title, "body": note.body, "folder_id": note.folder_id,
        "folder_name": names.get(note.folder_id or "", ""), "favorite": bool(note.favorite),
        "created_at": note.created_at, "modified_at": note.modified_at,
        "attachments": note_attachments(note),
    }


def _is_sha(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _log(message: str) -> None:
    print(f"luma-connect-sync: {message}", file=sys.stderr)


def _usage(reply: dict) -> dict:
    """The host's picture storage figures, when it gives them."""
    usage = {}
    for field, key in (("note_blob_quota_bytes", "quota_bytes"), ("note_blob_used_bytes", "used_bytes")):
        value = reply.get(field) if isinstance(reply, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            usage[key] = value
    return usage
