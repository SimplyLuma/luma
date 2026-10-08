# SPDX-License-Identifier: Apache-2.0
"""Calendar person actions: actual handler, fake people, all URI launches mocked.

Requires host GTK/Adw imports, but constructs no window and reads no EDS data.
"""
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch
from gi.repository import GLib

ROOT=Path(__file__).resolve().parents[2]
if find_spec("prairie_apps") is None:
    sys.path.insert(0,str(ROOT/"src/prairie-core"))
from prairie_apps.calendar_data import CalendarAttendee
from prairie_apps.calendar_window import CalendarWindow


class CalendarPersonRoutesTests(unittest.TestCase):
    def setUp(self):
        self.first=SimpleNamespace(name="Alex",email="first@example.test",phone="111")
        self.second=SimpleNamespace(name="Alex",email="second@example.test",phone="222")
        self.window=SimpleNamespace(fixture=None,people=(self.first,self.second),toast=Mock(),
            selected_event=SimpleNamespace(attendees=(CalendarAttendee("Alex",self.first.email),
                                                     CalendarAttendee("Alex",self.second.email))))
        self.launch_patch=patch("prairie_apps.calendar_window.Gio.AppInfo.launch_default_for_uri")
        self.launch=self.launch_patch.start()
        self.addCleanup(self.launch_patch.stop)

    def test_message_reaches_selected_attendees_number(self):
        CalendarWindow.contact_action(self.window,"Alex","message",self.second.email)
        self.launch.assert_called_once_with("sms:222",None)
        self.window.toast.assert_not_called()

    def test_email_does_not_collect_other_same_named_attendees(self):
        CalendarWindow.contact_action(self.window,"Alex","email",self.second.email)
        self.launch.assert_called_once_with("mailto:second@example.test",None)

    def test_message_without_phone_never_opens_mail(self):
        self.second.phone=""
        CalendarWindow.contact_action(self.window,"Alex","message",self.second.email)
        self.launch.assert_not_called()
        self.window.toast.assert_called_once_with("No phone number is available for this person",kind="warning")

    def test_unknown_email_cannot_use_a_same_named_contacts_phone(self):
        CalendarWindow.contact_action(self.window,"Alex","message","missing@example.test")
        self.launch.assert_not_called()
        self.window.toast.assert_called_once()

    def test_ambiguous_legacy_name_does_not_email_multiple_people(self):
        CalendarWindow.contact_action(self.window,"Alex","email")
        self.launch.assert_not_called()
        self.window.toast.assert_called_once_with("More than one address matches this person",kind="warning")

    def test_fixture_actions_never_launch_real_apps(self):
        self.window.fixture=object()
        CalendarWindow.contact_action(self.window,"Alex","message",self.second.email)
        CalendarWindow.contact_action(self.window,"Alex","email",self.second.email)
        self.launch.assert_not_called()
        self.assertEqual(self.window.toast.call_count,2)

    def test_missing_message_handler_reports_failure(self):
        self.launch.side_effect=GLib.Error("No SMS handler")
        CalendarWindow.contact_action(self.window,"Alex","message",self.second.email)
        self.window.toast.assert_called_once_with("Messages could not open",kind="warning")

    def test_rejected_group_mail_launch_reports_failure(self):
        self.launch.return_value=False
        CalendarWindow.contact_action(self.window,"","everyone")
        self.window.toast.assert_called_once_with("Mail could not open",kind="warning")

    def test_missing_contacts_handler_reports_failure(self):
        with patch("luma_appkit.application_directory.launch") as desktop:
            desktop.side_effect=lambda _identity, callback: callback(False, "No Contacts handler")
            CalendarWindow.open_contact(self.window,"Alex")
        desktop.assert_called_once()
        self.assertEqual(desktop.call_args.args, ("org.projectluma.Contacts.desktop",))
        self.window.toast.assert_called_once_with("No Contacts handler",kind="warning")

    def test_missing_maps_and_call_handlers_report_failures(self):
        self.launch.side_effect=GLib.Error("No URI handler")
        CalendarWindow.directions(self.window,"The studio")
        CalendarWindow.join_call(self.window,SimpleNamespace(url="https://example.test/meet"))
        self.assertEqual(self.window.toast.call_args_list,[
            unittest.mock.call("Maps could not open",kind="warning"),
            unittest.mock.call("Call link could not open",kind="warning")])

    def test_pending_reply_does_not_block_same_uid_in_another_calendar(self):
        event=SimpleNamespace(source_uid="second",uid="shared",rid="20260923T100000Z")
        window=SimpleNamespace(fixture=None,task=Mock(),
            _rsvp_pending={("first","shared",event.rid)})
        CalendarWindow.rsvp(window,event,"maybe")
        window.task.assert_called_once()
        self.assertIn(("second","shared",event.rid),window._rsvp_pending)

    def test_pending_same_occurrence_reply_does_not_submit_again(self):
        event=SimpleNamespace(source_uid="first",uid="shared",rid="20260923T100000Z")
        window=SimpleNamespace(fixture=None,task=Mock(),
            _rsvp_pending={(event.source_uid,event.uid,event.rid)})
        CalendarWindow.rsvp(window,event,"maybe")
        window.task.assert_not_called()


if __name__ == "__main__":
    unittest.main()
