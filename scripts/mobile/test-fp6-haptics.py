#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Inspect or perform one tightly bounded FP6 force-feedback pulse."""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import glob
import os
import struct
import sys
import time

EXPECTED_MODEL = "The Fairphone (Gen. 6)"
EXPECTED_INPUT = "aw86927-haptics"
EV_FF = 0x15
FF_RUMBLE = 0x50
MAX_MAGNITUDE = 24576
MAX_DURATION_MS = 150


class FFTrigger(ctypes.Structure):
    _fields_ = [("button", ctypes.c_uint16), ("interval", ctypes.c_uint16)]


class FFReplay(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint16), ("delay", ctypes.c_uint16)]


class FFEnvelope(ctypes.Structure):
    _fields_ = [
        ("attack_length", ctypes.c_uint16),
        ("attack_level", ctypes.c_uint16),
        ("fade_length", ctypes.c_uint16),
        ("fade_level", ctypes.c_uint16),
    ]


class FFConstant(ctypes.Structure):
    _fields_ = [("level", ctypes.c_int16), ("envelope", FFEnvelope)]


class FFRamp(ctypes.Structure):
    _fields_ = [
        ("start_level", ctypes.c_int16),
        ("end_level", ctypes.c_int16),
        ("envelope", FFEnvelope),
    ]


class FFPeriodic(ctypes.Structure):
    _fields_ = [
        ("waveform", ctypes.c_uint16),
        ("period", ctypes.c_uint16),
        ("magnitude", ctypes.c_int16),
        ("offset", ctypes.c_int16),
        ("phase", ctypes.c_uint16),
        ("envelope", FFEnvelope),
        ("custom_len", ctypes.c_uint32),
        ("custom_data", ctypes.POINTER(ctypes.c_int16)),
    ]


class FFCondition(ctypes.Structure):
    _fields_ = [
        ("right_saturation", ctypes.c_uint16),
        ("left_saturation", ctypes.c_uint16),
        ("right_coeff", ctypes.c_int16),
        ("left_coeff", ctypes.c_int16),
        ("deadband", ctypes.c_uint16),
        ("center", ctypes.c_int16),
    ]


class FFRumble(ctypes.Structure):
    _fields_ = [("strong_magnitude", ctypes.c_uint16), ("weak_magnitude", ctypes.c_uint16)]


class FFUnion(ctypes.Union):
    _fields_ = [
        ("constant", FFConstant),
        ("ramp", FFRamp),
        ("periodic", FFPeriodic),
        ("condition", FFCondition * 2),
        ("rumble", FFRumble),
    ]


class FFEffect(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint16),
        ("id", ctypes.c_int16),
        ("direction", ctypes.c_uint16),
        ("trigger", FFTrigger),
        ("replay", FFReplay),
        ("u", FFUnion),
    ]


def ioc(direction: int, io_type: str, number: int, size: int) -> int:
    return (direction << 30) | (ord(io_type) << 8) | number | (size << 16)


def eviocgname(length: int) -> int:
    return ioc(2, "E", 0x06, length)


def eviocgbit(event_type: int, length: int) -> int:
    return ioc(2, "E", 0x20 + event_type, length)


EVIOCSFF = ioc(1, "E", 0x80, ctypes.sizeof(FFEffect))
EVIOCRMFF = ioc(1, "E", 0x81, struct.calcsize("i"))


def device_name(fd: int) -> str:
    value = bytearray(256)
    fcntl.ioctl(fd, eviocgname(len(value)), value, True)
    return value.split(b"\0", 1)[0].decode("utf-8", "replace")


def has_rumble(fd: int) -> bool:
    bits = bytearray(16)
    fcntl.ioctl(fd, eviocgbit(EV_FF, len(bits)), bits, True)
    return bool(bits[FF_RUMBLE // 8] & (1 << (FF_RUMBLE % 8)))


def find_device() -> tuple[str, int]:
    matches: list[tuple[str, int]] = []
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
            if device_name(fd) == EXPECTED_INPUT:
                matches.append((path, fd))
            else:
                os.close(fd)
        except OSError:
            continue
    if len(matches) != 1:
        for _, fd in matches:
            os.close(fd)
        raise RuntimeError(f"expected exactly one {EXPECTED_INPUT!r} device, found {len(matches)}")
    return matches[0]


def write_event(fd: int, code: int, value: int) -> None:
    event = struct.pack("llHHi", 0, 0, EV_FF, code, value)
    if os.write(fd, event) != len(event):
        raise RuntimeError("short input-event write")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pulse", action="store_true", help="perform one bounded pulse")
    parser.add_argument(
        "--acknowledge-physical-actuation",
        action="store_true",
        help="required with --pulse",
    )
    parser.add_argument("--duration-ms", type=int, default=60)
    parser.add_argument("--magnitude", type=int, default=16384)
    args = parser.parse_args()

    model = open("/proc/device-tree/model", "rb").read().rstrip(b"\0").decode()
    if model != EXPECTED_MODEL:
        raise RuntimeError(f"device identity differs: {model!r}")
    if ctypes.sizeof(FFEffect) != 48 or struct.calcsize("llHHi") != 24:
        raise RuntimeError("unsupported input ABI; expected Linux AArch64")
    if not 1 <= args.duration_ms <= MAX_DURATION_MS:
        raise RuntimeError(f"duration must be 1..{MAX_DURATION_MS} ms")
    if not 1 <= args.magnitude <= MAX_MAGNITUDE:
        raise RuntimeError(f"magnitude must be 1..{MAX_MAGNITUDE}")
    if args.pulse and not args.acknowledge_physical_actuation:
        raise RuntimeError("--pulse requires --acknowledge-physical-actuation")

    path, fd = find_device()
    try:
        rumble = has_rumble(fd)
        print("LUMA_FP6_HAPTIC_TEST_VERSION=1")
        print(f"DEVICE={path}")
        print(f"INPUT_NAME={EXPECTED_INPUT}")
        print(f"FF_RUMBLE={'true' if rumble else 'false'}")
        print(f"ACTUATION_REQUESTED={'true' if args.pulse else 'false'}")
        if not rumble:
            raise RuntimeError("device does not advertise FF_RUMBLE")
        if not args.pulse:
            print("MUTATING_OPERATIONS=none")
            return 0

        effect = FFEffect(type=FF_RUMBLE, id=-1)
        effect.replay.length = args.duration_ms
        effect.u.rumble.strong_magnitude = args.magnitude
        payload = bytearray(bytes(effect))
        fcntl.ioctl(fd, EVIOCSFF, payload, True)
        effect_id = FFEffect.from_buffer_copy(payload).id
        if effect_id < 0:
            raise RuntimeError("kernel did not allocate a force-feedback effect")
        try:
            write_event(fd, effect_id, 1)
            time.sleep(args.duration_ms / 1000 + 0.05)
        finally:
            write_event(fd, effect_id, 0)
            # EVIOCRMFF is encoded as _IOW for historical ABI reasons, but
            # evdev consumes the ioctl argument value itself, not a pointer to
            # an integer.  Passing a packed buffer makes Python supply its
            # address and the kernel correctly rejects that bogus effect ID.
            fcntl.ioctl(fd, EVIOCRMFF, effect_id)
        print(f"DURATION_MS={args.duration_ms}")
        print(f"MAGNITUDE={args.magnitude}")
        print("PULSE_COMPLETED=true")
        print("PERSISTENT_MUTATIONS=none")
        return 0
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
