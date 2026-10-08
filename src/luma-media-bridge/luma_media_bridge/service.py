# SPDX-License-Identifier: Apache-2.0
"""luma-media-bridge: publish players that lack MPRIS as MPRIS players.

Each adapter watches one program's own local control interface. While the
program can be reached, its player appears on the session bus under
org.mpris.MediaPlayer2.<name> with the program's desktop entry, so the
shelf's live island, media keys and Ari treat it like any other player.
"""
from __future__ import annotations

import logging
import signal

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .kodi import Kodi  # noqa: E402
from .mpris import Player  # noqa: E402

ADAPTERS = (Kodi,)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    loop = GLib.MainLoop()
    adapters = []
    for kind in ADAPTERS:
        holder: dict = {}

        def present(available: bool, holder=holder) -> None:
            player = holder["player"]
            player.appear(connection) if available else player.disappear()

        def state(value, seeked: bool, holder=holder) -> None:
            holder["player"].update(value, seeked=seeked)
        adapter = kind(present, state)
        holder["player"] = Player(kind.bus_suffix, kind.identity, kind.desktop_entry, adapter)
        adapter.start()
        adapters.append(adapter)
    try:
        from gi.repository import GLibUnix
        add_signal = GLibUnix.signal_add
    except ImportError:
        add_signal = GLib.unix_signal_add
    for number in (signal.SIGTERM, signal.SIGINT):
        add_signal(GLib.PRIORITY_DEFAULT, number, lambda: (loop.quit(), False)[1])
    loop.run()
    for adapter in adapters:
        adapter.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
