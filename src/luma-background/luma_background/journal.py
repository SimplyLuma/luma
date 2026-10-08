# SPDX-License-Identifier: MPL-2.0
"""Structured journal entries, keyed by the app they concern.

Luma Vitals reads `LUMA_APP_ID` to say "crashed in the background". The
native journal protocol is a few lines, so the service needs no binding for
it; when there is no journal (a test, a container) it falls back to stderr.
"""

from __future__ import annotations

import os
import socket
import sys

JOURNAL_SOCKET = "/run/systemd/journal/socket"
PRIORITIES = {"error": 3, "warning": 4, "notice": 5, "info": 6, "debug": 7}
_socket: socket.socket | None = None


def _field(key: str, value: str) -> bytes:
    data = value.encode("utf-8", "replace")
    if b"\n" not in data:
        return key.encode() + b"=" + data + b"\n"
    return key.encode() + b"\n" + len(data).to_bytes(8, "little") + data + b"\n"


def send(message: str, *, priority: str = "info", app_id: str = "", event: str = "",
         **fields: str) -> None:
    global _socket
    payload = [
        _field("MESSAGE", message),
        _field("PRIORITY", str(PRIORITIES.get(priority, 6))),
        _field("SYSLOG_IDENTIFIER", "luma-background"),
    ]
    if app_id:
        payload.append(_field("LUMA_APP_ID", app_id))
    if event:
        payload.append(_field("LUMA_BACKGROUND_EVENT", event))
    for key, value in fields.items():
        if key.isupper() and key.replace("_", "").isalnum() and not key.startswith("_"):
            payload.append(_field(key, str(value)))
    data = b"".join(payload)
    if os.environ.get("LUMA_BACKGROUND_LOG_STDERR") != "1":
        try:
            if _socket is None:
                _socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            _socket.sendto(data, JOURNAL_SOCKET)
            return
        except OSError:
            pass
    prefix = f"<{PRIORITIES.get(priority, 6)}>"
    context = f" [{app_id}]" if app_id else ""
    print(f"{prefix}luma-background{context}: {message}", file=sys.stderr, flush=True)
