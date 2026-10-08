# SPDX-License-Identifier: Apache-2.0
"""How a category page may order its apps.

Only orders every listed app has the data for are offered. Download size and
release date are missing for roughly half of each shelf (system apps and
channels that publish neither), so "Size" and "Recently updated" would sort
half a page by nothing; they are left out until the catalogue carries them.
"""

from __future__ import annotations

from typing import Callable, Iterable, TypeVar

T = TypeVar("T")

NAME = "name"
INSTALLED = "installed"

#: (key, label) in the order the menu shows them. The first is the default.
SORT_ORDERS: tuple[tuple[str, str], ...] = (
    (NAME, "Name"),
    (INSTALLED, "Installed first"),
)
DEFAULT_SORT = SORT_ORDERS[0][0]


def _name_key(app) -> tuple[str, str]:
    # casefold so "qBittorrent" sits among the Qs; app_id breaks exact ties
    # so equal names never swap places between renders.
    return (app.name.casefold(), app.app_id)


def sort_apps(apps: Iterable[T], order: str, *,
              is_installed: Callable[[T], bool] = lambda _app: False) -> tuple[T, ...]:
    """The apps in ``order``; within any tie, by name. Unknown orders sort by name."""
    by_name = sorted(apps, key=_name_key)
    if order == INSTALLED:
        # sorted() is stable: installed apps come first, each group stays by name.
        return tuple(sorted(by_name, key=lambda app: not is_installed(app)))
    return tuple(by_name)
