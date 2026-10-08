# SPDX-License-Identifier: Apache-2.0
"""The only writes this producer performs: muting one PipeWire stream node.

Muting is applied to the *stream* node the application owns, never to the
capture device.  Muting the device would silence the user's microphone for
everything, including the calls this producer is not showing, and would
survive the call.  Muting the stream affects exactly the call on screen.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import Callable, Sequence


LOGGER = logging.getLogger("luma-calls")

PW_CLI = "pw-cli"
CALL_TIMEOUT_SECONDS = 5

Runner = Callable[[Sequence[str]], int]


def status(returncode: int, output: str) -> int:
    """Normalize pw-cli's result.

    Verified on the target: ``pw-cli set-param`` prints
    ``Error: "set-param: unknown global '999999'"`` and still exits 0, so a
    zero exit alone would report a write that never landed.  Any error line
    is therefore treated as a refusal.  The output is only ever inspected,
    never logged: it can echo the node's properties.
    """

    if returncode != 0:
        return returncode
    return 1 if "Error" in output else 0


def _default_runner(argv: Sequence[str]) -> int:
    executable = shutil.which(argv[0])
    if executable is None:
        raise FileNotFoundError(argv[0])
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [executable, *argv[1:]],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=CALL_TIMEOUT_SECONDS,
        check=False,
    )
    return status(
        completed.returncode, completed.stdout.decode("utf-8", "replace")
    )


def mute_command(node_id: int, muted: bool) -> list[str]:
    """Build the exact argv used to set a node's mute property."""

    if isinstance(node_id, bool) or not isinstance(node_id, int) or node_id < 0:
        raise ValueError("invalid PipeWire node identifier")
    return [
        PW_CLI,
        "set-param",
        str(node_id),
        "Props",
        json.dumps({"mute": bool(muted)}),
    ]


def set_node_mute(node_id: int, muted: bool, *, runner: Runner | None = None) -> bool:
    """Mute or unmute one node.  Returns whether PipeWire accepted the write."""

    argv = mute_command(node_id, muted)
    execute = runner or _default_runner
    try:
        status = execute(argv)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # Node ids and counts only: never the application or the call.
        LOGGER.warning(
            "pipewire mute write failed for node %d: %s", node_id, type(error).__name__
        )
        return False
    if status != 0:
        LOGGER.warning("pipewire refused the mute write for node %d", node_id)
        return False
    LOGGER.info("set mute=%s on node %d", bool(muted), node_id)
    return True
