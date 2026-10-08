#!/usr/bin/env python3
"""Generate and verify a bounded acoustic FP6 microphone stimulus."""

from __future__ import annotations

import argparse
import json
import math
import struct
import wave
from pathlib import Path


RATE = 48_000
FREQUENCY_HZ = 997
SECONDS = 7
TONE_START = 1
TONE_END = 4
AMPLITUDE = 1024  # -30 dBFS: distinct but intentionally quiet.


def generate(path: Path) -> None:
    frames = bytearray()
    for index in range(RATE * SECONDS):
        second = index / RATE
        sample = 0
        if TONE_START <= second < TONE_END:
            sample = round(
                AMPLITUDE * math.sin(2 * math.pi * FREQUENCY_HZ * second)
            )
        frames.extend(struct.pack("<hh", sample, sample))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(frames)


def goertzel(samples: list[int], frequency: float) -> float:
    omega = 2 * math.pi * frequency / RATE
    coefficient = 2 * math.cos(omega)
    q1 = 0.0
    q2 = 0.0
    for sample in samples:
        q0 = coefficient * q1 - q2 + sample
        q2, q1 = q1, q0
    power = q1 * q1 + q2 * q2 - coefficient * q1 * q2
    return math.sqrt(max(power, 0.0)) / max(len(samples), 1)


def rms(samples: list[int]) -> float:
    return math.sqrt(sum(sample * sample for sample in samples) / max(len(samples), 1))


def analyze(path: Path, selected_channel: int, search_tone: bool) -> bool:
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        width = source.getsampwidth()
        rate = source.getframerate()
        frames = source.getnframes()
        payload = source.readframes(frames)
    if width != 2 or rate != RATE or channels not in (1, 2):
        raise SystemExit(
            f"unsupported capture geometry: {channels}ch/{width * 8}bit/{rate}Hz"
        )
    values = struct.unpack(f"<{len(payload) // 2}h", payload)
    if not 0 <= selected_channel < channels:
        raise SystemExit(
            f"capture has {channels} channel(s); channel {selected_channel} is absent"
        )
    channel = list(values[selected_channel::channels])
    per_second = [
        round(rms(channel[offset : offset + RATE]), 4)
        for offset in range(0, min(len(channel), RATE * SECONDS), RATE)
    ]
    tone_start = TONE_START
    if search_tone:
        window_seconds = TONE_END - TONE_START
        latest_start = len(channel) // RATE - window_seconds
        candidates = range(1, max(2, latest_start + 1))
        tone_start = max(
            candidates,
            key=lambda start: goertzel(
                channel[RATE * start : RATE * (start + window_seconds)],
                FREQUENCY_HZ,
            ),
        )
    tone_end = tone_start + (TONE_END - TONE_START)
    tone = channel[RATE * tone_start : RATE * tone_end]
    after_startup = channel[RATE:]
    target = goertzel(tone, FREQUENCY_HZ)
    shoulders = max(goertzel(tone, FREQUENCY_HZ - 37), goertzel(tone, FREQUENCY_HZ + 37))
    nonzero_after_startup = sum(sample != 0 for sample in after_startup)
    accepted = (
        len(channel) >= RATE * SECONDS
        and nonzero_after_startup > RATE
        and rms(tone) > 3.0
        and target > max(shoulders * 3.0, 1.0)
    )
    print(
        json.dumps(
            {
                "accepted": accepted,
                "analyzed_channel": selected_channel,
                "channels": channels,
                "frames": frames,
                "nonzero_after_startup": nonzero_after_startup,
                "per_second_rms": per_second,
                "target_hz": FREQUENCY_HZ,
                "tone_start_second": tone_start,
                "target_amplitude": round(target, 4),
                "shoulder_amplitude": round(shoulders, 4),
                "target_to_shoulder": round(target / max(shoulders, 1e-9), 4),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return accepted


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("path", type=Path)
    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("path", type=Path)
    analyze_parser.add_argument("--channel", type=int, default=0)
    analyze_parser.add_argument("--search-tone", action="store_true")
    args = parser.parse_args()
    if args.command == "generate":
        generate(args.path)
        return
    if not analyze(args.path, args.channel, args.search_tone):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
