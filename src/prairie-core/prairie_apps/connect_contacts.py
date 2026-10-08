# SPDX-License-Identifier: Apache-2.0

"""Luma Connect contacts, over CardDAV.

Like the calendar, contact sync is a standard, not a Luma protocol: the source
of truth serves CardDAV (RFC 6352) and the device's own address book service
(Evolution Data Server) is its client, so every app that reads contacts from
EDS — Contacts, Phone, Messages — sees the same cards on every device, and an
edit anywhere is an edit everywhere.

What this adds is the setup nobody should do by hand: make sure the account
has a "Luma" address book, add it to this device and make it the one new
contacts go to, and once move the contacts that were only on this device into
it — after a backup, and only once the server confirms it holds every one.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import time

from .connect_dav import DavError, count_items, credential, ensure_collection

COLLECTION = "contacts"
LOCAL_BOOK = "system-address-book"
MKCOL = ('<?xml version="1.0" encoding="utf-8"?>'
         '<d:mkcol xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav"><d:set><d:prop>'
         '<d:resourcetype><d:collection/><card:addressbook/></d:resourcetype>'
         '<d:displayname>Luma</d:displayname></d:prop></d:set></d:mkcol>').encode()


class ContactsSyncError(RuntimeError):
    pass


def _modules():
    import gi
    gi.require_version("EDataServer", "1.2")
    gi.require_version("EBook", "1.2")
    gi.require_version("EBookContacts", "1.2")
    gi.require_version("GLib", "2.0")
    from gi.repository import EBook, EBookContacts, EDataServer, GLib
    return EDataServer, EBook, EBookContacts, GLib


def source_uid(device_id: str) -> str:
    return "luma-connect-contacts-" + re.sub(r"[^a-z0-9]", "", device_id.lower())[:12]


def _client(registry, uid: str, book):
    source = registry.ref_source(uid)
    if source is None:
        raise ContactsSyncError(f"The address book {uid} is not available.")
    return book.BookClient.connect_sync(source, 30, None)


def _vcard(contacts, card) -> str:
    """A card as vCard text. EDS 3.60 writes the card in its own version;
    older bindings took the version as an argument."""
    try:
        return card.to_string()
    except TypeError:
        return card.to_string(getattr(contacts.VCardVersion, "30"))


def _all_cards(client) -> list:
    _ok, cards = client.get_contacts_sync("(contains \"x-evolution-any-field\" \"\")", None)
    return list(cards or [])


def add_address_book(registry, data_server, glib, *, uid: str, url: str, username: str, password: str) -> None:
    source = data_server.Source.new_with_uid(uid, None)
    source.set_display_name("Luma")
    source.set_parent("carddav-stub")
    source.get_extension(data_server.SOURCE_EXTENSION_ADDRESS_BOOK).set_backend_name("carddav")
    webdav = source.get_extension(data_server.SOURCE_EXTENSION_WEBDAV_BACKEND)
    parsed = glib.Uri.parse(url, glib.UriFlags.NONE)
    webdav.set_uri(parsed)
    auth = source.get_extension(data_server.SOURCE_EXTENSION_AUTHENTICATION)
    auth.set_host(parsed.get_host() or "")
    auth.set_port(parsed.get_port() if parsed.get_port() > 0 else 443)
    auth.set_user(username)
    auth.set_method("plain/password")
    refresh = source.get_extension(data_server.SOURCE_EXTENSION_REFRESH)
    refresh.set_enabled(True)
    refresh.set_interval_minutes(15)
    source.get_extension(data_server.SOURCE_EXTENSION_OFFLINE).set_stay_synchronized(True)
    registry.commit_source_sync(source, None)
    registry.ref_source(uid).store_password_sync(password, True, None)


def sync_contacts(*, address: str, token: str, http, state: dict, scope: str,
                  environment: dict[str, str] | None, remote_changed: bool, data_directory: Path) -> str:
    if os.environ.get("PRAIRIE_EDS_MODE") == "disabled":
        return "contacts: address book service disabled, skipped"
    try:
        data_server, book, contacts, glib = _modules()
        registry = data_server.SourceRegistry.new_sync(None)
    except Exception as error:  # no EDS on this device
        return f"contacts: address book service unavailable ({error}), skipped"
    key = f"{scope}|contacts-dav"
    record = dict(state.get(key, {}))
    device_id = scope.rsplit("|", 1)[-1]
    uid = record.get("source_uid") or source_uid(device_id)
    status = "contacts: connected"
    if registry.ref_source(uid) is None:
        try:
            login = credential(address=address, token=token, device_id=device_id, http=http,
                               legacy_password=lambda: _legacy_calendar_password(registry, scope))
            url = ensure_collection(login["home"] + f"{COLLECTION}/", login["username"], login["password"], MKCOL, "MKCOL")
        except DavError as error:
            raise ContactsSyncError(str(error)) from None
        add_address_book(registry, data_server, glib, uid=uid, url=url, username=login["username"], password=login["password"])
        record.update({"source_uid": uid, "url": url, "username": login["username"]})
        default = registry.ref_default_address_book()
        if default is None or default.get_uid() == LOCAL_BOOK:
            registry.set_default_address_book(registry.ref_source(uid))
        state[key] = record
        status = "contacts: Luma address book added"

    if not record.get("migrated") and registry.ref_source(LOCAL_BOOK) is not None:
        local = _client(registry, LOCAL_BOOK, book)
        cards = _all_cards(local)
        if cards:
            backups = data_directory / "contacts-backups"
            backups.mkdir(mode=0o700, parents=True, exist_ok=True)
            backup = backups / f"personal-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.vcf"
            backup.write_text("".join(_vcard(contacts, card) + "\r\n" for card in cards), encoding="utf-8")
            os.chmod(backup, 0o600)
            from .connect_dav import stored_password
            password = stored_password(device_id, address) or ""
            before = count_items(record["url"], record["username"], password, b".vcf")
            remote = _client(registry, uid, book)
            copies = [contacts.Contact.new_from_vcard_with_uid(_vcard(contacts, card), card.get_property("id")) for card in cards]
            ok, _uids = remote.add_contacts_sync(copies, contacts.BookOperationFlags.NONE, None)
            if not ok:
                raise ContactsSyncError(f"The Luma address book did not accept the contacts; they stay in Personal (backup: {backup}).")
            deadline = time.monotonic() + 60
            while count_items(record["url"], record["username"], password, b".vcf") < before + len(cards):
                if time.monotonic() > deadline:
                    raise ContactsSyncError("The server has not confirmed every moved contact yet; they stay in "
                                            f"Personal for now (backup: {backup}).")
                time.sleep(2)
            local.remove_contacts_sync([card.get_property("id") for card in cards], contacts.BookOperationFlags.NONE, None)
            status += f", moved {len(cards)} contact(s) from Personal (backup kept)"
        record["migrated"] = True
        state[key] = record

    if remote_changed:
        try:
            _client(registry, uid, book).refresh_sync(None)
            status += ", refreshed"
        except Exception as error:
            status += f", refresh deferred ({getattr(error, 'message', error)})"
    state[key] = record
    return status


def _legacy_calendar_password(registry, scope: str) -> str | None:
    """Before contacts, the calendar source held the device's only copy."""
    device = re.sub(r"[^a-z0-9]", "", scope.rsplit("|", 1)[-1].lower())[:12]
    source = registry.ref_source(f"luma-connect-{device}")
    if source is None:
        return None
    try:
        found = source.lookup_password_sync(None)
    except Exception:
        return None
    return (found[1] if isinstance(found, tuple) else found) or None


def disconnect_contacts(state: dict, scope: str) -> str:
    """Copy Luma contacts into Personal on this device, then remove the address book here."""
    try:
        data_server, book, contacts, _glib = _modules()
        registry = data_server.SourceRegistry.new_sync(None)
    except Exception:
        return "address book service unavailable; nothing changed on this device"
    uid = state.get(f"{scope}|contacts-dav", {}).get("source_uid")
    if not uid or registry.ref_source(uid) is None:
        return "no Luma address book on this device"
    cards = _all_cards(_client(registry, uid, book))
    copied = 0
    if cards and registry.ref_source(LOCAL_BOOK) is not None:
        local = _client(registry, LOCAL_BOOK, book)
        copies = [contacts.Contact.new_from_vcard_with_uid(_vcard(contacts, card), card.get_property("id")) for card in cards]
        ok, _uids = local.add_contacts_sync(copies, contacts.BookOperationFlags.NONE, None)
        if not ok:
            return "Personal did not accept the copies; the Luma address book was kept"
        copied = len(copies)
        registry.set_default_address_book(registry.ref_source(LOCAL_BOOK))
    registry.ref_source(uid).remove_sync(None)
    return f"copied {copied} contact(s) into Personal and disconnected the Luma address book"
