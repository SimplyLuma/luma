#!/usr/bin/env python3
"""Generate deterministic, very-low-level FP6 speaker routing probes."""

from __future__ import annotations

import argparse
import math
import struct
import wave
from pathlib import Path


RATE = 48_000
DURATION_SECONDS = 2
FREQUENCY_HZ = 440
AMPLITUDE = 64  # Roughly -54 dBFS for signed 16-bit PCM.


def write_probe(path: Path, left: bool, right: bool) -> None:
    frames = bytearray()
    for index in range(RATE * DURATION_SECONDS):
        sample = round(AMPLITUDE * math.sin(2 * math.pi * FREQUENCY_HZ * index / RATE))
        frames.extend(struct.pack("<hh", sample if left else 0, sample if right else 0))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(frames)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_probe(args.output_dir / "quiet-stereo.wav", True, True)
    write_probe(args.output_dir / "quiet-left.wav", True, False)
    write_probe(args.output_dir / "quiet-right.wav", False, True)


if __name__ == "__main__":
    main()
