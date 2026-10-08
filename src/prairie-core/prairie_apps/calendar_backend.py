# SPDX-License-Identifier: Apache-2.0

"""Events, read from and written to Evolution Data Server.

Luma does not keep a calendar store of its own. EDS owns the data, the accounts
and the sync; this module is a reading of it in the terms the interface needs.
Storing through EDS is what makes a Luma event an ordinary iCalendar
(RFC 5545) object: the same one CalDAV servers, Google Calendar, Thunderbird,
iOS and Android exchange.

The rules that hold everywhere below:

* Recurrence is expanded by EDS, never by Luma. `generate_instances_sync` is
  the code path Evolution uses, so a repeating event occurs in Luma exactly
  where it occurs everywhere else.
* A timed event is stored in a named IANA time zone (DTSTART;TZID=...), never
  as floating local time and never flattened to UTC, so it keeps its wall-clock
  time across daylight saving changes and reads correctly on a device set to
  another zone. The zone's VTIMEZONE travels with the calendar.
* An all-day event is a DATE, with an exclusive end: a one-day event on the
  4th is DTSTART;VALUE=DATE:…04 and DTEND;VALUE=DATE:…05. It is on the 4th in
  every time zone.
* Editing starts from the stored component and changes only the fields the
  editor owns, so attendees, organisers, X- properties and anything else a
  server or another client put there survive a Luma edit.
* A repeating event is changed only with an explicit scope: this occurrence
  (a RECURRENCE-ID override), this and following (the series is ended the day
  before and a new one begins), or every occurrence.
"""

from __future__ import annotations

from datetime import date, datetime, time as clock_time, timedelta, timezone
import os
from pathlib import Path
import re
import uuid

import gi

from .calendar_data import CalendarAttendee, CalendarSource, Event, EventDraft


# The four identity tones the design draws calendars in. A calendar's stored
# colour decides which one it is shown as, so a colour the user chose in
# Evolution still reads as their colour here.
TONES = ("blue", "green", "violet", "amber")
_TONE_ANCHORS = {
    "blue": (0x52, 0x7F, 0xAE),
    "green": (0x3B, 0x89, 0x67),
    "violet": (0x86, 0x5F, 0x9F),
    "amber": (0xB1, 0x7A, 0x3C),
}
TONE_COLOURS = {tone: "#%02x%02x%02x" % rgb for tone, rgb in _TONE_ANCHORS.items()}

#: Repeat choices the editor offers, in the order it offers them. Anything else
#: a calendar holds (BYSETPOS, several rules, exceptions) is kept untouched and
#: shown as "Custom" rather than simplified into one of these.
REPEATS = (
    ("none", "Never", ""),
    ("daily", "Every day", "FREQ=DAILY"),
    ("weekdays", "Every weekday", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
    ("weekly", "Every week", "FREQ=WEEKLY"),
    ("biweekly", "Every 2 weeks", "FREQ=WEEKLY;INTERVAL=2"),
    ("monthly", "Every month", "FREQ=MONTHLY"),
    ("yearly", "Every year", "FREQ=YEARLY"),
)
REPEAT_CUSTOM = "custom"

#: Alerts, as minutes before the start. None is "no alert".
ALERTS = (
    (None, "None"),
    (0, "At time of event"),
    (5, "5 minutes before"),
    (10, "10 minutes before"),
    (15, "15 minutes before"),
    (30, "30 minutes before"),
    (60, "1 hour before"),
    (120, "2 hours before"),
    (1440, "1 day before"),
    (2880, "2 days before"),
    (10080, "1 week before"),
)

SCOPE_THIS = "this"
SCOPE_FUTURE = "future"
SCOPE_ALL = "all"

_WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")




_SKIPPED: list[str] = []


def _report_skip(error: BaseException) -> None:
    message = f"{type(error).__name__}: {error}"
    if len(_SKIPPED) < 50:
        _SKIPPED.append(message)


def skipped_occurrences() -> tuple[str, ...]:
    return tuple(_SKIPPED)


class CalendarUnavailable(RuntimeError):
    """Raised when Evolution Data Server cannot be reached."""


class CalendarPartialSave(CalendarUnavailable):
    """A series was created, but the split could not be completed or undone."""
    def __init__(self, message, uid):
        super().__init__(message)
        self.result = uid


def _modules():
    gi.require_version("EDataServer", "1.2")
    gi.require_version("ECal", "2.0")
    gi.require_version("ICalGLib", "3.0")
    from gi.repository import ECal, EDataServer, ICalGLib

    return EDataServer, ECal, ICalGLib


def _nearest_tone(colour: str) -> str:
    value = (colour or "").strip().lstrip("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    try:
        red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
    except (ValueError, IndexError):
        return TONES[0]
    def distance(anchor: tuple[int, int, int]) -> int:
        return sum((component - part) ** 2 for component, part in zip((red, green, blue), anchor))
    return min(_TONE_ANCHORS, key=lambda tone: distance(_TONE_ANCHORS[tone]))


def _registry():
    if os.environ.get("LUMA_CALENDAR_FIXTURE"):
        raise CalendarUnavailable("Fixture mode cannot access Evolution Data Server.")
    if os.environ.get("PRAIRIE_EDS_MODE") == "disabled":
        raise CalendarUnavailable("Evolution Data Server is disabled for this run.")
    data_server, _cal, _ical = _modules()
    try:
        return data_server.SourceRegistry.new_sync(None)
    except Exception as error:  # GLib.Error and friends
        raise CalendarUnavailable(str(error)) from error


# ── Time zones ────────────────────────────────────────────────────────────

def local_tzid() -> str:
    """The system's IANA zone name, e.g. America/Chicago."""
    override = os.environ.get("LUMA_CALENDAR_TZID", "").strip()
    if override:
        return override
    try:
        gi.require_version("GLib", "2.0")
        from gi.repository import GLib
        identifier = GLib.TimeZone.new_local().get_identifier()
        if identifier and "/" in identifier:
            return identifier
    except Exception:
        pass
    try:
        target = os.readlink("/etc/localtime")
        marker = "zoneinfo/"
        if marker in target:
            return target.split(marker, 1)[1]
    except OSError:
        pass
    return "UTC"


def _tzinfo(tzid: str):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        return ZoneInfo(tzid or local_tzid())
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def _ical_zone(ical, tzid: str):
    if not tzid or tzid == "UTC":
        return ical.Timezone.get_utc_timezone()
    zone = ical.Timezone.get_builtin_timezone(tzid)
    if zone is None:
        zone = ical.Timezone.get_builtin_timezone_from_tzid(tzid)
    return zone or ical.Timezone.get_utc_timezone()


def _bare_tzid(tzid: str) -> str:
    """libical's builtin zones carry a vendor prefix; the IANA name is the tail."""
    if not tzid:
        return ""
    match = re.search(r"((?:Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|Europe|Indian|Pacific|Etc)(?:/[A-Za-z0-9_+\-]+)+)$", tzid)
    if match:
        return match.group(1)
    return tzid.rsplit("/", 1)[-1] if tzid.startswith("/") else tzid


# ── Reading ───────────────────────────────────────────────────────────────

def _source_kind(data_server, source, backend: str) -> tuple[str, str]:
    """(kind, account) for the sidebar: local, subscription, caldav, google, …"""
    account = ""
    try:
        if source.has_extension(data_server.SOURCE_EXTENSION_AUTHENTICATION):
            account = source.get_extension(data_server.SOURCE_EXTENSION_AUTHENTICATION).get_user() or ""
    except Exception:
        pass
    if backend == "webcal":
        return "subscription", account
    if backend in {"birthdays", "contacts"}:
        return "birthdays", account
    if backend == "caldav":
        parent = source.get_parent() or ""
        return ("luma" if parent == "luma-connect-stub" or source.get_uid().startswith("luma-connect-")
                else "google" if "google" in parent.lower() else "caldav"), account
    if backend == "microsoft365" or backend == "ews":
        return "microsoft", account
    return "local", account


def list_sources() -> tuple[CalendarSource, ...]:
    data_server, _cal, _ical = _modules()
    registry = _registry()
    default = registry.ref_default_calendar()
    default_uid = default.get_uid() if default is not None else ""
    sources = []
    for source in registry.list_sources(data_server.SOURCE_EXTENSION_CALENDAR):
        if not source.get_enabled():
            continue
        extension = source.get_extension(data_server.SOURCE_EXTENSION_CALENDAR)
        colour = ""
        try:
            colour = extension.get_color() or ""
        except AttributeError:
            pass
        # A source can report itself writable and still refuse every write:
        # the birthday and contact calendars are generated from the address
        # book. Offering to save into one is offering something that fails.
        backend = ""
        try:
            backend = (extension.get_backend_name() or "").lower()
        except AttributeError:
            pass
        kind, account = _source_kind(data_server, source, backend)
        sources.append(CalendarSource(
            uid=source.get_uid(),
            name=source.get_display_name(),
            tone=_nearest_tone(colour),
            colour=colour,
            writable=source.get_writable() and kind not in {"birthdays", "subscription"},
            kind=kind,
            removable=bool(source.get_removable()),
            is_default=source.get_uid() == default_uid,
            account=account,
        ))
    sources.sort(key=lambda item: (not item.is_default, item.name.casefold()))
    return tuple(sources)


def _to_datetime(value, fallback: datetime) -> datetime:
    """Turn whatever EDS hands back into an aware datetime.

    `generate_instances_sync` reports each occurrence as an `ICalGLib.Time`,
    which is either a date (an all-day event) or a moment. A date is taken at
    face value — an all-day event on the 4th is on the 4th in every zone — and
    a moment is converted through its own timezone.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.astimezone()
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).astimezone()
    if value is None:
        return fallback
    try:
        if value.is_date():
            return datetime(value.get_year(), value.get_month(), value.get_day()).astimezone()
        if not value.is_utc() and value.get_timezone() is None:
            # Floating time (no zone, no Z): RFC 5545 says it is the same
            # wall-clock time wherever it is read, so it is local here.
            return datetime(value.get_year(), value.get_month(), value.get_day(),
                            value.get_hour(), value.get_minute(), value.get_second()).astimezone()
    except Exception:
        pass
    for reader in (
        lambda: value.as_timet_with_zone(value.get_timezone()),
        lambda: value.as_timet(),
    ):
        try:
            stamp = reader()
        except Exception:
            continue
        if stamp:
            return datetime.fromtimestamp(stamp, tz=timezone.utc).astimezone()
    return fallback


def _is_date(value) -> bool:
    try:
        return bool(value.is_date())
    except Exception:
        return False


def _component_tzid(component) -> str:
    try:
        start = component.get_dtstart()
        return (start.get_tzid() or "") if start is not None else ""
    except Exception:
        return ""


def _component_zone_label(component) -> str:
    zone = _bare_tzid(_component_tzid(component))
    if not zone:
        return ""
    if zone in {local_tzid(), "UTC"}:
        return ""
    return zone.split("/")[-1].replace("_", " ")


def _attendee_records(component) -> tuple[CalendarAttendee, ...]:
    people = []
    for attendee in component.get_attendees() or []:
        email = (attendee.get_value() or "").removeprefix("mailto:")
        status = attendee.get_partstat()
        answer = getattr(status, "value_nick", "NEEDS-ACTION").upper().replace("_", "-")
        if email:
            people.append(CalendarAttendee(attendee.get_cn() or email, email, answer))
    return tuple(people)


def _participants(component) -> tuple[tuple[str, str], ...]:
    names = {"ACCEPTED": "Going", "TENTATIVE": "Maybe", "DECLINED": "Can’t go", "NEEDS-ACTION": "Invited"}
    return tuple((person.name, names.get(person.answer, "Invited")) for person in _attendee_records(component))


def _set_invitees(component, people, cal, ical):
    # Keep every parameter on existing attendees unless the user removed them.
    existing = {(a.get_value() or "").removeprefix("mailto:").casefold(): a
                for a in component.get_attendees() or []}
    output = []
    for person in people:
        if not re.fullmatch(r"[^\s@]+@[^\s@]+", person.email):
            raise ValueError("Each invitee needs a valid email address.")
        attendee = existing.get(person.email.casefold())
        if attendee is None:
            attendee = cal.ComponentAttendee.new()
            attendee.set_value("mailto:" + person.email)
            attendee.set_cn(person.name)
            attendee.set_role(ical.ParameterRole.REQPARTICIPANT)
            attendee.set_cutype(ical.ParameterCutype.INDIVIDUAL)
            attendee.set_partstat(ical.ParameterPartstat.NEEDSACTION)
            attendee.set_rsvp(True)
        output.append(attendee)
    component.set_attendees(output)



def _rrule_strings(component) -> list[str]:
    try:
        return [rule.to_string() for rule in (component.get_rrules() or [])]
    except Exception:
        return []


def repeat_key(rules: list[str], extra: bool = False) -> str:
    """Which editor choice a stored rule set is, or custom."""
    if not rules:
        return "none"
    if len(rules) > 1 or extra:
        return REPEAT_CUSTOM
    parts = dict(part.split("=", 1) for part in rules[0].upper().split(";") if "=" in part)
    parts.pop("WKST", None)
    until = {key: parts.pop(key) for key in ("UNTIL", "COUNT") if key in parts}
    if until:
        return REPEAT_CUSTOM
    normal = ";".join(f"{key}={value}" for key, value in sorted(parts.items()))
    for key, _label, rule in REPEATS[1:]:
        wanted = dict(part.split("=", 1) for part in rule.split(";"))
        wanted_normal = ";".join(f"{k}={v}" for k, v in sorted(wanted.items()))
        if normal == wanted_normal:
            return key
        # FREQ=WEEKLY with the event's own weekday is the same as FREQ=WEEKLY.
        if key in {"weekly", "biweekly"} and set(parts) - {"BYDAY"} == set(wanted) and parts.get("BYDAY", "") in _WEEKDAY_CODES:
            without = {k: v for k, v in parts.items() if k != "BYDAY"}
            if ";".join(f"{k}={v}" for k, v in sorted(without.items())) == wanted_normal:
                return key
    return REPEAT_CUSTOM


def describe_rule(rule: str) -> str:
    """A plain-language reading of an RRULE, for the event details."""
    if not rule:
        return ""
    parts = dict(part.split("=", 1) for part in rule.upper().split(";") if "=" in part)
    frequency = {"DAILY": "day", "WEEKLY": "week", "MONTHLY": "month", "YEARLY": "year"}.get(parts.get("FREQ", ""), "")
    if not frequency:
        return "Repeats"
    interval = int(parts.get("INTERVAL", "1") or 1)
    text = f"Every {frequency}" if interval == 1 else f"Every {interval} {frequency}s"
    if parts.get("BYDAY"):
        names = {"MO": "Mon", "TU": "Tue", "WE": "Wed", "TH": "Thu", "FR": "Fri", "SA": "Sat", "SU": "Sun"}
        days = [names.get(code[-2:], code) for code in parts["BYDAY"].split(",")]
        text = "Every weekday" if days == ["Mon", "Tue", "Wed", "Thu", "Fri"] and interval == 1 else f"{text} on {', '.join(days)}"
    if parts.get("COUNT"):
        text += f", {parts['COUNT']} times"
    elif parts.get("UNTIL"):
        raw = parts["UNTIL"][:8]
        try:
            text += f", until {datetime.strptime(raw, '%Y%m%d').strftime('%b %-d, %Y')}"
        except ValueError:
            pass
    return text


def _alerts(component) -> tuple[int, ...]:
    minutes = []
    try:
        alarms = component.get_all_alarms() or []
    except Exception:
        return ()
    for alarm in alarms:
        try:
            trigger = alarm.get_trigger()
            if trigger is None:
                continue
            duration = trigger.get_duration()
            if duration is None:
                continue
            seconds = duration.as_int()
            if seconds <= 0:
                minutes.append(-seconds // 60)
        except Exception:
            continue
    return tuple(sorted(set(minutes)))


def _rid_of(component) -> str:
    """The RECURRENCE-ID of an occurrence as EDS names it: a UTC time or a date."""
    try:
        prop = component.get_first_property(_ical_kind("RECURRENCEID_PROPERTY"))
    except Exception:
        return ""
    if prop is None:
        return ""
    value = prop.get_recurrenceid()
    if value is None or value.is_null_time():
        return ""
    if value.is_date():
        return "%04d%02d%02d" % (value.get_year(), value.get_month(), value.get_day())
    naive = datetime(value.get_year(), value.get_month(), value.get_day(),
                     value.get_hour(), value.get_minute(), value.get_second())
    if value.is_utc():
        moment = naive.replace(tzinfo=timezone.utc)
    else:
        _ds, _cal, ical = _modules()
        parameter = prop.get_first_parameter(ical.ParameterKind.TZID_PARAMETER)
        tzid = _bare_tzid(parameter.get_tzid()) if parameter is not None else ""
        moment = naive.replace(tzinfo=_tzinfo(tzid)) if tzid else naive.astimezone()
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _ical_kind(name: str):
    _ds, _cal, ical = _modules()
    return getattr(ical.PropertyKind, name)


def _rid_for(instance_start, all_day: bool) -> str:
    """The RECURRENCE-ID string EDS uses for an occurrence: UTC, or a date."""
    moment = _to_datetime(instance_start, datetime.now().astimezone())
    if all_day:
        return moment.strftime("%Y%m%d")
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


#: How long one calendar is given to answer when it is opened.
#:
#: Thirty seconds is right for the Calendar application: a person waiting on a
#: slow or remote calendar would rather it arrived than be told nothing. It is
#: wrong for anything reading in the background at login. Each source is opened
#: in turn, so two calendars that are not ready yet cost a full minute before
#: the first read even returns -- measured at 60.1s on a cold session, against
#: 0.03s once the calendar service is up. That minute was the whole reason the
#: upcoming-event island was missing after a login and appeared, unprompted, a
#: minute later.
CONNECT_TIMEOUT_SECONDS = 30

#: For a reader that can simply come back: long enough for a calendar service
#: that is up, short enough that one which is not costs almost nothing.
BACKGROUND_CONNECT_TIMEOUT_SECONDS = 4


def list_events(start: datetime, end: datetime,
                sources: tuple[CalendarSource, ...] | None = None,
                *, timeout: int = CONNECT_TIMEOUT_SECONDS) -> tuple[Event, ...]:
    """Every occurrence between two moments, expanded by EDS.

    `timeout` is how long a single calendar is given to answer. The default
    suits somebody looking at the Calendar application, which should not
    abandon a slow source. A background publisher wants a much shorter one:
    see CONNECT_TIMEOUT_SECONDS.
    """
    data_server, cal, _ical = _modules()
    registry = _registry()
    wanted = {source.uid: source for source in (sources if sources is not None else list_sources())}
    found: list[Event] = []

    for source in registry.list_sources(data_server.SOURCE_EXTENSION_CALENDAR):
        known = wanted.get(source.get_uid())
        if known is None:
            continue
        try:
            client = cal.Client.connect_sync(
                source, cal.ClientSourceType.EVENTS, timeout, None)
        except Exception:
            continue
        writable = known.writable and not client.is_readonly()
        try:
            identity_result=client.get_backend_property_sync(cal.BACKEND_PROPERTY_CAL_EMAIL_ADDRESS,None)
            user_email=(identity_result[1] or "").removeprefix("mailto:") if identity_result[0] else ""
        except Exception:
            user_email=""

        def collect(component, instance_start, instance_end, *_rest) -> bool:
            try:
                comp = component
                if not hasattr(comp, "get_dtstart") or hasattr(comp, "clone"):
                    comp = cal.Component.new_from_icalcomponent(component.clone())
                summary = comp.get_summary()
                title = summary.get_value() if summary is not None else ""
                begins = _to_datetime(instance_start, start)
                finishes = _to_datetime(instance_end, begins)
                all_day = _is_date(instance_start)
                descriptions = comp.get_descriptions() or []
                description = ""
                for entry in descriptions:
                    text = entry.get_value() if entry is not None else ""
                    if text:
                        description = text
                        break
                rules = _rrule_strings(comp)
                rid = _rid_of(component)
                recurring = bool(rules) or bool(rid)
                try:
                    extra = bool(comp.get_rdates() or comp.get_exrules())
                except Exception:
                    extra = False
                url = ""
                try:
                    url = comp.get_url() or ""
                except Exception:
                    pass
                series_rules = rules or series_rules_for.get(comp.get_uid() or "", [])
                found.append(Event(
                    uid=comp.get_uid() or "",
                    source_uid=source.get_uid(),
                    summary=title or "Untitled event",
                    start=begins,
                    end=finishes,
                    all_day=all_day,
                    location=comp.get_location() or "",
                    description=description,
                    participants=_participants(comp),
                    attendees=_attendee_records(comp),
                    user_email=user_email,
                    organizer_email=(comp.get_organizer().get_value() or "").removeprefix("mailto:") if comp.get_organizer() else "",
                    recurring=recurring,
                    zone_label=_component_zone_label(comp),
                    editable=writable,
                    instance_start=begins,
                    rid=rid,
                    repeat=repeat_key(series_rules, extra) if series_rules else "none",
                    repeat_label=describe_rule(series_rules[0]) if len(series_rules) == 1 else ("Repeats" if recurring else ""),
                    alerts=_alerts(comp),
                    url=url,
                    tzid=_bare_tzid(_component_tzid(comp)),
                ))
            except Exception as error:  # pragma: no cover - defensive
                _report_skip(error)
            return True

        # Each series is expanded on its own. The whole-calendar expansion
        # (generate_instances_sync) does not apply a changed occurrence, so a
        # single edited meeting would still show as the original.
        begin_stamp, end_stamp = int(start.timestamp()), int(end.timestamp())
        query = ('(occur-in-time-range? (make-time "%s") (make-time "%s"))'
                 % (start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                    end.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")))
        try:
            listed = client.get_object_list_sync(query, None)
            components = listed[1] if isinstance(listed, tuple) else listed
        except Exception:
            continue
        masters: dict[str, object] = {}
        overrides: list[object] = []
        for component in components or []:
            if component.get_first_property(_ical.PropertyKind.RECURRENCEID_PROPERTY) is None:
                masters[component.get_uid()] = component
            else:
                overrides.append(component)
        series_rules_for = {uid: _rrule_strings(cal.Component.new_from_icalcomponent(master.clone()))
                            for uid, master in masters.items()}
        for uid_key, master in masters.items():
            try:
                client.generate_instances_for_object_sync(master, begin_stamp, end_stamp, None, collect, None)
            except Exception as error:
                _report_skip(error)
        # An occurrence moved into this range whose series starts outside it.
        for override in overrides:
            if override.get_uid() in masters:
                continue
            try:
                client.generate_instances_for_object_sync(override, begin_stamp, end_stamp, None, collect, None)
            except Exception as error:
                _report_skip(error)

    found.sort(key=lambda event: (event.start, event.summary.casefold()))
    return tuple(found)


# ── Writing ───────────────────────────────────────────────────────────────

def _client(source_uid: str, timeout: int = 30):
    data_server, cal, ical = _modules()
    registry = _registry()
    source = registry.ref_source(source_uid)
    if source is None:
        raise ValueError("That calendar is no longer available.")
    try:
        client = cal.Client.connect_sync(source, cal.ClientSourceType.EVENTS, timeout, None)
    except Exception as error:
        raise CalendarUnavailable(f"{source.get_display_name()} could not be opened: {error}") from error
    return client, source, cal, ical


def _fetch(client, uid: str, rid: str = ""):
    fetched = client.get_object_sync(uid, rid or None, None)
    return fetched[1] if isinstance(fetched, tuple) else fetched


def _datetime_value(cal, ical, client, moment: datetime, *, all_day: bool, tzid: str):
    """An ECal.ComponentDateTime in the zone the event is kept in."""
    if all_day:
        value = ical.Time.new_null_date()
        value.set_date(moment.year, moment.month, moment.day)
        value.set_is_date(True)
        return cal.ComponentDateTime.new(value, None)
    zone_name = tzid or local_tzid()
    local = moment.astimezone(_tzinfo(zone_name)) if moment.tzinfo else moment.replace(tzinfo=_tzinfo(zone_name))
    if zone_name == "UTC":
        value = ical.Time.new_null_time()
        value.set_date(local.year, local.month, local.day)
        value.set_time(local.hour, local.minute, local.second)
        value.set_timezone(ical.Timezone.get_utc_timezone())
        return cal.ComponentDateTime.new(value, "UTC")
    zone = _ical_zone(ical, zone_name)
    try:
        client.add_timezone_sync(zone, None)
    except Exception:
        pass
    value = ical.Time.new_null_time()
    value.set_date(local.year, local.month, local.day)
    value.set_time(local.hour, local.minute, local.second)
    value.set_timezone(zone)
    return cal.ComponentDateTime.new(value, zone.get_tzid())


def _stored_datetime(cdt, client, ical, fallback: datetime) -> datetime:
    """A stored DTSTART as an aware datetime, resolving its TZID properly.

    The ICalTime inside a ComponentDateTime does not carry the zone; the TZID
    sits beside it. Reading the time without it treats 09:00 Chicago as 09:00
    UTC, which is how a moved series lands five hours off.
    """
    if cdt is None:
        return fallback
    value = cdt.get_value()
    if value is None:
        return fallback
    if value.is_date():
        return datetime(value.get_year(), value.get_month(), value.get_day()).astimezone()
    naive = datetime(value.get_year(), value.get_month(), value.get_day(),
                     value.get_hour(), value.get_minute(), value.get_second())
    if value.is_utc():
        return naive.replace(tzinfo=timezone.utc)
    tzid = cdt.get_tzid() or ""
    if tzid:
        zone = _tzinfo(_bare_tzid(tzid))
        if zone is not timezone.utc or _bare_tzid(tzid) == "UTC":
            return naive.replace(tzinfo=zone)
        try:
            found = client.get_timezone_sync(tzid, None)
            ical_zone = found[1] if isinstance(found, tuple) else found
            value.set_timezone(ical_zone)
            return datetime.fromtimestamp(value.as_timet_with_zone(ical_zone), tz=timezone.utc)
        except Exception:
            pass
    return naive.astimezone()


def _now_utc(ical):
    return ical.Time.new_current_with_zone(ical.Timezone.get_utc_timezone())


def _apply_draft(comp, draft: EventDraft, cal, ical, client, *, rules: bool = True) -> None:
    changes = draft.changed_fields
    changed = lambda key: changes is None or key in changes
    if changed("summary"):
        comp.set_summary(cal.ComponentText.new(draft.summary, None))
    if changed("location"):
        comp.set_location(draft.location or None)
    if changed("description"):
        comp.set_descriptions([cal.ComponentText.new(draft.description, None)] if draft.description else [])
    if changed("url"):
        comp.set_url(draft.url or None)
    if any(changed(key) for key in ("start", "end", "all_day", "tzid")):
        start, end = draft.start, draft.end
        if draft.all_day:
            first = start.date() if isinstance(start, datetime) else start
            last = end.date() if isinstance(end, datetime) else end
            # The editor speaks inclusively ("the 4th to the 5th"); iCalendar's
            # DTEND for a date is the day after.
            exclusive = max(last, first) + timedelta(days=1)
            comp.set_dtstart(_datetime_value(cal, ical, client, datetime.combine(first, clock_time()), all_day=True, tzid=""))
            comp.set_dtend(_datetime_value(cal, ical, client, datetime.combine(exclusive, clock_time()), all_day=True, tzid=""))
        else:
            if end <= start:
                end = start + timedelta(hours=1)
            if changed("start") or changed("all_day") or changed("tzid"):
                comp.set_dtstart(_datetime_value(cal, ical, client, start, all_day=False, tzid=draft.tzid))
            if changed("end") or changed("all_day") or changed("tzid"):
                comp.set_dtend(_datetime_value(cal, ical, client, end, all_day=False, tzid=draft.tzid))
    if rules and changed("repeat") and draft.repeat != REPEAT_CUSTOM:
        rule = dict((key, value) for key, _label, value in REPEATS).get(draft.repeat, "")
        comp.set_rrules([ical.Recurrence.new_from_string(rule)] if rule else [])
    if changed("alerts"):
        comp.remove_all_alarms()
        for minutes in sorted(set(draft.alerts)):
            alarm = cal.ComponentAlarm.new()
            alarm.set_action(cal.ComponentAlarmAction.DISPLAY)
            alarm.set_description(cal.ComponentText.new(draft.summary, None))
            trigger = cal.ComponentAlarmTrigger.new_relative(
                cal.ComponentAlarmTriggerKind.RELATIVE_START, ical.Duration.new_from_int(-minutes * 60))
            alarm.take_trigger(trigger) if hasattr(alarm, "take_trigger") else alarm.set_trigger(trigger)
            comp.add_alarm(alarm)
    if draft.attendees is not None and changed("attendees"):
        _set_invitees(comp, draft.attendees, cal, ical)
    if draft.organizer_email and not comp.get_organizer():
        organizer = cal.ComponentOrganizer.new()
        organizer.set_value("mailto:" + draft.organizer_email)
        comp.set_organizer(organizer)
    comp.set_last_modified(_now_utc(ical))
    comp.set_dtstamp(_now_utc(ical))


def _bump_sequence(comp) -> None:
    try:
        comp.set_sequence(max(comp.get_sequence(), 0) + 1)
    except Exception:
        pass


def save_event(source_uid: str | EventDraft, summary: str = "", start: datetime | None = None,
               end: datetime | None = None, *, uid: str = "", location: str = "",
               description: str = "", all_day: bool = False, scope: str = SCOPE_ALL) -> str:
    """Create or update an event. Returns its UID.

    Accepts an EventDraft, or the older positional form. For an occurrence of
    a repeating event `scope` says what changes: SCOPE_THIS, SCOPE_FUTURE or
    SCOPE_ALL. Moving a whole series by an occurrence moves every occurrence
    by the same amount, as every calendar does.
    """
    if isinstance(source_uid, EventDraft):
        draft = source_uid
    else:
        draft = EventDraft(source_uid=source_uid, summary=summary, start=start, end=end,
                           all_day=all_day, uid=uid, location=location, description=description,
                           tzid=local_tzid())
    if not draft.summary.strip():
        raise ValueError("The event needs a title.")
    client, _source, cal, ical = _client(draft.source_uid)

    if not draft.uid:
        comp = cal.Component.new_vtype(cal.ComponentVType.EVENT)
        comp.set_uid(f"{uuid.uuid4()}")
        comp.set_created(_now_utc(ical))
        _apply_draft(comp, draft, cal, ical, client)
        created = client.create_object_sync(comp.get_icalcomponent(), 0, None)
        if isinstance(created, tuple):
            return created[1] or comp.get_uid()
        return created or comp.get_uid()

    master_ical = _fetch(client, draft.uid)
    master = cal.Component.new_from_icalcomponent(master_ical.clone())
    recurring = bool(master.has_recurrences()) or bool(draft.rid)

    if not recurring:
        _apply_draft(master, draft, cal, ical, client)
        _bump_sequence(master)
        client.modify_object_sync(master.get_icalcomponent(), cal.ObjModType.THIS, 0, None)
        return draft.uid

    if scope == SCOPE_THIS:
        if not draft.rid:
            raise ValueError("Choose which occurrence to change.")
        try:
            existing = _fetch(client, draft.uid, draft.rid)
            comp = cal.Component.new_from_icalcomponent(existing.clone())
        except Exception as error:
            if not (hasattr(error,"matches") and error.matches(cal.Client.error_quark(),cal.ClientError.OBJECT_NOT_FOUND)):
                raise
            comp = cal.Component.new_from_icalcomponent(master_ical.clone())
        if draft.instance_start is not None:
            original_start=_stored_datetime(comp.get_dtstart(),client,ical,draft.start)
            original_end=_stored_datetime(comp.get_dtend(),client,ical,draft.end)
            if not comp.get_recurid():
                zone_name=_bare_tzid(master.get_dtstart().get_tzid() or "") or "UTC"
                comp.set_dtstart(_datetime_value(cal,ical,client,draft.instance_start,all_day=draft.all_day,tzid=zone_name))
                comp.set_dtend(_datetime_value(cal,ical,client,draft.instance_start+(original_end-original_start),all_day=draft.all_day,tzid=zone_name))
        comp.set_rrules([])
        try:
            comp.set_rdates([])
            comp.set_exdates([])
        except Exception:
            pass
        # RFC 5545: RECURRENCE-ID has the value type and zone of the series'
        # DTSTART, so the override matches the occurrence it replaces.
        master_start = master.get_dtstart()
        occurrence = draft.instance_start or datetime.strptime(draft.rid.rstrip("Z"), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        if master_start is not None and master_start.get_value().is_date():
            rid_value = _datetime_value(cal, ical, client, datetime.combine(occurrence.date(), clock_time()), all_day=True, tzid="")
        else:
            zone_name = _bare_tzid(master_start.get_tzid() or "") if master_start is not None else ""
            rid_value = _datetime_value(cal, ical, client, occurrence, all_day=False, tzid=zone_name or "UTC")
        comp.set_recurid(cal.ComponentRange.new(cal.ComponentRangeKind.SINGLE, rid_value))
        _apply_draft(comp, draft, cal, ical, client, rules=False)
        _bump_sequence(comp)
        client.modify_object_sync(comp.get_icalcomponent(), cal.ObjModType.THIS, 0, None)
        return draft.uid

    if scope == SCOPE_FUTURE and draft.instance_start is not None:
        series_start = _stored_datetime(master.get_dtstart(), client, ical, draft.instance_start)
        if draft.instance_start.date() > series_start.date():
            original_rules = _rrule_strings(master)
            _end_series_before(master, draft.instance_start, cal, ical)
            _bump_sequence(master)
            follower = cal.Component.new_from_icalcomponent(master_ical.clone())
            follower.set_uid(f"{uuid.uuid4()}")
            follower.set_created(_now_utc(ical))
            try:
                follower.set_exdates([])
            except Exception:
                pass
            # Keep the original rule's shape but drop an UNTIL that was just
            # used to end the first half; a COUNT restarts from here.
            rules = [re.sub(r";?UNTIL=[^;]+", "", rule) for rule in _rrule_strings(follower)]
            follower.set_rrules([ical.Recurrence.new_from_string(rule) for rule in rules if rule])
            if draft.changed_fields is not None:
                stored_end = _stored_datetime(follower.get_dtend(),client,ical,series_start)
                duration = ((stored_end.date()-series_start.date())-timedelta(days=1) if draft.all_day
                            else stored_end-series_start)
                following_start = draft.start if "start" in draft.changed_fields else draft.instance_start
                following_end = draft.end if "end" in draft.changed_fields else following_start+duration
                draft = EventDraft(**{**draft.__dict__, "start":following_start,"end":following_end,
                                      "changed_fields":draft.changed_fields | {"start","end"}})
            _apply_draft(follower, draft, cal, ical, client, rules=draft.repeat not in {REPEAT_CUSTOM})
            created = client.create_object_sync(follower.get_icalcomponent(), 0, None)
            following_uid = (created[1] if isinstance(created, tuple) else created) or follower.get_uid()
            try:
                client.modify_object_sync(master.get_icalcomponent(), cal.ObjModType.ALL, 0, None)
            except Exception as error:
                try:
                    # A failed response might follow a committed update. Restore
                    # only the rule we changed, retaining fresh server fields.
                    current = cal.Component.new_from_icalcomponent(_fetch(client,draft.uid).clone())
                    current.set_rrules([ical.Recurrence.new_from_string(rule) for rule in original_rules])
                    _bump_sequence(current)
                    client.modify_object_sync(current.get_icalcomponent(),cal.ObjModType.ALL,0,None)
                    client.remove_object_sync(following_uid,None,cal.ObjModType.ALL,0,None)
                except Exception as rollback:
                    raise CalendarPartialSave("The new series exists, but Calendar could not finish or undo the split. "
                                              "Refresh before editing again. New event: "+str(following_uid),following_uid) from rollback
                raise error
            return following_uid

    # Every occurrence: shift the series by however far this occurrence moved.
    if draft.instance_start is not None and not draft.all_day:
        offset = draft.start - draft.instance_start.astimezone(draft.start.tzinfo or timezone.utc)
        length = draft.end - draft.start
        first = _stored_datetime(master.get_dtstart(), client, ical, draft.start)
        first_local = first.astimezone(_tzinfo(draft.tzid or local_tzid()))
        draft = EventDraft(**{**draft.__dict__, "start": first_local + offset, "end": first_local + offset + length})
    elif draft.instance_start is not None and draft.all_day:
        offset = draft.start.date() - draft.instance_start.date() if isinstance(draft.start, datetime) else timedelta()
        length = draft.end.date() - draft.start.date() if isinstance(draft.start, datetime) else timedelta()
        first = _stored_datetime(master.get_dtstart(), client, ical, draft.start)
        moved = datetime.combine(first.date() + offset, clock_time())
        draft = EventDraft(**{**draft.__dict__, "start": moved, "end": moved + length})
    _apply_draft(master, draft, cal, ical, client)
    _bump_sequence(master)
    client.modify_object_sync(master.get_icalcomponent(), cal.ObjModType.ALL, 0, None)
    return draft.uid


def _end_series_before(master, instance_start: datetime, cal, ical) -> None:
    """End a series with the occurrence before `instance_start` (RRULE UNTIL)."""
    until = (instance_start - timedelta(seconds=1)).astimezone(timezone.utc)
    rules = []
    for rule in _rrule_strings(master):
        rule = re.sub(r";?(UNTIL|COUNT)=[^;]+", "", rule)
        if _is_date(master.get_dtstart().get_value()):
            rule += f";UNTIL={(instance_start.date() - timedelta(days=1)).strftime('%Y%m%d')}"
        else:
            rule += f";UNTIL={until.strftime('%Y%m%dT%H%M%SZ')}"
        rules.append(ical.Recurrence.new_from_string(rule))
    master.set_rrules(rules)


def delete_event(source_uid: str, uid: str, *, rid: str = "", scope: str = SCOPE_ALL,
                 instance_start: datetime | None = None) -> None:
    client, _source, cal, ical = _client(source_uid)
    if rid and scope == SCOPE_THIS:
        client.remove_object_sync(uid, rid, cal.ObjModType.THIS, 0, None)
        return
    if rid and scope == SCOPE_FUTURE and instance_start is not None:
        master = cal.Component.new_from_icalcomponent(_fetch(client, uid).clone())
        series_start = _stored_datetime(master.get_dtstart(), client, ical, instance_start)
        if instance_start.date() > series_start.date():
            _end_series_before(master, instance_start, cal, ical)
            _bump_sequence(master)
            client.modify_object_sync(master.get_icalcomponent(), cal.ObjModType.ALL, 0, None)
            return
    client.remove_object_sync(uid, None, cal.ObjModType.ALL, 0, None)


def component_text(source_uid: str, uid: str, *, allow_missing: bool = False) -> str:
    """A stored series and its overrides, preserving every property for Undo."""
    client, _source, _cal, ical = _client(source_uid)
    result=client.get_object_list_sync("#t",None)
    if isinstance(result,tuple) and not result[0]:
        raise CalendarUnavailable("The calendar could not verify its stored events.")
    components=result[1] if isinstance(result,tuple) else result
    root=ical.Component.new_vcalendar()
    root.add_property(ical.Property.new_version("2.0"))
    root.add_property(ical.Property.new_prodid("-//Project Luma//Calendar//EN"))
    found=False
    for component in components or ():
        if component.get_uid()==uid:
            root.add_component(component.clone())
            found=True
    if not found and not allow_missing:
        raise ValueError("This event is no longer in the calendar.")
    return root.as_ical_string()


def restore_deletion(source_uid: str, before_text: str, after_text: str) -> int:
    """Undo only this deletion, preserving fresh fields in a surviving series.

    Absent events are recreated, never upserted. For a surviving master or
    override, only recurrence/exclusion/status properties changed by deletion
    are restored, after comparing them with the recorded post-delete state.
    Validate the whole plan before writing. Provider failures after a partial
    restore are reported explicitly; no stale full-object rollback is attempted.
    """
    if after_text is None:
        raise ValueError("The post-delete state could not be verified. The backup is preserved; Undo cannot safely write.")
    client, _source, cal, ical = _client(source_uid)
    if client.is_readonly():
        raise ValueError("This calendar is read-only.")

    def key(component):
        return component.get_uid(), _rid_of(component)

    def snapshot(text):
        root = ical.Component.new_from_string(text)
        if root is None or root.isa() != ical.ComponentKind.VCALENDAR_COMPONENT:
            raise ValueError("The deletion snapshot is not a complete calendar.")
        components = {}
        child = root.get_first_component(ical.ComponentKind.VEVENT_COMPONENT)
        while child is not None:
            identity = key(child)
            if not identity[0] or identity in components:
                raise ValueError("The deletion snapshot has an ambiguous event identity.")
            components[identity] = child.clone()
            child = root.get_next_component(ical.ComponentKind.VEVENT_COMPONENT)
        return components

    def properties(component, kind):
        values = []
        prop = component.get_first_property(kind)
        while prop is not None:
            values.append(prop.clone())
            prop = component.get_next_property(kind)
        return values

    def strings(values):
        return sorted(prop.as_ical_string() for prop in values)

    before, after = snapshot(before_text), snapshot(after_text)
    uids = {identity[0] for identity in before}
    if len(uids) != 1 or any(identity[0] not in uids for identity in after):
        raise ValueError("The deletion snapshot does not identify one event series.")
    result = client.get_object_list_sync("#t", None)
    if isinstance(result, tuple) and not result[0]:
        raise CalendarUnavailable("The calendar could not verify its current events for Undo.")
    stored = result[1] if isinstance(result, tuple) else result
    current = {key(component): component.clone() for component in stored or ()
               if component.get_uid() in uids}
    kinds = tuple(getattr(ical.PropertyKind, name) for name in
                  ("RRULE_PROPERTY", "RDATE_PROPERTY", "EXDATE_PROPERTY", "STATUS_PROPERTY"))
    plan = []
    for identity, original in before.items():
        post = after.get(identity)
        fresh = current.get(identity)
        if post is None:
            if fresh is not None:
                raise ValueError("This event already exists again. Undo will not overwrite it.")
            plan.append(("override" if identity[1] else "create", original.clone()))
            continue
        changed = [(kind, properties(original, kind), properties(post, kind)) for kind in kinds]
        changed = [(kind, old, new) for kind, old, new in changed if strings(old) != strings(new)]
        if not changed:
            continue
        if fresh is None:
            raise ValueError("This series changed after deletion and is no longer available.")
        restored = fresh.clone()
        needs_write = False
        for kind, old, new in changed:
            now = properties(fresh, kind)
            if strings(now) == strings(old):
                continue  # Another client already undid this property.
            if strings(now) != strings(new):
                raise ValueError("This event’s recurrence changed after deletion. Undo will not overwrite it.")
            for _ in properties(restored, kind):
                # Remove the owned properties, not the detached clones above.
                owned = restored.get_first_property(kind)
                restored.remove_property(owned)
            for prop in old:
                restored.add_property(prop.clone())
            needs_write = True
        if needs_write:
            wrapped = cal.Component.new_from_icalcomponent(restored)
            _bump_sequence(wrapped)
            wrapped.set_dtstamp(_now_utc(ical))
            wrapped.set_last_modified(_now_utc(ical))
            plan.append(("modify-this" if identity[1] else "modify-all", wrapped.get_icalcomponent().clone()))
    plan.sort(key=lambda item: (item[0] != "create", bool(key(item[1])[1])))
    completed = 0
    for operation, component in plan:
        try:
            if operation == "create":
                client.create_object_sync(component, 0, None)
            else:
                scope = cal.ObjModType.ALL if operation == "modify-all" else cal.ObjModType.THIS
                client.modify_object_sync(component, scope, 0, None)
        except Exception as error:
            if completed:
                raise RuntimeError(f"Undo restored {completed} part(s), then stopped: {error}. Refresh before retrying; the backup is preserved.") from error
            raise
        completed += 1
    return completed


# ── Calendars ─────────────────────────────────────────────────────────────

def _commit(registry, source) -> str:
    registry.commit_source_sync(source, None)
    return source.get_uid()


def create_calendar(name: str, *, tone: str = "blue", kind: str = "local", url: str = "",
                    user: str = "", password: str = "", uid: str = "",
                    refresh_minutes: int = 60) -> str:
    """Add a calendar: kept on this device, a subscription to a published
    calendar (webcal/.ics), or a calendar on a CalDAV server."""
    data_server, _cal, _ical = _modules()
    registry = _registry()
    name = name.strip()
    if not name:
        raise ValueError("Give the calendar a name.")
    source = data_server.Source.new_with_uid(uid, None) if uid else data_server.Source.new(None, None)
    source.set_display_name(name)
    extension = source.get_extension(data_server.SOURCE_EXTENSION_CALENDAR)
    extension.set_color(TONE_COLOURS.get(tone, tone if tone.startswith("#") else TONE_COLOURS["blue"]))
    if kind == "local":
        source.set_parent("local-stub")
        extension.set_backend_name("local")
    elif kind in {"subscription", "caldav", "luma"}:
        address = url.strip()
        if address.lower().startswith("webcal://"):
            address = "https://" + address[len("webcal://"):]
        if not re.match(r"^https?://", address, re.I):
            raise ValueError("Enter a web address that starts with https:// or webcal://.")
        if address.lower().startswith("http://") and kind != "subscription":
            raise ValueError("Calendar accounts need an https:// address.")
        gi.require_version("GLib", "2.0")
        from gi.repository import GLib
        parsed = GLib.Uri.parse(address, GLib.UriFlags.NONE)
        source.set_parent({"subscription": "webcal-stub", "caldav": "caldav-stub", "luma": "caldav-stub"}[kind])
        extension.set_backend_name("webcal" if kind == "subscription" else "caldav")
        webdav = source.get_extension(data_server.SOURCE_EXTENSION_WEBDAV_BACKEND)
        webdav.set_uri(parsed)
        if kind != "subscription":
            webdav.set_calendar_auto_schedule(False)
        auth = source.get_extension(data_server.SOURCE_EXTENSION_AUTHENTICATION)
        auth.set_host(parsed.get_host() or "")
        auth.set_port(parsed.get_port() if parsed.get_port() > 0 else (443 if address.startswith("https") else 80))
        if user:
            auth.set_user(user)
            auth.set_method("plain/password")
        refresh = source.get_extension(data_server.SOURCE_EXTENSION_REFRESH)
        refresh.set_enabled(True)
        refresh.set_interval_minutes(max(5, int(refresh_minutes)))
        offline = source.get_extension(data_server.SOURCE_EXTENSION_OFFLINE)
        offline.set_stay_synchronized(True)
    else:
        raise ValueError(f"Unknown calendar kind: {kind}")
    committed = _commit(registry, source)
    if password:
        stored = registry.ref_source(committed)
        stored.store_password_sync(password, True, None)
    return committed


def rename_calendar(source_uid: str, name: str, tone: str = "") -> None:
    data_server, _cal, _ical = _modules()
    registry = _registry()
    source = registry.ref_source(source_uid)
    if source is None:
        raise ValueError("That calendar is no longer available.")
    if name.strip():
        source.set_display_name(name.strip())
    if tone:
        source.get_extension(data_server.SOURCE_EXTENSION_CALENDAR).set_color(TONE_COLOURS.get(tone, tone))
    source.write_sync(None)


def remove_calendar(source_uid: str) -> None:
    registry = _registry()
    source = registry.ref_source(source_uid)
    if source is None:
        return
    if not source.get_removable():
        raise ValueError(f"{source.get_display_name()} cannot be removed here.")
    source.remove_sync(None)


def set_default_calendar(source_uid: str) -> None:
    registry = _registry()
    source = registry.ref_source(source_uid)
    if source is not None:
        registry.set_default_calendar(source)


def import_ics(source_uid: str, text: str) -> int:
    """Add every event in an iCalendar file to a calendar. Returns the count.

    Time zones are added first so every TZID resolves; a series is written
    before its overrides; an event whose UID is already there is updated
    rather than duplicated.
    """
    client, _source, cal, ical = _client(source_uid)
    try:
        root = ical.Component.new_from_string(text)
    except Exception as error:
        raise ValueError("That file is not an iCalendar (.ics) file.") from error
    if root is None:
        raise ValueError("That file is not an iCalendar (.ics) file.")
    events, zones = [], []
    kind = root.isa()
    if kind == ical.ComponentKind.VEVENT_COMPONENT:
        events.append(root)
    else:
        child = root.get_first_component(ical.ComponentKind.VTIMEZONE_COMPONENT)
        while child is not None:
            zones.append(child)
            child = root.get_next_component(ical.ComponentKind.VTIMEZONE_COMPONENT)
        child = root.get_first_component(ical.ComponentKind.VEVENT_COMPONENT)
        while child is not None:
            events.append(child)
            child = root.get_next_component(ical.ComponentKind.VEVENT_COMPONENT)
    for zone_component in zones:
        try:
            zone = ical.Timezone.new()
            zone.set_component(zone_component.clone())
            client.add_timezone_sync(zone, None)
        except Exception:
            continue
    events.sort(key=lambda component: component.get_first_property(ical.PropertyKind.RECURRENCEID_PROPERTY) is not None)
    count = 0
    for event in events:
        component = event.clone()
        if not component.get_uid():
            component.set_uid(f"{uuid.uuid4()}")
        is_override = component.get_first_property(ical.PropertyKind.RECURRENCEID_PROPERTY) is not None
        try:
            if is_override:
                client.modify_object_sync(component, cal.ObjModType.THIS, 0, None)
            else:
                client.create_object_sync(component, 0, None)
        except Exception as error:
            if "exist" in str(error).lower() or "already" in str(error).lower():
                client.modify_object_sync(component, cal.ObjModType.ALL, 0, None)
            else:
                raise
        count += 1
    return count


def export_event_ics(source_uid: str, uid: str) -> str:
    """A stored event/series, retaining time zones, alarms and server fields."""
    _client_unused, _source, _cal, ical = _client(source_uid)
    whole = ical.Component.new_from_string(export_ics(source_uid))
    root = ical.Component.new_vcalendar()
    root.add_property(ical.Property.new_version('2.0'))
    root.add_property(ical.Property.new_prodid('-//Project Luma//Calendar//EN'))
    found = False
    component = whole.get_first_component(ical.ComponentKind.ANY_COMPONENT)
    while component is not None:
        if component.isa() == ical.ComponentKind.VTIMEZONE_COMPONENT:
            root.add_component(component.clone())
        elif component.isa() == ical.ComponentKind.VEVENT_COMPONENT and component.get_uid() == uid:
            root.add_component(component.clone()); found = True
        component = whole.get_next_component(ical.ComponentKind.ANY_COMPONENT)
    if not found:
        raise ValueError('This event is no longer in the calendar.')
    return root.as_ical_string()


def export_ics(source_uid: str) -> str:
    """The whole calendar as one iCalendar file, time zones included."""
    client, source, cal, ical = _client(source_uid)
    result = client.get_object_list_sync("#t", None)
    components = result[1] if isinstance(result, tuple) else result
    root = ical.Component.new_vcalendar()
    root.add_property(ical.Property.new_version("2.0"))
    root.add_property(ical.Property.new_prodid("-//Project Luma//Calendar//EN"))
    root.add_property(ical.Property.new_calscale("GREGORIAN"))
    try:
        name = ical.Property.new_x(source.get_display_name())
        name.set_x_name("X-WR-CALNAME")
        root.add_property(name)
    except Exception:
        pass
    seen = set()
    for component in components or []:
        for getter in (ical.PropertyKind.DTSTART_PROPERTY, ical.PropertyKind.DTEND_PROPERTY):
            prop = component.get_first_property(getter)
            if prop is None:
                continue
            parameter = prop.get_first_parameter(ical.ParameterKind.TZID_PARAMETER)
            tzid = parameter.get_tzid() if parameter is not None else ""
            if tzid and tzid not in seen:
                seen.add(tzid)
                try:
                    found = client.get_timezone_sync(tzid, None)
                    zone = found[1] if isinstance(found, tuple) else found
                    if zone is not None and zone.get_component() is not None:
                        root.add_component(zone.get_component().clone())
                except Exception:
                    pass
        root.add_component(component.clone())
    return root.as_ical_string()
