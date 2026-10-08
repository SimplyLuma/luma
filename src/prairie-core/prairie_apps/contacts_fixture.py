# SPDX-License-Identifier: Apache-2.0
"""Contacts from a JSON file, in memory: LUMA_CONTACTS_FIXTURE=<path>.

For conformance runs (tools/lumaui-conform) and tests. It never opens the
address book, Messages or the sharing store on disk; edits, deletes and
sharing changes live in memory until the window closes. Paths in the file
are relative to it. The v71 sample is tests/fixtures/contacts-v71.json.

    {
      "me": {"name", "handle", "phone", "email", "organization", "role", "photo", "link", "hue"},
      "lists": {"launch": "Launch team", ...},                 # list key → name
      "share_to": [uid, ...],                                  # who Share offers (v71 "Send ___'s card to")
      "sheet_to": [uid, ...],                                  # who the computer's share sheet offers (v71 SHPPL)
      "contacts": [{"uid", "name", "handle", "on_luma", "phone", "email", "organization", "role",
                    "birthday", "address", "note", "favourite", "lists": [key, ...], "hue",
                    "photo": "face.jpg", "light": "wide.jpg", "light_focus": [x, y]}],
      "together": {uid: [{"kind": "conversation" | "note" | "album" | "file", "icon", "title",
                          "subtitle", "when", "app", "name", "size", "content_type"}]},
      "sharing": {uid: {"share_mobile": true, "share_email": false}},
      "selected": uid,                                         # the card the window opens on
      "edit": false                                            # open it in edit mode
    }

A field that is missing or empty is not shown. LUMA_CONTACTS_EDIT=1 also
opens the selected card in edit mode, and LUMA_CONTACTS_SELECTED=<uid | "me">
names another card to open on. At phone width the window opens on its
list (v71 list-first); LUMA_CONTACTS_OPEN=1 pushes the selected card instead.

The fixture makes every write v71 asks for (favourite, lists, block, link a
duplicate, your own card), in memory, so the gate and the tests see them.

Every element the conformance tool compares carries a stable widget name
(Gtk.Widget.set_name) given by contacts.py: WIDGET_NAMES lists them.
"""
from __future__ import annotations

import itertools
import json
import os
from dataclasses import replace
from pathlib import Path

from .contacts_data import NEW_WRITES, ContactsSource, Me, SharingStore
from .contacts_together import TogetherItem
from .eds_backend import LOCAL_ADDRESS_BOOK, AddressBookName, ContactRecord

_new_ids = itertools.count(1)


def _read(base: Path, name: str | None) -> bytes:
    if not name:
        return b""
    try:
        return (base / name).read_bytes()
    except OSError:
        return b""


class FixtureSource(ContactsSource):
    writes = NEW_WRITES
    rehearsal = True

    def __init__(self, path: Path) -> None:
        super().__init__(SharingStore(None))
        self.path = Path(path)
        base = self.path.parent
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.lists = dict(document.get("lists", {}))
        self.selected = os.environ.get("LUMA_CONTACTS_SELECTED") or document.get("selected")
        self.edit = bool(document.get("edit")) or os.environ.get("LUMA_CONTACTS_EDIT") == "1"
        self.open = os.environ.get("LUMA_CONTACTS_OPEN") == "1"
        self.share_to = [str(uid) for uid in document.get("share_to", ())]
        self.sheet_to = [str(uid) for uid in document.get("sheet_to", ())]
        self.blocked: set[str] = set()
        self._records: dict[str, ContactRecord] = {}
        self._lights: dict[str, tuple[bytes, tuple[float, float]]] = {}
        for raw in document.get("contacts", []):
            record = ContactRecord(
                str(raw["uid"]), raw["name"], raw.get("phone", ""), raw.get("email", ""),
                raw.get("organization", ""), raw.get("role", ""),
                categories=tuple(raw.get("lists", ())), book=LOCAL_ADDRESS_BOOK.label,
                birthday=raw.get("birthday", ""), address=raw.get("address", ""),
                photo=_read(base, raw.get("photo")), note=raw.get("note", ""),
                favourite=bool(raw.get("favourite")), handle=raw.get("handle", "") or "",
                on_luma=raw.get("on_luma"), hue=raw.get("hue"))
            self._records[record.uid] = record
            if raw.get("light"):
                focus = raw.get("light_focus") or (0.5, 0.5)
                self._lights[record.uid] = (_read(base, raw["light"]), (float(focus[0]), float(focus[1])))
        raw_me = document.get("me")
        self._me = Me(raw_me["name"], raw_me.get("handle", ""), raw_me.get("phone", ""), raw_me.get("email", ""),
                      raw_me.get("organization", ""), raw_me.get("role", ""), _read(base, raw_me.get("photo")),
                      raw_me.get("link", ""), raw_me.get("hue")) if raw_me else None
        self._together = {str(uid): tuple(self._item(base, item) for item in items)
                          for uid, items in document.get("together", {}).items()}
        for uid, sharing in document.get("sharing", {}).items():
            self.sharing.set_share_mobile(str(uid), bool(sharing.get("share_mobile")))
            self.sharing.set_share_email(str(uid), bool(sharing.get("share_email")))

    @staticmethod
    def _item(base: Path, raw: dict) -> TogetherItem:
        kind = raw.get("kind", "conversation")
        if kind == "file":
            return TogetherItem("file", raw.get("name", ""), raw.get("subtitle", ""), 0,
                                path=str(base / raw["path"]) if raw.get("path") else raw.get("name", ""),
                                content_type=raw.get("content_type", ""), size=int(raw.get("size", 0)),
                                icon=raw.get("icon", ""), when=raw.get("when", ""))
        return TogetherItem(kind, raw.get("title", ""), raw.get("subtitle", ""), 0,
                            icon=raw.get("icon", ""), when=raw.get("when", ""))

    def load(self) -> tuple[list[ContactRecord], AddressBookName]:
        return list(self._records.values()), LOCAL_ADDRESS_BOOK

    def together(self, record: ContactRecord) -> tuple[TogetherItem, ...]:
        return self._together.get(record.uid, ())

    def update(self, uid: str, changes: dict[str, str]) -> str:
        record = self._records[uid]
        organization, _, role = changes.get("work", f"{record.organization} · {record.role}").partition(" · ")
        self._records[uid] = replace(record, **{key: value for key, value in changes.items()
                                                if key in ("name", "phone", "email", "birthday", "address", "note", "handle")},
                                     organization=organization.strip(), role=role.strip())
        return uid

    def create(self, fields: dict[str, str]) -> str:
        uid = f"new-{next(_new_ids)}"
        self._records[uid] = ContactRecord(uid, fields.get("name", ""), book=LOCAL_ADDRESS_BOOK.label)
        return self.update(uid, fields)

    def delete(self, uid: str) -> None:
        self._records.pop(uid, None)

    def me(self) -> Me | None:
        return self._me

    def share_people(self, records: list[ContactRecord]) -> list[ContactRecord]:
        known = {record.uid: record for record in records}
        return [known[uid] for uid in self.share_to if uid in known] or super().share_people(records)

    def sheet_people(self, records: list[ContactRecord]) -> list[ContactRecord]:
        known = {record.uid: record for record in records}
        return [known[uid] for uid in self.sheet_to if uid in known] or super().sheet_people(records)

    def set_favourite(self, uid: str, favourite: bool) -> None:
        self._records[uid] = replace(self._records[uid], favourite=favourite)

    def set_lists(self, uid: str, lists: tuple[str, ...]) -> None:
        self._records[uid] = replace(self._records[uid], categories=tuple(lists))

    def block(self, uid: str) -> None:
        self.blocked.add(uid)

    def link_duplicate(self, uid: str) -> None:
        if uid not in self._records:
            raise KeyError(uid)

    def update_me(self, changes: dict[str, str]) -> None:
        organization, _, role = changes.get("work", f"{self._me.organization} · {self._me.role}").partition(" · ")
        self._me = replace(self._me, **{key: value for key, value in changes.items() if key in ("name", "phone", "email")},
                           organization=organization.strip(), role=role.strip())

    def light(self, record: ContactRecord) -> tuple[bytes, tuple[float, float]]:
        return self._lights.get(record.uid, (record.photo, (0.5, 0.5)))
