# SPDX-License-Identifier: Apache-2.0
"""Local presentation of canonical message instants.

Mail dates are stored as timezone-aware UTC values.  This module is the single
boundary that converts those instants to the person's current system timezone
before deriving either a clock time or a calendar-relative label.
"""
from __future__ import annotations

from datetime import datetime, timezone, tzinfo


def local_datetime(value: datetime, zone: tzinfo | None = None) -> datetime:
    """Return ``value`` in ``zone`` or the process's current local timezone."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(zone) if zone is not None else value.astimezone()


def display_time(
    value: datetime,
    *,
    now: datetime | None = None,
    zone: tzinfo | None = None,
) -> str:
    """Format an absolute mail timestamp using local clock and calendar days."""
    local_value = local_datetime(value, zone)
    local_now = local_datetime(now or datetime.now(timezone.utc), zone)
    if local_value.date() == local_now.date():
        return local_value.strftime("%-I:%M %p")
    if (local_now.date() - local_value.date()).days == 1:
        return "Yesterday"
    return local_value.strftime("%b %-d")
