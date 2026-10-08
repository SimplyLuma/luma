# SPDX-License-Identifier: Apache-2.0

"""Luma Connect calendars, over CalDAV.

Calendar sync is not a Luma protocol. The source of truth serves CalDAV
(RFC 4791), and the device's own calendar service (Evolution Data Server) is
an ordinary CalDAV client of it: it keeps an offline copy, syncs with
sync-tokens and ETags, and sends every change the Calendar app makes. Phones,
Thunderbird, iOS and DAVx5 connect the same way with an app password.

What this module adds is only the setup a person should not have to do:

1. ask the source of truth for this device's calendar password,
2. make sure the account has a "Luma" calendar,
3. add it to the device's calendars and make it the one new events go to,
4. once, move the events kept only on this device into it — after writing a
   backup, and only once the server confirms it holds every one of them,
5. when the source of truth says the account changed, ask the calendar
   service to fetch now rather than at its next refresh.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree

COLLECTION = "luma"
LOCAL_CALENDAR = "system-calendar"
PROPFIND_DEPTH1 = (b'<?xml version="1.0" encoding="utf-8"?>'
                   b'<d:propfind xmlns:d="DAV:"><d:prop><d:getetag/></d:prop></d:propfind>')
MKCALENDAR = ('<?xml version="1.0" encoding="utf-8"?>'
              '<c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" '
              'xmlns:i="http://apple.com/ns/ical/"><d:set><d:prop>'
              '<d:displayname>Luma</d:displayname><i:calendar-color>#527FAE</i:calendar-color>'
              '<c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set>'
              '</d:prop></d:set></c:mkcalendar>').encode()
CALENDAR_QUERY = (b'<?xml version="1.0" encoding="utf-8"?>'
                  b'<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
                  b'<d:prop><d:getetag/><c:calendar-data/></d:prop>'
                  b'<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"/>'
                  b'</c:comp-filter></c:filter></c:calendar-query>')
# How long one pass waits for the server to list moved events.
CONFIRM_SECONDS = 45


class CalendarSyncError(RuntimeError):
    pass


def _dav(method: str, url: str, username: str, password: str, body: bytes | None = None,
         headers: dict[str, str] | None = None, timeout: float = 20.0) -> tuple[int, bytes]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    request = Request(url, data=body, method=method, headers={
        "Authorization": f"Basic {token}", "User-Agent": "ProjectLuma-Connect/1",
        "Content-Type": "application/xml; charset=utf-8", **(headers or {})})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read() if error.fp else b""
    except (URLError, TimeoutError, OSError) as error:
        raise CalendarSyncError(f"The calendar server could not be reached ({getattr(error, 'reason', error)}).") from None


def ensure_collection(home: str, username: str, password: str) -> str:
    url = home.rstrip("/") + f"/{COLLECTION}/"
    status, _body = _dav("PROPFIND", url, username, password, PROPFIND_DEPTH1, {"Depth": "0"})
    if status == 207:
        return url
    if status == 404:
        status, _body = _dav("MKCALENDAR", url, username, password, MKCALENDAR)
        if status in (201, 405):
            return url
    if status == 401:
        raise CalendarSyncError("The calendar server did not accept this device's password.")
    raise CalendarSyncError(f"The Luma calendar could not be prepared (HTTP {status}).")


def server_event_uids(url: str, username: str, password: str) -> set[str]:
    """The UID of every event the server holds in the collection (RFC 4791 calendar-query)."""
    status, body = _dav("REPORT", url, username, password, CALENDAR_QUERY, {"Depth": "1"})
    if status != 207:
        raise CalendarSyncError(f"The calendar server could not list the Luma calendar (HTTP {status}).")
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        raise CalendarSyncError("The calendar server sent an unreadable list of the Luma calendar.") from None
    held: set[str] = set()
    for data in root.iter("{urn:ietf:params:xml:ns:caldav}calendar-data"):
        held.update(_event_uids(data.text or ""))
    return held


def _backup(directory: Path, text: str) -> Path:
    """Write Personal's events aside before moving them; reuse the last copy if unchanged."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    previous = sorted(directory.glob("personal-*.ics"))
    if previous and previous[-1].read_bytes() == text.encode("utf-8"):
        return previous[-1]
    backup = directory / f"personal-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.ics"
    backup.write_bytes(text.encode("utf-8"))
    os.chmod(backup, 0o600)
    return backup


def _source_uid(device_id: str) -> str:
    return "luma-connect-" + re.sub(r"[^a-z0-9]", "", device_id.lower())[:12]


def _event_uids(text: str) -> list[str]:
    uids, inside = [], False
    for line in re.sub(r"\r?\n[ \t]", "", text).splitlines():
        if line == "BEGIN:VEVENT":
            inside = True
        elif line == "END:VEVENT":
            inside = False
        elif inside and line.startswith("UID:"):
            uids.append(line[4:].strip())
    return list(dict.fromkeys(uids))


def _source_password(uid: str) -> str | None:
    from .calendar_backend import _registry
    try:
        stored = _registry().ref_source(uid)
        found = stored.lookup_password_sync(None) if stored is not None else None
    except Exception:
        return None
    return (found[1] if isinstance(found, tuple) else found) or None


def sync_calendar(*, address: str, token: str, http, state: dict, scope: str,
                  environment: dict[str, str] | None, remote_changed: bool, data_directory: Path) -> str:
    from .calendar_backend import (
        CalendarUnavailable, create_calendar, delete_event, export_ics, import_ics,
        list_sources, set_default_calendar,
    )
    key = f"{scope}|calendar"
    record = dict(state.get(key, {}))
    try:
        sources = {source.uid: source for source in list_sources()}
    except CalendarUnavailable as error:
        return f"calendar: calendar service unavailable ({error}), skipped"
    device_id = scope.rsplit("|", 1)[-1]
    uid = record.get("source_uid") or _source_uid(device_id)
    if uid not in sources:
        from .connect_dav import credential as dav_credential
        login = dav_credential(address=address, token=token, device_id=device_id, http=http,
                               legacy_password=lambda: _source_password(uid))
        username, password = login["username"], login["password"]
        url = ensure_collection(login["home"], username, password)
        create_calendar("Luma", kind="luma", url=url, user=username, password=password, uid=uid,
                        tone="blue", refresh_minutes=15)
        record.update({"source_uid": uid, "url": url, "username": username})
        default = next((source for source in sources.values() if source.is_default), None)
        if default is None or default.uid == LOCAL_CALENDAR:
            set_default_calendar(uid)
        state[key] = record
        sources = {source.uid: source for source in list_sources()}
        status = "calendar: Luma calendar added"
    else:
        status = "calendar: connected"

    # Move what was only on this device, once, and never without a backup and
    # the server's confirmation. Confirmation is by UID: the server must list
    # every moved event. Counting resources before and after cannot work once
    # an earlier pass got the events there after it had stopped waiting: the
    # count never grows again and the move would wait forever.
    if not record.get("migrated") and LOCAL_CALENDAR in sources:
        text = export_ics(LOCAL_CALENDAR)
        uids = _event_uids(text)
        if uids:
            backup = _backup(data_directory / "calendar-backups", text)
            username = record.get("username") or ""
            from .connect_dav import stored_password
            password = stored_password(device_id, address) or _source_password(uid) or ""
            # Sent again on every pass until confirmed: an event already in the
            # Luma calendar is updated, not duplicated, so it holds Personal's
            # current version before Personal's copy is removed.
            import_ics(uid, text)
            deadline = time.monotonic() + CONFIRM_SECONDS
            while True:
                held = server_event_uids(record["url"], username, password)
                missing = [event_uid for event_uid in uids if event_uid not in held]
                if not missing or time.monotonic() > deadline:
                    break
                time.sleep(2)
            if missing:
                # Not a failure: nothing was lost, and the next pass tries
                # again. Failing here failed the whole sync on every pass.
                status += (f", {len(uids) - len(missing)} of {len(uids)} event(s) from Personal "
                           f"confirmed by the Luma calendar, waiting for the rest; they stay in "
                           f"Personal for now (backup: {backup})")
                state[key] = record
                return status
            for event_uid in uids:
                delete_event(LOCAL_CALENDAR, event_uid)
            status += f", moved {len(uids)} event(s) from Personal (backup kept)"
        record["migrated"] = True
        state[key] = record

    if remote_changed and uid in sources:
        try:
            from .calendar_backend import _client
            client, _source, _cal, _ical = _client(uid, timeout=10)
            client.refresh_sync(None)
            status += ", refreshed"
        except Exception as error:  # a refresh is an optimisation; the service syncs anyway
            status += f", refresh deferred ({getattr(error, 'message', error)})"
    state[key] = record
    return status
