# SPDX-License-Identifier: Apache-2.0
"""Depot's pages: one per sidebar place, the item page and search."""
from __future__ import annotations


def create_pages(shell) -> dict:
    from .apps import AppsPage
    from .item import ItemPage
    from .library import LibraryPage
    from .looks import LooksPage
    from .search import SearchPage
    from .today import TodayPage
    from .tweaks import TweaksPage
    from .updates import UpdatesPage
    return {"today": TodayPage(shell), "apps": AppsPage(shell), "looks": LooksPage(shell),
            "tweaks": TweaksPage(shell), "library": LibraryPage(shell), "updates": UpdatesPage(shell),
            "item": ItemPage(shell), "search": SearchPage(shell)}
