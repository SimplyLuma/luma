# SPDX-License-Identifier: Apache-2.0
"""v70 Calendar data and pure helpers; fixture operations never reach EDS.

The JSON keeps the mockup's IDs, offsets and minute values verbatim. The
adapter exposes the same value objects as the real backend. Its editable
copy is held in memory; neither the JSON nor another store is written.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import re

from .calendar_data import CalendarSource, Event, EventDraft


def month_days(anchor: date, week_start: int = 0) -> tuple[date, ...]:
    """Only the weeks that contain this month, including adjacent days."""
    if not 0 <= week_start <= 6:
        raise ValueError("Week start must be from 0 to 6.")
    first = anchor.replace(day=1)
    following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    origin = first - timedelta(days=(first.weekday() - week_start) % 7)
    count = ((following - origin).days + 6) // 7 * 7
    return tuple(origin + timedelta(days=i) for i in range(count))


def resolve_calendar(key, sources):
    """Resolve a parsed category against writable real source identifiers."""
    writable=tuple(source for source in sources if source.writable)
    return next((s.uid for s in writable if key in (s.uid,s.name.casefold())),
                next((s.uid for s in writable if s.is_default),writable[0].uid if writable else ""))


def events_on(events, day: date, hidden=()) -> tuple[Event, ...]:
    return tuple(sorted((e for e in events if e.source_uid not in hidden and day in e.days),
                        key=lambda e: (not e.all_day, e.start, e.uid)))


def free_gaps(day: date, events, *, today: date, now_minute: int) -> tuple[tuple[int, int], ...]:
    """v70's one-hour free regions in 09–18 (10–18 at weekends)."""
    cursor = 600 if day.weekday() >= 5 else 540
    if day == today:
        cursor = max(cursor, (now_minute + 14) // 15 * 15)
    gaps = []
    busy = sorted((0 if e.start.date() < day else e.start.hour * 60 + e.start.minute,
                   1440 if e.end.date() > day else e.end.hour * 60 + e.end.minute)
                  for e in events if not e.all_day and day in e.days)
    for start, end in busy:
        if min(start, 1080) - cursor >= 60:
            gaps.append((cursor, min(start, 1080)))
        cursor = max(cursor, end)
    if 1080 - cursor >= 60:
        gaps.append((cursor, 1080))
    return tuple(gaps)


def overlap_lanes(events) -> tuple[tuple[Event, int, int], ...]:
    """v70 lane allocation: adjacent intervals can use the same lane."""
    placed = []
    def overlaps(a, b):
        return a.start < b.end and b.start < a.end
    for event in events:
        if event.all_day:
            continue
        lane = 0
        while any(other_lane == lane and overlaps(other, event) for other, other_lane in placed):
            lane += 1
        placed.append((event, lane))
    return tuple((event, lane, max(l for e, l in placed if overlaps(e, event)) + 1)
                 for event, lane in placed)


def duration_text(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes}m"
    hours, rest = divmod(minutes, 60)
    return f"{hours}h" + (f" {rest}m" if rest else "")


def minute_text(minutes: int) -> str:
    hour, minute = divmod(minutes, 60)
    return f"{hour % 12 or 12}" + (f":{minute:02}" if minute else "") + (" AM" if hour < 12 else " PM")


@dataclass(frozen=True)
class ParsedEvent:
    day: date
    start: int
    end: int
    title: str
    calendar: str
    people: tuple[str, ...] = ()
    location: str = ""
    alert: int = 10


def parse_event(text: str, *, today: date, now_minute: int, people=None) -> ParsedEvent | None:
    """The mockup's quick-entry vocabulary, without GTK or storage."""
    if len(text.strip()) < 3:
        return None
    text = " " + text.strip() + " "
    from .quick_time import extract_time_range
    text, interval = extract_time_range(text)
    day, start, length = today, None, 60
    def relative(match):
        nonlocal day, start
        word = match[1].lower()
        day = today + timedelta(days=word == "tomorrow")
        if word == "tonight":
            start = 1140
        return " "
    text = re.sub(r"\s(today|tonight|tomorrow)\s", relative, text, flags=re.I)
    def weekday(match):
        nonlocal day
        target = ("mon", "tue", "wed", "thu", "fri", "sat", "sun").index(match[1].lower())
        day = today + timedelta(days=(target - today.weekday()) % 7 or 7)
        return " "
    text = re.sub(r"\s(?:on\s)?(sun|mon|tue|wed|thu|fri|sat)[a-z]*\s", weekday, text, flags=re.I)
    def clock(match):
        nonlocal start
        from .quick_time import quick_clock
        hour, minute, suffix = int(match[1]), int(match[2] or 0), (match[3] or "").lower()
        if minute > 59 or hour > 23 or (suffix and not 1 <= hour <= 12):
            raise ValueError("Enter a valid event time.")
        if not suffix and not match[2] and hour > 12:
            return match[0]
        parsed = quick_clock(hour, minute, suffix)
        start = parsed.hour * 60 + parsed.minute
        return " "
    text = re.sub(r"\s(?:at\s)?(\d{1,2})(?::(\d{2}))?\s?(am|pm)?\s", clock, text, count=1, flags=re.I)
    def duration(match):
        nonlocal length
        length = round(float(match[1]) * (60 if match[2].lower().startswith("h") else 1))
        return " "
    text = re.sub(r"\sfor\s(\d+(?:\.\d+)?)\s?(h|hr|hours?|m|min|minutes?)\s", duration, text, flags=re.I)
    location = ""
    def place(match):
        nonlocal location
        location = match[1]
        return " "
    text = re.sub(r"\s(?:at|@)\s+([A-Z][\w'’&.-]*(?:\s+[A-Z][\w'’&.-]*)*)\s", place, text)
    title = " ".join(text.split())
    found = tuple(key for key, name in (people or {}).items() if key != "me" and name.split()
                  and re.search(r"\b" + re.escape(name.split()[0]) + r"\b", title, re.I))
    start = min(1260, (now_minute + 89) // 60 * 60) if start is None else start
    if interval:
        start = interval[0].hour * 60 + interval[0].minute
        end = interval[1].hour * 60 + interval[1].minute + interval[2] * 1440
    else:
        end = min(1440, start + length)
    if end <= start:
        raise ValueError("The event must end after it starts.")
    calendar = "work" if re.search(r"stand|review|call|press|launch|meeting|sync", title, re.I) else "family" if "MO" in found else "personal"
    return ParsedEvent(day, start, end, title[:1].upper() + title[1:], calendar, found, location)


class CalendarFixture:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        raw = json.loads(self.path.read_text())
        self.base = date.fromisoformat(raw["base"])
        self.today = date.fromisoformat(raw["today"])
        self.now_minute = int(raw.get("now_minute",630))
        self.view = raw["view"]
        self.week_start = int(raw["week_start"])
        self.people = raw["people"]
        self.metadata = {str(row["id"]): row for row in raw["events"]}
        # Legacy tone colours are data values for the existing renderer. The
        # v70 category hue is retained in metadata for the requested kit API.
        colours = {"blue": "#527fae", "green": "#3b8967", "amber": "#b17a3c"}
        self.sources = tuple(CalendarSource(key, row["name"], row["tone"], colours[row["tone"]],
                                           writable=row.get("writable", True), is_default=key == "personal")
                             for key, row in raw["calendars"].items())
        self.calendars = raw["calendars"]
        self.events = tuple(self._event(row) for row in raw["events"])

    def _event(self, row) -> Event:
        day = self.base + timedelta(days=row["o"])
        origin = datetime.combine(day, time(), timezone.utc)
        all_day = row.get("all", False)
        start = origin if all_day else origin + timedelta(minutes=row["s"])
        end = origin + timedelta(days=1) if all_day else origin + timedelta(minutes=row["e"])
        return Event(str(row["id"]), row["cal"], row["t"], start, end, all_day=all_day,
                     location=row.get("loc", ""), description=row.get("note", ""),
                     participants=tuple((self.people[key]["name"], "Maybe" if key == "SK" else "Going")
                                        for key in row.get("people", ())),
                     editable=self.calendars[row["cal"]].get("writable", True), alerts=(10,), tzid="UTC")

    def read(self, start: datetime, end: datetime, hidden=()) -> tuple[Event, ...]:
        return tuple(e for e in self.events if e.source_uid not in hidden and e.start < end and e.end > start)

    def save(self, draft: EventDraft) -> str:
        source = next(s for s in self.sources if s.uid == draft.source_uid)
        if not source.writable:
            raise ValueError("This calendar is read-only.")
        if not draft.summary.strip():
            raise ValueError("The event needs a title.")
        uid = draft.uid or str(max((int(e.uid) for e in self.events), default=0) + 1)
        existing = next((e for e in self.events if e.uid == uid), None)
        end = draft.end + timedelta(days=1) if draft.all_day else draft.end
        if end <= draft.start:
            raise ValueError("The event must end after it starts.")
        event = replace(existing or Event(uid, source.uid, "", draft.start, end),
                        source_uid=source.uid, summary=draft.summary, start=draft.start, end=end,
                        all_day=draft.all_day, location=draft.location, description=draft.description,
                        url=draft.url, repeat=draft.repeat, alerts=draft.alerts, tzid=draft.tzid)
        self.events = tuple(e for e in self.events if e.uid != uid) + (event,)
        return uid

    def delete(self, event: Event) -> None:
        if not event.editable:
            raise ValueError("This calendar is read-only.")
        self.events = tuple(e for e in self.events if e.uid != event.uid)
