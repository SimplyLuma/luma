# SPDX-License-Identifier: Apache-2.0
"""`luma-vitals battery`: the last run on battery, in words and watts."""
from __future__ import annotations

import time

from .detectors import friendly


def _hours(value: float | None) -> str:
    if not value:
        return "unknown"
    if value < 1:
        return f"{value * 60:.0f} min"
    return f"{int(value)} h {int(round((value % 1) * 60)):02d} min"


def _clock(stamp: float | None) -> str:
    return time.strftime("%H:%M", time.localtime(stamp)) if stamp else "now"


def render(report: dict | None) -> list[str]:
    if report is None:
        return ["Nothing has been recorded on battery yet."]
    lines = []
    percent = report["percent"]
    state = "Ran" if report["closed"] else "Running"
    lines.append(f"{state} on battery for {_hours(report['hours'])}, "
                 f"{_clock(report['started'])} to {_clock(report['ended'])}"
                 + (f" · {percent['start']}% to {percent['end']}%" if percent["start"] is not None else ""))
    watts = report["watts"]
    if watts["average"]:
        span = ""
        if watts["lowest"] and watts["highest"]:
            span = f" ({watts['lowest']:.1f}–{watts['highest']:.1f} W)"
        lines.append(f"  Drawing {watts['average']:.1f} W on average{span}")
        left = f"about {_hours(report['runtime_hours'])} left at this rate" if report["runtime_hours"] else ""
        full = f"{_hours(report['full_charge_hours'])} from a full charge" if report["full_charge_hours"] else ""
        if left or full:
            lines.append("  " + " · ".join(part for part in (left, full) if part))
    domains = report["domains"]
    named = [(label, domains[key]) for key, label in
             (("package", "Processor package"), ("core", "cores"), ("graphics", "graphics"), ("memory", "memory"))
             if domains.get(key)]
    if named:
        lines.append("  " + " · ".join(f"{label} {value:.1f} W" for label, value in named))
    rest = []
    if report.get("screen_percent") is not None:
        rest.append(f"screen {report['screen_percent']:.0f}%")
    if report.get("gpu_idle_percent") is not None:
        rest.append(f"graphics idle {report['gpu_idle_percent']:.0f}% of the time")
    if report.get("package_idle_percent") is not None:
        rest.append(f"deepest processor idle {report['package_idle_percent']:.1f}%")
    if rest:
        lines.append("  " + " · ".join(rest))

    costs = report["costs"]
    if costs["unit"]:
        lines.append("")
        lines.append("What the energy went to")
        for row in costs["unit"][:6]:
            lines.append(f"  {row['value']:6.1f}% of a core   {friendly(row['name'])}")
    if costs["wakeups"]:
        lines.append("What woke the machine most")
        for row in costs["wakeups"][:6]:
            lines.append(f"  {row['value']:6.0f} a second     {row['name']}")
    if costs["interrupt"]:
        lines.append("Busiest interrupts")
        for row in costs["interrupt"][:4]:
            lines.append(f"  {row['value']:6.0f} a second     {row['name']}")
    if costs["device"]:
        lines.append("Devices asking to be woken")
        for row in costs["device"][:4]:
            lines.append(f"  {row['value']:6.1f} a second     {row['name']}")
    if report["blockers"]:
        lines.append("")
        lines.append("Keeping the machine out of its low-power states")
        for blocker in report["blockers"]:
            lines.append(f"  · {blocker}")
    overhead = report.get("overhead_ms_per_sample")
    if overhead and report.get("samples"):
        lines.append("")
        lines.append(f"Measuring this took {overhead:.2f} ms a sample "
                     f"({overhead / 150:.3f}% of one processor) over {report['samples']} samples.")
    return lines
