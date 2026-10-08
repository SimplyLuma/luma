# SPDX-License-Identifier: Apache-2.0
"""Deterministic quick-add parsing, independent of GTK and EDS."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import re
from .quick_time import quick_clock, extract_time_range


@dataclass(frozen=True)
class Choice:
    uid: str
    name: str


@dataclass(frozen=True)
class ParsedTask:
    title: str
    due: date | datetime | None = None
    priority: int = 0
    source_uid: str = ""
    assignee: str = ""
    chips: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    start: datetime | None = None


def _match(token, choices):
    normalize = lambda s: re.sub(r"[\s.]", "", s.casefold())
    matches = [c for c in choices if normalize(c.name).startswith(normalize(token)) or normalize(c.uid).startswith(normalize(token))]
    return matches[0] if len(matches) == 1 else None


def parse_clock(value: str) -> time:
    value = re.sub(r"[\s.]", "", value.casefold())
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)?", value)
    if not match:
        raise ValueError("Enter a time like 15:00 or 3:00 PM.")
    hour, minute, period = int(match[1]), int(match[2] or 0), match[3]
    if minute >= 60 or (not period and hour >= 24) or (period and not 1 <= hour <= 12):
        raise ValueError("Enter a valid hour and minute.")
    return time(hour % 12 + (12 if period == "pm" else 0) if period else hour, minute)


def parse_task(text: str, *, today: date | None = None, lists=(), people=(),
               source_uid="", view="inbox", me="") -> ParsedTask:
    today = today or date.today()
    words, chips, warnings = [], [], []
    due, clock, priority, assignee = None, None, 0, ""
    text, interval = extract_time_range(text)
    weekdays = {name: index for index, names in enumerate(("mon monday", "tue tuesday", "wed wednesday", "thu thursday", "fri friday", "sat saturday", "sun sunday")) for name in names.split()}
    def normalize_clock(match):
        try:
            value = re.sub(r'[\s.]', '', (match['clock'] or match['plain']).lower())
            part = re.fullmatch(r'(\d{1,2})(?::(\d{2}))?(am|pm)?', value)
            clock = quick_clock(int(part[1]), int(part[2] or 0), part[3] or '')
        except ValueError:
            return match[0]
        return ' ' + clock.strftime('%I:%M%p').lower() + ' '
    text = re.sub(r"(?<!\w)(?:at\s*(?P<clock>\d{1,2}(?::\d{2})?(?:\s*[ap]\.?m\.?)?)|(?P<plain>\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?|\d{1,2}\s*[ap]\.?m\.?))(?![\w:.])",
                  normalize_clock, text, flags=re.IGNORECASE)
    for token in re.sub(r"\bnext\s+week\b", "nextweek", text, flags=re.IGNORECASE).split():
        lower = token.casefold()
        if lower in ("!high", "!medium", "!med", "!low"):
            priority = {"!high": 1, "!medium": 5, "!med": 5, "!low": 9}[lower]
        elif re.fullmatch(r"!{1,3}", token):
            priority = {1: 9, 2: 5, 3: 1}[len(token)]
        elif token.startswith(("#", "@")) and len(token) > 1:
            choice = _match(token[1:], lists if token[0] == "#" else people)
            if choice:
                if token[0] == "#": source_uid = choice.uid
                else: assignee = choice.uid
                chips.append(choice.name)
            else:
                words.append(token)
                warnings.append(f"No unique match for {token}")
        elif lower == "nextweek":
            due = today + timedelta(days=7)
        elif lower in ("today", "tonight", "tomorrow", "tmr"):
            due = today + timedelta(days=lower in ("tomorrow", "tmr"))
            if lower == "tonight": clock = time(20)
        elif lower in weekdays:
            due = today + timedelta(days=(weekdays[lower] - today.weekday()) % 7 or 7)
        elif (match := re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)", lower)) and 1 <= int(match[1]) <= 12 and int(match[2] or 0) < 60:
            clock = time(int(match[1]) % 12 + (12 if match[3] == "pm" else 0), int(match[2] or 0))
        else:
            words.append(token)
    if due is None and (clock or view == "today"): due = today
    if clock: due = datetime.combine(due, clock).astimezone()
    start = None
    if interval:
        anchor = due.date() if isinstance(due, datetime) else due or today
        start = datetime.combine(anchor, interval[0]).astimezone()
        due = datetime.combine(anchor + timedelta(days=interval[2]), interval[1]).astimezone()
    if due: chips.append(due.strftime("%a %b %-d" + (" · %-I:%M %p" if isinstance(due, datetime) else "")))
    if priority: chips.append({1: "High priority", 5: "Medium priority", 9: "Low priority"}[priority])
    if view == "mine" and not assignee: assignee = me
    return ParsedTask(" ".join(words), due, priority, source_uid, assignee, tuple(chips), tuple(warnings), start)
