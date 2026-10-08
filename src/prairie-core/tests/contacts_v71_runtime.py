#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Contacts at simulator v71, on a computer and on a phone.

Real GTK windows over v71's own sample address book (tests/fixtures/contacts-v71.json,
in memory) and over a source that makes none of v71's new writes. Nothing touches the
address book, Messages or the sharing store on disk.

1. What Add a person understands, and who it suggests.
2. A computer: the foot's Lists (with counts, "Favorites"), the corner's ⋯ (Block, Delete),
   your own card (Share, Edit, v71's words), Add a person and your code.
3. A phone: the list comes first, its bar is Search · Lists · Add a person; picking someone
   pushes their card, whose bar is Edit · Share · Favorite · ⋯; deleting goes back to the list.
4. A source that cannot favourite, block or keep lists is never offered them.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("PRAIRIE_EDS_MODE", "disabled")
os.environ.setdefault("GSK_RENDERER", "cairo")
_home = tempfile.mkdtemp(prefix="contacts-v71-")
for variable, leaf in (("HOME", ""), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state"),
                       ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache")):
    os.environ[variable] = os.path.join(_home, leaf)
    os.makedirs(os.environ[variable], exist_ok=True)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from prairie_apps import contacts as contacts_module  # noqa: E402
from prairie_apps.contacts_data import ContactsSource, SharingStore  # noqa: E402
from prairie_apps.contacts_fixture import FixtureSource  # noqa: E402
from prairie_apps.eds_backend import LOCAL_ADDRESS_BOOK, ContactRecord  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[3] / "tests/fixtures/contacts-v71.json"
context = GLib.MainContext.default()


def pump(seconds: float = 0.05) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def find(widget, name):
    return contacts_module._find_named(widget, name)


def labels(widget) -> list[str]:
    found = []

    def walk(node):
        if isinstance(node, Gtk.Label):
            found.append(node.get_text())
        child = node.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(widget)
    return found


def command_labels(registry) -> list[str]:
    return [command.label for group in registry.groups for command in group.commands]


class ReadOnlySource(ContactsSource):
    """An address book that makes none of v71's new writes (the real one, today)."""

    def __init__(self, records):
        super().__init__(SharingStore(None))
        self.records = list(records)

    def load(self):
        return list(self.records), LOCAL_ADDRESS_BOOK

    def delete(self, uid):
        pass


class Application(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.projectluma.Test.ContactsV71")


APP = Application()
APP.register(None)


class AddPersonTests(unittest.TestCase):
    def test_what_add_a_person_understands(self):
        add_query = contacts_module.add_query
        self.assertEqual(add_query("nora@simplyluma.com"), ("email", "nora@simplyluma.com"))
        self.assertEqual(add_query(" +1 (510) 555-0100 "), ("phone", "+1 (510) 555-0100"))
        self.assertEqual(add_query("@NO"), ("handle", "no"))
        self.assertEqual(add_query("n"), ("", ""))
        self.assertEqual(add_query("555"), ("handle", "555"))      # too short for a number: a username

    def test_matches_are_usernames_that_start_with_it(self):
        records, _book = FixtureSource(FIXTURE).load()
        names = [record.name for record in contacts_module.add_matches(records, "no")]
        self.assertEqual(names, ["Noah Bennett", "Nora Feld"])
        self.assertEqual(len(contacts_module.add_matches(records, "a")), contacts_module.ADD_MATCHES)

    def test_v71_address_book_and_spelling(self):
        source = FixtureSource(FIXTURE)
        records, _book = source.load()
        self.assertEqual(len(records), 61)
        labels_ = [label for _key, label, _icon in contacts_module.list_filters(records, source.lists)]
        self.assertEqual(labels_, ["All contacts", "Favorites", "Launch team", "Family"])
        counts = contacts_module.filter_counts(records, contacts_module.list_filters(records, source.lists))
        self.assertEqual(counts["all"], 61)
        self.assertEqual(counts[contacts_module.filter_key("list", "launch")], 4)

    def test_list_keys_make_valid_action_names(self):
        # A list's key is part of the Lists menu's action name; "list:launch" aborted GLib (v71 gate, lists).
        for name in ("launch", "Book club: Tuesdays", "Café ☕"):
            key = contacts_module.filter_key("list", name)
            self.assertRegex(key, r"^[A-Za-z0-9.-]+$")
            self.assertEqual(contacts_module.filter_parts(key), ("list", name))


class WindowTests(unittest.TestCase):
    size = (1180, 740)

    def make(self, source):
        window = contacts_module.ContactsWindow(APP, source=source)
        window.set_default_size(*self.size)
        window.present()
        pump(0.2)
        window._reload(wait=True, then=window._open_first)
        pump(0.3)
        return window

    def setUp(self):
        self.source = FixtureSource(FIXTURE)
        self.window = self.make(self.source)

    def tearDown(self):
        self.window.close()
        pump(0.05)


class ComputerTests(WindowTests):
    def test_opens_on_priya_with_the_corner(self):
        window = self.window
        self.assertFalse(window.phone)
        self.assertEqual(window.selected.uid, "PR")
        self.assertIsNotNone(window.corner)
        self.assertFalse(window.card_bar.get_visible())
        self.assertFalse(window.list_bar.get_visible())
        self.assertEqual(command_labels(window._card_commands(window.selected)), ["Block", "Delete contact"])

    def test_foot_lists_narrow_the_list(self):
        window = self.window
        self.assertEqual(window.foot.filter_button.get_name(), "ct-lists")
        window.foot.open_filters()                          # builds the menu's actions from the list keys
        pump(0.05)
        window.foot._menu.popdown()
        window.foot.set_filter("favourites", notify=True)
        pump(0.05)
        self.assertEqual(sorted(record.name for record in window.records.values()),
                         ["Dad", "Nora Feld", "Priya Raman", "Theo Marsh"])
        self.assertIsNone(window.my_card)                   # My card is for everyone's list only

    def test_my_card_shares_its_code_and_edits(self):
        window = self.window
        window._open_me()
        pump(0.05)
        self.assertEqual(set(window.corner.controls), {"share", "actions.0"})
        self.assertIn("Your name, photo and @nick. Your phone number and email stay hidden unless you share "
                      "them with someone.", labels(window.sees_card))
        window._open_my_code(window.corner.controls["share"])
        pump(0.05)
        self.assertIsNotNone(window._panel)
        self.assertIsNotNone(find(window._panel, "ct-code"))
        window._close_panel()
        window._show_me(edit=True)
        window._fields["work"].entry.set_text("Luma · CEO")
        window._finish_edit()
        pump(0.05)
        self.assertEqual(self.source.me().role, "CEO")
        self.assertFalse(window.editing)

    def test_add_a_person_opens_a_card_for_a_number(self):
        window = self.window
        window._open_add(window.foot.add_button)
        pump(0.05)
        self.assertIsNotNone(window._add_results)
        window._add_typed("@no")
        self.assertIn("Noah Bennett", labels(window._add_results))
        window._add_typed("+1 (415) 555-0101")
        self.assertIn("+1 (415) 555-0101", labels(window._add_results))
        window._add_submit("+1 (415) 555-0101")
        pump(0.05)
        self.assertTrue(window.editing)
        self.assertIsNone(window.selected)
        self.assertEqual(window._fields["phone"].text, "+1 (415) 555-0101")


class PhoneTests(WindowTests):
    size = (402, 874)

    def test_list_first_with_its_bar(self):
        window = self.window
        self.assertTrue(window.phone)
        self.assertEqual(window.list_first.showing, "list")
        self.assertFalse(window.foot.get_visible())
        self.assertIsNone(window.corner)
        self.assertTrue(window.list_bar.get_visible())
        for name in ("ct-lists", "ct-add"):
            self.assertIsNotNone(find(window.list_bar, name), name)
        pump(0.1)
        self.assertTrue(window.index.get_visible())             # A to Z on the right edge
        self.assertEqual([letter for letter, _row in window.index.headings() if letter][:3], ["A", "B", "C"])

    def test_lists_and_add_grow_the_bar(self):
        window = self.window
        self.assertIs(find(window, "ct-lists"), find(window.list_bar, "ct-lists"))
        find(window, "ct-lists").emit("clicked")
        pump(0.05)
        self.assertEqual(window.list_bar.grown, "lists")
        self.assertIn("Show", labels(window.list_bar))
        self.assertIn("Launch team", labels(window.list_bar))
        find(window, "ct-add").emit("clicked")
        pump(0.05)
        self.assertEqual(window.list_bar.grown, "add")
        window._add_typed("+1 (415) 555-0101")
        self.assertIn("Add +1 (415) 555-0101 as a new contact", labels(window._add_results))
        window._add_submit("+1 (415) 555-0101")
        pump(0.05)
        self.assertEqual(window.list_first.showing, "detail")
        self.assertTrue(window.editing)
        self.assertIsNone(window.list_bar.grown)

    def test_picking_someone_pushes_their_card_and_its_bar(self):
        window = self.window
        window._show_contact(window.records["NF"])
        window._push()
        pump(0.1)
        self.assertEqual(window.list_first.showing, "detail")
        for name in ("ct-edit", "ct-share", "ct-favourite", "ct-more"):
            self.assertIsNotNone(find(window.card_bar, name), name)
        self.assertEqual([item.label for item in window._more_items(window.selected)],
                         ["Add to list", "Link a duplicate", "Block Nora", "Delete contact"])
        window._confirm_block(window.selected)
        pump(0.05)
        self.assertEqual(window.card_bar.grown, "confirm")                # v71: confirmed inside the bar
        window.card_bar.fold_panel()
        window._toggle_favourite(window.selected)
        pump(0.05)
        self.assertFalse(window.all_records["NF"].favourite)
        window.list_first.back_button.emit("clicked")
        pump(0.05)
        self.assertEqual(window.list_first.showing, "list")

    def test_editing_is_cancel_and_done(self):
        window = self.window
        window._show_contact(window.records["PR"], edit=True)
        window._push()
        pump(0.05)
        words = labels(window.card_bar)
        self.assertIn("Cancel", words)
        self.assertIn("Done", words)

    def test_deleting_goes_back_to_the_list(self):
        window = self.window
        window._show_contact(window.records["AR"])
        window._push()
        window._delete(window.records["AR"])
        pump(0.05)
        self.assertEqual(window.list_first.showing, "list")
        self.assertNotIn("AR", window.records)
        window._undo_delete(window.all_records.get("AR") or window._pending_delete[0])
        pump(0.05)
        self.assertIn("AR", window.records)


class ReadOnlySourceTests(WindowTests):
    size = (402, 874)

    def setUp(self):
        self.source = ReadOnlySource([ContactRecord("u-ada", "Ada Okafor", "(555) 010-0142", "ada@example.org",
                                                    favourite=True, categories=("team",))])
        self.window = self.make(self.source)

    def test_new_writes_are_not_offered(self):
        window = self.window
        window._show_contact(window.records["u-ada"])
        window._push()
        pump(0.05)
        self.assertIsNone(find(window.card_bar, "ct-favourite"))
        self.assertEqual([item.label for item in window._more_items(window.selected)], ["Delete contact"])
        self.assertEqual(command_labels(window._card_commands(window.selected)), ["Delete contact"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
