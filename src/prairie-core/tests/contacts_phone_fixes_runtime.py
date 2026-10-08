#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Contacts search, the edit dialog's account, and Phone without a modem.

Real GTK windows over invented contacts (555-01xx numbers); no address book
service, no modem and no phone are touched.

1. Typing in Contacts' search narrows the list live on name, phone and email,
   and a search that matches nobody shows the empty state.
2. A new contact's card does not show storage implementation copy.
   Edit mode offers every field the card shows. The Together card is as tall
   as its content however late its items arrive (audit #26).
3. Phone on a computer with no modem shows no notice in the sidebar and one
   calm line under the keypad with a link to pair a phone in Connect; with a
   phone paired through Connect, calls go through it and the line is gone.
   No text shown names a service or tool.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from unittest import mock

os.environ.setdefault("PRAIRIE_EDS_MODE", "disabled")
os.environ.setdefault("GSK_RENDERER", "cairo")
_home = tempfile.mkdtemp(prefix="contacts-phone-fixes-")
for variable, leaf in (("HOME", ""), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state"),
                       ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache")):
    os.environ[variable] = os.path.join(_home, leaf)
    os.makedirs(os.environ[variable], exist_ok=True)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from prairie_apps import contacts as contacts_module  # noqa: E402
from prairie_apps.contacts_data import ContactsSource, Me, SharingStore  # noqa: E402
from prairie_apps import phone as phone_module  # noqa: E402
from prairie_apps import phone_backend, phone_control, eds_backend  # noqa: E402
from prairie_apps.eds_backend import (  # noqa: E402
    LOCAL_ADDRESS_BOOK, AddressBookName, ContactRecord, address_book_name,
)

INTERNAL_NAMES = ("modemmanager", "mmcli", "prairie", "evolution data server", "ofono", "wpctl")

PEOPLE = (
    ContactRecord("u-ada", "Ada Okafor", "(555) 010-0142", "ada@example.org", "Studio North", "Architect"),
    ContactRecord("u-ben", "Ben Hartley", "+1 555 010 0177", "ben.hartley@example.com"),
    ContactRecord("u-cleo", "Cleo Marsh", "555-0103", "cleo@marsh.example"),
    ContactRecord("u-dev", "Devika Rao", "", "devika@example.net"),
    ContactRecord("u-eli", "Eli Brandt", "555-0199", ""),
)

context = GLib.MainContext.default()


def pump(seconds: float = 0.05) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def names_in(window) -> list[str]:
    names = []
    child = window.rows.get_first_child()
    while child is not None:
        record = getattr(child, "record", None)
        if record is not None:
            names.append(record.name)
        child = child.get_next_sibling()
    return names


def type_search(window, text: str) -> None:
    window.search.set_text(text)
    pump(0.08)


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


class ListSource(ContactsSource):
    """People from a list, in memory: what the window reads and writes, recorded."""

    def __init__(self, records, book=LOCAL_ADDRESS_BOOK):
        super().__init__(SharingStore(None))
        self.records, self.book, self.loads, self.sent, self.items = list(records), book, 0, [], ()

    def load(self):
        self.loads += 1
        return list(self.records), self.book

    def together(self, record):
        return self.items

    def update(self, uid, changes):
        self.sent.append((uid, changes))
        return uid

    def create(self, fields):
        self.sent.append(("", fields))
        return "new-1"

    def delete(self, uid):
        self.sent.append((uid, None))

    def me(self):
        return getattr(self, "owner", None)


class Application(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.projectluma.Test.ContactsPhoneFixes")


APP = Application()
APP.register(None)


class ContactMatchingTests(unittest.TestCase):
    def matches(self, query: str) -> list[str]:
        return [record.name for record in PEOPLE if contacts_module.contact_matches(record, query)]

    def test_name_any_case_and_every_word(self):
        self.assertEqual(self.matches("ada"), ["Ada Okafor"])
        self.assertEqual(self.matches("MARSH"), ["Cleo Marsh"])
        self.assertEqual(self.matches("ben hart"), ["Ben Hartley"])
        self.assertEqual(self.matches("ben marsh"), [])

    def test_phone_however_punctuated(self):
        self.assertEqual(self.matches("5550100142"), ["Ada Okafor"])
        self.assertEqual(self.matches("010 0177"), ["Ben Hartley"])
        self.assertEqual(self.matches("(555) 0103"), ["Cleo Marsh"])
        self.assertEqual(self.matches("0199"), ["Eli Brandt"])

    def test_email(self):
        self.assertEqual(self.matches("devika@"), ["Devika Rao"])
        self.assertEqual(self.matches("example.com"), ["Ben Hartley"])

    def test_blank_query_matches_everyone(self):
        self.assertEqual(len(self.matches("   ")), len(PEOPLE))


class AddressBookNameTests(unittest.TestCase):
    def test_local_book_is_this_computer(self):
        name = address_book_name("system-address-book", "local", "local-stub", "On This Computer", "Personal")
        self.assertEqual(name, LOCAL_ADDRESS_BOOK)
        self.assertEqual(name.label, "On this computer")

    def test_luma_account(self):
        name = address_book_name("luma-connect-contacts-abc123", "carddav", "carddav-stub", "", "Luma")
        self.assertEqual(name.label, "Luma account")
        self.assertEqual(name.place, "your Luma account")

    def test_online_account_uses_the_account_name(self):
        name = address_book_name("7f3c", "google", "4a1b", "sam@example.com", "Contacts")
        self.assertEqual(name.label, "sam@example.com")

    def test_lone_book_uses_its_own_name(self):
        self.assertEqual(address_book_name("x", "carddav", "carddav-stub", "", "Family").label, "Family")

    def test_never_blank_never_internal(self):
        for name in (address_book_name("", "", "", "", ""), LOCAL_ADDRESS_BOOK):
            self.assertTrue(name.label)
            self.assertNotIn("prairie", name.label.casefold())


class ContactsWindowTests(unittest.TestCase):
    def setUp(self):
        self.source = ListSource(PEOPLE)
        self.window = contacts_module.ContactsWindow(APP, source=self.source)
        self.window.present()
        self.window._reload(wait=True)
        pump(0.2)

    def tearDown(self):
        self.window.close()
        pump(0.05)

    def test_search_narrows_live_and_restores(self):
        window = self.window
        everyone = names_in(window)
        self.assertEqual(len(everyone), len(PEOPLE))
        type_search(window, "b")
        self.assertEqual(names_in(window), ["Ben Hartley", "Eli Brandt"])
        type_search(window, "br")
        self.assertEqual(names_in(window), ["Eli Brandt"])
        type_search(window, "0142")
        self.assertEqual(names_in(window), ["Ada Okafor"])
        type_search(window, "devika@example")
        self.assertEqual(names_in(window), ["Devika Rao"])
        type_search(window, "")
        self.assertEqual(names_in(window), everyone)
        self.assertEqual(len(window.all_records), len(PEOPLE))   # the search narrows, never reloads

    def test_no_match_shows_the_empty_state(self):
        window = self.window
        type_search(window, "zzqx")
        self.assertEqual(names_in(window), [])
        self.assertTrue(window.list_empty.get_mapped())
        self.assertIn("No contacts match", window.list_empty.get_label())
        first_holder = window.list_empty.get_parent()
        type_search(window, "zzqxy")
        self.assertIsNone(first_holder.get_child())
        self.assertTrue(window.list_empty.get_mapped())
        self.assertIs(window.list_empty.get_parent().get_parent(), window.rows)
        type_search(window, "")
        self.assertFalse(window.list_empty.get_mapped())

    def test_repeated_empty_address_book_loads(self):
        window = self.window
        window.all_records = {}
        window._apply_records()
        pump()
        old_holder = window.list_empty.get_parent()
        window._apply_records()
        pump()
        self.assertIsNone(old_holder.get_child())
        self.assertTrue(window.list_empty.get_mapped())
        self.assertIs(window.list_empty.get_parent().get_parent(), window.rows)

    def test_search_does_not_reload_the_address_book(self):
        before = self.source.loads
        type_search(self.window, "ada")
        type_search(self.window, "ad")
        self.assertEqual(self.source.loads, before)

    def test_new_card_has_no_storage_caption(self):
        self.window._new_contact()
        pump(0.05)
        self.assertFalse(any(text.startswith("Saved to ") for text in labels(self.window.contact_card)))

    def test_resolved_person_prefills_native_title_and_keeps_entered_email(self):
        window = self.window
        window._add_current_query = "ben.hartley@example.com"
        window._resolved_luma_person = {"account": "owned-test-account", "handle": "ben",
                                        "display_name": "Ben Hartley"}
        window._add_submit(window._add_current_query)
        pump()
        self.assertTrue(window.editing)
        self.assertEqual(window._fields["name"].text, "Ben Hartley")
        self.assertEqual(window._fields["handle"].text, "ben")
        self.assertEqual(window._fields["email"].text, "ben.hartley@example.com")

    def test_changed_identity_never_keeps_previous_verified_badge(self):
        original = replace(PEOPLE[1], handle="ben", luma_account="owned-test-account", on_luma=True)
        for handle in ("", "different-user"):
            updated = contacts_module.with_fields(original, original.uid, {"handle": handle}, original.book)
            self.assertEqual(updated.luma_account, "")
            self.assertIsNone(updated.on_luma)
        untouched = contacts_module.with_fields(original, original.uid, {"company": "Changed"}, original.book)
        self.assertEqual(untouched.luma_account, original.luma_account)
        self.assertTrue(untouched.on_luma)

    def test_sidebar_offers_lists_only_when_there_are_lists(self):
        # v71 (2026-09-30) brings Lists back to the foot: All contacts, Favorites and each list.
        self.assertIsNone(self.window.foot.filter_button)
        self.source.records = [replace(PEOPLE[0], favourite=True, categories=("team",)), *PEOPLE[1:]]
        self.window._reload(wait=True)
        self.assertIsNotNone(self.window.foot.filter_button)
        self.assertIsNotNone(self.window.foot.add_button)

    def test_sharing_card_has_independent_mobile_and_email_controls(self):
        self.source.owner = Me("My card", handle="me", phone="555-0100", email="me@example.org")
        self.window._show_contact(replace(PEOPLE[0], on_luma=True))
        shown = labels(self.window.sees_card)
        self.assertIn("What Ada sees", shown)
        self.assertIn("Share my mobile number", shown)
        self.assertIn("Share my email address", shown)
        self.assertFalse(any("name and photo" in text or "Only with" in text for text in shown))
        self.source.sharing.set_share_mobile("u-ada", True)
        self.source.sharing.set_share_email("u-ada", True)
        self.assertTrue(self.source.sharing.share_mobile("u-ada"))
        self.assertTrue(self.source.sharing.share_email("u-ada"))

    def test_private_note_is_a_multiline_editor_with_one_privacy_hint(self):
        self.window._show_contact(PEOPLE[0], edit=True)
        self.assertIsInstance(self.window._fields["note"], Gtk.TextView)
        self.assertGreaterEqual(self.window._fields["note"].get_size_request()[1], 96)
        shown = labels(self.window.note_card)
        self.assertEqual(shown.count("Only you can see this"), 1)       # v71 .cnh

    def test_contact_actions_target_person_and_place_call(self):
        person = contacts_module.Person("Ada Okafor", phone="5550100142", email="ada@example.org", username="ada")
        with mock.patch.object(self.window, "_launch_core_app", return_value=True) as launch:
            self.assertTrue(self.window._contact_action("message", person))
            launch.assert_called_with("prairie-messages", "luma-messages://u/ada")
            self.assertTrue(self.window._contact_action("call", person))
            launch.assert_called_with("prairie-phone", "--call", "tel:5550100142")
        with mock.patch.object(self.window, "_launch_uri") as launch_uri:
            self.assertTrue(self.window._contact_action("email", person))
            launch_uri.assert_called_with("mailto:ada@example.org")
        self.assertFalse(self.window._contact_action("video", person))

    def test_share_button_opens_share_sheet_with_contact_card(self):
        self.window._show_contact(PEOPLE[0])
        self.window._open_share(self.window.name_field, PEOPLE[0])
        sheet = contacts_module.ShareSheet._open
        try:
            self.assertIsNotNone(sheet)
            self.assertEqual(sheet.document.kind, "contact")
            self.assertEqual(sheet.document.title, "Ada Okafor")
            with mock.patch.object(self.window, "_save_contact_card") as save:
                sheet._choose("save")
                save.assert_called_once_with("Ada Okafor", "(555) 010-0142", "ada@example.org")
        finally:
            if sheet is not None:
                pump(0.05)
                sheet.close()

    def test_edit_mode_offers_every_field_and_sends_only_changes(self):
        window = self.window
        ada = PEOPLE[0]
        window._show_contact(ada, edit=True)
        pump(0.05)
        self.assertEqual(set(window._fields), {key for key, *_rest in contacts_module.EDIT_FIELDS} | {"note"})
        self.assertEqual(window._fields["work"].text, "Studio North · Architect")
        window._fields["phone"].entry.set_text("(555) 010-0100")
        window._finish_edit()
        for _ in range(40):
            pump(0.05)
            if not window.editing and self.source.sent:
                break
        self.assertEqual(self.source.sent, [("u-ada", {"phone": "(555) 010-0100"})])   # only what changed
        self.assertFalse(window.editing)

    def test_together_sizes_to_content_when_items_arrive_late(self):
        """Audit #26: a card measured before its data arrives must grow to fit it, label inside."""
        window = self.window
        window.set_default_size(1180, 740)
        pump(0.1)
        items = tuple(contacts_module.TogetherItem("conversation", f"Thread {n}", "Last line", 1_700_000_000 + n,
                                                   address=str(n)) for n in range(3))
        window._show_contact(PEOPLE[1])
        pump(0.1)                                      # measured and laid out empty
        self.source.items = items
        window._show_contact(PEOPLE[1])
        pump(0.3)                                      # then the data lands
        card = window.together_card
        heading = card.get_first_child()
        ok, card_box = card.compute_bounds(window)
        ok2, heading_box = heading.compute_bounds(window)
        self.assertTrue(ok and ok2)
        self.assertGreaterEqual(card_box.get_height(), contacts_module.TOGETHER_MIN_HEIGHT)
        self.assertGreaterEqual(heading_box.get_y(), card_box.get_y())      # never drawn outside its card
        rows = window.together_list.get_first_child()
        self.assertIsNotNone(rows)
        ok3, row_box = rows.compute_bounds(window)
        self.assertTrue(ok3 and row_box.get_height() > 0)
        # Beside it, Contact keeps its own height: the row is as tall as the taller card.
        ok4, contact_box = window.contact_card.compute_bounds(window)
        self.assertLessEqual(contact_box.get_height(), card_box.get_height() + 1)


class FakePairedPhone:
    """A phone paired through Luma Connect, as Phone's call provider sees it."""

    closed = False

    def __init__(self):
        self.dialled = []

    def start(self, callback):
        self.callback = callback

    def control_authorized(self):
        return True

    def calls(self):
        return ()

    def invalidate(self):
        pass

    def dial(self, number):
        self.dialled.append(number)
        return "paired-call-1"

    def close(self):
        pass


def shown_text(window) -> str:
    texts = []

    def walk(widget):
        if isinstance(widget, Gtk.Label) and widget.get_mapped():
            texts.append(widget.get_text())
        child = widget.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(window)
    return "\n".join(texts)


class PhoneWithoutModemTests(unittest.TestCase):
    def open(self, provider):
        APP.call_provider = provider
        self.addCleanup(lambda: setattr(APP, "call_provider", None))
        no_modem = phone_backend.PhoneCapability(False, phone_backend.NO_MODEM_REASON, no_modem=True)
        patches = (
            mock.patch.object(phone_control, "inspect_phone_capability", lambda: no_modem),
            mock.patch.object(eds_backend, "load_contacts", lambda *_a, **_k: PEOPLE),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        window = phone_module.PhoneWindow(APP)
        self.addCleanup(lambda: (window.close(), pump(0.05)))
        window.set_default_size(1100, 760)
        window.present()
        for _ in range(250):
            pump(0.02)
            if window.voice is not None and (provider is not None and hasattr(provider, "callback")
                                             or provider is None and window.capability_ready):
                break
        self.assertIsNotNone(window.voice)
        if provider is not None:
            self.assertTrue(hasattr(provider, "callback"), "paired provider must have started")
        else:
            self.assertTrue(window.capability_ready, "modem inspection must have completed")
        window._switch("pad")
        pump(0.3)
        return window

    def test_backend_reports_no_modem_without_tool_names(self):
        with mock.patch.object(phone_backend.shutil, "which", lambda _name: None):
            missing = phone_backend.inspect_phone_capability()
        with mock.patch.object(phone_backend.shutil, "which", lambda name: f"/usr/bin/{name}"), \
                mock.patch.object(phone_backend, "_run", lambda _args: "No modems were found\n"):
            none_found = phone_backend.inspect_phone_capability()
        for capability in (missing, none_found):
            self.assertFalse(capability.available)
            self.assertTrue(capability.no_modem)
            self.assertEqual(capability.reason, phone_backend.NO_MODEM_REASON)
            for name in INTERNAL_NAMES:
                self.assertNotIn(name, capability.reason.casefold())

    def test_no_modem_no_phone_shows_one_calm_line_with_a_link(self):
        window = self.open(None)
        readiness = window._find(window, "pn-call-readiness")
        self.assertIsNone(window._find(window.sidebar, "pn-call-readiness"), "the sidebar notice must be gone")
        self.assertTrue(readiness.get_mapped())
        self.assertEqual(readiness.get_text(), "To make calls, pair your phone in Connect.")
        self.assertIn('<a href="luma-connect:">', readiness.get_label())
        opened = []
        window._open_connect = lambda: opened.append(True)
        readiness.emit("activate-link", "luma-connect:")
        self.assertEqual(opened, [True])
        visible = shown_text(window).casefold()
        for name in INTERNAL_NAMES:
            self.assertNotIn(name, visible)
        self.assertNotIn("not installed", visible)
        self.assertEqual(visible.count("pair your phone in connect"), 1)
        # Typing a number keeps the same single line; the call button waits.
        window.set_dial_address("5550100142")
        pump(0.05)
        self.assertFalse(window._find(window, "pn-call-voice").get_sensitive())
        self.assertEqual(shown_text(window).casefold().count("pair your phone in connect"), 1)

    def test_no_modem_with_paired_phone_calls_through_it(self):
        phone = FakePairedPhone()
        window = self.open(phone)
        phone.callback({"voice_available": True}, True)
        pump(0.1)
        self.assertTrue(window.capability.available)
        self.assertIsNone(window._find(window, "pn-call-readiness"))
        self.assertEqual(window.capability.reason, "Calls via your phone.")
        self.assertIs(window.voice.provider, phone)
        self.assertNotIn("pair your phone", shown_text(window).casefold())
        window.set_dial_address("5550100177")
        pump(0.05)
        self.assertTrue(window._find(window, "pn-call-voice").get_sensitive())
        window._find(window, "pn-call-voice").emit("clicked")
        for _ in range(100):
            pump(0.02)
            if not window.voice.pending:
                break
        self.assertIs(window.voice.transport, phone)
        self.assertEqual((window.voice.session.call_id, phone.dialled),
                         ("paired-call-1", ["5550100177"]))
        visible = shown_text(window).casefold()
        for name in INTERNAL_NAMES:
            self.assertNotIn(name, visible)


if __name__ == "__main__":
    result = unittest.main(argv=[sys.argv[0], "-v"], exit=False).result
    raise SystemExit(0 if result.wasSuccessful() else 1)
