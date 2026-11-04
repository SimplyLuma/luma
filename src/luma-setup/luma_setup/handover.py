# SPDX-License-Identifier: Apache-2.0
"""The hand-over from the setup's last frame to the first session's (with Luma's gdm).

The setup signs in with its sheet on screen and its last step turning. Luma's
gdm (0001-luma-setup-handover-vt) keeps the setup's VT while
/run/luma-setup/greeter exists, so the setup keeps drawing while the session
starts. The session's Shell says when it is up with the setup's frame as its
cover (session/cover-ready, gnome-shell 0321); the setup then holds its step at
its first angle, draws that, and says go (greeter/go); gdm switches to the
session, whose first frame is the same frame, and the cover turns the step on
from there and lifts. gdm stops waiting after 30 s, the setup after 28 s.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path("/run/luma-setup")
GREETER = ROOT / "greeter"
SESSION = ROOT / "session"
GDM_SUPPORT = Path("/usr/share/gdm/luma-setup-handover")
WAIT_S = 28


def supported() -> bool:
    """Whether gdm waits for the setup's go before switching to the session."""
    return GDM_SUPPORT.exists()


def prepare(greeter_uid: int, user_uid: int) -> None:
    """Root, just before the sign-in: a place for each side to speak (luma-setup-apply)."""
    if not supported():
        return
    ROOT.mkdir(mode=0o755, exist_ok=True)
    os.chmod(ROOT, 0o755)
    for path, uid in ((GREETER, greeter_uid), (SESSION, user_uid)):
        path.mkdir(mode=0o755, exist_ok=True)
        os.chown(path, uid, -1)
        os.utime(path)
        for stale in path.iterdir():
            stale.unlink()


def _touch(path: Path) -> bool:
    try:
        path.touch()
        return True
    except OSError:
        return False


def cover_is_ready() -> bool:
    return (SESSION / "cover-ready").exists()


def waiting() -> bool:
    """Whether gdm is holding the setup's VT for this hand-over."""
    return GREETER.is_dir()


def go() -> bool:
    """The setup's frame is held: gdm may switch to the session."""
    return _touch(GREETER / "go")
