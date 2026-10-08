# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
import re
import uuid


@dataclass(frozen=True)
class EdsSource:
    name: str


@dataclass(frozen=True)
class EdsInventory:
    available: bool
    reason: str
    address_books: tuple[EdsSource, ...] = ()
    calendars: tuple[EdsSource, ...] = ()
    task_lists: tuple[EdsSource, ...] = ()


@dataclass(frozen=True)
class ContactRecord:
    uid: str
    name: str
    phone: str = ""
    email: str = ""
    organization: str = ""
    role: str = ""
    # vCard CATEGORIES, as the lists a person files contacts under.
    categories: tuple[str, ...] = ()
    # Read only, for showing the card: which address book it is in (as a
    # person names it), a birthday ("March 14"), a short address, a photo.
    book: str = ""
    birthday: str = ""
    address: str = ""
    photo: bytes = b""
    # The private note (vCard NOTE): only the owner of the address book sees it.
    note: str = ""
    # Filed under the favourites category ("Favorites", "Favourites", "starred").
    favourite: bool = False
    # Their Luma @username and whether they are on Luma; unknown (None) until
    # the Luma directory can say (ADR-051).
    handle: str = ""
    on_luma: bool | None = None
    # The hue their face and light take when there is no photo (0–360), or
    # None to derive one from the name.
    hue: int | None = None
    luma_account: str = ""


#: Categories that mean "favourite" rather than a list, as other address books write them.
FAVOURITE_CATEGORIES = frozenset({"favorites", "favourites", "starred"})


@dataclass(frozen=True)
class CalendarRecord:
    uid: str
    title: str
    start: datetime | None = None
    due: datetime | None = None
    completed: bool = False
    description: str = ""


def _modules():
    import gi

    gi.require_version("EDataServer", "1.2")
    gi.require_version("EBook", "1.2")
    gi.require_version("EBookContacts", "1.2")
    from gi.repository import EBook, EBookContacts, EDataServer

    return EDataServer, EBook, EBookContacts


def inspect_eds_inventory() -> EdsInventory:
    if os.environ.get("PRAIRIE_EDS_MODE") == "disabled":
        return EdsInventory(False, "Evolution Data Server probing is disabled for this test.")
    try:
        data_server, _book, _contacts = _modules()
        registry = data_server.SourceRegistry.new_sync(None)

        def sources(extension: str) -> tuple[EdsSource, ...]:
            return tuple(
                EdsSource(source.get_display_name() or "Untitled")
                for source in registry.list_enabled(extension)
            )

        return EdsInventory(
            True,
            "Evolution Data Server is ready.",
            sources(data_server.SOURCE_EXTENSION_ADDRESS_BOOK),
            sources(data_server.SOURCE_EXTENSION_CALENDAR),
            sources(data_server.SOURCE_EXTENSION_TASK_LIST),
        )
    except (ImportError, ValueError, RuntimeError, OSError) as error:
        return EdsInventory(False, f"Evolution Data Server is unavailable: {error}")


def load_contacts(search: str = "") -> tuple[ContactRecord, ...]:
    if os.environ.get("PRAIRIE_EDS_MODE") == "disabled":
        return ()
    try:
        data_server, book, contacts = _modules()
        registry = data_server.SourceRegistry.new_sync(None)
        query = contacts.BookQuery.any_field_contains(search).to_string()
        result: list[ContactRecord] = []
        for source in registry.list_enabled(data_server.SOURCE_EXTENSION_ADDRESS_BOOK):
            book_label = _source_name(data_server, registry, source).label
            client = book.BookClient.connect_sync(source, 5, None)
            _ok, records = client.get_contacts_sync(query, None)
            for record in records:
                name = _attribute_value(record, "FN") or "Unnamed contact"
                phones = _attribute_values(record, "TEL")
                emails = _attribute_values(record, "EMAIL")
                organization = _attribute_value(record, "ORG")
                role = _attribute_value(record, "TITLE") or _attribute_value(record, "ROLE")
                phone = phones[0] if phones else ""
                email = emails[0] if emails else ""
                result.append(
                    ContactRecord(
                        _attribute_value(record, "UID"),
                        name,
                        phone,
                        email,
                        organization,
                        role,
                        _categories(record),
                        book_label,
                        _birthday(_attribute_value(record, "BDAY")),
                        _short_address(record),
                        _photo(record),
                        _attribute_value(record, "NOTE"),
                        any(c.casefold() in FAVOURITE_CATEGORIES for c in _categories(record)),
                        handle=_attribute_value(record, "X-LUMA-USERNAME"),
                        luma_account=_attribute_value(record, "X-LUMA-ACCOUNT"),
                        on_luma=True if _attribute_value(record, "X-LUMA-ACCOUNT") else None,
                    )
                )
        return tuple(sorted(result, key=lambda item: item.name.casefold()))
    except (ImportError, ValueError, RuntimeError, OSError):
        return ()


@dataclass(frozen=True)
class AddressBookName:
    """Where new contacts go, as a person reads it.

    ``label`` stands alone ("On this computer"); ``place`` finishes a sentence
    ("removed from this computer").
    """
    label: str
    place: str


LOCAL_ADDRESS_BOOK = AddressBookName("On this computer", "this computer")


def address_book_name(uid: str, backend: str, parent_uid: str = "",
                      parent_name: str = "", display_name: str = "") -> AddressBookName:
    """Name an address book by the account it belongs to, never by its backend."""
    backend = (backend or "").casefold()
    if backend == "local" or uid == "system-address-book":
        return LOCAL_ADDRESS_BOOK
    if uid.startswith("luma-connect-") or parent_uid == "luma-connect-stub":
        return AddressBookName("Luma account", "your Luma account")
    # An online account's books sit under its collection, whose name is the
    # account itself (an address, usually); a lone book has only its own name.
    account = (parent_name or "").strip() if parent_uid and not parent_uid.endswith("-stub") else ""
    name = account or (display_name or "").strip()
    if not name:
        return LOCAL_ADDRESS_BOOK
    return AddressBookName(name, name)


def _source_name(data_server, registry, source) -> AddressBookName:
    backend = ""
    try:
        backend = source.get_extension(data_server.SOURCE_EXTENSION_ADDRESS_BOOK).get_backend_name() or ""
    except (AttributeError, TypeError):
        pass
    parent_uid = source.get_parent() or ""
    parent = registry.ref_source(parent_uid) if parent_uid else None
    return address_book_name(source.get_uid() or "", backend, parent_uid,
                             parent.get_display_name() if parent is not None else "",
                             source.get_display_name() or "")


def default_address_book_name() -> AddressBookName:
    """The account new contacts are saved to; this computer when unknown."""
    if os.environ.get("PRAIRIE_EDS_MODE") == "disabled":
        return LOCAL_ADDRESS_BOOK
    try:
        data_server, _book, _contacts = _modules()
        registry = data_server.SourceRegistry.new_sync(None)
        source = registry.ref_default_address_book()
        if source is None:
            return LOCAL_ADDRESS_BOOK
        return _source_name(data_server, registry, source)
    except (ImportError, ValueError, RuntimeError, OSError, AttributeError):
        return LOCAL_ADDRESS_BOOK


def _attribute_values(contact, name: str) -> tuple[str, ...]:
    values: list[str] = []
    for attribute in contact.get_attributes_by_name(name) or ():
        values.extend(str(value) for value in attribute.get_values() if value)
    return tuple(values)


def _attribute_value(contact, name: str) -> str:
    values = _attribute_values(contact, name)
    return values[0] if values else ""


def _categories(contact) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for value in _attribute_values(contact, "CATEGORIES"):
        for part in value.split(","):
            if part.strip():
                seen.setdefault(part.strip(), None)
    return tuple(seen)


_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")


def _birthday(value: str) -> str:
    """"1990-03-14" as "March 14, 1990", "--0314" as "March 14"; anything else as written."""
    digits = "".join(character for character in value if character.isdigit())
    month_day = digits[-4:] if len(digits) in (4, 8) else ""
    if month_day and 1 <= int(month_day[:2]) <= 12 and 1 <= int(month_day[2:]) <= 31:
        day = f"{_MONTHS[int(month_day[:2]) - 1]} {int(month_day[2:])}"
        # 1604 is how phones write "no year" (Apple and Android both).
        year = digits[:4] if len(digits) == 8 and digits[:4] != "1604" else ""
        return f"{day}, {year}" if year else day
    return value.strip()


def _short_address(contact) -> str:
    """The first address as a line: street, town, region (ADR's 3rd–5th parts)."""
    for attribute in contact.get_attributes_by_name("ADR") or ():
        parts = [str(value).strip() for value in attribute.get_values() or ()]
        line = ", ".join(part for part in parts[2:5] if part)
        if line:
            return line
    return ""


def _photo(contact) -> bytes:
    """The card's photo: inline (base64) or a local file the address book keeps; never fetched."""
    import base64
    from urllib.parse import unquote, urlparse

    attributes = contact.get_attributes_by_name("PHOTO") or ()
    if not attributes:
        return b""
    attribute = attributes[0]
    try:
        value = "".join(str(v) for v in attribute.get_values() or ())
        params = {(p.get_name() or "").upper(): [str(v).upper() for v in p.get_values() or ()]
                  for p in attribute.get_params() or ()}
        if "URI" in params.get("VALUE", []) or value.startswith("file://"):
            location = urlparse(value)
            if location.scheme != "file":
                return b""
            path = unquote(location.path)
            if os.path.getsize(path) > _PHOTO_LIMIT:
                return b""
            with open(path, "rb") as picture:
                return picture.read()
        data = base64.b64decode(value, validate=False)
        return data if len(data) <= _PHOTO_LIMIT else b""
    except (AttributeError, TypeError, ValueError, OSError):
        return b""


_PHOTO_LIMIT = 8 * 1024 * 1024


def _escape_vcard(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _contact_vcard(name: str, phone: str, email: str, uid: str = "") -> str:
    lines = ["BEGIN:VCARD", "VERSION:3.0"]
    if uid:
        lines.append(f"UID:{_escape_vcard(uid)}")
    lines.extend((f"FN:{_escape_vcard(name)}", f"N:;{_escape_vcard(name)};;;"))
    if phone:
        lines.append(f"TEL;TYPE=CELL:{_escape_vcard(phone)}")
    if email:
        lines.append(f"EMAIL:{_escape_vcard(email)}")
    lines.extend(("END:VCARD", ""))
    return "\r\n".join(lines)


#: The fields Contacts edits, as they are shown: "name", "phone" (the first
#: number), "email" (the first address), "birthday" ("March 14"), "address"
#: (street, town, region), "work" ("Company · Title") and "note".
EDITABLE_FIELDS = ("name", "phone", "email", "birthday", "address", "work", "note", "handle", "luma_account")


def _first(contact, name: str):
    attributes = contact.get_attributes_by_name(name) or ()
    return attributes[0] if attributes else None


def _set_values(contact, contacts_module, name: str, values: list[str], attribute=None, *,
                params: tuple[tuple[str, str], ...] = ()) -> None:
    """Give `attribute` (or the card's first `name`) these values, in place; create it if missing."""
    attribute = attribute if attribute is not None else _first(contact, name)
    if attribute is None:
        attribute = contacts_module.VCardAttribute.new(None, name)
        for param_name, value in params:
            param = contacts_module.VCardAttributeParam.new(param_name)
            param.add_value(value)
            attribute.add_param(param)
        for value in values:
            attribute.add_value(value)
        contact.append_attribute(attribute)
        return
    attribute.remove_values()
    for value in values:
        attribute.add_value(value)


def _remove_first(contact, name: str) -> None:
    attribute = _first(contact, name)
    if attribute is not None:
        contact.remove_attribute(attribute)


def _parse_birthday(text: str) -> str:
    """"March 14", "14 March", "March 14, 1990" or "1990-03-14" as a vCard BDAY."""
    import re

    text = text.strip()
    iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if iso:
        year, month, day = (int(part) for part in iso.groups())
    else:
        words = re.findall(r"[A-Za-z]+|\d+", text)
        months = [index for index, month in enumerate(_MONTHS, 1)
                  for word in words if word.isalpha() and len(word) >= 3
                  and month.casefold().startswith(word.casefold())]
        numbers = [int(word) for word in words if word.isdigit()]
        if len(months) != 1 or not numbers or len(numbers) > 2:
            raise ValueError("Write the birthday like March 14 or March 14, 1990.")
        month = months[0]
        day = next((n for n in numbers if n <= 31), 0)
        year = next((n for n in numbers if n > 31), 0)
    if not (1 <= month <= 12 and 1 <= day <= 31) or (year and not 1800 <= year <= 2200):
        raise ValueError("Write the birthday like March 14 or March 14, 1990.")
    return f"{year:04d}-{month:02d}-{day:02d}" if year else f"--{month:02d}{day:02d}"


def _split_name(name: str) -> tuple[str, str]:
    """(family, given): the last word is the family name, as Evolution guesses it."""
    words = name.split()
    return (words[-1], " ".join(words[:-1])) if len(words) > 1 else ("", name)


def apply_contact_changes(contact, changes: dict[str, str], contacts_module) -> None:
    """Change only these fields of a card, in place, and leave every other property as it was.

    Read-modify-write: the card comes from the address book with everything
    it holds (photos, extra numbers, labels, the sync tag), and only the
    attributes behind the edited fields are touched. A cleared field removes
    just its own attribute.
    """
    unknown = set(changes) - set(EDITABLE_FIELDS)
    if unknown:
        raise ValueError(f"not an editable field: {sorted(unknown)}")
    values = {key: (value or "").strip() for key, value in changes.items()}
    if values.get("handle"):
        from .messages_luma import handle_from
        handle = handle_from(values["handle"])
        if handle is None:
            raise ValueError("Enter a valid Luma username, such as @alex.")
        values["handle"] = handle
    if values.get("luma_account") and not re.fullmatch(r"[A-Za-z0-9_-]{8,200}", values["luma_account"]):
        raise ValueError("Invalid Luma account identity.")
    if "handle" in values and "luma_account" not in values:
        # A username is mutable; changing it cannot retain an old identity.
        _remove_first(contact, "X-LUMA-ACCOUNT")
    if "luma_account" in values:
        if values["luma_account"]:
            _set_values(contact, contacts_module, "X-LUMA-ACCOUNT", [values["luma_account"]])
        else:
            _remove_first(contact, "X-LUMA-ACCOUNT")
    if "handle" in values:
        if values["handle"]:
            _set_values(contact, contacts_module, "X-LUMA-USERNAME", [values["handle"]])
        else:
            _remove_first(contact, "X-LUMA-USERNAME")
    if "name" in values:
        name = values["name"]
        if not name:
            raise ValueError("Enter a name for the contact.")
        _set_values(contact, contacts_module, "FN", [name])
        family, given = _split_name(name)
        existing = _first(contact, "N")
        old = [str(v) for v in existing.get_values()] if existing is not None else []
        old += [""] * (5 - len(old))
        _set_values(contact, contacts_module, "N", [family, given, *old[2:5]], existing)
        if _first(contact, "X-EVOLUTION-FILE-AS") is not None:
            _set_values(contact, contacts_module, "X-EVOLUTION-FILE-AS",
                        [f"{family}, {given}" if family else given])
    for key, vcard_name in (("phone", "TEL"), ("email", "EMAIL")):
        if key not in values:
            continue
        if not values[key]:
            _remove_first(contact, vcard_name)
            continue
        attribute = _first(contact, vcard_name)
        if attribute is not None and vcard_name == "TEL":
            # The address book's normalised copy of the old number; it works
            # out the new one itself.
            attribute.remove_param("X-EVOLUTION-E164")
        _set_values(contact, contacts_module, vcard_name, [values[key]], attribute,
                    params=(("TYPE", "CELL"),) if vcard_name == "TEL" else ())
    if "birthday" in values:
        if values["birthday"]:
            _set_values(contact, contacts_module, "BDAY", [_parse_birthday(values["birthday"])])
        else:
            _remove_first(contact, "BDAY")
    if "address" in values:
        _change_address(contact, contacts_module, values["address"])
    if "note" in values:
        if values["note"]:
            _set_values(contact, contacts_module, "NOTE", [values["note"]])
        else:
            _remove_first(contact, "NOTE")
    if "work" in values:
        organization, _, role = values["work"].partition(" · ")
        role, organization = role.strip(), organization.strip()
        org = _first(contact, "ORG")
        if organization:
            units = [str(v) for v in org.get_values()][1:] if org is not None else []
            _set_values(contact, contacts_module, "ORG", [organization, *units], org)
        elif org is not None:
            contact.remove_attribute(org)
        title = _first(contact, "TITLE") or _first(contact, "ROLE")
        if role:
            _set_values(contact, contacts_module, "TITLE", [role], title)
        elif title is not None:
            contact.remove_attribute(title)


def _change_address(contact, contacts_module, text: str) -> None:
    """Street, town and region from one line; the post-office box, code and country stay."""
    attribute = _first(contact, "ADR")
    parts = [str(v) for v in attribute.get_values()] if attribute is not None else []
    parts += [""] * (7 - len(parts))
    typed = [part.strip() for part in text.split(",")] if text.strip() else []
    shown = [index for index in (2, 3, 4) if parts[index].strip()]
    # Put each typed part back where it was shown; otherwise the usual order:
    # one part is the street, two are town and region, three are all three.
    slots = shown if len(typed) == len(shown) else {1: [2], 2: [3, 4], 3: [2, 3, 4]}.get(len(typed), [2, 3, 4])
    for index in (2, 3, 4):
        parts[index] = ""
    for index, value in zip(slots, typed[:len(slots) - 1] + [", ".join(typed[len(slots) - 1:])] if typed else []):
        parts[index] = value
    if not any(part.strip() for part in parts):
        if attribute is not None:
            contact.remove_attribute(attribute)
        return
    _set_values(contact, contacts_module, "ADR", parts, attribute)


def _book_holding(data_server, book, registry, uid: str):
    """The client and card for `uid`, from whichever address book holds it."""
    from gi.repository import GLib

    for source in registry.list_enabled(data_server.SOURCE_EXTENSION_ADDRESS_BOOK):
        client = book.BookClient.connect_sync(source, 5, None)
        try:
            ok, contact = client.get_contact_sync(uid, None)
        except GLib.Error:
            continue
        if ok and contact is not None:
            return client, contact
    raise RuntimeError("That contact is no longer in your address book.")


def update_contact(uid: str, changes: dict[str, str]) -> str:
    """Change these fields of an existing card and nothing else. Returns its uid."""
    if not uid:
        raise ValueError("a contact identifier is required")
    if not changes:
        return uid
    data_server, book, contacts = _modules()
    registry = data_server.SourceRegistry.new_sync(None)
    client, contact = _book_holding(data_server, book, registry, uid)
    apply_contact_changes(contact, changes, contacts)
    if not client.modify_contact_sync(contact, contacts.BookOperationFlags.CONFLICT_FAIL, None):
        raise RuntimeError("The address book did not save the changes.")
    return uid


def set_contact_favourite(uid: str, favourite: bool) -> None:
    """Change only the favorite category on the current provider card."""
    if not uid or not isinstance(favourite, bool):
        raise ValueError('A contact and favorite choice are required.')
    data_server, book, contacts = _modules()
    registry = data_server.SourceRegistry.new_sync(None)
    client, contact = _book_holding(data_server, book, registry, uid)
    categories = [value for value in _categories(contact) if value.casefold() not in FAVOURITE_CATEGORIES]
    if favourite:
        categories.append('Favorites')
    for attribute in list(contact.get_attributes_by_name('CATEGORIES') or ()):
        contact.remove_attribute(attribute)
    if categories:
        _set_values(contact, contacts, 'CATEGORIES', categories)
    if not client.modify_contact_sync(contact, contacts.BookOperationFlags.CONFLICT_FAIL, None):
        raise RuntimeError('The address book did not save the favorite choice.')


def create_contact(fields: dict[str, str]) -> str:
    """Add a new card with these fields to the address book new contacts go to."""
    data_server, book, contacts = _modules()
    contact = contacts.Contact.new_from_vcard("BEGIN:VCARD\r\nVERSION:3.0\r\nEND:VCARD\r\n")
    apply_contact_changes(contact, {"name": "", **fields}, contacts)
    registry = data_server.SourceRegistry.new_sync(None)
    client = book.BookClient.connect_sync(registry.ref_default_address_book(), 5, None)
    ok, created_uid = client.add_contact_sync(contact, contacts.BookOperationFlags.CONFLICT_FAIL, None)
    if not ok or not created_uid:
        raise RuntimeError("The address book did not save the contact.")
    return str(created_uid)


def save_contact(name: str, phone: str = "", email: str = "", uid: str = "") -> str:
    """Name, first number and first email; everything else on the card is kept (update_contact)."""
    fields = {"name": name, "phone": phone, "email": email}
    return update_contact(uid, fields) if uid else create_contact(fields)


def import_contacts_vcard(text: str) -> tuple[str, ...]:
    """Import complete cards through EDS, never overwrite an existing UID.

    EBookContacts parses every field and EBookClient owns the batch operation.
    All envelopes and names are validated before issuing the mutation.
    """
    from .contacts_import import split_vcards

    vcards = split_vcards(text)
    data_server, book, contacts = _modules()
    parsed = []
    for vcard in vcards:
        contact = contacts.Contact.new_from_vcard_with_uid(vcard, str(uuid.uuid4()))
        if contact is None or not _attribute_value(contact, "FN").strip():
            raise ValueError("Every imported contact needs a name.")
        parsed.append(contact)
    registry = data_server.SourceRegistry.new_sync(None)
    source = registry.ref_default_address_book()
    if source is None:
        raise RuntimeError("No writable address book is available.")
    client = book.BookClient.connect_sync(source, 5, None)
    ok, uids = client.add_contacts_sync(parsed, contacts.BookOperationFlags.CONFLICT_FAIL, None)
    if not ok or not uids or len(uids) != len(parsed):
        raise RuntimeError("The address book did not confirm all imported contacts. Check it before importing again.")
    return tuple(str(uid) for uid in uids)


def delete_contact(uid: str) -> None:
    """Remove the card from the address book that holds it."""
    if not uid:
        raise ValueError("a contact identifier is required")
    data_server, book, contacts = _modules()
    registry = data_server.SourceRegistry.new_sync(None)
    client, contact = _book_holding(data_server, book, registry, uid)
    if not client.remove_contact_sync(contact, contacts.BookOperationFlags.CONFLICT_FAIL, None):
        raise RuntimeError("The address book did not delete the contact.")


def _calendar_modules():
    import gi

    gi.require_version("EDataServer", "1.2")
    gi.require_version("ECal", "2.0")
    gi.require_version("ICalGLib", "3.0")
    from gi.repository import ECal, EDataServer, ICalGLib

    return EDataServer, ECal, ICalGLib


def _calendar_client(source_type: str):
    data_server, calendar, _ical = _calendar_modules()
    registry = data_server.SourceRegistry.new_sync(None)
    if source_type == "tasks":
        source = registry.ref_default_task_list()
        kind = calendar.ClientSourceType.TASKS
    elif source_type == "events":
        source = registry.ref_default_calendar()
        kind = calendar.ClientSourceType.EVENTS
    else:
        raise ValueError("unknown EDS calendar source type")
    return calendar, calendar.Client.connect_sync(source, kind, 5, None)


def _component_record(component, source_type: str) -> CalendarRecord:
    summary = component.get_summary()
    title = summary.get_value() if summary else "Untitled"
    start = component.get_dtstart()
    due = component.get_due()
    descriptions = component.get_descriptions() or ()
    description = descriptions[0].get_value() if descriptions else ""

    def converted(value) -> datetime | None:
        if value is None or value.get_value() is None:
            return None
        timestamp = value.get_value().as_timet_with_zone(None)
        return datetime.fromtimestamp(timestamp, timezone.utc)

    completed = bool(component.get_percent_complete() == 100) if source_type == "tasks" else False
    return CalendarRecord(component.get_uid(), title, converted(start), converted(due), completed, description)


def list_calendar_records(source_type: str) -> tuple[CalendarRecord, ...]:
    if os.environ.get("PRAIRIE_EDS_MODE") == "disabled":
        return ()
    calendar, client = _calendar_client(source_type)
    ok, components = client.get_object_list_as_comps_sync("#t", None)
    if not ok:
        raise RuntimeError("Evolution Data Server did not return calendar records")
    records = tuple(_component_record(component, source_type) for component in components)
    return tuple(sorted(records, key=lambda record: (record.completed, record.start or record.due or datetime.max.replace(tzinfo=timezone.utc), record.title.casefold())))


def _ical_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def save_calendar_record(source_type: str, title: str, *, uid: str = "", when: datetime | None = None, completed: bool = False, description: str = "") -> str:
    title = title.strip()
    if not title:
        raise ValueError("a title is required")
    calendar, client = _calendar_client(source_type)
    identifier = uid or uuid.uuid4().hex
    now = _ical_timestamp(datetime.now(timezone.utc))
    kind = "VTODO" if source_type == "tasks" else "VEVENT"
    lines = [f"BEGIN:{kind}", f"UID:{identifier}", f"DTSTAMP:{now}", f"SUMMARY:{_escape_vcard(title)}"]
    if source_type == "events":
        start = when or datetime.now(timezone.utc)
        lines.extend((f"DTSTART:{_ical_timestamp(start)}", f"DTEND:{_ical_timestamp(start + timedelta(hours=1))}"))
    elif when:
        lines.append(f"DUE:{_ical_timestamp(when)}")
    if description.strip(): lines.append(f"DESCRIPTION:{_escape_vcard(description.strip())}")
    if source_type == "tasks" and completed:
        lines.extend(("STATUS:COMPLETED", "PERCENT-COMPLETE:100", f"COMPLETED:{now}"))
    lines.extend((f"END:{kind}", ""))
    component = calendar.Component.new_from_string("\r\n".join(lines))
    if component is None:
        raise RuntimeError("the calendar record could not be encoded")
    flags = calendar.OperationFlags.CONFLICT_FAIL
    if uid:
        if not client.modify_object_sync(component.get_icalcomponent(), calendar.ObjModType.THIS, flags, None):
            raise RuntimeError("Evolution Data Server did not update the record")
        return uid
    ok, created_uid = client.create_object_sync(component.get_icalcomponent(), flags, None)
    if not ok or not created_uid:
        raise RuntimeError("Evolution Data Server did not create the record")
    return str(created_uid)


def delete_calendar_record(source_type: str, uid: str) -> None:
    if not uid:
        raise ValueError("a record identifier is required")
    calendar, client = _calendar_client(source_type)
    if not client.remove_object_sync(uid, None, calendar.ObjModType.THIS, calendar.OperationFlags.CONFLICT_FAIL, None):
        raise RuntimeError("Evolution Data Server did not delete the record")
