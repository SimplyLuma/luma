#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bounded, opt-in device telemetry for development previews.

No command lines, environment, network addresses, document names, recordings or
account data are collected. Records describe counters, not estimated per-app
watts. Charging power must not be interpreted as system discharge power.
"""
import argparse
import glob
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import subprocess
import time


def read(path):
    try:
        return Path(path).read_text(errors="replace")[:65536].strip()
    except (OSError, ValueError):
        return ""


def integer(value):
    try:
        return int(value)
    except ValueError:
        return None


def fields(text):
    return dict(line.split(":", 1) for line in text.splitlines() if ":" in line)


class Observer:
    def __init__(self, shell_metrics=None):
        self.shell_metrics = shell_metrics
        self.previous = {}
        self.last = time.monotonic()
        self.ticks = os.sysconf("SC_CLK_TCK")
        self.page = os.sysconf("SC_PAGE_SIZE")
        self.next_inventory = 0
        self.inventory = None
        self.cpu_previous = None

    def sample(self):
        now = time.monotonic()
        elapsed = max(.001, now - self.last)
        previous, self.previous = self.previous, {}
        processes = []
        for directory in Path("/proc").glob("[0-9]*"):
            stat = read(directory / "stat")
            # comm may contain spaces and parentheses; parse after its last ')'.
            end = stat.rfind(")")
            if end < 0:
                continue
            values = stat[end + 2:].split()
            if len(values) < 22:
                continue
            pid = int(directory.name)
            identity = (pid, values[19])  # starttime disambiguates reused PIDs
            ticks = int(values[11]) + int(values[12])
            self.previous[identity] = ticks
            cpu = (ticks - previous.get(identity, ticks)) / self.ticks / elapsed * 100
            processes.append({"pid": pid, "name": stat[stat.find("(") + 1:end],
                              "cpu_one_core_pct": round(cpu, 2),
                              "rss_bytes": int(values[21]) * self.page})
        self.last = now
        processes.sort(key=lambda row: row["cpu_one_core_pct"], reverse=True)
        memory = fields(read("/proc/meminfo"))
        cpu_stat = read('/proc/stat').splitlines()[:1]
        counters = [int(value) for value in cpu_stat[0].split()[1:9]] if cpu_stat else []
        busy_pct = None
        if len(counters) == 8:
            total, idle = sum(counters), counters[3] + counters[4]
            if self.cpu_previous and total > self.cpu_previous[0]:
                total_delta = total - self.cpu_previous[0]
                idle_delta = idle - self.cpu_previous[1]
                busy_pct = round(100 * (total_delta - idle_delta) / total_delta, 3)
            self.cpu_previous = (total, idle)
        record = {"unix_time": time.time(), "monotonic": now,
                  "interval_seconds": round(elapsed, 3), "load": read("/proc/loadavg").split()[:3],
                  "cpu_stat": cpu_stat, "cpu_machine_busy_pct": busy_pct,
                  "process_cpu_one_core_pct": round(sum(row['cpu_one_core_pct'] for row in processes), 2),
                  "cpu_online_count": os.cpu_count(),
                  "process_count": len(processes), "processes": processes[:24],
                  "largest_rss": sorted(processes, key=lambda row: row["rss_bytes"], reverse=True)[:12],
                  "pressure": {key: read(f"/proc/pressure/{key}") for key in ("cpu", "memory", "io")}}
        record["memory_kib"] = {key: integer(memory.get(key, "").strip().split(" ")[0])
                                for key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree", "Dirty")}
        selected = {row["pid"]: row for row in record["processes"] + record["largest_rss"]}
        for pid, row in selected.items():
            row["cgroup"] = read(f"/proc/{pid}/cgroup").splitlines()[:4]
            engines = []
            clients = set()
            for info in list(Path(f"/proc/{pid}/fdinfo").glob("*"))[:96]:
                metrics = {key: value.strip() for key, value in fields(read(info)).items()
                           if key.startswith("drm-")}
                identity = (metrics.get("drm-driver"), metrics.get("drm-pdev"), metrics.get("drm-client-id"))
                if metrics and identity not in clients:
                    clients.add(identity)
                    engines.append(metrics)
            if engines:
                row["drm_counters"] = engines
        if now >= self.next_inventory:
            self.next_inventory = now + 300
            inventory = {}
            for name, command in (("flatpak_apps", ["flatpak", "list", "--app", "--columns=application,branch,arch,active"]),
                                  ("packages", ["rpm", "-qa", "--qf", "%{NAME} %{VERSION}-%{RELEASE}.%{ARCH}\n"])):
                try:
                    result = subprocess.run(command, capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        inventory[name] = sorted(result.stdout.splitlines())[:4096]
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if inventory != self.inventory:
                record["inventory_changed"] = inventory
                self.inventory = inventory
        record["power"] = []
        for supply in Path("/sys/class/power_supply").glob("*"):
            values = dict(line.split("=", 1) for line in read(supply / "uevent").splitlines() if "=" in line)
            keys = ("TYPE", "SCOPE", "STATUS", "ONLINE", "PRESENT", "CAPACITY", "POWER_NOW",
                    "ENERGY_NOW", "ENERGY_FULL", "CURRENT_NOW", "VOLTAGE_NOW", "TEMP")
            record["power"].append({key.lower(): values[f"POWER_SUPPLY_{key}"] for key in keys
                                    if f"POWER_SUPPLY_{key}" in values})
        record["thermal"] = [{"type": read(zone / "type"), "millidegrees_c": integer(read(zone / "temp"))}
                             for zone in Path("/sys/class/thermal").glob("thermal_zone*")]
        record["cpu_idle"] = [{"cpu": cpu.parent.parent.name, "state": read(cpu / "name"),
                               "time_us": integer(read(cpu / "time")), "entries": integer(read(cpu / "usage"))}
                              for cpu in Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpuidle/state*")]
        record["cpu_frequency"] = [{"policy": policy.name,
            **{name: read(policy / name) for name in ("scaling_driver", "scaling_governor",
                 "scaling_cur_freq", "scaling_min_freq", "scaling_max_freq")}}
            for policy in Path("/sys/devices/system/cpu/cpufreq").glob("policy*")]
        record["gpu"] = [{"device": str(Path(device).parent.parent.name),
                          "runtime_status": read(Path(device) / "runtime_status"),
                          "runtime_active_ms": integer(read(Path(device) / "runtime_active_time")),
                          "runtime_suspended_ms": integer(read(Path(device) / "runtime_suspended_time"))}
                         for device in glob.glob("/sys/class/drm/card[0-9]/device/power")]
        if self.shell_metrics:
            try:
                shell = json.loads(read(self.shell_metrics))
                keys = ("unix_time", "mode", "locked", "home", "window_count", "minimized", "fullscreen",
                        "stage_paint_counter_available", "stage_paint_events_per_second")
                record["shell"] = {key: shell[key] for key in keys if key in shell}
            except (ValueError, TypeError):
                pass
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=15)
    parser.add_argument("--duration", type=float, default=3600)
    parser.add_argument("--shell-metrics", type=Path, help="Optional metrics emitted by a private shell preview")
    args = parser.parse_args()
    if not 5 <= args.interval <= 300 or not 10 <= args.duration <= 86400:
        parser.error("interval must be 5..300 seconds; duration 10..86400 seconds")
    os.umask(0o077)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("device-observer")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(args.output, maxBytes=4 * 1024 * 1024, backupCount=4)
    logger.addHandler(handler)
    running = True
    def stop(*_args):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    observer = Observer(args.shell_metrics)
    deadline = time.monotonic() + args.duration
    while running and time.monotonic() < deadline:
        started = time.monotonic()
        logger.info(json.dumps(observer.sample(), separators=(",", ":")))
        delay = max(0, args.interval - (time.monotonic() - started))
        # Interruptible one-second sleeps keep shutdown bounded without polling
        # the device more frequently than the requested sampling interval.
        until = time.monotonic() + delay
        while running and time.monotonic() < until:
            time.sleep(max(0, min(1, until - time.monotonic())))
    handler.close()


if __name__ == "__main__":
    main()
