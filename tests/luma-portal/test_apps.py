# SPDX-License-Identifier: Apache-2.0
"""No row is blank, and no two rows read the same."""

import types
import unittest
from unittest import mock

from luma_portal.apps import (
    Application, app_id_for, desktop_id_for, disambiguate, origin)


class Fake:
    """Just enough of a GAppInfo for the parts that do not touch GIO."""

    def __init__(self, path=""):
        self._path = path

    def get_filename(self):
        return self._path


def row(name, desktop_id, *, org="", description="", default=False):
    return Application(desktop_id=desktop_id, name=name, description=description,
                       icon=None, is_default=default, origin=org)


class Origin(unittest.TestCase):
    def test_a_flatpak_export_is_a_flatpak(self):
        self.assertEqual(
            origin(Fake("/var/lib/flatpak/exports/share/applications/x.desktop")),
            "Flatpak")

    def test_a_user_flatpak_counts_too(self):
        self.assertEqual(
            origin(Fake("/home/p/.local/share/flatpak/exports/share/applications/x.desktop")),
            "Flatpak")

    def test_a_snap_is_a_snap(self):
        self.assertEqual(origin(Fake("/var/lib/snapd/desktop/applications/x.desktop")), "Snap")

    def test_the_system_prefix_has_no_qualifier(self):
        self.assertEqual(origin(Fake("/usr/share/applications/x.desktop")), "")

    def test_an_appinfo_with_no_file_is_not_guessed_at(self):
        self.assertEqual(origin(object()), "")


class Disambiguation(unittest.TestCase):
    def names(self, rows):
        return sorted(r.name for r in disambiguate(rows))

    def test_a_name_that_does_not_collide_is_left_alone(self):
        self.assertEqual(self.names([row("Notes", "a.desktop")]), ["Notes"])

    def test_the_system_copy_keeps_the_plain_name(self):
        rows = [row("Calendar", "luma.desktop"),
                row("Calendar", "gnome.desktop", org="Flatpak")]
        self.assertEqual(self.names(rows), ["Calendar", "Calendar (Flatpak)"])

    def test_two_packagings_of_one_application_are_told_apart(self):
        rows = [row("Spotify", "snap.desktop", org="Snap"),
                row("Spotify", "flatpak.desktop", org="Flatpak")]
        self.assertEqual(self.names(rows), ["Spotify (Flatpak)", "Spotify (Snap)"])

    def test_matching_is_case_and_space_insensitive(self):
        rows = [row("Calendar", "a.desktop"), row("calendar", "b.desktop", org="Snap")]
        self.assertEqual(len({r.name for r in disambiguate(rows)}), 2)

    def test_two_copies_from_the_same_place_fall_back_to_the_identifier(self):
        rows = [row("Thing", "one.desktop"), row("Thing", "two.desktop")]
        self.assertEqual(self.names(rows), ["Thing", "Thing (two)"])

    def test_three_colliding_rows_all_end_up_distinct(self):
        rows = [row("Thing", "a.desktop"),
                row("Thing", "b.desktop", org="Flatpak"),
                row("Thing", "c.desktop", org="Flatpak")]
        self.assertEqual(len({r.name for r in disambiguate(rows)}), 3)

    def test_nothing_is_lost_or_duplicated(self):
        rows = [row("A", "a.desktop"), row("B", "b.desktop", org="Snap"),
                row("A", "c.desktop", org="Flatpak")]
        out = disambiguate(rows)
        self.assertEqual(len(out), 3)
        self.assertEqual({r.desktop_id for r in out},
                         {"a.desktop", "b.desktop", "c.desktop"})

    def test_the_default_flag_survives_being_renamed(self):
        rows = [row("Calendar", "luma.desktop", default=True),
                row("Calendar", "gnome.desktop", org="Flatpak")]
        marked = [r for r in disambiguate(rows) if r.is_default]
        self.assertEqual([r.desktop_id for r in marked], ["luma.desktop"])

    def test_the_description_survives_being_renamed(self):
        rows = [row("Calendar", "a.desktop", description="Manage events"),
                row("Calendar", "b.desktop", org="Flatpak")]
        kept = {r.desktop_id: r.description for r in disambiguate(rows)}
        self.assertEqual(kept["a.desktop"], "Manage events")


class SpokenName(unittest.TestCase):
    def test_the_chip_is_never_the_only_signal(self):
        self.assertEqual(
            row("Notes", "a.desktop", description="Plain text", default=True).accessible_name,
            "Notes, Plain text, default")

    def test_a_row_with_no_description_still_reads(self):
        self.assertEqual(row("Notes", "a.desktop").accessible_name, "Notes")


if __name__ == "__main__":
    unittest.main()


class UnknownDesktopId(unittest.TestCase):
    """An id the portal hands over need not name anything installed.

    PyGObject turns a GObject constructor returning NULL into a TypeError, so
    such an id arrived as an exception rather than as a None to test for. It
    escaped ChooseApplication, the dialog was never built, and the calling
    application was never answered -- "Open with" did nothing at all, with no
    error anywhere the person could see.
    """

    def _gio(self, *, bad):
        def new(desktop_id):
            if desktop_id in bad:
                raise TypeError("constructor returned NULL")
            return Fake(f"/usr/share/applications/{desktop_id}")
        return types.SimpleNamespace(
            AppInfo=types.SimpleNamespace(
                get_default_for_type=lambda _ct, _must: None,
                get_all_for_type=lambda _ct: [],
                get_all=lambda: []),
            DesktopAppInfo=types.SimpleNamespace(new=new))

    def test_an_id_naming_nothing_installed_is_skipped(self):
        from luma_portal import apps
        with mock.patch.object(apps, "Gio", self._gio(bad={"gone.desktop"})):
            recommended, others, default = apps.for_content_type(
                "text/plain", extra_ids=("gone.desktop",))
        self.assertEqual(recommended, ())
        self.assertEqual(others, ())
        self.assertIsNone(default)

    def test_the_good_ids_either_side_of_a_bad_one_survive(self):
        """A bad id in the middle must not take the ones around it with it."""
        from luma_portal import apps
        gio = self._gio(bad={"gone.desktop"})
        seen = []

        def record(infos, _default):
            seen.append(list(infos))
            return ()

        with mock.patch.object(apps, "Gio", gio), \
             mock.patch.object(apps, "_sorted", record):
            apps.for_content_type(
                "text/plain",
                extra_ids=("a.desktop", "gone.desktop", "b.desktop"))
        self.assertEqual(len(seen[0]), 2)


class AppIds(unittest.TestCase):
    """The portal deals in app ids; GIO deals in desktop file names.

    Conflating the two broke the chooser at both ends: every id a caller
    suggested failed to resolve, and the choice answered with carried a suffix
    the frontend appended to again, so nothing ever opened.
    """

    def test_a_bare_app_id_becomes_a_desktop_file_name(self):
        self.assertEqual(desktop_id_for("org.gnome.TextEditor"),
                         "org.gnome.TextEditor.desktop")

    def test_an_id_that_already_has_the_suffix_is_left_alone(self):
        self.assertEqual(desktop_id_for("org.gnome.TextEditor.desktop"),
                         "org.gnome.TextEditor.desktop")

    def test_the_suffix_comes_back_off_for_the_answer(self):
        self.assertEqual(app_id_for("org.gnome.TextEditor.desktop"),
                         "org.gnome.TextEditor")

    def test_an_answer_without_the_suffix_is_left_alone(self):
        self.assertEqual(app_id_for("org.gnome.TextEditor"),
                         "org.gnome.TextEditor")

    def test_the_pair_round_trips(self):
        for app_id in ("firefox", "org.gnome.Nautilus", "a.b.c.d"):
            self.assertEqual(app_id_for(desktop_id_for(app_id)), app_id)

    def test_desktop_in_the_middle_of_a_name_is_not_a_suffix(self):
        self.assertEqual(app_id_for("org.desktop.Thing"), "org.desktop.Thing")
        self.assertEqual(desktop_id_for("org.desktop.Thing"),
                         "org.desktop.Thing.desktop")
