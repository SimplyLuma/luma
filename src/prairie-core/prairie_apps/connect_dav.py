# SPDX-License-Identifier: Apache-2.0

"""The one CalDAV/CardDAV login a device has with its source of truth.

A device gets one app password (POST /api/hub/connect/dav-credential). Asking
again rotates it, which would silently break every calendar or address book
already using the old one, so it is asked for once, kept in the keyring, and
shared by every standard-protocol service on the device.
"""

from __future__ import annotations

import base64
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SCHEMA_NAME = "org.projectluma.Connect.DavPassword"


class DavError(RuntimeError):
    pass


def _secret():
    import gi
    gi.require_version("Secret", "1")
    from gi.repository import Secret
    schema = Secret.Schema.new(SCHEMA_NAME, Secret.SchemaFlags.NONE, {
        "device": Secret.SchemaAttributeType.STRING, "hub": Secret.SchemaAttributeType.STRING})
    return Secret, schema


def stored_password(device_id: str, hub: str) -> str | None:
    Secret, schema = _secret()
    return Secret.password_lookup_sync(schema, {"device": device_id, "hub": hub}, None)


def store_password(device_id: str, hub: str, password: str) -> None:
    Secret, schema = _secret()
    if not Secret.password_store_sync(schema, {"device": device_id, "hub": hub}, Secret.COLLECTION_DEFAULT,
                                      "Luma Connect calendar and contacts", password, None):
        raise DavError("The keyring would not save the Luma calendar password. Unlock it and sync again.")


def clear_password(device_id: str, hub: str) -> None:
    Secret, schema = _secret()
    Secret.password_clear_sync(schema, {"device": device_id, "hub": hub}, None)


def credential(*, address: str, token: str, device_id: str, http, legacy_password=None) -> dict:
    """{username, password, home} for this device, minting a password only if none is kept.

    `legacy_password` returns a password an older version stored elsewhere
    (the calendar source's own keyring entry), so upgrading does not rotate it.
    """
    username = http.get_json(f"{address}/api/hub/sync/account", token=token).get("dav_username")
    password = stored_password(device_id, address)
    if not password and legacy_password is not None:
        password = legacy_password() or None
        if password:
            store_password(device_id, address, password)
    if not password or not username:
        issued = http.post_json(f"{address}/api/hub/connect/dav-credential", {}, token=token)
        password, username = issued["password"], issued["username"]
        store_password(device_id, address, password)
    return {"username": username, "password": password, "home": f"{address}/dav/{username}/"}


def request(method: str, url: str, username: str, password: str, body: bytes | None = None,
            headers: dict[str, str] | None = None, timeout: float = 20.0) -> tuple[int, bytes]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    outgoing = Request(url, data=body, method=method, headers={
        "Authorization": f"Basic {token}", "User-Agent": "ProjectLuma-Connect/1",
        "Content-Type": "application/xml; charset=utf-8", **(headers or {})})
    try:
        with urlopen(outgoing, timeout=timeout) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read() if error.fp else b""
    except (URLError, TimeoutError, OSError) as error:
        raise DavError(f"The calendar and contacts server could not be reached ({getattr(error, 'reason', error)}).") from None


def ensure_collection(url: str, username: str, password: str, body: bytes, method: str) -> str:
    probe = (b'<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:"><d:prop>'
             b'<d:resourcetype/></d:prop></d:propfind>')
    status, _ = request("PROPFIND", url, username, password, probe, {"Depth": "0"})
    if status == 207:
        return url
    if status == 404:
        status, _ = request(method, url, username, password, body)
        if status in (201, 405):
            return url
    if status == 401:
        raise DavError("The calendar and contacts server did not accept this device's password.")
    raise DavError(f"The Luma collection could not be prepared (HTTP {status}).")


def count_items(url: str, username: str, password: str, suffix: bytes) -> int:
    import re
    probe = (b'<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:"><d:prop>'
             b'<d:getetag/></d:prop></d:propfind>')
    status, body = request("PROPFIND", url, username, password, probe, {"Depth": "1"})
    if status != 207:
        raise DavError(f"The server could not list the Luma collection (HTTP {status}).")
    return len(re.findall(rb"<[A-Za-z]*:?href>[^<]+" + re.escape(suffix) + rb"</[A-Za-z]*:?href>", body))
