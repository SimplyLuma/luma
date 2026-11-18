#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Play synthetic Precision Touchpad reports through the kernel with UHID.

Creates a HID device with the XPS 14 touchpad's report descriptor (bus I2C,
2C2F:0034, so libinput applies the same quirks), answers the feature reads
hid-multitouch makes, then sends the reports of a synthetic session at their
original pace. hid-multitouch turns them into a real hidraw node and evdev
device, so the recorder and libinput can be checked end to end.

Root only, and only on a machine with no graphical session: a compositor
would take the device as a real touchpad.

    uhid_touchpad.py [--seed N] [--trials N] [--hold SECONDS]
"""

from __future__ import annotations

import argparse
import os
import random
import select
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from touchlab import hid, synth  # noqa: E402

UHID_DESTROY, UHID_START, UHID_OPEN = 1, 2, 4
UHID_GET_REPORT, UHID_GET_REPORT_REPLY = 9, 10
UHID_CREATE2, UHID_INPUT2 = 11, 12
UHID_SET_REPORT, UHID_SET_REPORT_REPLY = 13, 14
BUS_I2C = 0x18
EVENT_SIZE = 4 + 4372


def create(fd: int, descriptor: bytes, name: str) -> None:
    body = struct.pack("<128s64s64sHHIIII", name.encode(), b"touchlab-uhid", b"", len(descriptor), BUS_I2C,
                       0x2C2F, 0x0034, 0x0100, 0) + descriptor.ljust(4096, b"\0")
    os.write(fd, struct.pack("<I", UHID_CREATE2) + body)


def send(fd: int, report: bytes) -> None:
    os.write(fd, struct.pack("<IH", UHID_INPUT2, len(report)) + report.ljust(4096, b"\0"))


def feature_reply(layout: hid.TouchpadLayout, desc: hid.Descriptor, rnum: int) -> bytes:
    size = desc.report_bytes("feature", rnum)
    if rnum == 8:
        return bytes([8, 0x15])                       # five contacts, pressure pad
    return bytes([rnum]) + bytes(max(0, size - 1))


def service(fd: int, layout, desc, log=None) -> None:
    while select.select([fd], [], [], 0)[0]:
        ev = os.read(fd, EVENT_SIZE)
        kind = struct.unpack_from("<I", ev)[0]
        if kind == UHID_GET_REPORT:
            rid, rnum, _rtype = struct.unpack_from("<IBB", ev, 4)
            data = feature_reply(layout, desc, rnum)
            os.write(fd, struct.pack("<IIHH", UHID_GET_REPORT_REPLY, rid, 0, len(data)) + data.ljust(4096, b"\0"))
            if log:
                log(f"GET_REPORT {rnum} -> {data.hex()}")
        elif kind == UHID_SET_REPORT:
            rid, rnum, _rtype, size = struct.unpack_from("<IBBH", ev, 4)
            os.write(fd, struct.pack("<IIH", UHID_SET_REPORT_REPLY, rid, 0).ljust(EVENT_SIZE, b"\0"))
            if log:
                log(f"SET_REPORT {rnum} {ev[12:12 + size].hex()}")
        elif log:
            log(f"uhid event {kind}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=3)
    p.add_argument("--trials", type=int, default=1, help="repeats of every scenario")
    p.add_argument("--settle", type=float, default=4.0, help="seconds to wait before playing")
    p.add_argument("--hold", type=float, default=2.0, help="seconds to keep the device after playing")
    p.add_argument("--name", default="TOUCHLAB 2C2F:0034")
    args = p.parse_args()
    raw = synth.load_descriptor()
    desc = hid.parse_descriptor(raw)
    layout = hid.TouchpadLayout(desc)
    fd = os.open("/dev/uhid", os.O_RDWR | os.O_CLOEXEC)
    log = lambda m: print(f"uhid: {m}", file=sys.stderr, flush=True)
    create(fd, raw, args.name)
    deadline = time.monotonic() + args.settle
    while time.monotonic() < deadline:
        service(fd, layout, desc, log)
        time.sleep(0.01)
    rng = random.Random(args.seed)
    order = [s for s in synth.SCENARIOS for _ in range(args.trials)]
    rng.shuffle(order)
    trials, t = [], 0.5
    for name in order:
        tr = synth.scenario_actors(name, t, rng)
        trials.append(tr)
        t = tr.t1 + 0.6
    start = time.monotonic()
    sent = 0
    for t_s, report, _frame in synth.render_reports(trials, layout, rng):
        delay = start + t_s - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        service(fd, layout, desc)
        send(fd, report)
        sent += 1
    log(f"sent {sent} reports over {time.monotonic() - start:.1f}s; scenarios: {' '.join(order)}")
    deadline = time.monotonic() + args.hold
    while time.monotonic() < deadline:
        service(fd, layout, desc)
        time.sleep(0.05)
    os.write(fd, struct.pack("<I", UHID_DESTROY).ljust(EVENT_SIZE, b"\0"))
    os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
