# SPDX-License-Identifier: Apache-2.0

"""Calendar value objects, shared by EDS and the memory-only fixture. No GTK."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class CalendarAttendee:
    name: str
    email: str
    answer: str = "NEEDS-ACTION"


@dataclass(frozen=True)
class CalendarSource:
    uid: str
    name: str
    tone: str
    colour: str
    writable: bool = True
    kind: str = "local"
    removable: bool = False
    is_default: bool = False
    account: str = ""


@dataclass(frozen=True)
class Event:
    uid: str
    source_uid: str
    summary: str
    start: datetime
    end: datetime
    all_day: bool = False
    location: str = ""
    description: str = ""
    participants: tuple[tuple[str, str], ...] = ()
    recurring: bool = False
    zone_label: str = ""
    editable: bool = True
    instance_start: datetime | None = None
    rid: str = ""
    repeat: str = "none"
    repeat_label: str = ""
    alerts: tuple[int, ...] = ()
    url: str = ""
    tzid: str = ""
    attendees: tuple[CalendarAttendee, ...] = ()
    organizer_email: str = ""
    user_email: str = ""

    @property
    def days(self) -> tuple[date, ...]:
        """Every day this event touches, so a multi-day event spans cells."""
        first = self.start.date()
        # An all-day event's end is exclusive in iCalendar; a timed event that
        # ends at midnight belongs to the day before, not to the next one.
        last = self.end.date()
        if self.all_day or (self.end.time() == datetime.min.time() and self.end > self.start):
            last = (self.end - timedelta(seconds=1)).date()
        span = (last - first).days
        return tuple(first + timedelta(days=offset) for offset in range(max(span, 0) + 1))

    @property
    def multi_day(self) -> bool:
        return len(self.days) > 1


@dataclass
class EventDraft:
    """What the editor hands back. Times are wall-clock times in `tzid`."""
    source_uid: str
    summary: str
    start: datetime
    end: datetime
    all_day: bool = False
    tzid: str = ""
    location: str = ""
    description: str = ""
    url: str = ""
    repeat: str = "none"
    alerts: tuple[int, ...] = ()
    uid: str = ""
    rid: str = ""
    instance_start: datetime | None = None
    changed_fields: frozenset[str] | None = None
    attendees: tuple[CalendarAttendee, ...] | None = None
    organizer_email: str = ""


def sms_uri(phone: str) -> str:
    """Keep a contact's dialable number intact when opening Messages."""
    import re
    from urllib.parse import unquote

    phone = phone.strip()
    if phone.lower().startswith("tel:"):
        phone = unquote(phone[4:])
    if not re.fullmatch(r"\+?[0-9\s().-]+", phone):
        raise ValueError("No usable phone number is available")
    digits = re.sub(r"[^0-9]", "", phone)
    if not 3 <= len(digits) <= 15:
        raise ValueError("No usable phone number is available")
    return "sms:" + ("+" if phone.startswith("+") else "") + digits


def same_occurrence(first: Event, second: Event) -> bool:
    """A UID identifies a series; selection identifies one occurrence in one source."""
    if (first.source_uid, first.uid) != (second.source_uid, second.uid):
        return False
    if first.rid and second.rid:
        return first.rid == second.rid
    return (first.instance_start or first.start) == (second.instance_start or second.start)


def participant_rows(event: Event) -> tuple[tuple[str, str, str], ...]:
    """Keep each displayed attendee paired with its address, including repeated names."""
    rows = []
    for index, (name, status) in enumerate(event.participants):
        attendee = event.attendees[index] if index < len(event.attendees) else None
        email = attendee.email if attendee is not None and attendee.name == name else ""
        rows.append((name, status, email))
    return tuple(rows)


def contact_for_person(people, name: str, email: str = ""):
    """Resolve a known address first; a display name alone must be unambiguous."""
    identity = email.strip().casefold()
    matches = [person for person in people
               if (person.email.strip().casefold() == identity if identity else person.name == name)]
    return matches[0] if len(matches) == 1 else None
