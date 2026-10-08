# SPDX-License-Identifier: Apache-2.0
"""luma-vitals: sample every 15 seconds, record, and say when something misbehaves."""
from __future__ import annotations

import json
import os
import signal
import sys
import time

from . import power, sampler
from .agents import RunawayAgents
from .crashes import CHECK_SECONDS, CrashWatch
from .detectors import Detectors, PowerDetectors
from .memory_feedback import MemoryFeedback, MemoryWatch
from .store import Store, state_dir

INTERVAL = 15.0


#: Evidence that the machine or a program died is an error; the rest are warnings.
PRIORITIES = {"out-of-memory": 3, "agent-restart-failed": 3, "unclean-shutdown": 3, "kernel-crash-record": 3,
              "firmware-crash-record": 3}


def journal(event) -> None:
    """One structured line per event; `journalctl -t luma-vitals` shows them."""
    fields = {"LUMA_VITALS_KIND": event.kind, "LUMA_VITALS_UNIT": event.unit}
    priority = PRIORITIES.get(event.kind, 4)
    try:
        from systemd import journal as systemd_journal  # optional
        systemd_journal.send(event.summary, SYSLOG_IDENTIFIER="luma-vitals", PRIORITY=priority, **fields,
                             LUMA_VITALS_DETAILS=json.dumps(event.details))
    except ImportError:
        print(f"<{priority}>{event.kind}: {event.summary}", file=sys.stderr, flush=True)


def main() -> int:
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(True))
    store = Store()
    detectors = Detectors()
    agents = RunawayAgents()
    attributor = power.Attributor()
    battery = PowerDetectors()
    crashes = CrashWatch(store)
    memory_watch = MemoryWatch(crashes)
    memory_feedback = MemoryFeedback()
    last_prune = last_report = time.monotonic()
    last_crash_check = None
    os.nice(10)
    while not stop:
        started = time.monotonic()
        if last_crash_check is None or started - last_crash_check >= CHECK_SECONDS:
            found = crashes.previous_boot_events() if last_crash_check is None else []
            found += crashes.firmware_events()
            found += crashes.crash_events()
            found += crashes.launch_failure_events()
            store.add_events(found)
            for event in found:
                journal(event)
            last_crash_check = started
        memory_events = memory_watch.events()
        store.add_events(memory_events)
        for event in memory_events:
            journal(event)
        sample = sampler.machine()
        events, cpu = detectors.observe(sample)
        # Not just a report: a background agent that keeps growing is restarted.
        events += agents.observe(sample)
        # On battery only: what the machine is drawing and what is spending it.
        reading = power.sample(attributor)
        if store.record_power(reading, cpu) is not None:
            left = reading.draw.seconds_to_empty
            events += battery.observe(reading, left / 3600 if left else None)
        store.record(sample, cpu, events)
        memory_feedback.observe(memory_events + events)
        for event in events:
            journal(event)
        if started - last_prune > 3600:
            store.prune()
            last_prune = started
        if started - last_report > 3600:
            path = state_dir() / "reports" / time.strftime("%Y-%m-%dT%H.json")
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(store.report(3600), indent=1))
            last_report = started
        time.sleep(max(1.0, INTERVAL - (time.monotonic() - started)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
