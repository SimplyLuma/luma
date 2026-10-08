from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import threading
from pathlib import Path

from .config import load_config
from .engine import WineEngine
from .errors import RelayError
from .native_notifications import NotificationBroker
from .registry import capsule_root, read_manifest
from .sandbox import sandbox_command
from .wine_integration import sync_relay_bridge


def _create_notification_broker(
    socket_path: Path, manifest: dict[str, object]
) -> NotificationBroker | None:
    """Keep application launch independent from the desktop notification service."""
    try:
        return NotificationBroker(socket_path, manifest)
    except (OSError, RelayError):
        return None


def run(app_id: str) -> int:
    manifest = read_manifest(app_id)
    root = capsule_root(app_id)
    sync_relay_bridge(root)
    notifications_enabled = bool(manifest.get("notifications", True))
    stop = threading.Event()
    thread: threading.Thread | None = None
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    runtime_key = hashlib.sha256(app_id.encode("utf-8")).hexdigest()[:16]
    socket_directory = runtime / "luma-relay" / runtime_key
    socket_path = socket_directory / "notify.sock"
    broker: NotificationBroker | None = None
    if notifications_enabled:
        broker = _create_notification_broker(socket_path, manifest)
        if broker is not None:
            ready = threading.Event()
            thread = threading.Thread(
                target=broker.serve, args=(stop, ready), daemon=True
            )
            thread.start()
            ready.wait(timeout=1)
            if not socket_path.is_socket():
                broker = None

    command = [
        "/bin/sh",
        "-c",
        '/usr/bin/wine "$1"; status=$?; /usr/bin/wineserver -w; exit "$status"',
        "luma-relay-session",
        str(manifest["executable"]),
    ]
    argv, environment = sandbox_command(
        root,
        command,
        network=bool(manifest.get("network", True)),
        grants=list(manifest.get("grants", [])),
        notification_socket="/relay-notify/notify.sock" if broker is not None else None,
        notification_socket_host=socket_path if broker is not None else None,
        notifications_enabled=notifications_enabled,
    )
    try:
        return subprocess.run(argv, env=environment, check=False).returncode
    finally:
        stop.set()
        if thread is not None:
            thread.join(timeout=2)
        socket_path.unlink(missing_ok=True)
        try:
            socket_directory.rmdir()
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="luma-relay-session")
    parser.add_argument("app_id")
    arguments = parser.parse_args(argv)
    try:
        if not WineEngine(load_config()).available:
            raise RelayError("The Relay Windows runtime is not installed on this device.")
        return run(arguments.app_id)
    except RelayError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
