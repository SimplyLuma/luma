# SPDX-License-Identifier: Apache-2.0
"""Meeting changes preserve iCalendar properties; all EDS clients are fake."""
from datetime import datetime, timezone
from importlib.util import find_spec
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[2]
if find_spec("prairie_apps") is None:
    sys.path.insert(0,str(ROOT/"src/prairie-core"))
from prairie_apps import calendar_backend as backend
from prairie_apps.calendar_data import CalendarAttendee, Event, EventDraft
from prairie_apps.calendar_backup import ensure_backup
from prairie_apps import calendar_meeting as meeting

ICS="""BEGIN:VEVENT
UID:meeting-1
DTSTART:20260923T120000Z
DTEND:20260923T130000Z
SUMMARY:Original
LOCATION:Server's newer location
DESCRIPTION:Keep this description
ORGANIZER;CN=Organiser:mailto:organiser@example.test
ATTENDEE;CN=You;PARTSTAT=NEEDS-ACTION;X-EXTRA=opaque:mailto:you@example.test
ATTENDEE;CN=Other;PARTSTAT=TENTATIVE:mailto:other@example.test
X-PRIVATE:preserve
END:VEVENT
"""


class BackupTests(unittest.TestCase):
    def test_private_backup_before_first_write_of_each_kind(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{"LUMA_CALENDAR_FIXTURE":""}):
            export=Mock(return_value="BEGIN:VCALENDAR\nEND:VCALENDAR\n")
            path=ensure_backup("source","rsvp",export,root=directory)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertIn("BEGIN:VCALENDAR",path.read_text())
            self.assertIsNone(ensure_backup("source","rsvp",export,root=directory))
            self.assertEqual(export.call_count,1)
            self.assertIsNotNone(ensure_backup("source","invitations",export,root=directory))

    def test_missing_or_failed_export_prevents_write(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{"LUMA_CALENDAR_FIXTURE":""}):
            with self.assertRaises(ValueError):
                ensure_backup("source","edit",lambda _:"",root=directory)
            self.assertEqual(list(Path(directory).iterdir()),[])

    def test_fixture_never_exports_or_writes_backups(self):
        export=Mock(side_effect=AssertionError("read a real calendar"))
        with patch.dict(os.environ,{"LUMA_CALENDAR_FIXTURE":"fixture.json"}),self.assertRaises(RuntimeError):
            ensure_backup("source","edit",export)
        export.assert_not_called()


class MeetingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _data,cls.cal,cls.ical=backend._modules()

    def setUp(self):
        self.stored=self.ical.Component.new_from_string(ICS)
        self.client=Mock()
        self.client.check_save_schedules.return_value=True
        self.client.get_backend_property_sync.return_value=(True,"you@example.test")
        self.fake=(self.client,Mock(),self.cal,self.ical)
        self.start=datetime(2026,9,23,12,tzinfo=timezone.utc)
        self.end=datetime(2026,9,23,13,tzinfo=timezone.utc)
        self.event=Event("meeting-1","source","Original",self.start,self.end)

    def test_fixture_registry_refuses_before_opening_eds(self):
        with patch.dict(os.environ,{"LUMA_CALENDAR_FIXTURE":"fixture.json"}),patch.object(backend,"_modules") as modules:
            with self.assertRaises(backend.CalendarUnavailable):
                backend._registry()
        modules.assert_not_called()

    def test_summary_only_edit_preserves_newer_fields_and_people(self):
        comp=self.cal.Component.new_from_icalcomponent(self.stored.clone())
        draft=EventDraft("source","Renamed",self.start,self.end,location="stale UI value",uid="meeting-1",changed_fields=frozenset({"summary"}))
        backend._apply_draft(comp,draft,self.cal,self.ical,self.client)
        text=comp.get_icalcomponent().as_ical_string()
        self.assertIn("SUMMARY:Renamed",text)
        self.assertIn("LOCATION:Server's newer location",text)
        self.assertIn("X-PRIVATE:preserve",text)
        self.assertIn("X-EXTRA=opaque",text)
        self.assertIn("PARTSTAT=TENTATIVE",text)
        self.assertIn("DTSTART:20260923T120000Z",text)

    def test_invitee_edit_preserves_existing_answer_and_parameters(self):
        comp=self.cal.Component.new_from_icalcomponent(self.stored.clone())
        backend._set_invitees(comp,(CalendarAttendee("You","you@example.test"),CalendarAttendee("New","new@example.test")),self.cal,self.ical)
        text=comp.get_icalcomponent().as_ical_string()
        self.assertIn("X-EXTRA=opaque",text)
        self.assertIn("new@example.test",text)
        self.assertNotIn("other@example.test",text)
        self.assertIn("X-PRIVATE:preserve",text)

    def future_draft(self,*,all_day=False):
        selected=datetime(2026,9,30,0 if all_day else 12,tzinfo=timezone.utc)
        return EventDraft("source","Renamed",selected,selected.replace(hour=13) if not all_day else selected,
                          uid="meeting-1",instance_start=selected,rid="20260930T120000Z",repeat=backend.REPEAT_CUSTOM,
                          tzid="UTC",all_day=all_day,changed_fields=frozenset({"summary"}))

    def test_future_title_only_split_starts_at_instance_and_keeps_fresh_duration(self):
        stored=self.ical.Component.new_from_string(ICS.replace("20260923T130000Z","20260923T140000Z").replace("END:VEVENT","RRULE:FREQ=WEEKLY\nEND:VEVENT"))
        order=[]
        self.client.create_object_sync.side_effect=lambda *_:(order.append("create"),(True,"following-uid"))[1]
        self.client.modify_object_sync.side_effect=lambda *_:order.append("end-original")
        with patch.object(backend,"_client",return_value=self.fake),patch.object(backend,"_fetch",return_value=stored):
            self.assertEqual(backend.save_event(self.future_draft(),scope=backend.SCOPE_FUTURE),"following-uid")
        self.assertEqual(order,["create","end-original"])
        text=self.client.create_object_sync.call_args.args[0].as_ical_string()
        self.assertIn("DTSTART:20260930T120000Z",text)
        self.assertIn("DTEND:20260930T140000Z",text)
        self.assertIn("X-PRIVATE:preserve",text)
        self.assertIn("LOCATION:Server's newer location",text)
        self.assertIn("RRULE:FREQ=WEEKLY",text)

    def test_future_create_failure_does_not_end_original_series(self):
        stored=self.ical.Component.new_from_string(ICS.replace("END:VEVENT","RRULE:FREQ=WEEKLY\nEND:VEVENT"))
        self.client.create_object_sync.side_effect=RuntimeError("create failed")
        with patch.object(backend,"_client",return_value=self.fake),patch.object(backend,"_fetch",return_value=stored),self.assertRaisesRegex(RuntimeError,"create failed"):
            backend.save_event(self.future_draft(),scope=backend.SCOPE_FUTURE)
        self.client.modify_object_sync.assert_not_called()
        self.client.remove_object_sync.assert_not_called()

    def test_future_failed_split_restores_only_rule_before_removing_follower(self):
        stored=self.ical.Component.new_from_string(ICS.replace("END:VEVENT","RRULE:FREQ=WEEKLY\nEND:VEVENT"))
        fresh=self.ical.Component.new_from_string(stored.as_ical_string().replace("Server's newer location","Concurrent server location"))
        self.client.create_object_sync.return_value=(True,"following-uid")
        self.client.modify_object_sync.side_effect=[RuntimeError("split failed"),None]
        with patch.object(backend,"_client",return_value=self.fake),patch.object(backend,"_fetch",side_effect=[stored,fresh]),self.assertRaisesRegex(RuntimeError,"split failed"):
            backend.save_event(self.future_draft(),scope=backend.SCOPE_FUTURE)
        restored=self.client.modify_object_sync.call_args_list[1].args[0].as_ical_string()
        self.assertIn("LOCATION:Concurrent server location",restored)
        self.assertIn("RRULE:FREQ=WEEKLY",restored)
        self.assertNotIn("UNTIL=",restored)
        self.client.remove_object_sync.assert_called_once_with("following-uid",None,self.cal.ObjModType.ALL,0,None)

    def test_future_unknown_partial_save_reports_uid_without_deleting_follower(self):
        stored=self.ical.Component.new_from_string(ICS.replace("END:VEVENT","RRULE:FREQ=WEEKLY\nEND:VEVENT"))
        self.client.create_object_sync.return_value=(True,"following-uid")
        self.client.modify_object_sync.side_effect=RuntimeError("split failed")
        with patch.object(backend,"_client",return_value=self.fake),patch.object(backend,"_fetch",side_effect=[stored,RuntimeError("server unavailable")]),self.assertRaises(backend.CalendarPartialSave) as raised:
            backend.save_event(self.future_draft(),scope=backend.SCOPE_FUTURE)
        self.assertEqual(raised.exception.result,"following-uid")
        self.client.remove_object_sync.assert_not_called()

    def test_future_all_day_split_keeps_fresh_exclusive_end(self):
        stored=self.ical.Component.new_from_string(ICS.replace("DTSTART:20260923T120000Z","DTSTART;VALUE=DATE:20260923").replace("DTEND:20260923T130000Z","DTEND;VALUE=DATE:20260925").replace("END:VEVENT","RRULE:FREQ=WEEKLY\nEND:VEVENT"))
        self.client.create_object_sync.return_value=(True,"following-uid")
        with patch.object(backend,"_client",return_value=self.fake),patch.object(backend,"_fetch",return_value=stored):
            backend.save_event(self.future_draft(all_day=True),scope=backend.SCOPE_FUTURE)
        text=self.client.create_object_sync.call_args.args[0].as_ical_string()
        self.assertIn("DTSTART;VALUE=DATE:20260930",text)
        self.assertIn("DTEND;VALUE=DATE:20261002",text)

    def test_rsvp_changes_only_my_answer_after_backup(self):
        order=[]
        self.client.modify_object_sync.side_effect=lambda *_:order.append("write")
        with patch.object(backend,"_client",return_value=self.fake),patch.object(backend,"_fetch",return_value=self.stored),patch.object(meeting,"ensure_backup",side_effect=lambda *_:order.append("backup")):
            meeting.reply_to_event(self.event,"yes")
        self.assertEqual(order,["backup","write"])
        text=self.client.modify_object_sync.call_args.args[0].as_ical_string()
        self.assertIn("PARTSTAT=ACCEPTED",text)
        self.assertIn("PARTSTAT=TENTATIVE",text)
        self.assertIn("X-EXTRA=opaque",text)
        self.assertIn("X-PRIVATE:preserve",text)
        self.assertIn("DTEND:20260923T130000Z",text)
        self.client.send_objects_sync.assert_not_called()

    def accepted_component(self):
        component=self.cal.Component.new_from_icalcomponent(self.stored.clone())
        attendees=component.get_attendees()
        attendees[0].set_partstat(self.ical.ParameterPartstat.ACCEPTED)
        component.set_attendees(attendees)
        return component.get_icalcomponent()

    def test_unchanged_server_scheduled_reply_does_not_write_or_back_up(self):
        stored=self.accepted_component()
        before=stored.as_ical_string()
        with patch.object(backend,"_client",return_value=self.fake),patch.object(backend,"_fetch",return_value=stored),patch.object(meeting,"ensure_backup") as backup:
            meeting.reply_to_event(self.event,"yes")
        backup.assert_not_called()
        self.client.modify_object_sync.assert_not_called()
        self.client.send_objects_sync.assert_not_called()
        self.assertEqual(stored.as_ical_string(),before)

    def test_unchanged_manual_reply_retries_delivery_without_rewriting_event(self):
        stored=self.accepted_component()
        before=stored.as_ical_string()
        order=[]
        self.client.check_save_schedules.return_value=False
        self.client.send_objects_sync.side_effect=lambda *_:(order.append("send"),(True,[],None))[1]
        with patch.object(backend,"_client",return_value=self.fake),patch.object(backend,"_fetch",return_value=stored),patch.object(meeting,"ensure_backup",side_effect=lambda *_:order.append("backup")):
            meeting.reply_to_event(self.event,"yes")
        self.assertEqual(order,["backup","send"])
        self.client.modify_object_sync.assert_not_called()
        self.assertEqual(stored.as_ical_string(),before)
        text=self.client.send_objects_sync.call_args.args[0].as_ical_string()
        self.assertIn("METHOD:REPLY",text)
        self.assertIn("PARTSTAT=ACCEPTED",text)
        self.assertNotIn("other@example.test",text)

    def test_rsvp_nonmatching_identity_cannot_mutate(self):
        with patch.object(backend,"_client",return_value=self.fake),patch.object(backend,"_fetch",return_value=self.stored),patch.object(meeting,"ensure_backup") as backup:
            with self.assertRaises(ValueError):
                meeting.reply_to_event(self.event,"yes",email="unknown@example.test")
        self.client.modify_object_sync.assert_not_called()
        backup.assert_not_called()

    def test_rsvp_backup_failure_prevents_mutation_and_send(self):
        with patch.object(backend,"_client",return_value=self.fake),patch.object(backend,"_fetch",return_value=self.stored),patch.object(meeting,"ensure_backup",side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                meeting.reply_to_event(self.event,"yes")
        self.client.modify_object_sync.assert_not_called()
        self.client.send_objects_sync.assert_not_called()

    def test_reply_envelope_contains_only_my_attendee(self):
        self.client.check_save_schedules.return_value=False
        self.client.send_objects_sync.return_value=(True,[],None)
        with patch.object(backend,"_client",return_value=self.fake),patch.object(backend,"_fetch",return_value=self.stored),patch.object(meeting,"ensure_backup"):
            meeting.reply_to_event(self.event,"maybe")
        text=self.client.send_objects_sync.call_args.args[0].as_ical_string()
        self.assertIn("METHOD:REPLY",text)
        self.assertIn("you@example.test",text)
        self.assertNotIn("other@example.test",text)
        self.assertIn("ORGANIZER",text)

    def test_backend_requesting_email_is_reported_as_unsent(self):
        self.client.check_save_schedules.return_value=False
        self.client.send_objects_sync.return_value=(True,["organiser@example.test"],self.stored)
        with patch.object(backend,"_client",return_value=self.fake),patch.object(backend,"_fetch",return_value=self.stored),patch.object(meeting,"ensure_backup"):
            with self.assertRaises(meeting.CalendarDeliveryError) as caught:
                meeting.reply_to_event(self.event,"no")
        self.assertEqual(caught.exception.uid,"meeting-1")

    def test_end_only_change_preserves_newer_server_start(self):
        comp=self.cal.Component.new_from_icalcomponent(self.stored.clone())
        draft=EventDraft("source","Original",self.start.replace(hour=10),self.end.replace(hour=14),uid="meeting-1",tzid="UTC",changed_fields=frozenset({"end"}))
        backend._apply_draft(comp,draft,self.cal,self.ical,self.client)
        text=comp.get_icalcomponent().as_ical_string()
        self.assertIn("DTSTART:20260923T120000Z",text)
        self.assertIn("DTEND:20260923T140000Z",text)

    def test_nonorganiser_cannot_change_invitees(self):
        draft=EventDraft("source","Original",self.start,self.end,uid="meeting-1",attendees=(CalendarAttendee("New","new@example.test"),))
        with patch.object(backend,"_client",return_value=self.fake),patch.object(backend,"_fetch",return_value=self.stored),patch.object(meeting,"ensure_backup") as backup,patch.object(backend,"save_event") as save:
            with self.assertRaisesRegex(ValueError,"organiser"):
                meeting.save_meeting(draft)
        backup.assert_not_called()
        save.assert_not_called()

    def test_organiser_field_change_sends_request_without_replacing_attendees(self):
        self.client.get_backend_property_sync.return_value=(True,"organiser@example.test")
        self.client.check_save_schedules.return_value=False
        self.client.send_objects_sync.return_value=(True,[],None)
        draft=EventDraft("source","Renamed",self.start,self.end,uid="meeting-1",changed_fields=frozenset({"summary"}))
        with patch.object(backend,"_client",return_value=self.fake),patch.object(backend,"_fetch",return_value=self.stored),patch.object(meeting,"ensure_backup"),patch.object(backend,"save_event",return_value="meeting-1"):
            meeting.save_meeting(draft)
        envelope=self.client.send_objects_sync.call_args.args[0].as_ical_string()
        self.assertIn("METHOD:REQUEST",envelope)
        self.assertIn("other@example.test",envelope)
        self.assertIn("PARTSTAT=TENTATIVE",envelope)

    def test_removed_invitee_receives_cancel_and_kept_people_receive_request(self):
        self.client.get_backend_property_sync.return_value=(True,"organiser@example.test")
        self.client.check_save_schedules.return_value=False
        self.client.send_objects_sync.return_value=(True,[],None)
        draft=EventDraft("source","Original",self.start,self.end,uid="meeting-1",attendees=(CalendarAttendee("You","you@example.test"),),changed_fields=frozenset({"attendees"}))
        with patch.object(backend,"_client",return_value=self.fake),patch.object(backend,"_fetch",return_value=self.stored),patch.object(meeting,"ensure_backup"),patch.object(backend,"save_event",return_value="meeting-1"):
            meeting.save_meeting(draft)
        envelopes=[call.args[0].as_ical_string() for call in self.client.send_objects_sync.call_args_list]
        self.assertEqual(len(envelopes),2)
        self.assertIn("METHOD:CANCEL",envelopes[0])
        self.assertIn("other@example.test",envelopes[0])
        self.assertNotIn("you@example.test",envelopes[0])
        self.assertIn("METHOD:REQUEST",envelopes[1])

    def test_undo_export_keeps_master_and_detached_instances_only(self):
        override=self.ical.Component.new_from_string(ICS.replace("DTSTART:20260923T120000Z","RECURRENCE-ID:20260930T120000Z\nDTSTART:20260930T150000Z"))
        unrelated=self.ical.Component.new_from_string(ICS.replace("UID:meeting-1","UID:unrelated"))
        self.client.get_object_list_sync.return_value=(True,[self.stored,override,unrelated])
        with patch.object(backend,"_client",return_value=self.fake):
            text=backend.component_text("source","meeting-1")
        self.assertEqual(text.count("BEGIN:VEVENT"),2)
        self.assertIn("RECURRENCE-ID:20260930T120000Z",text)
        self.assertIn("DTSTART:20260930T150000Z",text)
        self.assertNotIn("UID:unrelated",text)

    def test_rsvp_read_failure_cannot_fall_back_to_master_and_write(self):
        from dataclasses import replace
        event=replace(self.event,rid="20260930T120000Z",instance_start=self.start)
        with patch.object(backend,"_client",return_value=self.fake),patch.object(backend,"_fetch",side_effect=RuntimeError("network down")),patch.object(meeting,"ensure_backup") as backup:
            with self.assertRaisesRegex(RuntimeError,"network down"):
                meeting.reply_to_event(event,"yes")
        backup.assert_not_called()
        self.client.modify_object_sync.assert_not_called()

    def test_rsvp_missing_override_creates_only_the_chosen_occurrence(self):
        from dataclasses import replace
        from gi.repository import GLib
        occurrence=self.start.replace(day=30)
        event=replace(self.event,start=occurrence,end=self.end.replace(day=30),rid="20260930T120000Z",instance_start=occurrence,tzid="UTC")
        missing=GLib.Error.new_literal(self.cal.Client.error_quark(),"missing",int(self.cal.ClientError.OBJECT_NOT_FOUND))
        with patch.object(backend,"_client",return_value=self.fake),patch.object(backend,"_fetch",side_effect=[missing,self.stored]),patch.object(meeting,"ensure_backup"):
            meeting.reply_to_event(event,"yes")
        text=self.client.modify_object_sync.call_args.args[0].as_ical_string()
        self.assertIn("RECURRENCE-ID:20260930T120000Z",text)
        self.assertIn("DTSTART:20260930T120000Z",text)
        self.assertIn("DTEND:20260930T130000Z",text)
        self.assertNotIn("RRULE",text)

    def test_delivery_exception_preserves_saved_uid_for_ui_no_duplicate_retry(self):
        self.client.check_save_schedules.return_value=False
        self.client.send_objects_sync.side_effect=OSError("connection lost")
        with patch.object(backend,"_client",return_value=self.fake),patch.object(backend,"_fetch",return_value=self.stored),patch.object(meeting,"ensure_backup"):
            with self.assertRaises(meeting.CalendarDeliveryError) as caught:
                meeting.reply_to_event(self.event,"yes")
        self.assertEqual(caught.exception.uid,"meeting-1")
        self.client.modify_object_sync.assert_called_once()

    def test_new_invitation_validates_identity_and_backs_up_before_save(self):
        order=[]
        draft=EventDraft("source","Invite",self.start,self.end,attendees=(CalendarAttendee("Other","other@example.test"),))
        with patch.object(backend,"_client",return_value=self.fake),patch.object(meeting,"ensure_backup",side_effect=lambda *_:order.append("backup")),patch.object(backend,"save_event",side_effect=lambda *_a,**_k:(order.append("write"),"new-uid")[1]) as save:
            self.assertEqual(meeting.save_meeting(draft),"new-uid")
        self.assertEqual(order,["backup","write"])
        self.assertEqual(save.call_args.args[0].organizer_email,"you@example.test")


if __name__=="__main__":
    unittest.main()
