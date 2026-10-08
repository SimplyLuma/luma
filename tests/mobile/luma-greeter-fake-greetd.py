#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Bounded greetd IPC peer for the native greeter behavioral twin.

This test process compares the single secret response in memory and records
only message types. It never prints or writes the response value.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import struct
from pathlib import Path


MAX_MESSAGE = 1024 * 1024


def receive(peer: socket.socket) -> dict[str, object]:
    header = peer.recv(4)
    if len(header) != 4:
        raise RuntimeError("short greetd header")
    length = struct.unpack("=I", header)[0]
    if length <= 0 or length > MAX_MESSAGE:
        raise RuntimeError("invalid greetd request size")
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = peer.recv(remaining)
        if not chunk:
            raise RuntimeError("short greetd request")
        chunks.append(chunk)
        remaining -= len(chunk)
    message = json.loads(b"".join(chunks))
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise RuntimeError("invalid greetd request")
    return message


def send(peer: socket.socket, message: dict[str, object]) -> None:
    payload = json.dumps(message, separators=(",", ":")).encode()
    peer.sendall(struct.pack("=I", len(payload)) + payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--outcome", choices=("success", "failure"), required=True)
    args = parser.parse_args()
    expected = os.environ.pop("LUMA_TEST_EXPECTED_PIN", None)
    if expected is None or len(expected) != 4 or not expected.isdecimal():
        raise RuntimeError("test PIN must be four decimal digits")

    args.socket.unlink(missing_ok=True)
    events: list[str] = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(args.socket))
        os.chmod(args.socket, 0o600)
        listener.listen(1)
        with listener.accept()[0] as peer:
            request = receive(peer)
            events.append(str(request["type"]))
            if request["type"] != "create_session" or request.get("username") != "nick":
                raise RuntimeError("unexpected create_session request")
            send(
                peer,
                {
                    "type": "auth_message",
                    "auth_message_type": "secret",
                    "auth_message": "Password:",
                },
            )
            response = receive(peer)
            events.append(str(response["type"]))
            accepted = (
                response["type"] == "post_auth_message_response"
                and response.get("response") == expected
            )
            expected = "\0" * len(expected)
            if not accepted or args.outcome == "failure":
                send(
                    peer,
                    {
                        "type": "error",
                        "error_type": "auth_error",
                        "description": "Authentication failed",
                    },
                )
                try:
                    cancel = receive(peer)
                    events.append(str(cancel["type"]))
                except (RuntimeError, ConnectionError):
                    pass
            else:
                send(peer, {"type": "success"})
                start = receive(peer)
                events.append(str(start["type"]))
                expected = (
                    "/usr/bin/env LUMA_DEVICE_CLASS=handheld "
                    "LUMA_PRESENTATION_MODE=fullscreen-mobile LUMA_INPUT_MODE=touch "
                    "/opt/luma/phosh/bin/phosh-session >/dev/null 2>&1"
                )
                if start.get("cmd") != [expected]:
                    raise RuntimeError("unexpected authenticated session command")
                send(peer, {"type": "success"})

    args.result.write_text("\n".join(events) + "\n", encoding="utf-8")
    os.chmod(args.result, 0o600)
    args.socket.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
