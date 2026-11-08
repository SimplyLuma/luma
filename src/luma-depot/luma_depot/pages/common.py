# SPDX-License-Identifier: Apache-2.0
"""What the browse pages share: opening an item, installing from a row, the empty line."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .base import label

__all__ = ["empty", "install_here", "opener"]


def opener(shell, kind: str, item_id: str):
    return lambda: shell.open_item(kind, item_id)


def install_here(shell, kind: str, item_id: str):
    def run() -> None:
        item = shell.world.item(kind, item_id)
        shell.store.install(item, [shell.world.here], shell.done)
    return run


def empty(text: str = "Nothing here yet.") -> Gtk.Widget:
    return label(text, "dq-empty", wrap=True)
