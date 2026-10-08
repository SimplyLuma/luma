# SPDX-License-Identifier: Apache-2.0
"""Which of today's events, if any, is the one worth showing.

The rule is deliberately narrow: the event happening now, or the next one that
starts before the day is out. Nothing from tomorrow, because a shelf that
announces tomorrow's stand-up all evening is noise rather than information, and
nothing from a calendar the person has turned off.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta


@dataclass(frozen=True)
class Upcoming:
    """One event, and where the clock stands in relation to it."""

    summary: str
    start: datetime
    end: datetime
    all_day: bool
    in_progress: bool

    def seconds_until(self, now: datetime) -> int:
        return max(0, int((self.start - now).total_seconds()))

    def seconds_remaining(self, now: datetime) -> int:
        return max(0, int((self.end - now).total_seconds()))


def end_of_day(now: datetime) -> datetime:
    """Midnight at the end of the day `now` falls in, in the same zone."""

    return datetime.combine(now.date(), time.max, tzinfo=now.tzinfo)


def select(events, now: datetime) -> Upcoming | None:
    """The event in progress, else the next one to start today.

    An event already under way wins over one that has not started: the thing a
    person most wants confirmed is the meeting they are in.
    """

    running = [e for e in events if not _all_day(e) and e.start <= now < e.end]
    if running:
        soonest = min(running, key=lambda e: e.end)
        return _upcoming(soonest, in_progress=True)
    ahead = [e for e in events if not _all_day(e) and e.start > now]
    if ahead:
        soonest = min(ahead, key=lambda e: e.start)
        return _upcoming(soonest, in_progress=False)
    # An all-day event has no hour to count down to, so it only shows when
    # there is nothing timed left in the day to show instead.
    whole_day = [e for e in events if _all_day(e) and e.end > now]
    if whole_day:
        return _upcoming(min(whole_day, key=lambda e: e.start), in_progress=True)
    return None


def _all_day(event) -> bool:
    return bool(getattr(event, "all_day", False))


def _upcoming(event, *, in_progress: bool) -> Upcoming:
    return Upcoming(
        summary=(getattr(event, "summary", "") or "").strip() or "Busy",
        start=event.start,
        end=event.end,
        all_day=_all_day(event),
        in_progress=in_progress,
    )


#: No wake-up is ever further apart than this. It is a ceiling rather than a
#: preference: the publication has to be refreshed before it expires, and
#: extension.EXPIRY_SECONDS is set against this number. Raising one without the
#: other leaves the shelf empty between refreshes, which is how this first went
#: wrong — a five minute sleep against a two-and-a-half minute expiry meant the
#: event was on screen for half of each cycle and absent for the rest.
MAX_REFRESH_SECONDS = 120

#: How long to wait before looking again while nothing has been published yet.
#: A producer started with the session races the calendar backend coming up, so
#: its first reads can come back empty simply because no calendar has
#: registered. Sleeping the full ceiling then left the shelf empty for two
#: minutes after every login, and the event only appeared once the Calendar
#: application had been opened by hand -- which read as the feature not working
#: at all. Retry soon at first and settle at the ceiling once the backend has
#: plainly had its chance.
WARMUP_DELAYS = (5, 10, 20, 40, 80)


def refresh_delay(state: Upcoming | None, now: datetime,
                  *, warmup: int | None = None) -> int:
    """How long until the wording would change, bounded.

    While a countdown is on screen it has to move every minute; the rest of the
    time there is nothing to redraw, so the daemon should not wake for it —
    but never for longer than the publication survives.
    """

    if state is None:
        if warmup is not None and warmup < len(WARMUP_DELAYS):
            return WARMUP_DELAYS[warmup]
        return MAX_REFRESH_SECONDS
    if state.in_progress:
        return 60
    until = state.seconds_until(now)
    if until <= 3600:
        return 30
    return min(MAX_REFRESH_SECONDS, max(60, until - 3600))
