# SPDX-License-Identifier: Apache-2.0

"""The operating system's own updates, read rather than driven.

`src/luma-update-client/` already owns this: a timer asks rpm-ostree to stage an
accepted deployment from the signed Recent channel, and deliberately does not
reboot, live-apply, change channels, or discard the booted rollback deployment.
Depot's job is to *say what that has done*, not to do it again.

So everything here is read-only:

* enrollment is the presence and content of `/etc/luma/update-channel.conf`,
  which the enrollment tool writes and this never touches;
* deployments come from `rpm-ostree status --json`, which is unprivileged;
* nothing in Depot stages, reboots or rolls back.

**Proposed interface, not yet built:** `luma-recent-update --status --json`
would be a better source than parsing rpm-ostree's output, because it would
report the client's own view — when the timer last ran, and whether the staged
deployment came from the accepted channel. That belongs to the update client,
so it is proposed here rather than added there.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import pathlib
import shutil
import subprocess

from .providers import run_async


CHANNEL_CONFIG = pathlib.Path(os.environ.get("LUMA_UPDATE_CONFIG",
                                             "/etc/luma/update-channel.conf"))


@dataclass(frozen=True)
class OsState:
    enrolled: bool
    channel: str = ""
    remote: str = ""
    ref: str = ""
    booted_version: str = ""
    staged_version: str = ""
    staged_bytes: int = 0
    rollback_version: str = ""
    reason: str = ""
    notes: tuple[str, ...] = ()

    @property
    def update_ready(self) -> bool:
        return bool(self.staged_version)

    @property
    def rollback_kept(self) -> bool:
        return bool(self.rollback_version) or bool(self.booted_version)


def _read_channel() -> tuple[bool, dict[str, str], str]:
    if not CHANNEL_CONFIG.is_file():
        return False, {}, (
            "This computer is not enrolled in an update channel. Luma's Recent "
            "channel is opt-in: until someone enrolls this image against a "
            "channel and its signing key, there is nothing for Depot to check."
        )
    try:
        values: dict[str, str] = {}
        for line in CHANNEL_CONFIG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
        return True, values, ""
    except OSError as error:
        return False, {}, f"The channel configuration could not be read: {error}"


def _read_deployments() -> tuple[str, str, str, tuple[str, ...]]:
    """Booted, staged and rollback versions, from rpm-ostree's own report."""
    if shutil.which("rpm-ostree") is None:
        return "", "", "", ()
    try:
        output = subprocess.run(["rpm-ostree", "status", "--json"],
                                capture_output=True, text=True, timeout=20, check=False)
        data = json.loads(output.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return "", "", "", ()
    deployments = data.get("deployments", []) or []
    booted = staged = rollback = ""
    for deployment in deployments:
        version = str(deployment.get("version") or deployment.get("base-checksum") or "")
        if deployment.get("booted"):
            booted = version
        elif deployment.get("staged"):
            staged = version
        elif not rollback:
            rollback = version
    return booted, staged, rollback, ()


def read_state() -> OsState:
    enrolled, values, reason = _read_channel()
    booted, staged, rollback, notes = _read_deployments()
    if not enrolled:
        return OsState(enrolled=False, reason=reason, booted_version=booted)
    if shutil.which("rpm-ostree") is None:
        return OsState(
            enrolled=True,
            channel=values.get("channel", ""),
            remote=values.get("remote", ""),
            ref=values.get("ref", ""),
            reason="This image is enrolled, but rpm-ostree is not installed, so no "
                   "deployment can be staged or reported.",
        )
    return OsState(
        enrolled=True,
        channel=values.get("channel", ""),
        remote=values.get("remote", ""),
        ref=values.get("ref", ""),
        booted_version=booted,
        staged_version=staged,
        rollback_version=rollback,
        notes=notes,
    )


class OsUpdateReader:
    """Asynchronous, like everything else that crosses a boundary."""

    def state(self, callback, cancellable=None) -> None:
        run_async(read_state, callback, cancellable)
