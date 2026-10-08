# SPDX-License-Identifier: Apache-2.0
"""GRUB's hidden menu for the release just staged.

bootupd writes /boot/grub2/grub.cfg only when it installs a bootloader, so a
release that changes the bootupd pieces it ships (luma-boot-theme's
``09_luma_hidden_menu.cfg``) never reaches a computer that updates. The
release carries its own helper for that, ``luma-boot-hidden-menu``, which a
boot unit runs from the booted release; running the staged release's helper
right after staging means the very next start already uses the new menu
setting. The helper touches only its own piece of a bootupd-generated
grub.cfg and leaves an administrator's menu timing alone.

It runs as a transient unit in a private mount namespace (``MountFlags=slave``),
the way bootupd's own ``bootloader-update.service`` does, so it can remount a
read-only /boot without changing the host's mounts. It never stops an update:
the boot unit retries at every start.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

__all__ = ("HELPER", "PIECE", "command", "refresh")

HELPER = Path("usr/libexec/luma-boot-hidden-menu")
PIECE = Path("usr/lib/bootupd/grub2-static/configs.d/09_luma_hidden_menu.cfg")
TIMEOUT_SECONDS = 60


def command(root: Path) -> list[str] | None:
    """The command that brings grub.cfg up to date from the release at ``root``,
    or None when that release carries no hidden-menu helper."""
    helper, piece = Path(root) / HELPER, Path(root) / PIECE
    if not helper.is_file() or not piece.is_file():
        return None
    return ["systemd-run", "--wait", "--pipe", "--quiet", "--collect",
            "--description=Luma: GRUB hidden menu for the staged release",
            "-p", "MountFlags=slave", "-p", "PrivateNetwork=yes", "-p", "ProtectHome=yes",
            "/usr/bin/python3", "-sP", str(helper), "--remount", "--source", str(piece)]


def refresh(root: Path, run=subprocess.run) -> str | None:
    """Run the staged release's helper. Returns what it said, None when the
    release has no helper; raises RuntimeError when the helper failed."""
    argv = command(root)
    if argv is None:
        return None
    try:
        result = run(argv, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"luma-boot-hidden-menu could not run: {error}") from None
    output = " ".join(line.strip() for line in (result.stdout + result.stderr).splitlines() if line.strip())
    if result.returncode != 0:
        raise RuntimeError(f"luma-boot-hidden-menu exited {result.returncode}: {output or 'no output'}")
    return output
