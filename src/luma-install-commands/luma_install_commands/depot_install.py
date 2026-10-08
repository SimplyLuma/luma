# SPDX-License-Identifier: MPL-2.0
"""Installing a name Fedora does not package, from Depot's catalogue.

Used by ``apt``/``apt-get`` and ``dnf``/``yum`` once their own resolution
(Fedora's repositories, the Debian name table) has found nothing: see
``depot_catalog`` for what is looked up and why it needs no separate trust
of its own.
"""

from __future__ import annotations

import subprocess
from typing import Callable

from .depot_catalog import Match

FLATPAK = "/usr/bin/flatpak"


def choose(matches: list[Match], say: Callable[[str], None], *, interactive: bool,
          read_line: Callable[[], str] | None = None) -> Match | None:
    """One match is used as is. Several are offered as a short numbered list.

    ``interactive`` and ``read_line`` come from the caller's own stdin (real
    ``sys.stdin`` for apt, ``Context.stdin`` for dnf, so tests can supply
    one), never read here: a caller piping input, like the rest of this
    package, gets a plain message and nothing installed -- never a hang.
    """
    if len(matches) == 1:
        return matches[0]
    say("More than one application matches that name:")
    for index, match in enumerate(matches, 1):
        say(f"  {index}) {match.name} ({match.repository or match.backend})")
    if not interactive or read_line is None:
        say(f"Run the command again with the number (1-{len(matches)}) or the exact name.")
        return None
    say(f"Which one? [1-{len(matches)}]")
    try:
        reply = read_line().strip()
    except (EOFError, KeyboardInterrupt, OSError, ValueError):
        return None
    if reply.isdigit() and 1 <= int(reply) <= len(matches):
        return matches[int(reply) - 1]
    say("Not one of the choices; nothing installed.")
    return None


def install_flatpak(match: Match, say: Callable[[str], None], runner=subprocess.run) -> int:
    """Install a Flatpak-backed match from its (already-configured, trusted) remote.

    A system Flatpak install needs no reboot and dnf5/rpm-ostree play no part
    in it; it shows up in Depot's installed list and the app grid the way any
    Flatpak does, through the desktop files flatpak itself exports.
    """
    repo = match.repository or "flathub"
    say(f"{match.name} isn't a Fedora package; installing it from "
        f"{repo[:1].upper()}{repo[1:]} instead: flatpak install -y {repo} {match.source_id}")
    result = runner([FLATPAK, "install", "--system", "--noninteractive", "-y", repo, match.source_id],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output = (result.stdout or "").strip()
    if result.returncode != 0:
        say(output.splitlines()[-1] if output else f"flatpak install failed (exit {result.returncode}).")
        return 1
    say(f"Complete! {match.name} is ready to use. No restart needed.")
    say(f"To remove it: flatpak uninstall {match.source_id}")
    return 0
