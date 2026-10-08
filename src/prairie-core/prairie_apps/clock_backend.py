# SPDX-License-Identifier: Apache-2.0

"""Clock's data and its arithmetic.

Everything in this module is deliberately free of GTK so the parts that are
easy to get wrong — band slicing, calendar-day rollover, fractional offsets,
DST, monotonic timing, alarm recurrence — can be tested without a display.

The one idea worth repeating here, because the rest of the file exists to
serve it: the World tab's bands all share ONE absolute time axis. The axis is
the next 24 hours of *your* day starting at your local midnight, and every
city's band is that same interval expressed in that city's local hours. A
per-row axis would draw six identical pictures.
"""

from __future__ import annotations

import json
import locale as _locale
import os
import re
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MINUS = "−"  # U+2212. Not a hyphen; a hyphen in a signed offset is a typo.

# ---------------------------------------------------------------------------
# Day/night tones
# ---------------------------------------------------------------------------

NIGHT = "night"
TWILIGHT = "twilight"
DAY = "day"

TONE_COLOURS = {NIGHT: "#59636f", TWILIGHT: "#9aa4b0", DAY: "#dfe5ec"}
TONE_WORDS = {NIGHT: "night", TWILIGHT: "twilight", DAY: "daytime"}

# The axis is cut into 15-minute slices, and that number is not arbitrary.
# Every real UTC offset is a whole multiple of 15 minutes (+5:30 India, +5:45
# Nepal, +9:30 Adelaide, +12:45 Chatham), so every tone boundary a city can
# produce lands exactly on a slice edge. A coarser grid would round Kathmandu
# onto the hour; a finer one would buy nothing.
SLICE_MINUTES = 15
SLICES = 24 * 60 // SLICE_MINUTES

ASLEEP_FROM, ASLEEP_UNTIL = 22, 6


def tone_for_hour(hour: int) -> str:
    """22:00–05:59 night · 06:00–08:59 and 18:00–21:59 twilight · 09:00–17:59 day."""
    hour %= 24
    if hour >= 22 or hour < 6:
        return NIGHT
    if hour < 9 or hour >= 18:
        return TWILIGHT
    return DAY


@dataclass(frozen=True)
class BandRun:
    """One merged run of consecutive same-tone slices, as fractions of the axis."""

    start: float
    end: float
    tone: str

    @property
    def colour(self) -> str:
        return TONE_COLOURS[self.tone]


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------


def home_zone_name() -> str | None:
    """The IANA id of the system zone, or None if it cannot be named.

    Naming it matters: a fixed offset taken from `datetime.now().astimezone()`
    is frozen at this instant, and the axis runs 24 hours forward. A band drawn
    against a frozen offset is an hour wrong on the two days a year it is most
    obvious.
    """
    candidate = os.environ.get("TZ", "").strip().lstrip(":")
    if candidate and _zone(candidate) is not None:
        return candidate
    try:
        target = os.readlink("/etc/localtime")
    except OSError:
        target = ""
    if "zoneinfo/" in target:
        candidate = target.split("zoneinfo/", 1)[1]
        if _zone(candidate) is not None:
            return candidate
    try:
        candidate = Path("/etc/timezone").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        candidate = ""
    return candidate if candidate and _zone(candidate) is not None else None


def _zone(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def zone_city(zone: str) -> str:
    """`America/Los_Angeles` → `Los Angeles`.

    A shortcut, as the handoff says: it yields `Kolkata` for India and loses the
    diacritic in `Sao_Paulo`. The localised CLDR exemplar city is the right
    answer and is not available to us here.
    """
    return zone.rsplit("/", 1)[-1].replace("_", " ")


def _resolve(zone: str | None, fallback: datetime) -> ZoneInfo | object:
    resolved = _zone(zone) if zone else None
    return resolved if resolved is not None else fallback.astimezone().tzinfo


def _home_tz(now: datetime, home: str | None):
    return _resolve(home if home is not None else home_zone_name(), now)


def zone_offset(zone: str, *, now: datetime | None = None, home: str | None = None) -> timedelta:
    """This city's offset from your zone, at this instant. May be fractional."""
    now = now or datetime.now().astimezone()
    here = now.astimezone(_home_tz(now, home))
    there = now.astimezone(ZoneInfo(zone))
    return there.utcoffset() - here.utcoffset()


def offset_text(zone: str, *, now: datetime | None = None, home: str | None = None) -> str:
    """`Local time` · `+8 hrs` · `+5:45 hrs` · `−5 hrs`. Singular at exactly one."""
    delta = zone_offset(zone, now=now, home=home)
    total = int(delta.total_seconds()) // 60
    if total == 0:
        return "Local time"
    sign = "+" if total > 0 else MINUS
    hours, minutes = divmod(abs(total), 60)
    unit = "hr" if (hours == 1 and minutes == 0) else "hrs"
    body = f"{hours}:{minutes:02d}" if minutes else str(hours)
    return f"{sign}{body} {unit}"


def day_rollover(zone: str, *, now: datetime | None = None, home: str | None = None) -> int:
    """+1 if the city is already on tomorrow, −1 if still on yesterday, else 0.

    A calendar-day comparison, never an hour comparison. Comparing hours puts
    Auckland on the wrong day for several hours out of every one of them.
    """
    now = now or datetime.now().astimezone()
    here = now.astimezone(_home_tz(now, home))
    there = now.astimezone(ZoneInfo(zone))
    return (date(there.year, there.month, there.day) - date(here.year, here.month, here.day)).days


def rollover_chip(delta: int) -> str:
    return f"+{delta} day" if delta > 0 else f"{MINUS}{abs(delta)} day" if delta < 0 else ""


def is_asleep(zone: str, *, now: datetime | None = None) -> bool:
    hour = (now or datetime.now().astimezone()).astimezone(ZoneInfo(zone)).hour
    return hour >= ASLEEP_FROM or hour < ASLEEP_UNTIL


# ---------------------------------------------------------------------------
# The shared axis
# ---------------------------------------------------------------------------


def axis_origin(*, now: datetime | None = None, home: str | None = None) -> datetime:
    """Your local midnight today — the zero of the one axis every band shares."""
    now = now or datetime.now().astimezone()
    here = now.astimezone(_home_tz(now, home))
    return here.replace(hour=0, minute=0, second=0, microsecond=0)


def now_fraction(*, now: datetime | None = None, home: str | None = None) -> float:
    """Where the NOW line goes, 0–1. Computed once and used at the same x in every row."""
    now = now or datetime.now().astimezone()
    here = now.astimezone(_home_tz(now, home))
    return (here.hour + here.minute / 60 + here.second / 3600) / 24


def day_slices(
    zone: str, *, now: datetime | None = None, home: str | None = None
) -> tuple[BandRun, ...]:
    """Slice one city's day onto YOUR axis, merged into runs.

    Walks the axis in wall-clock steps from your midnight, converts each step's
    instant into the city's own zone, and takes the tone of the hour it lands
    in. Because the walk is in real instants converted through both zones, a
    fractional offset slices mid-column on its own and a DST transition in
    either zone is simply where the tone stops matching.

    Consecutive slices of the same tone are merged, so a band is five or six
    runs rather than ninety-six — which is the whole point: it is painted as
    one gradient with hard stops, never as a row of adjacent boxes whose
    fractional widths leave seams that line up into vertical striping down the
    column.
    """
    now = now or datetime.now().astimezone()
    origin = axis_origin(now=now, home=home)
    naive = origin.replace(tzinfo=None)
    home_tz = origin.tzinfo
    city_tz = ZoneInfo(zone)
    step = timedelta(minutes=SLICE_MINUTES)
    half = step / 2

    runs: list[BandRun] = []
    for index in range(SLICES):
        # Sample the middle of the slice: an edge sample sits exactly on a tone
        # boundary and would be decided by rounding rather than by the clock.
        moment = (naive + index * step + half).replace(tzinfo=home_tz)
        tone = tone_for_hour(moment.astimezone(city_tz).hour)
        end = (index + 1) / SLICES
        if runs and runs[-1].tone == tone:
            runs[-1] = replace(runs[-1], end=end)
        else:
            runs.append(BandRun(index / SLICES, end, tone))
    return tuple(runs)


def gradient_stops(runs: tuple[BandRun, ...]) -> tuple[tuple[str, float], ...]:
    """Runs → hard colour stops: two per run, same colour, at its two edges.

    Two stops at the same colour is what makes the boundary hard instead of a
    ramp, and emitting them from merged runs is what keeps a band at a handful
    of stops rather than 48.
    """
    stops: list[tuple[str, float]] = []
    for run in runs:
        stops.append((run.colour, run.start))
        stops.append((run.colour, run.end))
    return tuple(stops)


def band_description(
    label: str, zone: str, *, now: datetime | None = None, home: str | None = None, hour24: bool | None = None
) -> str:
    """The band's text alternative. It is the row's primary content, so it needs one."""
    now = now or datetime.now().astimezone()
    hour24 = uses_24_hour() if hour24 is None else hour24
    there = now.astimezone(ZoneInfo(zone))
    clock, period = format_clock(there, hour24=hour24)
    offset = offset_text(zone, now=now, home=home)
    spoken = "same time as you" if offset == "Local time" else (
        offset.replace(MINUS, "").replace("+", "").replace("hrs", "hours").replace("hr", "hour").strip()
        + (" behind" if offset.startswith(MINUS) else " ahead")
    )
    delta = day_rollover(zone, now=now, home=home)
    day = ", tomorrow" if delta > 0 else ", yesterday" if delta < 0 else ""
    tone = TONE_WORDS[tone_for_hour(there.hour)]
    asleep = ", likely asleep" if is_asleep(zone, now=now) else ""
    return f"{label}, {clock}{' ' + period if period else ''}, {spoken}{day}, {tone}{asleep}."


# ---------------------------------------------------------------------------
# 12- vs 24-hour
# ---------------------------------------------------------------------------


def hour_format_from_pattern(pattern: str) -> bool:
    """True when a locale's time pattern is 24-hour. Split out so it is testable."""
    return "%p" not in pattern and "%I" not in pattern and "%r" not in pattern


def uses_24_hour() -> bool:
    """Follow the system locale. The simulator hard-codes en-US; that is a placeholder."""
    override = os.environ.get("LUMA_CLOCK_HOUR_FORMAT", "").strip()
    if override in {"12", "24"}:
        return override == "24"
    try:
        pattern = _locale.nl_langinfo(_locale.T_FMT)
    except (AttributeError, ValueError):
        return False
    return hour_format_from_pattern(pattern or "%I:%M:%S %p")


def format_clock(moment: datetime, *, hour24: bool) -> tuple[str, str]:
    """(`9:47`, `PM`) or (`21:47`, ``). No seconds — a glanceable surface, not a fidget."""
    if hour24:
        return f"{moment.hour:02d}:{moment.minute:02d}", ""
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d}", "AM" if moment.hour < 12 else "PM"


def axis_ticks(hour24: bool) -> tuple[str, ...]:
    return ("00", "06", "12", "18", "00") if hour24 else ("12a", "6a", "12p", "6p", "12a")


# ---------------------------------------------------------------------------
# Stopwatch and timer — monotonic, always
# ---------------------------------------------------------------------------


class Stopwatch:
    """Elapsed time from the monotonic clock.

    Wall time is not usable here: an NTP step or somebody fixing the system
    clock would add hours to a running stopwatch, and the numbers it reports
    are the only thing it is for.
    """

    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._base = 0.0
        self._started: float | None = None
        self.laps: list[tuple[float, float]] = []  # (split, total), newest last

    @property
    def running(self) -> bool:
        return self._started is not None

    def elapsed(self) -> float:
        extra = self._clock() - self._started if self._started is not None else 0.0
        return self._base + extra

    def start(self) -> None:
        if self._started is None:
            self._started = self._clock()

    def stop(self) -> None:
        if self._started is not None:
            self._base += self._clock() - self._started
            self._started = None

    def reset(self) -> None:
        self._base = 0.0
        self._started = None
        self.laps.clear()

    def lap(self) -> tuple[float, float]:
        """Record a lap and give back (split, total).

        Both, because a lap list that only holds totals cannot answer the one
        question the Lap button exists for.
        """
        total = self.elapsed()
        previous = self.laps[-1][1] if self.laps else 0.0
        entry = (total - previous, total)
        self.laps.append(entry)
        return entry


class Countdown:
    """A timer on the monotonic clock, with the same reasoning as Stopwatch."""

    def __init__(self, duration: float, *, clock=time.monotonic) -> None:
        self._clock = clock
        self.total = float(duration)
        self._remaining = float(duration)
        self._deadline: float | None = None

    @property
    def running(self) -> bool:
        return self._deadline is not None

    def remaining(self) -> float:
        if self._deadline is None:
            return max(0.0, self._remaining)
        return max(0.0, self._deadline - self._clock())

    def finished(self) -> bool:
        return self._deadline is not None and self.remaining() <= 0

    def start(self) -> None:
        if self._deadline is None:
            if self._remaining <= 0:
                self._remaining = self.total
            self._deadline = self._clock() + self._remaining

    def pause(self) -> None:
        if self._deadline is not None:
            self._remaining = self.remaining()
            self._deadline = None

    def reset(self, duration: float | None = None) -> None:
        if duration is not None:
            self.total = float(duration)
        self._remaining = self.total
        self._deadline = None

    def resume_with(self, remaining: float) -> None:
        """Pick a running timer back up part-way through.

        A timer outlives its window, so an app that opens onto one already
        counting has to join it rather than start it: the deadline comes from
        the saved timer record, not from the preset.
        """
        self._remaining = max(0.0, float(remaining))
        self._deadline = self._clock() + self._remaining

    def fraction(self) -> float:
        return 0.0 if self.total <= 0 else max(0.0, min(1.0, self.remaining() / self.total))


def format_stopwatch(value: float) -> str:
    """MM:SS.CC, zero padded, hours folded into minutes past 60."""
    hundredths = int(value * 100)
    minutes, rest = divmod(hundredths, 6000)
    seconds, fraction = divmod(rest, 100)
    return f"{minutes:02d}:{seconds:02d}.{fraction:02d}"


def format_countdown(value: float) -> str:
    """MM:SS, rounded up so a timer reads 00:01 until it is actually done."""
    whole = max(0, int(-(-value // 1)))
    minutes, seconds = divmod(whole, 60)
    return f"{minutes:02d}:{seconds:02d}"


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAYS = (0, 1, 2, 3, 4)
WEEKENDS = (5, 6)
EVERY_DAY = (0, 1, 2, 3, 4, 5, 6)
SOUNDS = ("Chime", "Ripple", "Ascend")


@dataclass(frozen=True)
class WorldClock:
    uid: str
    label: str
    zone: str


@dataclass(frozen=True)
class Alarm:
    uid: str
    label: str
    hour: int
    minute: int
    days: tuple[int, ...] = ()
    enabled: bool = True
    sound: str = SOUNDS[0]
    snooze_minutes: int = 9
    ring_seconds: int = 60

    def repeat_text(self) -> str:
        days = tuple(sorted(set(self.days)))
        if not days:
            return "Once"
        if days == EVERY_DAY:
            return "Every day"
        if days == WEEKDAYS:
            return "Weekdays"
        if days == WEEKENDS:
            return "Weekends"
        return " ".join(WEEKDAY_NAMES[day] for day in days)

    def subtitle(self) -> str:
        """The list's second column: the custom label, else the derived repeat."""
        return self.label or self.repeat_text()


@dataclass(frozen=True)
class TimerRecord:
    uid: str
    label: str
    fires_at: int
    total_seconds: int


def _wall(day: date, hour: int, minute: int, now: datetime) -> datetime:
    """`hour:minute` on `day` as a wall-clock time in the zone `now` stands for.

    A real zone keeps its own rules, so the offset is the one in force on that
    day. A fixed offset equal to this machine's is what
    `datetime.now().astimezone()` gives, and it is frozen at this instant: a
    week-ahead alarm computed with it would ring an hour off across a
    daylight-saving change. That case means "local time", so it is resolved
    through the system zone for that day. Any other fixed offset is kept.
    """
    tz = now.tzinfo
    if isinstance(tz, ZoneInfo):
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
    if now.utcoffset() == now.astimezone().utcoffset():
        return datetime(day.year, day.month, day.day, hour, minute).astimezone()
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)


def next_occurrence(alarm: Alarm, now: datetime | None = None) -> datetime:
    """When this alarm next rings.

    Once: today at the time if it is still ahead, otherwise tomorrow. Repeating:
    the next listed weekday whose time is still ahead, searched over eight days
    so a same-weekday-next-week alarm resolves rather than falling off the end.
    """
    # An alarm's time is a wall-clock time in whatever zone `now` is expressed
    # in, so `now` is used as given rather than converted — converting it would
    # quietly reinterpret 06:30 as 06:30 somewhere else.
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.astimezone()
    days = tuple(sorted(set(alarm.days)))
    today = now.date()
    for ahead in range(9):
        day = today + timedelta(days=ahead)
        if days and day.weekday() not in days:
            continue
        candidate = _wall(day, alarm.hour, alarm.minute, now)
        if candidate > now:
            return candidate
    raise ValueError("Alarm has no valid repeat days.")


def previous_occurrence(alarm: Alarm, now: datetime | None = None) -> datetime | None:
    """The most recent time at or before `now` this alarm would have rung.

    What a scheduler that was not running needs in order to tell a missed alarm
    from one that is simply not due yet.
    """
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.astimezone()
    days = tuple(sorted(set(alarm.days)))
    today = now.date()
    for behind in range(9):
        day = today - timedelta(days=behind)
        if days and day.weekday() not in days:
            continue
        candidate = _wall(day, alarm.hour, alarm.minute, now)
        if candidate <= now:
            return candidate
    return None


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

_EMPTY = {"world": [], "alarms": [], "timers": []}


class ClockStore:
    def __new__(cls, path=None):
        if cls is ClockStore and path is None and Path('/.flatpak-info').exists():
            from .clock_host import HostClockStore, host_available
            if host_available(): return object.__new__(HostClockStore)
        return object.__new__(cls)

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
            path = data_home / "prairie/clock/state.json"
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        if not self.path.exists():
            self._write({**_EMPTY, "world_setup_done": False})

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {key: [] for key in _EMPTY}
        return data if isinstance(data, dict) else {key: [] for key in _EMPTY}

    def _write(self, data: dict) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".state-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(data, output, ensure_ascii=False, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    # -- world ------------------------------------------------------------
    def seed_home_clock(self, zone: str | None) -> bool:
        """Use setup's system zone once, then preserve the person's city list.

        The alarm agent can create this store before the window is opened.
        A marker distinguishes that fresh empty list from a city removed by
        the person, so a later launch never puts their deleted card back.
        """
        if not zone or _zone(zone) is None:
            return False
        data = self._read()
        if data.get("world_setup_done") is not False:
            return False
        added = not data.get("world")
        if added:
            data["world"] = [asdict(WorldClock(uuid.uuid4().hex, zone_city(zone), zone))]
        data["world_setup_done"] = True
        self._write(data)
        return added

    def world_clocks(self) -> tuple[WorldClock, ...]:
        result = []
        for item in self._read().get("world", []):
            try:
                result.append(WorldClock(item["uid"], item["label"], item["zone"]))
            except (KeyError, TypeError):
                continue
        return tuple(result)

    def add_world_clock(self, label: str, zone: str, *, uid: str | None = None) -> WorldClock:
        label, zone = label.strip(), zone.strip()
        if not label:
            raise ValueError("Enter a city name.")
        if _zone(zone) is None:
            raise ValueError("Enter a valid time zone, such as America/Chicago.")
        record = WorldClock(uid or uuid.uuid4().hex, label, zone)
        data = self._read()
        data.setdefault("world", []).append(asdict(record))
        self._write(data)
        return record

    def insert_world_clock(self, record: WorldClock, index: int) -> None:
        """Put a city back where it was — what Undo needs and append cannot give."""
        data = self._read()
        cities = data.setdefault("world", [])
        cities.insert(max(0, min(index, len(cities))), asdict(record))
        self._write(data)

    def remove_world_clock(self, uid: str) -> None:
        data = self._read()
        data["world"] = [item for item in data.get("world", []) if item.get("uid") != uid]
        self._write(data)

    def move_world_clock(self, uid: str, offset: int) -> bool:
        data = self._read()
        cities = data.get("world", [])
        index = next((i for i, item in enumerate(cities) if item.get("uid") == uid), -1)
        target = index + offset
        if index < 0 or not 0 <= target < len(cities):
            return False
        cities.insert(target, cities.pop(index))
        self._write(data)
        return True

    # -- alarms -----------------------------------------------------------
    def alarms(self) -> tuple[Alarm, ...]:
        result = []
        for item in self._read().get("alarms", []):
            try:
                result.append(
                    Alarm(
                        item["uid"],
                        str(item.get("label", "")),
                        int(item["hour"]),
                        int(item["minute"]),
                        tuple(int(day) for day in item.get("days", ())),
                        bool(item.get("enabled", True)),
                        str(item.get("sound", SOUNDS[0])),
                        int(item.get("snooze_minutes", 9)),
                        int(item.get("ring_seconds", 60)),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return tuple(sorted(result, key=lambda item: (item.hour, item.minute, item.uid)))

    def save_alarm(self, alarm: Alarm, *, index: int | None = None) -> Alarm:
        """Write the record. Scheduling is the alarm service's job (clock_alarms)."""
        valid_uid(alarm.uid)
        if not 0 <= alarm.hour <= 23 or not 0 <= alarm.minute <= 59:
            raise ValueError("Choose a valid time.")
        data = self._read()
        records = data.setdefault("alarms", [])
        remaining = [item for item in records if item.get("uid") != alarm.uid]
        position = len(remaining) if index is None else max(0, min(index, len(remaining)))
        remaining.insert(position, asdict(alarm))
        data["alarms"] = remaining
        self._write(data)
        return alarm

    def update_alarm_enabled(self, uid: str, enabled: bool) -> bool:
        """Toggle only the edited field, retaining future or unknown alarm data."""
        valid_uid(uid)
        data = self._read()
        for item in data.get("alarms", []):
            if isinstance(item, dict) and item.get("uid") == uid:
                if item.get("enabled") != enabled:
                    item["enabled"] = enabled
                    self._write(data)
                return True
        return False

    def delete_alarm(self, uid: str) -> None:
        data = self._read()
        data["alarms"] = [item for item in data.get("alarms", []) if item.get("uid") != uid]
        self._write(data)

    # -- timers -----------------------------------------------------------
    def timers(self) -> tuple[TimerRecord, ...]:
        result = []
        for item in self._read().get("timers", []):
            try:
                result.append(
                    TimerRecord(item["uid"], str(item.get("label", "Timer")), int(item["fires_at"]), int(item.get("total_seconds", 0)))
                )
            except (KeyError, TypeError, ValueError):
                continue
        return tuple(result)

    def save_timer(self, record: TimerRecord) -> TimerRecord:
        valid_uid(record.uid)
        data = self._read()
        data["timers"] = [item for item in data.get("timers", []) if item.get("uid") != record.uid]
        data["timers"].append(asdict(record))
        self._write(data)
        return record

    def clear_timer(self, uid: str) -> None:
        data = self._read()
        data["timers"] = [item for item in data.get("timers", []) if item.get("uid") != uid]
        self._write(data)


_UID = re.compile(r"[0-9a-f]{32}")


def valid_uid(uid: str) -> str:
    """Alarm and timer ids name notifications, actions and files: 32 hex digits only."""
    if not isinstance(uid, str) or not _UID.fullmatch(uid):
        raise ValueError("Invalid alarm identifier.")
    return uid


def snooze_until(alarm: Alarm, now: datetime | None = None) -> datetime:
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.astimezone()
    return (now + timedelta(minutes=max(1, alarm.snooze_minutes))).replace(second=0, microsecond=0)


@dataclass(frozen=True)
class WorldLayout:
    stacked: bool
    bands: bool
    hint: bool


def world_layout(size_class: str, *, handheld: bool) -> WorldLayout:
    """The two axes, decided in one place and out of reach of a display.

    Size class decides the layout: at Compact the row becomes two lines with the
    band full width underneath, because the band was only ever fighting the name
    and the time for one row.

    Presentation decides the chrome, and decides it separately: the hint exists
    to tell you the bands are hidden and what to do about it, so it appears only
    where "widen the window" is advice you can take. On a handheld it never
    appears — and the bands are never hidden either, because the 520px rule is
    about a band squeezed into a column, and a stacked band on a 360px phone is
    still 13px an hour, comfortably above the 8.5px floor. Losing the bands on a
    phone would gut the feature on the device where the question gets asked.
    """
    stacked = size_class in {"stacked", "narrow"}
    bands = not (size_class == "narrow" and not handheld)
    return WorldLayout(stacked=stacked, bands=bands, hint=not bands and not handheld)


def volume_ramp(elapsed: float, *, ramp_seconds: float = 20.0, floor: float = 0.15) -> float:
    """A volume that climbs rather than detonates. 0–1, linear from `floor` to 1."""
    if ramp_seconds <= 0:
        return 1.0
    progress = max(0.0, min(1.0, elapsed / ramp_seconds))
    return floor + (1.0 - floor) * progress


def local_time(zone: str, now: datetime | None = None) -> datetime:
    return (now or datetime.now().astimezone()).astimezone(ZoneInfo(zone))


__all__ = [
    "Alarm", "BandRun", "ClockStore", "Countdown", "Stopwatch", "TimerRecord", "WorldClock",
    "EVERY_DAY", "MINUS", "SOUNDS", "TONE_COLOURS", "WEEKDAYS", "WEEKDAY_NAMES", "WEEKENDS",
    "axis_origin", "axis_ticks", "band_description",
    "day_rollover", "day_slices", "format_clock", "format_countdown",
    "format_stopwatch", "gradient_stops", "home_zone_name", "hour_format_from_pattern",
    "is_asleep", "local_time", "next_occurrence", "now_fraction", "offset_text",
    "previous_occurrence", "rollover_chip", "snooze_until", "tone_for_hour",
    "uses_24_hour", "valid_uid", "volume_ramp", "world_layout", "WorldLayout", "zone_city",
    "zone_offset",
]
