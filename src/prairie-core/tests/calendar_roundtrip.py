#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

"""An event Luma does not fully understand survives a Luma edit.

A repeating event carrying a property Luma never writes is renamed across the
whole series. The rename must land on every occurrence and the unknown
property must still be there, byte for byte.

Run against a session with Evolution Data Server. Without one it reports that
it could not run rather than passing quietly.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import sys

from prairie_apps.calendar_backend import (
    CalendarUnavailable, component_text, delete_event, list_events,
    list_sources, save_event,
)

RECURRING = """BEGIN:VEVENT
UID:luma-roundtrip-test
DTSTAMP:20260101T090000Z
DTSTART:20260105T090000Z
DTEND:20260105T093000Z
SUMMARY:Standing meeting
RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=8
X-LUMA-UNKNOWN-FIELD:kept verbatim
END:VEVENT"""


def main() -> int:
    try:
        sources = list_sources()
    except CalendarUnavailable as error:
        print(f"could not run: {error}")
        return 77
    writable = [source for source in sources if source.writable]
    if not writable:
        print("could not run: no writable calendar")
        return 77
    target = writable[0]

    import gi
    gi.require_version("ECal", "2.0")
    gi.require_version("EDataServer", "1.2")
    gi.require_version("ICalGLib", "3.0")
    from gi.repository import ECal, EDataServer, ICalGLib

    registry = EDataServer.SourceRegistry.new_sync(None)
    source = registry.ref_source(target.uid)
    client = ECal.Client.connect_sync(source, ECal.ClientSourceType.EVENTS, 30, None)
    component = ICalGLib.Component.new_from_string(RECURRING)
    try:
        client.create_object_sync(component, 0, None)
    except Exception as error:
        if "exists" not in str(error).lower():
            print(f"could not run: {error}")
            return 77


    window = datetime(2026, 1, 1).astimezone()
    occurrences = [event for event in
                   list_events(window, window + timedelta(days=60), (target,))
                   if event.uid == "luma-roundtrip-test"]
    if not occurrences:
        print("FAIL: the repeating event did not appear at all")
        return 1
    if len(occurrences) < 2:
        print(f"FAIL: expected the series to expand, saw {len(occurrences)}")
        return 1
    if not all(event.editable and event.recurring and event.rid for event in occurrences):
        print("FAIL: occurrences of a series in a writable calendar should be editable")
        return 1
    from prairie_apps.calendar_backend import SCOPE_ALL, EventDraft
    first = occurrences[0]
    save_event(EventDraft(source_uid=target.uid, summary="Renamed by a test", start=first.start,
                          end=first.end, uid=first.uid, rid=first.rid, instance_start=first.instance_start,
                          repeat=first.repeat, tzid=first.tzid or "UTC"), scope=SCOPE_ALL)
    after = component_text(target.uid, "luma-roundtrip-test")
    renamed = [event for event in list_events(window, window + timedelta(days=60), (target,))
               if event.uid == "luma-roundtrip-test"]
    if "X-LUMA-UNKNOWN-FIELD:kept verbatim" not in after:
        print("FAIL: the unknown property was lost")
        return 1
    if len(renamed) != len(occurrences) or any(event.summary != "Renamed by a test" for event in renamed):
        print("FAIL: the rename did not reach every occurrence")
        return 1
    if "RRULE:FREQ=WEEKLY;COUNT=8;BYDAY=MO" not in after and "COUNT=8" not in after:
        print("FAIL: the recurrence rule changed")
        return 1
    print(f"OK: {len(occurrences)} occurrences renamed together, rule and unknown property kept")
    try:
        delete_event(target.uid, "luma-roundtrip-test")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
