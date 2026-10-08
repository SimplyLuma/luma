# SPDX-License-Identifier: Apache-2.0
"""Where Contacts' people come from, and the sharing choices it keeps itself.

The window talks to one `ContactsSource`. `EdsSource` is the real one: the
address book (eds_backend) for cards, Messages' stores for Together (read
only), and `SharingStore` for what you share with each person. The fixture
source (contacts_fixture, LUMA_CONTACTS_FIXTURE) serves the same calls from a
JSON file in memory, for conformance runs and tests.

`SharingStore` is contained: a small JSON file keyed by contact UID,
written atomically, nothing else reads or writes it. It is shaped to sync
later (a version, and each entry's own `updated` time for last-writer-wins).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .contacts_together import TogetherItem, together
from .eds_backend import (
    AddressBookName, ContactRecord, create_contact, default_address_book_name, delete_contact,
    load_contacts, update_contact,
)


@dataclass(frozen=True)
class Me:
    """Your own card ("My card"): who you are to the people you share with."""

    name: str
    handle: str = ""
    phone: str = ""
    email: str = ""
    organization: str = ""
    role: str = ""
    photo: bytes = b""
    link: str = ""
    hue: int | None = None


class SharingStore:
    """Per person, which contact details are shared through Luma Contacts."""

    VERSION = 1

    def __init__(self, path: Path | None) -> None:
        self.path = path                     # None keeps it in memory (fixtures, tests)
        self._entries: dict[str, dict] = {}
        self._loaded = False

    @staticmethod
    def default_path() -> Path:
        data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        return data_home / "luma/contacts/sharing.json"

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self.path is None or not self.path.is_file():
            return
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if document.get("version") == self.VERSION and isinstance(document.get("contacts"), dict):
                self._entries = {str(k): dict(v) for k, v in document["contacts"].items() if isinstance(v, dict)}
        except (OSError, ValueError, TypeError, AttributeError):
            self._entries = {}

    def share_mobile(self, uid: str) -> bool:
        self._load()
        return bool(self._entries.get(uid, {}).get("share_mobile", False))

    def set_share_mobile(self, uid: str, shared: bool) -> None:
        self.set_shared(uid, "mobile", shared)

    def share_email(self, uid: str) -> bool:
        self._load()
        return bool(self._entries.get(uid, {}).get("share_email", False))

    def set_share_email(self, uid: str, shared: bool) -> None:
        self.set_shared(uid, "email", shared)

    def set_shared(self, uid: str, field: str, shared: bool) -> None:
        """Remember the choice; written before this returns (a temporary file, then a rename)."""
        if field not in ("mobile", "email"):
            raise ValueError(f"unknown contact detail: {field}")
        self._load()
        entries = {**self._entries, uid: {**self._entries.get(uid, {}), f"share_{field}": bool(shared),
                                         "updated": int(time.time())}}
        if self.path is None:
            self._entries = entries
            return
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        document = {"version": self.VERSION, "contacts": entries}
        handle, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".sharing-", suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(document, stream, indent=1, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            self._entries = entries
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise


#: The writes v71 asks for that the address book path does not make yet (favourite, lists, block,
#: link a duplicate, your own card). A source names the ones it makes in `writes`; the window offers
#: only those, so nothing on screen claims to have done what it did not do.
NEW_WRITES = frozenset({"favourite", "lists", "block", "link-duplicate", "edit-me"})


class ContactsSource:
    """What the window needs from wherever people live. Slow calls run off the main loop."""

    lists: dict[str, str] = {}               # list key (a vCard category) → how it reads
    writes: frozenset[str] = frozenset()     # which of NEW_WRITES this source makes
    #: A rehearsal (the fixture) says what v71's hand-offs Luma has no path for yet would do (a video call,
    #: Nearby) in a toast; a real source offers a video call disabled and says Nearby is not there yet.
    rehearsal = False

    def __init__(self, sharing: SharingStore) -> None:
        self.sharing = sharing

    def can(self, write: str) -> bool:
        """Whether this source makes one of v71's new writes (NEW_WRITES)."""
        if write not in NEW_WRITES:
            raise ValueError(f"not one of v71's new writes: {write!r}")
        return write in self.writes

    def share_people(self, records: list[ContactRecord]) -> list[ContactRecord]:
        """Who Share offers to send a card to (v71 "Send ___'s card to"): your favourites, A to Z."""
        return sorted((record for record in records if record.favourite),
                      key=lambda record: (record.name or "").casefold())

    def sheet_people(self, records: list[ContactRecord]) -> list[ContactRecord]:
        """Who the computer's share sheet offers (v71 lShare's row of faces, which scrolls)."""
        return self.share_people(records)

    def set_favourite(self, uid: str, favourite: bool) -> None:
        raise NotImplementedError

    def set_lists(self, uid: str, lists: tuple[str, ...]) -> None:
        raise NotImplementedError

    def block(self, uid: str) -> None:
        raise NotImplementedError

    def link_duplicate(self, uid: str) -> None:
        raise NotImplementedError

    def update_me(self, changes: dict[str, str]) -> None:
        raise NotImplementedError

    def load(self) -> tuple[list[ContactRecord], AddressBookName]:
        raise NotImplementedError

    def together(self, record: ContactRecord) -> tuple[TogetherItem, ...]:
        return ()

    def update(self, uid: str, changes: dict[str, str]) -> str:
        raise NotImplementedError

    def create(self, fields: dict[str, str]) -> str:
        raise NotImplementedError

    def delete(self, uid: str) -> None:
        raise NotImplementedError

    def me(self) -> Me | None:
        return None

    def light(self, record: ContactRecord) -> tuple[bytes, tuple[float, float]]:
        """The picture the card's lit header is made from, and where to centre it."""
        return record.photo, (0.5, 0.5)


class EdsSource(ContactsSource):
    """The real address book, Messages (read only) and the sharing store."""

    writes = frozenset({'favourite'})

    def __init__(self) -> None:
        super().__init__(SharingStore(SharingStore.default_path()))

    def load(self) -> tuple[list[ContactRecord], AddressBookName]:
        return list(load_contacts()), default_address_book_name()

    def together(self, record: ContactRecord) -> tuple[TogetherItem, ...]:
        return together(record.name, record.phone)

    def update(self, uid: str, changes: dict[str, str]) -> str:
        return update_contact(uid, changes)

    def create(self, fields: dict[str, str]) -> str:
        return create_contact(fields)

    def delete(self, uid: str) -> None:
        delete_contact(uid)

    def set_favourite(self, uid: str, favourite: bool) -> None:
        from .eds_backend import set_contact_favourite
        set_contact_favourite(uid, favourite)


def source_from_environment() -> ContactsSource:
    """The fixture when LUMA_CONTACTS_FIXTURE names one, otherwise the real address book."""
    fixture = os.environ.get("LUMA_CONTACTS_FIXTURE")
    if fixture:
        from .contacts_fixture import FixtureSource
        return FixtureSource(Path(fixture))
    return EdsSource()
