#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Moving Personal events into the Luma calendar, against a stub CalDAV server.

No Evolution Data Server and no network: the calendar service and the server
are small in-memory fakes, and the clock is fake so a timeout costs nothing.

The case this exists for: the first pass timed out before the calendar service
had uploaded the events, so the server already held them on every later pass.
Counting the server's resources before and after a re-import can never grow
by the number of events again, and the step failed on every pass forever.
"""
import sys
import tempfile
import types
from pathlib import Path
from xml.sax.saxutils import escape

import prairie_apps
from prairie_apps import connect_calendar

SCOPE = "https://hub.example|1834aa7c-6ea0-437e-ba62-146b761ca162"
KEY = f"{SCOPE}|calendar"
LUMA = "luma-connect-1834aa7c6ea0"
URL = "https://hub.example/dav/u1/luma/"


def event(uid: str, summary: str = "Lunch") -> str:
    return ("BEGIN:VEVENT\r\n"
            f"UID:{uid}\r\n"
            "DTSTART:20260913T180000Z\r\n"
            f"SUMMARY:{summary}\r\n"
            "END:VEVENT\r\n")


def calendar(*events: str) -> str:
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join(events) + "END:VCALENDAR\r\n"


class World:
    """The device's calendar service and the Hub's CalDAV collection."""

    def __init__(self, personal: dict[str, str], server: dict[str, str], uploads: bool = True):
        self.personal = dict(personal)        # uid -> VEVENT text
        self.server = dict(server)            # href -> iCalendar text
        self.uploads = uploads                # whether the calendar service reaches the server
        self.imports = 0
        self.now = 0.0

    # -- the calendar service -------------------------------------------------
    def backend(self):
        world = self

        class Source:
            def __init__(self, uid, is_default=False):
                self.uid, self.is_default = uid, is_default

        module = types.ModuleType("prairie_apps.calendar_backend")
        module.CalendarUnavailable = type("CalendarUnavailable", (RuntimeError,), {})
        module.list_sources = lambda: (Source("system-calendar", True), Source(LUMA))
        module.export_ics = lambda uid: calendar(*world.personal.values())
        module.set_default_calendar = lambda uid: None
        module.create_calendar = lambda *a, **k: LUMA

        def import_ics(uid, text):
            assert uid == LUMA, uid
            world.imports += 1
            if world.uploads:
                for found in connect_calendar._event_uids(text):
                    world.server[f"{found}.ics"] = calendar(world.personal[found])
            return len(connect_calendar._event_uids(text))

        def delete_event(uid, event_uid):
            assert uid == "system-calendar", uid
            del world.personal[event_uid]

        module.import_ics = import_ics
        module.delete_event = delete_event
        return module

    # -- the server -----------------------------------------------------------
    def dav(self, method, url, username, password, body=None, headers=None, timeout=20.0):
        assert url == URL, url
        responses = []
        for href, text in self.server.items():
            data = ""
            if method == "REPORT":
                assert b"calendar-data" in body and headers.get("Depth") == "1", (body, headers)
                data = f"<C:calendar-data>{escape(text)}</C:calendar-data>"
            elif method != "PROPFIND":
                raise AssertionError(f"unexpected {method}")
            responses.append(f"<D:response><D:href>/dav/u1/luma/{href}</D:href><D:propstat><D:prop>"
                             f'<D:getetag>"1"</D:getetag>{data}</D:prop>'
                             "<D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>")
        return 207, ('<?xml version="1.0"?><D:multistatus xmlns:D="DAV:" '
                     'xmlns:C="urn:ietf:params:xml:ns:caldav">'
                     f"<D:response><D:href>/dav/u1/luma/</D:href></D:response>{''.join(responses)}"
                     "</D:multistatus>").encode()

    # -- time -----------------------------------------------------------------
    def clock(self):
        world = self
        return types.SimpleNamespace(monotonic=lambda: world.now,
                                     sleep=lambda seconds: setattr(world, "now", world.now + seconds))


def run(world: World, state: dict, data: Path) -> str:
    sys.modules["prairie_apps.calendar_backend"] = world.backend()
    prairie_apps.calendar_backend = sys.modules["prairie_apps.calendar_backend"]
    dav = types.ModuleType("prairie_apps.connect_dav")
    dav.stored_password = lambda device_id, address: "secret"
    dav.credential = lambda **kwargs: {"username": "u1", "password": "secret", "home": URL[:-5]}
    sys.modules["prairie_apps.connect_dav"] = dav
    connect_calendar._dav = world.dav
    connect_calendar.time = world.clock()
    return connect_calendar.sync_calendar(
        address="https://hub.example", token="t", http=None, state=state, scope=SCOPE,
        environment=None, remote_changed=False, data_directory=data)


def fresh_state() -> dict:
    return {KEY: {"source_uid": LUMA, "url": URL, "username": "u1"}}


failures = 0
ran = 0


def case(name):
    def wrap(function):
        global failures, ran
        ran += 1
        with tempfile.TemporaryDirectory(prefix="luma-connect-calendar-test-") as directory:
            try:
                function(Path(directory))
                print(f"PASS {name}")
            except Exception as error:  # report every case, then fail
                failures += 1
                print(f"FAIL {name}: {type(error).__name__}: {error}")
        return function
    return wrap


A, B = "7c6e464d-10b5-4f9f-87b0-1017a88bab74", "80435155-d305-4dd2-83d1-327033abe185"


@case("events the server already holds from an earlier pass are confirmed, not waited on forever")
def already_uploaded(data):
    # Exactly the ThinkPad's state: an earlier pass imported both events, the
    # calendar service uploaded them after that pass gave up, and they are
    # still in Personal because the move was never confirmed.
    world = World({A: event(A), B: event(B)},
                  {f"{A}.ics": calendar(event(A)), f"{B}.ics": calendar(event(B))})
    state = fresh_state()
    status = run(world, state, data)
    assert state[KEY].get("migrated") is True, (status, state)
    assert world.personal == {}, f"Personal still holds {sorted(world.personal)}"
    assert "moved 2 event(s)" in status, status
    # Re-sent once, so the Luma calendar holds Personal's current version of
    # each event before Personal's copy is removed.
    assert world.imports == 1, world.imports


@case("a fresh move uploads, confirms by UID and empties Personal")
def fresh_move(data):
    world = World({A: event(A), B: event(B)}, {"other.ics": calendar(event("someone-else"))})
    state = fresh_state()
    status = run(world, state, data)
    assert state[KEY].get("migrated") is True, status
    assert world.personal == {} and "moved 2 event(s)" in status, status
    assert len(list((data / "calendar-backups").glob("*.ics"))) == 1


@case("events kept under other file names on the server are recognised by their UID")
def other_names(data):
    world = World({A: event(A)}, {"0001.ics": calendar(event(A))})
    state = fresh_state()
    run(world, state, data)
    assert state[KEY].get("migrated") is True and world.personal == {}


@case("a server that never confirms leaves Personal untouched, reports it, and does not fail the pass")
def never_confirmed(data):
    world = World({A: event(A), B: event(B)}, {}, uploads=False)
    state = fresh_state()
    # A backup of these same events from an earlier pass is reused, not copied again.
    (data / "calendar-backups").mkdir()
    earlier = data / "calendar-backups" / "personal-20260101T000000Z.ics"
    earlier.write_bytes(calendar(event(A), event(B)).encode())
    first = run(world, state, data)
    second = run(world, state, data)
    for status in (first, second):
        assert "waiting" in status and "Personal" in status, status
    assert sorted(world.personal) == sorted([A, B]), "nothing may leave Personal unconfirmed"
    assert not state[KEY].get("migrated"), "the move is retried next pass"
    backups = list((data / "calendar-backups").glob("*.ics"))
    assert len(backups) == 1, f"one backup of unchanged events, not one per pass: {len(backups)}"
    assert backups == [earlier] and str(earlier) in first, (backups, first)
    # Once the server has them, the next pass finishes the move.
    world.uploads = True
    world.server.update({f"{A}.ics": calendar(event(A)), f"{B}.ics": calendar(event(B))})
    third = run(world, state, data)
    assert state[KEY].get("migrated") is True and world.personal == {}, third


@case("a partial confirmation removes nothing from Personal")
def partial(data):
    world = World({A: event(A), B: event(B)}, {f"{A}.ics": calendar(event(A))}, uploads=False)
    state = fresh_state()
    status = run(world, state, data)
    assert sorted(world.personal) == sorted([A, B]), status
    assert "1 of 2" in status, status


@case("an unreadable server is an error, never a confirmation")
def server_error(data):
    world = World({A: event(A)}, {f"{A}.ics": calendar(event(A))})
    world.dav = lambda *a, **k: (500, b"")
    state = fresh_state()
    try:
        run(world, state, data)
    except connect_calendar.CalendarSyncError:
        pass
    else:
        raise AssertionError("an HTTP 500 must not confirm anything")
    assert world.personal == {A: event(A)} and not state[KEY].get("migrated")


@case("an empty Personal calendar is marked moved without touching the server")
def empty(data):
    world = World({}, {})
    world.dav = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no request expected"))
    state = fresh_state()
    run(world, state, data)
    assert state[KEY].get("migrated") is True


print(f"connect_calendar_unit: {ran - failures} of {ran} passed")
if failures or ran != 7:  # a case that silently stopped running is a failure too
    sys.exit(1)
