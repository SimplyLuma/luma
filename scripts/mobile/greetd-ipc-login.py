#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Request one authenticated session through an existing greetd greeter.

The password is read once from standard input and is never printed or written.
This is a bounded physical-rescue helper, not a persistent login manager.
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
from typing import Any


MAX_MESSAGE_BYTES = 1024 * 1024


def send_message(stream: socket.socket, message: dict[str, Any]) -> None:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise RuntimeError("refusing oversized greetd request")
    stream.sendall(struct.pack("=I", len(payload)) + payload)


def receive_exact(stream: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = stream.recv(remaining)
        if not chunk:
            raise RuntimeError("greetd closed the socket unexpectedly")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def receive_message(stream: socket.socket) -> dict[str, Any]:
    length = struct.unpack("=I", receive_exact(stream, 4))[0]
    if length > MAX_MESSAGE_BYTES:
        raise RuntimeError("refusing oversized greetd response")
    response = json.loads(receive_exact(stream, length).decode("utf-8"))
    if not isinstance(response, dict) or not isinstance(response.get("type"), str):
        raise RuntimeError("invalid greetd response")
    return response


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--command", required=True)
    args = parser.parse_args()

    password = sys.stdin.readline().rstrip("\n")
    if not password:
        raise RuntimeError("empty password input")

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.connect(args.socket)
        send_message(stream, {"type": "create_session", "username": args.username})
        starting = False
        secret_replies = 0

        while True:
            response = receive_message(stream)
            response_type = response["type"]

            if response_type == "auth_message":
                message_type = response.get("auth_message_type")
                if message_type == "secret":
                    secret_replies += 1
                    if secret_replies > 1:
                        raise RuntimeError("refusing multiple secret prompts")
                    reply: str | None = password
                elif message_type in ("info", "error"):
                    reply = None
                else:
                    raise RuntimeError(f"unsupported authentication prompt: {message_type}")
                send_message(
                    stream,
                    {"type": "post_auth_message_response", "response": reply},
                )
            elif response_type == "success":
                if starting:
                    print("GREETD_SESSION_REQUEST=accepted")
                    return 0
                starting = True
                send_message(
                    stream,
                    {
                        "type": "start_session",
                        "cmd": [args.command],
                        "env": [],
                    },
                )
            elif response_type == "error":
                raise RuntimeError(
                    f"greetd {response.get('error_type', 'error')}: "
                    f"{response.get('description', 'no description')}"
                )
            else:
                raise RuntimeError(f"unexpected greetd response: {response_type}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
