# SPDX-License-Identifier: Apache-2.0
"""A category page's "Sort by": every order ties by name, so nothing jumps around."""

from dataclasses import dataclass
import unittest

from luma_depot.sorting import DEFAULT_SORT, INSTALLED, NAME, SORT_ORDERS, sort_apps


@dataclass(frozen=True)
class App:
    app_id: str
    name: str


# Catalogue order, which is neither alphabetical nor stable across refreshes.
SHELF = (App("org.zim", "Zim"), App("com.b", "bitwarden"), App("org.q", "qBittorrent"),
         App("com.anydesk", "AnyDesk"), App("org.clock", "Clock"))


def names(apps):
    return [app.name for app in apps]


class SortApps(unittest.TestCase):
    def test_name_is_the_default_and_ignores_case(self):
        self.assertEqual(DEFAULT_SORT, NAME)
        self.assertEqual(names(sort_apps(SHELF, NAME)),
                         ["AnyDesk", "bitwarden", "Clock", "qBittorrent", "Zim"])

    def test_installed_first_keeps_each_group_in_name_order(self):
        installed = {"org.zim", "org.clock"}
        ordered = sort_apps(SHELF, INSTALLED, is_installed=lambda app: app.app_id in installed)
        self.assertEqual(names(ordered), ["Clock", "Zim", "AnyDesk", "bitwarden", "qBittorrent"])

    def test_same_name_ties_break_by_app_id_whatever_the_input_order(self):
        twins = (App("org.b.Notes", "Notes"), App("org.a.Notes", "Notes"))
        self.assertEqual(sort_apps(twins, NAME), sort_apps(tuple(reversed(twins)), NAME))
        self.assertEqual([app.app_id for app in sort_apps(twins, NAME)], ["org.a.Notes", "org.b.Notes"])

    def test_an_unknown_order_falls_back_to_name(self):
        self.assertEqual(sort_apps(SHELF, "size"), sort_apps(SHELF, NAME))

    def test_only_orders_backed_by_data_every_app_has_are_offered(self):
        """Size and release date are missing for about half of each shelf."""
        self.assertEqual([key for key, _label in SORT_ORDERS], [NAME, INSTALLED])


if __name__ == "__main__":
    unittest.main()
