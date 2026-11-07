# SPDX-License-Identifier: Apache-2.0
"""Wait for an existing recovery and replay only interrupted launch requests."""
from __future__ import annotations

import os
from pathlib import Path
import pwd
import re
import subprocess
import time

from .engine import _session_identity
from .errors import RuntimeUnavailableError

RECOVERY_WAIT_SECONDS = 30


def _recovery_state() -> str | None:
    owner = pwd.getpwuid(os.getuid()).pw_name
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", owner):
        return None
    try:
        result = subprocess.run(
            ["/usr/bin/systemctl", "show", f"luma-android-recover@{owner}.service",
             "--property=ActiveState", "--value"],
            capture_output=True, text=True, check=False, timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def wait_for_recovery(timeout: float = RECOVERY_WAIT_SECONDS) -> bool:
    """Wait for a recovery already running; never start or reset the runtime."""
    deadline = time.monotonic() + timeout
    waited = False
    while True:
        state = _recovery_state()
        if state == "failed":
            raise RuntimeUnavailableError("Android is unavailable after a failed recovery.")
        if state not in {"activating", "deactivating", "reloading"}:
            if waited and state is None:
                raise RuntimeUnavailableError("Android recovery status became unavailable.")
            return waited
        waited = True
        if time.monotonic() >= deadline:
            raise RuntimeUnavailableError("Android is still recovering. Please try again shortly.")
        time.sleep(0.25)


def recovered_since(identity: str | None) -> bool:
    waited = wait_for_recovery()
    current = _session_identity()
    return waited or (current is not None and current != identity)


def launch_with_recovery(engine, package: str, restore) -> None:
    """Replay once only when a runtime transition interrupted this launch.

    Restore/close RPCs retain their existing authority: the caller that already
    requested a launch owns this replay. A missing window alone is insufficient.
    """
    def dispatch():
        engine.launch(package)
        return restore(package)

    if Path(engine.executable).name != "waydroid":
        dispatch()
        return
    identity = _session_identity()
    try:
        restored = dispatch()
    except RuntimeUnavailableError:
        if not recovered_since(identity):
            raise
    else:
        if restored or not recovered_since(identity):
            return
    # Deliberately outside the try: a second failure is returned, not retried.
    dispatch()
