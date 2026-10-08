# SPDX-License-Identifier: Apache-2.0
"""Samples for a day, hourly aggregates for a month, events kept (ADR-026 §2)."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

SAMPLE_DAYS = 1
HOURLY_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (time REAL, unit TEXT, cpu REAL, memory INTEGER, swap INTEGER,
                                    io_read INTEGER, io_write INTEGER);
CREATE INDEX IF NOT EXISTS samples_time ON samples(time);
CREATE TABLE IF NOT EXISTS machine (time REAL, available INTEGER, swap_used INTEGER, cpu_stall REAL,
                                    memory_stall REAL, io_stall REAL, celsius REAL, watts REAL);
CREATE TABLE IF NOT EXISTS hourly (hour TEXT, unit TEXT, cpu_seconds REAL, memory_peak INTEGER,
                                   PRIMARY KEY (hour, unit));
CREATE TABLE IF NOT EXISTS events (time REAL, kind TEXT, unit TEXT, summary TEXT, details TEXT);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
-- One row per sample taken on battery (ADR-026 §8).
CREATE TABLE IF NOT EXISTS power (time REAL, session INTEGER, watts REAL, percent INTEGER,
                                  energy_wh REAL, screen REAL, package_w REAL, core_w REAL,
                                  graphics_w REAL, memory_w REAL, package_idle REAL, gpu_idle REAL);
CREATE INDEX IF NOT EXISTS power_session ON power(session);
-- Running totals for the session, so a ranking never has to re-read the samples.
CREATE TABLE IF NOT EXISTS power_cost (session INTEGER, kind TEXT, name TEXT, value REAL,
                                       PRIMARY KEY (session, kind, name));
CREATE TABLE IF NOT EXISTS power_sessions (session INTEGER PRIMARY KEY, started REAL, ended REAL,
                                           start_percent INTEGER, end_percent INTEGER,
                                           start_energy_wh REAL, end_energy_wh REAL,
                                           full_wh REAL, samples INTEGER, readings INTEGER,
                                           cost_ms REAL, blockers TEXT);
"""


def state_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    path = base / "luma" / "vitals"
    path.mkdir(parents=True, exist_ok=True)
    return path


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.db = sqlite3.connect(path or state_dir() / "vitals.db")
        self.db.executescript(SCHEMA)
        self.db.execute("PRAGMA journal_mode=WAL")

    def record(self, sample, cpu: dict[str, float], events) -> None:
        with self.db:
            self.db.executemany("INSERT INTO samples VALUES (?, ?, ?, ?, ?, ?, ?)", [
                (sample.time, u.unit, round(cpu.get(u.unit, 0.0), 2), u.memory, u.swap, u.io_read, u.io_write)
                for u in sample.units if cpu.get(u.unit, 0.0) >= 0.1 or u.memory >= 32 << 20])
            p = sample.pressure
            self.db.execute("INSERT INTO machine VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
                sample.time, sample.memory_available, sample.swap_total - sample.swap_free,
                p.get("cpu", {}).get("some", 0.0), p.get("memory", {}).get("some", 0.0),
                p.get("io", {}).get("some", 0.0), sample.temperature_c, sample.battery_watts))
            hour = time.strftime("%Y-%m-%dT%H", time.localtime(sample.time))
            for u in sample.units:
                seconds = cpu.get(u.unit, 0.0) / 100 * 15
                if seconds or u.memory >= 32 << 20:
                    self.db.execute("""INSERT INTO hourly VALUES (?, ?, ?, ?) ON CONFLICT(hour, unit) DO UPDATE SET
                                       cpu_seconds = cpu_seconds + excluded.cpu_seconds,
                                       memory_peak = MAX(memory_peak, excluded.memory_peak)""",
                                    (hour, u.unit, seconds, u.memory))
            self.db.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?)", [
                (sample.time, e.kind, e.unit, e.summary, json.dumps(e.details)) for e in events])

    def add_events(self, events, when: float | None = None) -> None:
        """Events that are not tied to a sample, such as crash evidence."""
        when = time.time() if when is None else when
        with self.db:
            self.db.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?)", [
                (when, e.kind, e.unit, e.summary, json.dumps(e.details)) for e in events])

    # ------------------------------------------------------------- battery

    def record_power(self, power, unit_cpu: dict[str, float] | None = None) -> int | None:
        """A sample taken on battery, and what it cost, kept per session.

        A session is one unbroken run on battery: it opens when the mains are
        pulled out and closes when they go back in, which is the span a person
        means by "how long did it last".
        """
        with self.db:
            open_session = self.db.execute(
                "SELECT session, end_energy_wh FROM power_sessions WHERE ended IS NULL").fetchone()
            if not power.draw.on_battery:
                if open_session:
                    self.db.execute("UPDATE power_sessions SET ended = ? WHERE session = ?",
                                    (power.time, open_session[0]))
                return None
            if open_session:
                session = open_session[0]
            else:
                session = int(power.time)
                self.db.execute(
                    "INSERT INTO power_sessions VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 0, 0, 0, '[]')",
                    (session, power.time, power.draw.percent, power.draw.percent,
                     power.draw.energy_wh, power.draw.energy_wh, power.draw.energy_full_wh))
            domains = power.domains
            attribution = power.attribution
            self.db.execute("INSERT INTO power VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                power.time, session, power.draw.watts, power.draw.percent, power.draw.energy_wh,
                power.draw.screen_percent, domains.get("package"), domains.get("core"),
                domains.get("graphics"), domains.get("memory"),
                attribution.package_idle_percent if attribution else None,
                attribution.gpu_idle_percent if attribution else None))
            self.db.execute("""UPDATE power_sessions SET ended = NULL, end_percent = ?, end_energy_wh = ?,
                               samples = samples + 1, readings = readings + ?, cost_ms = cost_ms + ?
                               WHERE session = ?""",
                            (power.draw.percent, power.draw.energy_wh, 1 if attribution else 0,
                             power.cost_ms, session))
            totals = []
            if attribution:
                for kind, values in (("wakeups", attribution.wakeups), ("cpu", attribution.cpu),
                                     ("interrupt", attribution.interrupts), ("device", attribution.devices)):
                    totals += [(session, kind, name, float(value)) for name, value in values.items()]
                if attribution.blockers:
                    self.db.execute("UPDATE power_sessions SET blockers = ? WHERE session = ?",
                                    (json.dumps(attribution.blockers), session))
            for name, percent in (unit_cpu or {}).items():
                if percent >= 0.1:
                    totals.append((session, "unit", name, percent))
            self.db.executemany("""INSERT INTO power_cost VALUES (?, ?, ?, ?)
                                   ON CONFLICT(session, kind, name) DO UPDATE
                                   SET value = value + excluded.value""", totals)
        return session

    def battery_report(self, session: int | None = None) -> dict | None:
        """The last run on battery: what it drew, how long it would last, what cost it."""
        row = self.db.execute(
            "SELECT * FROM power_sessions WHERE session = ? OR ? IS NULL ORDER BY session DESC LIMIT 1",
            (session, session)).fetchone()
        if row is None:
            return None
        (identity, started, ended, start_percent, end_percent, start_energy, end_energy,
         full_energy, samples, readings, cost_ms, blockers) = row
        last = self.db.execute("SELECT MAX(time) FROM power WHERE session = ?", (identity,)).fetchone()[0]
        finished = ended or last or started
        hours = max(finished - started, 1.0) / 3600
        spent = (start_energy or 0) - (end_energy or 0)
        # The charge actually spent over the whole session is the honest average;
        # the instant rate is only what the battery happened to say at the end.
        instant = self.db.execute(
            "SELECT AVG(watts), MIN(watts), MAX(watts) FROM power WHERE session = ?", (identity,)).fetchone()
        average = (spent / hours) if spent > 0 else instant[0]
        means = self.db.execute("""SELECT AVG(package_w), AVG(core_w), AVG(graphics_w), AVG(memory_w),
                                          AVG(package_idle), AVG(gpu_idle), AVG(screen)
                                   FROM power WHERE session = ?""", (identity,)).fetchone()
        costs: dict[str, list] = {}
        for kind in ("unit", "cpu", "wakeups", "interrupt", "device"):
            rows = self.db.execute("""SELECT name, value FROM power_cost WHERE session = ? AND kind = ?
                                      ORDER BY value DESC LIMIT 10""", (identity, kind)).fetchall()
            # Each row is a sum of per-reading rates; a mean is what a person reads.
            divisor = max(samples if kind == "unit" else readings, 1)
            costs[kind] = [{"name": name, "value": round(value / divisor, 2)} for name, value in rows]
        return {
            "session": identity,
            "started": started,
            "ended": ended,
            "hours": round(hours, 2),
            "closed": ended is not None,
            "percent": {"start": start_percent, "end": end_percent},
            "energy_wh": {"start": start_energy, "end": end_energy, "full": full_energy,
                          "spent": round(spent, 2)},
            "watts": {"average": round(average, 2) if average else None,
                      "lowest": round(instant[1], 2) if instant[1] else None,
                      "highest": round(instant[2], 2) if instant[2] else None},
            "runtime_hours": round((end_energy or 0) / average, 2) if average else None,
            "full_charge_hours": round(full_energy / average, 2) if average and full_energy else None,
            "domains": {"package": means[0], "core": means[1], "graphics": means[2], "memory": means[3]},
            "package_idle_percent": means[4],
            "gpu_idle_percent": means[5],
            "screen_percent": means[6],
            "samples": samples,
            "readings": readings,
            "overhead_ms_per_sample": round(cost_ms / samples, 3) if samples else None,
            "blockers": json.loads(blockers or "[]"),
            "costs": costs,
        }

    def get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT INTO state VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                            (key, value))

    def prune(self, now: float | None = None) -> None:
        now = now or time.time()
        with self.db:
            self.db.execute("DELETE FROM samples WHERE time < ?", (now - SAMPLE_DAYS * 86400,))
            self.db.execute("DELETE FROM machine WHERE time < ?", (now - SAMPLE_DAYS * 86400,))
            cutoff = time.strftime("%Y-%m-%dT%H", time.localtime(now - HOURLY_DAYS * 86400))
            self.db.execute("DELETE FROM hourly WHERE hour < ?", (cutoff,))
            self.db.execute("DELETE FROM events WHERE time < ?", (now - HOURLY_DAYS * 86400,))

    def report(self, since_seconds: float = 3600) -> dict:
        since = time.time() - since_seconds
        rows = self.db.execute("""SELECT unit, SUM(cpu) * 15 / 100.0, MAX(memory) FROM samples WHERE time >= ?
                                  GROUP BY unit""", (since,)).fetchall()
        machine = self.db.execute("""SELECT MIN(available), MAX(swap_used), MAX(memory_stall), MAX(celsius)
                                     FROM machine WHERE time >= ?""", (since,)).fetchone()
        events = self.db.execute("SELECT time, kind, unit, summary FROM events WHERE time >= ? ORDER BY time",
                                 (since,)).fetchall()
        return {
            "since": since,
            "top_cpu": sorted(({"unit": r[0], "cpu_seconds": round(r[1] or 0, 1)} for r in rows),
                              key=lambda r: -r["cpu_seconds"])[:10],
            "top_memory": sorted(({"unit": r[0], "memory_peak": r[2] or 0} for r in rows),
                                 key=lambda r: -r["memory_peak"])[:10],
            "machine": {"lowest_available": machine[0], "most_swap": machine[1],
                        "worst_memory_stall": machine[2], "hottest": machine[3]},
            "events": [{"time": e[0], "kind": e[1], "unit": e[2], "summary": e[3]} for e in events],
        }
