# SPDX-License-Identifier: Apache-2.0
"""The shared Calendar/Tasks quick-entry interpretation of an unqualified hour."""
from datetime import time
import re


def quick_clock(hour: int, minute: int = 0, suffix: str = '') -> time:
    suffix = suffix.lower()
    if minute > 59 or minute < 0 or hour < 0 or hour > 23 or (suffix and not 1 <= hour <= 12):
        raise ValueError('Enter a valid time.')
    if suffix == 'pm' and hour < 12:
        hour += 12
    elif suffix == 'am' and hour == 12:
        hour = 0
    elif not suffix and hour < 8:
        hour += 12
    return time(hour, minute)


_CLOCK = r'\d{1,2}(?::\d{2})?(?:\s*[ap]\.?m\.?)?'
_RANGE = re.compile(r'(?<![\w:.])(?:from\s+|at\s+)?(?P<start>' + _CLOCK +
                    r')\s*(?:to|until|[–−-])\s*(?P<end>' + _CLOCK + r')(?![\w:.])', re.I)


def extract_time_range(text):
    """Remove one explicit interval; return its clocks and overnight day offset.

    Unqualified clocks follow the same quick-entry convention as a single
    clock. A trailing period applies to both clocks when that makes a forward
    interval (8–9 PM); 11–1 PM starts in the morning. Explicit overnight
    ranges retain both periods rather than silently truncating the end.
    """
    match = _RANGE.search(text)
    if match is None:
        return text, None
    parts = []
    for key in ('start', 'end'):
        value = re.sub(r'[\s.]', '', match[key].lower())
        clock = re.fullmatch(r'(\d{1,2})(?::(\d{2}))?(am|pm)?', value)
        parts.append((int(clock[1]), int(clock[2] or 0), clock[3] or ''))
    first, last = parts
    start = quick_clock(*first)
    end = quick_clock(*last)
    if not first[2] and last[2] and 1 <= first[0] <= 12:
        start = quick_clock(first[0], first[1], last[2])
        if start >= end and last[2] == 'pm' and start.hour >= 12:
            start = time(start.hour - 12, start.minute)
    if start == end:
        raise ValueError('The end time must be after the start time.')
    return text[:match.start()] + ' ' + text[match.end():], (start, end, int(end < start))
