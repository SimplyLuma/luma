# SPDX-License-Identifier: Apache-2.0
"""Authorised meeting writes through EDS; no alternate mail transport or store.

send_objects_sync: GNOME EDS libecal/method.Client.send_objects_sync.html.
Backend-requested email delivery is reported as incomplete, never as sent.
"""
from dataclasses import replace
import re
from . import calendar_backend as backend
from .calendar_backup import ensure_backup


class CalendarDeliveryError(RuntimeError):
    def __init__(self, message, uid):
        super().__init__(message)
        self.uid = uid
        self.result = uid


def calendar_email(client, cal):
    result = client.get_backend_property_sync(cal.BACKEND_PROPERTY_CAL_EMAIL_ADDRESS, None)
    email = result[1] if isinstance(result,tuple) and result[0] else ""
    email = email.removeprefix("mailto:").strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+", email):
        raise ValueError("This calendar has no email address for meeting invitations.")
    return email


def _send(client, component, ical, method, uid):
    envelope = ical.Component.new_vcalendar()
    envelope.add_property(ical.Property.new_version("2.0"))
    envelope.add_property(ical.Property.new_prodid("-//Project Luma//Calendar//EN"))
    envelope.set_method(method)
    envelope.add_component(component.clone())
    try:
        result = client.send_objects_sync(envelope,0,None)
    except Exception as error:
        raise CalendarDeliveryError("The calendar saved the change but could not send it: "+str(error),uid) from error
    if not result[0]:
        raise CalendarDeliveryError("The calendar saved the change but could not send it.",uid)
    if result[1]:
        raise CalendarDeliveryError("The calendar saved the change but its backend requires email delivery. Invitations have not been sent.",uid)


def save_meeting(draft, *, scope=backend.SCOPE_ALL):
    client, _source, cal, ical = backend._client(draft.source_uid)
    invited = draft.attendees is not None and bool(draft.attendees)
    existing = backend._fetch(client,draft.uid) if draft.uid else None
    existing_people=()
    organizer=""
    if existing is not None:
        stored=cal.Component.new_from_icalcomponent(existing.clone())
        existing_people=backend._attendee_records(stored)
        organizer=(stored.get_organizer().get_value() or "").removeprefix("mailto:") if stored.get_organizer() else ""
    meeting=invited or bool(existing_people)
    email=calendar_email(client,cal) if meeting else ""
    if draft.attendees is not None and organizer and organizer.casefold()!=email.casefold():
        raise ValueError("Only this meeting’s organiser can change its invitees.")
    ensure_backup(draft.source_uid,"invitations" if meeting else "event-edit",backend.export_ics)
    if email and not organizer:
        draft = replace(draft,organizer_email=email)
    uid = backend.save_event(draft,scope=scope)
    if meeting and (not organizer or organizer.casefold()==email.casefold()) and not client.check_save_schedules():
        try:
            component = backend._fetch(client,uid,draft.rid if scope==backend.SCOPE_THIS else "")
        except Exception as error:
            raise CalendarDeliveryError("The event was saved but could not be read back for invitation delivery.",uid) from error
        if draft.attendees is not None:
            kept={a.email.casefold() for a in draft.attendees}
            removed=[a for a in existing_people if a.email.casefold() not in kept]
            if removed:
                cancel=cal.Component.new_from_icalcomponent(component.clone())
                backend._set_invitees(cancel,removed,cal,ical)
                _send(client,cancel.get_icalcomponent(),ical,ical.PropertyMethod.CANCEL,uid)
        if invited or (draft.attendees is None and existing_people):
            _send(client,component,ical,ical.PropertyMethod.REQUEST,uid)

    return uid


def reply_to_event(event, answer, *, email=None):
    answers={"yes":"ACCEPTED","maybe":"TENTATIVE","no":"DECLINED"}
    if answer not in answers:
        raise ValueError("Choose Going, Maybe or Can’t go.")
    client, _source, cal, ical = backend._client(event.source_uid)
    identity = email or calendar_email(client,cal)
    try:
        stored = backend._fetch(client,event.uid,event.rid)
        component = cal.Component.new_from_icalcomponent(stored.clone())
    except Exception as error:
        if not (hasattr(error,"matches") and error.matches(cal.Client.error_quark(),cal.ClientError.OBJECT_NOT_FOUND)):
            raise
        if not event.rid or event.instance_start is None:
            raise
        # Only use a master to instantiate an occurrence when its object is
        # absent. Other read errors must propagate before any write.
        stored=backend._fetch(client,event.uid)
        component=cal.Component.new_from_icalcomponent(stored.clone())
        component.set_rrules([])
        component.set_rdates([])
        component.set_exdates([])
        component.set_dtstart(backend._datetime_value(cal,ical,client,event.start,all_day=event.all_day,tzid=event.tzid))
        component.set_dtend(backend._datetime_value(cal,ical,client,event.end,all_day=event.all_day,tzid=event.tzid))
        rid=backend._datetime_value(cal,ical,client,event.instance_start,all_day=event.all_day,tzid=event.tzid)
        component.set_recurid(cal.ComponentRange.new(cal.ComponentRangeKind.SINGLE,rid))
    attendees = component.get_attendees() or []
    mine = [person for person in attendees if (person.get_value() or "").removeprefix("mailto:").casefold()==identity.casefold()]
    if len(mine)!=1:
        raise ValueError("Your calendar email does not identify one attendee in this event.")
    desired=getattr(ical.ParameterPartstat,answers[answer])
    changed=mine[0].get_partstat()!=desired
    schedules=client.check_save_schedules()
    if not changed and schedules:
        return
    ensure_backup(event.source_uid,"rsvp",backend.export_ics)
    if changed:
        mine[0].set_partstat(desired)
        component.set_attendees(attendees)
        component.set_last_modified(backend._now_utc(ical))
        component.set_dtstamp(backend._now_utc(ical))
        client.modify_object_sync(component.get_icalcomponent(),cal.ObjModType.THIS,0,None)
    if not schedules:
        # A reply contains the responding attendee only; other people's
        # answers stay in the stored object and are not sent as our reply.
        # An explicit repeated answer can retry delivery after a saved but
        # unsent reply, without rewriting the event or its timestamps.
        reply=cal.Component.new_from_icalcomponent(component.get_icalcomponent().clone())
        reply.set_attendees(mine)
        reply.set_dtstamp(backend._now_utc(ical))
        _send(client,reply.get_icalcomponent(),ical,ical.PropertyMethod.REPLY,event.uid)
