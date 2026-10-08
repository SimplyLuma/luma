# SPDX-License-Identifier: Apache-2.0
"""Anonymous update reports (ADR-030 section 6).

Two shapes are posted to ``https://hub.simplyluma.com/api/updates/events``,
exactly as the ADR lists them and nothing more:

* weekly check-in: ``{channel, version, arch, countme_bucket}``
* transition: ``{channel, from_version, to_version, arch, result, error_class}``
  with ``result`` one of ``staged``, ``booted``, ``rolled_back``, ``failed``.

No machine id, account, token, cookie, wariness, commit, timestamp or
hostname is sent, and the user agent is only ``luma-update/1``. The switch is
``/etc/luma/statistics.conf``; when it is off nothing is queued and anything
already queued is discarded.

The countme bucket uses libdnf's scheme, shared with Depot: windows are weeks
starting Monday 00:00 UTC, and the bucket says roughly how long this
installation has existed (1: first two weeks, 2: up to five, 3: up to 25,
4: longer).
"""

from __future__ import annotations

from .config import Paths, Settings, statistics_enabled
from .http import Http, HttpError
from .state import StateStore

__all__ = ("RESULTS", "ERROR_CLASSES", "window_start", "bucket", "transition_event",
           "queue_transition", "send", "countme_payload", "statistics_enabled")

RESULTS = ("staged", "booted", "rolled_back", "failed")
ERROR_CLASSES = ("", "network", "signature", "graph", "stale-graph", "busy", "transaction",
                 "disk-space", "health-check", "finalize", "unmanaged", "not-authorized",
                 "preview", "not-entitled", "unknown")

COUNTME_OFFSET = 345600          # 1970-01-05 00:00:00 UTC, a Monday
COUNTME_WINDOW = 7 * 24 * 60 * 60
COUNTME_BUCKETS = (2, 5, 25)


def window_start(now: float) -> int:
    delta = int(now) - COUNTME_OFFSET
    return delta - (delta % COUNTME_WINDOW) + COUNTME_OFFSET


def bucket(first_window: int, current_window: int) -> int:
    step = max(0, (current_window - first_window) // COUNTME_WINDOW)
    for index, limit in enumerate(COUNTME_BUCKETS):
        if step < limit:
            return index + 1
    return len(COUNTME_BUCKETS) + 1


def transition_event(*, channel: str, from_version: str, to_version: str, arch: str,
                     result: str, error_class: str = "") -> dict:
    if result not in RESULTS:
        raise ValueError(f"unknown result {result}")
    return {"channel": channel, "from_version": from_version, "to_version": to_version,
            "arch": arch, "result": result,
            "error_class": error_class if error_class in ERROR_CLASSES else "unknown"}


def queue_transition(store: StateStore, paths: Paths, **fields) -> None:
    if not statistics_enabled(paths):
        store.data["reports"] = []
        return
    store.queue_report(transition_event(**fields))


def countme_payload(store: StateStore, *, channel: str, version: str, arch: str, now: float) -> dict | None:
    """The check-in due this week, or None. Records the first window on first use."""
    countme = store.data.get("countme") or {}
    current = window_start(now)
    first = countme.get("first_window")
    if not isinstance(first, int) or first > current:
        first = current
        countme["first_window"] = first
    store.data["countme"] = countme
    counted = countme.get("counted_window")
    # Only this week's window counts as done. A window after this one was recorded
    # while the clock ran ahead; waiting for it would stop the check-in for as long.
    if isinstance(counted, int) and counted == current:
        return None
    return {"channel": channel, "version": version, "arch": arch, "countme_bucket": bucket(first, current)}


def send(http: Http, settings: Settings, payload: dict) -> bool | None:
    """Post one report. True: accepted. False: refused for good (4xx). None: retry later."""
    try:
        status, _ = http.request_json("POST", settings.events_url, payload)
    except HttpError:
        return None
    if 200 <= status < 300:
        return True
    if 400 <= status < 500 and status not in (408, 429):
        return False
    return None
