#!/usr/bin/python3
"""Copy files to, or run bounded commands in, a libvirt lab guest via QGA.

This intentionally uses the QEMU guest agent rather than exposing a guest
network service. Run it on the libvirt host as root (or through sudo).
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path


CHUNK_SIZE = 48 * 1024
POLL_INTERVAL_SECONDS = 0.1
POLL_TIMEOUT_SECONDS = 120


def qga(vm_name: str, request: dict) -> dict:
    completed = subprocess.run(
        ["virsh", "qemu-agent-command", vm_name, json.dumps(request)],
        check=True,
        capture_output=True,
        text=True,
    )
    response = json.loads(completed.stdout)
    if "error" in response:
        raise RuntimeError(response["error"])
    return response["return"]


def guest_exec(vm_name: str, command: list[str], *, capture: bool = True) -> int:
    result = qga(
        vm_name,
        {
            "execute": "guest-exec",
            "arguments": {
                "path": command[0],
                "arg": command[1:],
                "capture-output": capture,
            },
        },
    )
    pid = result["pid"]
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status = qga(
            vm_name,
            {
                "execute": "guest-exec-status",
                "arguments": {"pid": pid},
            },
        )
        if not status.get("exited"):
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        for key, destination in (
            ("out-data", sys.stdout.buffer),
            ("err-data", sys.stderr.buffer),
        ):
            if key in status:
                destination.write(base64.b64decode(status[key]))
                destination.flush()
        return int(status.get("exitcode", 0))
    raise TimeoutError(f"guest command did not finish within {POLL_TIMEOUT_SECONDS}s")


def guest_copy(vm_name: str, source: Path, destination: str, mode: str) -> None:
    handle = qga(
        vm_name,
        {
            "execute": "guest-file-open",
            "arguments": {"path": destination, "mode": "wb"},
        },
    )
    try:
        with source.open("rb") as stream:
            while chunk := stream.read(CHUNK_SIZE):
                written = qga(
                    vm_name,
                    {
                        "execute": "guest-file-write",
                        "arguments": {
                            "handle": handle,
                            "buf-b64": base64.b64encode(chunk).decode("ascii"),
                        },
                    },
                )["count"]
                if written != len(chunk):
                    raise OSError(
                        f"short guest-agent write: expected {len(chunk)}, got {written}"
                    )
        qga(
            vm_name,
            {"execute": "guest-file-flush", "arguments": {"handle": handle}},
        )
    finally:
        qga(
            vm_name,
            {"execute": "guest-file-close", "arguments": {"handle": handle}},
        )

    exit_code = guest_exec(vm_name, ["/usr/bin/chmod", mode, destination])
    if exit_code:
        raise RuntimeError(f"chmod failed with exit status {exit_code}")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("vm_name")
    subparsers = parser.add_subparsers(dest="operation", required=True)

    copy_parser = subparsers.add_parser("copy")
    copy_parser.add_argument("source", type=Path)
    copy_parser.add_argument("destination")
    copy_parser.add_argument("--mode", default="0644")

    exec_parser = subparsers.add_parser("exec")
    exec_parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    if args.operation == "copy":
        guest_copy(args.vm_name, args.source, args.destination, args.mode)
        return 0
    if not args.command:
        raise SystemExit("exec requires a command after --")
    command = args.command[1:] if args.command[0] == "--" else args.command
    return guest_exec(args.vm_name, command)


if __name__ == "__main__":
    raise SystemExit(main())
