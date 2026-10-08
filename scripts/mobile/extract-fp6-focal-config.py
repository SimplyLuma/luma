#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Extract the FocalTech configuration embedded in the exact FP6 stock HAL.

The QREL 16.95.0 fingerprint HAL stores its default JSON as a bytewise
XOR-obfuscated blob.  This tool is deliberately tied to that one audited ELF
and fails closed for every other input.  It never opens a device or sends a
trustlet command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


HAL_SHA256 = "ae08b39c8c78b40a769795684afc7d342c19acf8c1b443fdc56be47b5ca8bedd"
EMBEDDED_OFFSET = 0x37A70
EMBEDDED_LENGTH = 0x6A50
XOR_KEY = 0x84
SOURCE_SHA256 = "9cf66476f53ebac0103282b0e73b2f828c7ae7129dee458500b6c3b475523f4f"
COMPACT_SHA256 = "e8265e63afffd2f9a87149149db65f52a19eb131efe8f43b705d3b7ea8f2c892"
PAYLOAD_SHA256 = "8b013facc735799355dd39b78d0df0a11b547b5faa0f93ff18dd080a78a220c5"
LINUX_NATIVE_ENROLLMENT_PAYLOAD_SHA256 = (
    "0b34dac598933331f43866dcbce18feda9909efaa4726a19ffccf100ecb29f10"
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_line_comments(source: str) -> str:
    """Remove // comments without changing string contents or key order."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False

    while index < len(source):
        char = source[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue

        if char == "/" and index + 1 < len(source) and source[index + 1] == "/":
            index += 2
            while index < len(source) and source[index] not in "\r\n":
                index += 1
            continue

        output.append(char)
        index += 1

    if in_string or escaped:
        raise ValueError("unterminated string in decoded configuration")
    return "".join(output)


def extract(hal: bytes) -> tuple[bytes, bytes]:
    if sha256(hal) != HAL_SHA256:
        raise ValueError("input is not the audited QREL 16.95.0 stock fingerprint HAL")

    end = EMBEDDED_OFFSET + EMBEDDED_LENGTH
    if len(hal) <= end or hal[end] != 0:
        raise ValueError("embedded blob boundary or trailing terminator differs")

    encoded = hal[EMBEDDED_OFFSET:end]
    source = bytes(byte ^ XOR_KEY for byte in encoded)
    if sha256(source) != SOURCE_SHA256:
        raise ValueError("decoded source hash differs")

    text = source.decode("utf-8")
    if "/*" in text or "*/" in text:
        raise ValueError("unexpected block comment in decoded configuration")
    parsed = json.loads(strip_line_comments(text))
    compact = json.dumps(
        parsed,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if sha256(compact) != COMPACT_SHA256:
        raise ValueError("normalized configuration hash differs")
    return source, compact


def render_c_header(payload: bytes, expected_payload_sha256: str) -> bytes:
    """Render the exact NUL-terminated payload as a private kernel header."""
    if sha256(payload) != expected_payload_sha256:
        raise ValueError("0x100d payload hash differs")

    lines = [
        "/* Generated from the audited FP6 QREL 16.95.0 stock HAL. */",
        "#ifndef LUMA_FP6_FOCAL_CONFIG_H",
        "#define LUMA_FP6_FOCAL_CONFIG_H",
        "",
        "static const u8 luma_focal_stock_config[] = {",
    ]
    for offset in range(0, len(payload), 12):
        values = ", ".join(f"0x{byte:02x}" for byte in payload[offset : offset + 12])
        lines.append(f"\t{values},")
    lines.extend(
        [
            "};",
            f"#define LUMA_FOCAL_STOCK_CONFIG_SIZE {len(payload)}U",
            f'#define LUMA_FOCAL_STOCK_CONFIG_SHA256 "{expected_payload_sha256}"',
            "",
            "#endif",
            "",
        ]
    )
    return "\n".join(lines).encode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hal", type=Path, help="exact fingerprint.default.so")
    parser.add_argument("output", type=Path, help="explicit output file")
    parser.add_argument(
        "--format",
        choices=("compact", "source", "c-header"),
        default="compact",
        help="compact JSON, decoded source, or a private kernel C header",
    )
    parser.add_argument(
        "--nul",
        action="store_true",
        help="append the NUL byte included in the 0x100d request payload",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing output file",
    )
    parser.add_argument(
        "--linux-native-enrollment",
        action="store_true",
        help=(
            "change only trustlet.enable_trusted_enrollment from true to "
            "false so Linux PAM/root authorizes administrative enrollment"
        ),
    )
    args = parser.parse_args()

    try:
        source, compact = extract(args.hal.read_bytes())
        expected_payload_sha256 = PAYLOAD_SHA256
        if args.linux_native_enrollment:
            if args.format == "source":
                raise ValueError(
                    "Linux-native enrollment is defined only for normalized JSON"
                )
            parsed = json.loads(compact.decode("utf-8"))
            if parsed.get("trustlet", {}).get("enable_trusted_enrollment") is not True:
                raise ValueError("trusted-enrollment source setting differs")
            parsed["trustlet"]["enable_trusted_enrollment"] = False
            compact = json.dumps(
                parsed,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            expected_payload_sha256 = LINUX_NATIVE_ENROLLMENT_PAYLOAD_SHA256
        payload = compact if args.format in ("compact", "c-header") else source
        if args.nul:
            payload += b"\0"
        if args.format == "c-header":
            if not args.nul:
                raise ValueError("c-header output requires --nul")
            output_bytes = render_c_header(payload, expected_payload_sha256)
        else:
            output_bytes = payload
        mode = "wb" if args.force else "xb"
        with args.output.open(mode) as output:
            output.write(output_bytes)
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    print(
        f"format={args.format} bytes={len(payload)} nul={int(args.nul)} "
        f"sha256={sha256(payload)} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
