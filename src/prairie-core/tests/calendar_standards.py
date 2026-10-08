#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

"""Calendar events are stored the way RFC 5545 says, and survive editing.

Runs against a live Evolution Data Server session, in a temporary calendar it
creates and removes, so a person's own calendars are never touched. Without a
session it reports that it could not run (exit 77) rather than passing.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import re
import sys
from zoneinfo import ZoneInfo

from prairie_apps.calendar_backend import (
    SCOPE_ALL, SCOPE_FUTURE, SCOPE_THIS, CalendarUnavailable, EventDraft,
    component_text, create_calendar, delete_event, export_ics, import_ics,
    list_events, list_sources, remove_calendar, save_event,
)

CHICAGO = ZoneInfo("America/Chicago")
failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)
        print(f"FAIL: {message}")


def events_between(source, first: datetime, last: datetime, uid: str = ""):
    return [event for event in list_events(first, last, (source,)) if not uid or event.uid == uid]


def unfold(text: str) -> str:
    return re.sub(r"\r?\n[ \t]", "", text)


def main() -> int:
    try:
        list_sources()
    except CalendarUnavailable as error:
        print(f"could not run: {error}")
        return 77
    uid = create_calendar("Luma standards test (temporary)", tone="green")
    try:
        source = next(item for item in list_sources() if item.uid == uid)
        check(source.writable and source.kind == "local" and source.removable, "a new local calendar is writable and removable")

        # Timed event: named zone, not floating, not UTC.
        start = datetime(2026, 11, 3, 9, 30, tzinfo=CHICAGO)
        event_uid = save_event(EventDraft(source_uid=uid, summary="Standup", start=start,
                                          end=start + timedelta(minutes=30), tzid="America/Chicago",
                                          alerts=(10,), location="Room 4"))
        text = unfold(component_text(uid, event_uid))
        check(re.search(r"DTSTART;TZID=[^:]*America/Chicago:20261103T093000", text) is not None, f"timed DTSTART keeps its zone:\n{text}")
        check("BEGIN:VALARM" in text and "TRIGGER" in text and "-PT10M" in text, "a 10 minute alert is a VALARM")
        check("SEQUENCE" not in text or "SEQUENCE:0" in text, "a new event starts at sequence 0")
        check("CREATED:" in text and "DTSTAMP:" in text and "LAST-MODIFIED:" in text, "created, stamp and modified are recorded")

        # A property Luma does not know survives an edit.
        from prairie_apps.calendar_backend import _client
        client, _s, cal, ical = _client(uid)
        stored = client.get_object_sync(event_uid, None, None)
        stored = stored[1] if isinstance(stored, tuple) else stored
        extra = ical.Property.new_from_string("X-OTHER-CLIENT:keep me")
        stored.add_property(extra)
        client.modify_object_sync(stored, cal.ObjModType.THIS, 0, None)
        [instance] = events_between(source, start - timedelta(hours=1), start + timedelta(hours=2), event_uid)
        check(instance.alerts == (10,) and instance.tzid == "America/Chicago", f"reads back alerts and zone: {instance.alerts} {instance.tzid}")
        save_event(EventDraft(source_uid=uid, summary="Standup (moved)", start=start + timedelta(hours=1),
                              end=start + timedelta(hours=1, minutes=30), tzid="America/Chicago", uid=event_uid))
        text = unfold(component_text(uid, event_uid))
        check("X-OTHER-CLIENT:keep me" in text, "an unknown property survives a Luma edit")
        check("SEQUENCE:1" in text, "an edit raises SEQUENCE")
        check("BEGIN:VALARM" not in text, "clearing alerts removes the VALARM")

        # All-day: DATE values with an exclusive end.
        all_day = save_event(EventDraft(source_uid=uid, summary="Conference", all_day=True,
                                        start=datetime(2026, 11, 4), end=datetime(2026, 11, 5)))
        text = unfold(component_text(uid, all_day))
        check("DTSTART;VALUE=DATE:20261104" in text and "DTEND;VALUE=DATE:20261106" in text, f"all-day is DATE with exclusive end:\n{text}")
        [conference] = events_between(source, datetime(2026, 11, 1, tzinfo=CHICAGO), datetime(2026, 11, 10, tzinfo=CHICAGO), all_day)
        check(conference.all_day and conference.days == (date(2026, 11, 4), date(2026, 11, 5)), f"two-day all-day event spans two days: {conference.days}")

        # A weekly series across the daylight-saving change keeps 9:00 local.
        first = datetime(2026, 10, 26, 9, 0, tzinfo=CHICAGO)
        series = save_event(EventDraft(source_uid=uid, summary="Weekly review", start=first,
                                       end=first + timedelta(hours=1), tzid="America/Chicago", repeat="weekly"))
        window = (datetime(2026, 10, 25, tzinfo=CHICAGO), datetime(2026, 11, 30, tzinfo=CHICAGO))
        occurrences = events_between(source, *window, series)
        check(len(occurrences) >= 5, f"the series expands: {len(occurrences)}")
        check(all(item.start.astimezone(CHICAGO).hour == 9 for item in occurrences), "every occurrence is 9:00 local across DST")
        check(all(item.repeat == "weekly" and item.rid for item in occurrences), "occurrences know their repeat and recurrence id")

        # This occurrence only.
        second = occurrences[1]
        save_event(EventDraft(source_uid=uid, summary="Weekly review (room change)", start=second.start,
                              end=second.end, tzid="America/Chicago", uid=series, rid=second.rid,
                              instance_start=second.instance_start, repeat="weekly"), scope=SCOPE_THIS)
        occurrences = events_between(source, *window, series)
        titles = [item.summary for item in occurrences]
        check(titles.count("Weekly review (room change)") == 1 and titles.count("Weekly review") == len(titles) - 1,
              f"only one occurrence changed: {titles}")

        # Delete one occurrence.
        third = occurrences[2]
        delete_event(uid, series, rid=third.rid, scope=SCOPE_THIS)
        after = events_between(source, *window, series)
        check(len(after) == len(occurrences) - 1 and all(item.rid != third.rid for item in after), "deleting one occurrence leaves the rest")

        # Every occurrence moves by the same amount.
        base = after[0]
        save_event(EventDraft(source_uid=uid, summary="Weekly review", start=base.start + timedelta(hours=1),
                              end=base.end + timedelta(hours=1), tzid="America/Chicago", uid=series, rid=base.rid,
                              instance_start=base.instance_start, repeat="weekly"), scope=SCOPE_ALL)
        moved = events_between(source, *window, series)
        check(all(item.start.astimezone(CHICAGO).hour == 10 for item in moved if "room change" not in item.summary),
              f"the whole series moved to 10:00: {[item.start.astimezone(CHICAGO).hour for item in moved]}")

        # This and following: the series ends, a new one begins.
        split_at = moved[3]
        new_uid = save_event(EventDraft(source_uid=uid, summary="Weekly review v2", start=split_at.start,
                                        end=split_at.end, tzid="America/Chicago", uid=series, rid=split_at.rid,
                                        instance_start=split_at.instance_start, repeat="weekly"), scope=SCOPE_FUTURE)
        everything = events_between(source, *window)
        old = [item for item in everything if item.uid == series]
        new = [item for item in everything if item.uid == new_uid]
        check(new_uid != series and new and all(item.start >= split_at.start for item in new), "following occurrences moved to a new series")
        check(old and all(item.start < split_at.start for item in old), "the original series ends before the split")

        # Export, then import into a second calendar: same events.
        exported = export_ics(uid)
        check("BEGIN:VTIMEZONE" in exported and "TZID" in exported, "export carries the VTIMEZONE")
        copy = create_calendar("Luma import test (temporary)", tone="amber")
        try:
            count = import_ics(copy, exported)
            copy_source = next(item for item in list_sources() if item.uid == copy)
            before = sorted((e.summary, e.start.isoformat(), e.all_day) for e in events_between(source, *window))
            after_import = sorted((e.summary, e.start.isoformat(), e.all_day) for e in events_between(copy_source, *window))
            check(count >= 4 and before == after_import, f"import reproduces every occurrence ({count}):\n{before}\n{after_import}")
            check(import_ics(copy, exported) == count and len(events_between(copy_source, *window)) == len(after_import),
                  "importing the same file twice does not duplicate")
        finally:
            remove_calendar(copy)

        subscription = create_calendar("Luma subscription test (temporary)", kind="subscription",
                                       url="webcal://example.com/holidays.ics")
        try:
            listed = next(item for item in list_sources() if item.uid == subscription)
            check(listed.kind == "subscription" and not listed.writable, "a subscription is read-only")
        finally:
            remove_calendar(subscription)
    finally:
        remove_calendar(uid)
    check(all(item.uid != uid for item in list_sources()), "the test calendar is gone")
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("OK: zone-kept times, DATE all-day with exclusive end, VALARM, SEQUENCE, unknown "
          "properties kept, DST-stable series, this / following / all edits, occurrence "
          "delete, export and idempotent import, read-only subscriptions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
