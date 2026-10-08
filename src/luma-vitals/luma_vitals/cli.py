# SPDX-License-Identifier: Apache-2.0
"""`luma-vitals report`: what cost the most. `luma-vitals battery`: what the last run on battery drew."""
from __future__ import annotations

import argparse
import json
import time

from . import battery_cli
from .detectors import friendly
from .store import Store

GIB = 1 << 30


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="luma-vitals")
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report", help="Top consumers and events")
    report.add_argument("--hours", type=float, default=1.0)
    report.add_argument("--json", action="store_true")
    battery = sub.add_parser("battery", help="The last run on battery: draw, runtime and what spent it")
    battery.add_argument("--session", type=int, default=None, help="A session id; the latest by default")
    battery.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "battery":
        data = Store().battery_report(args.session)
        if args.json:
            print(json.dumps(data, indent=1))
            return 0 if data else 1
        for line in battery_cli.render(data):
            print(line)
        return 0 if data else 1
    data = Store().report(args.hours * 3600)
    if args.json:
        print(json.dumps(data, indent=1))
        return 0
    print(f"Last {args.hours:g} h")
    m = data["machine"]
    if m["lowest_available"] is not None:
        print(f"  Lowest free memory {m['lowest_available'] / GIB:.1f} GB · most swap {m['most_swap'] / GIB:.1f} GB · "
              f"worst memory stall {m['worst_memory_stall'] or 0:.0f}%" +
              (f" · hottest {m['hottest']:.0f}°C" if m["hottest"] else ""))
    print("Most CPU")
    for row in data["top_cpu"][:5]:
        print(f"  {row['cpu_seconds'] / 60:7.1f} min  {friendly(row['unit'])}")
    print("Most memory")
    for row in data["top_memory"][:5]:
        print(f"  {row['memory_peak'] / GIB:7.2f} GB   {friendly(row['unit'])}")
    print("Events" if data["events"] else "No events")
    for event in data["events"]:
        print(f"  {time.strftime('%H:%M', time.localtime(event['time']))}  {event['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
