#!/usr/bin/env python3
"""Decode a stride-padded MIPI packed RAW10 frame to a half-size PNG preview."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image


def unpack_raw10(data: bytes, width: int, height: int, stride: int) -> np.ndarray:
    if width % 4:
        raise ValueError("RAW10 width must be divisible by four")
    expected = stride * height
    if len(data) != expected:
        raise ValueError(f"expected {expected} bytes, found {len(data)}")

    packed_width = width * 5 // 4
    rows = np.frombuffer(data, dtype=np.uint8).reshape(height, stride)
    groups = rows[:, :packed_width].reshape(height, width // 4, 5).astype(np.uint16)
    pixels = np.empty((height, width), dtype=np.uint16)
    low = groups[:, :, 4]
    pixels[:, 0::4] = (groups[:, :, 0] << 2) | (low & 0x03)
    pixels[:, 1::4] = (groups[:, :, 1] << 2) | ((low >> 2) & 0x03)
    pixels[:, 2::4] = (groups[:, :, 2] << 2) | ((low >> 4) & 0x03)
    pixels[:, 3::4] = (groups[:, :, 3] << 2) | ((low >> 6) & 0x03)
    return pixels


def bayer_preview(raw: np.ndarray, pattern: str) -> np.ndarray:
    """Bin each Bayer cell to one RGB output pixel."""
    cells = {
        "RGGB": (raw[0::2, 0::2], raw[0::2, 1::2], raw[1::2, 0::2], raw[1::2, 1::2]),
        "GRBG": (raw[0::2, 1::2], raw[0::2, 0::2], raw[1::2, 1::2], raw[1::2, 0::2]),
        "GBRG": (raw[1::2, 0::2], raw[0::2, 0::2], raw[1::2, 1::2], raw[0::2, 1::2]),
        "BGGR": (raw[1::2, 1::2], raw[0::2, 1::2], raw[1::2, 0::2], raw[0::2, 0::2]),
    }
    red, green_a, green_b, blue = cells[pattern]
    green = (green_a.astype(np.uint32) + green_b) // 2
    rgb = np.stack((red, green, blue), axis=-1).astype(np.float32)

    black = float(np.percentile(rgb, 0.5))
    white = float(np.percentile(rgb, 99.5))
    if white <= black:
        raise ValueError("frame has no usable pixel range")
    rgb = np.clip((rgb - black) / (white - black), 0.0, 1.0)
    rgb = np.power(rgb, 1.0 / 2.2)
    return np.rint(rgb * 255.0).astype(np.uint8)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--stride", type=int, required=True)
    parser.add_argument(
        "--bayer",
        choices=("RGGB", "GRBG", "GBRG", "BGGR"),
        default="GRBG",
        help="Bayer order at the top-left pixel (default: GRBG)",
    )
    args = parser.parse_args()

    raw = unpack_raw10(args.input.read_bytes(), args.width, args.height, args.stride)
    print(
        f"pixels min={raw.min()} max={raw.max()} mean={raw.mean():.2f} "
        f"p01={np.percentile(raw, 1):.1f} p99={np.percentile(raw, 99):.1f}"
    )
    Image.fromarray(bayer_preview(raw, args.bayer), "RGB").save(args.output)


if __name__ == "__main__":
    main()
