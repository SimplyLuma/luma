# SPDX-License-Identifier: Apache-2.0
"""Structured operations through the same model APIs as Luma's applications.

No windows, screenshots, widget traversal or application table/file writes. Each
adapter owns its validation and readback. Ari owns authorization and receipts.
Existing records require an expiring, revision-bound target from a query.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
import dataclasses
from functools import wraps
import hashlib
import importlib
import json
import os
from pathlib import Path
import secrets
import sys
import time
from zoneinfo import ZoneInfo

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ari.mcp import serve

_targets = {}
_destination = ContextVar('application_destination', default=None)
APPLICATIONS = ('calendar', 'notes', 'contacts', 'weather', 'tasks')
OPERATIONS = {
    'calendar': ('create', 'update', 'remove', 'restore'),
    'notes': ('create', 'update', 'remove', 'restore'),
    'contacts': ('create', 'update'),
    'weather': ('add', 'remove', 'restore'),
    'tasks': ('create', 'update'),
}
FIELDS = {
    'calendar': {'title', 'start', 'end', 'time_zone', 'location', 'description'},
    'notes': {'title', 'body'},
    'contacts': {'name', 'phone', 'email', 'organization', 'role'},
    'weather': set(),
    'tasks': {'title', 'body', 'due', 'completed'},
}


def _application_destination(application):
    """Resolve the same owner as the launcher, never an empty native fallback.

    Native model APIs run as Ari, with Ari's authorization. Selecting a store
    does not run code as the application or grant its D-Bus/account identity.
    """
    app_id = 'org.projectluma.' + application.title()
    flatpak = False
    try:
        from luma_installer import native_app_roles
    except ImportError:
        native_app_roles = None
    if native_app_roles is not None and native_app_roles.required(app_id):
        # Refuses missing/ambiguous/unsigned deployments rather than writing
        # a second library when the real app needs repair.
        native_app_roles.resolve_owned(app_id)
        flatpak = True
    else:
        from gi.repository import Gio
        try:
            desktop = Gio.DesktopAppInfo.new(app_id + '.desktop')
        except TypeError:  # PyGObject's nullable constructor raises when absent.
            desktop = None
        if desktop is not None:
            owner = desktop.get_string('X-Flatpak')
            if owner and owner != app_id:
                raise ValueError('The application launcher has an unexpected owner.')
            flatpak = owner == app_id
    data = (Path.home()/'.var/app'/app_id/'data' if flatpak else
            Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local/share'))
    return {'application_id':app_id, 'storage':'flatpak' if flatpak else 'native',
            'data_home':str(data)}


def _at_destination(function):
    @wraps(function)
    def invoke(arguments):
        app = arguments['application']
        if app not in APPLICATIONS:
            raise ValueError('Choose a supported application.')
        destination = _application_destination(app)
        token = _destination.set(destination)
        try:
            result = function(arguments)
            if result.get('verified'):
                result['data']['verification'] = {
                    'scope':'saved-record', 'display_verified':False,
                    **{key:destination[key] for key in ('application_id','storage')}}
            return result
        finally:
            _destination.reset(token)
    return invoke


def _place_store(module):
    destination = _destination.get()
    return (module.PlaceStore(Path(destination['data_home'])/'prairie/weather/places.json')
            if destination and destination['storage']=='flatpak' else module.PlaceStore())


def backend(application):
    names = {'calendar':'calendar_backend', 'notes':'notes_backend', 'contacts':'eds_backend',
             'weather':'weather_backend', 'tasks':'tasks_backend'}
    if application not in names:
        raise ValueError('Choose a supported application.')
    return importlib.import_module('prairie_apps.'+names[application])


def _digest(value):
    if dataclasses.is_dataclass(value):
        value = dataclasses.asdict(value)
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _target(application, record, **extra):
    now = time.monotonic()
    for key, row in list(_targets.items()):
        if row['expires'] < now:
            del _targets[key]
    if len(_targets) >= 512:
        raise ValueError('Too many outstanding application targets. Query a smaller result set.')
    key = secrets.token_urlsafe(20)
    _targets[key] = dict(application=application, record=record, revision=_digest(record),
                         expires=now+300, destination=_destination.get(), **extra)
    return key


def _existing(application, token):
    row = _targets.get(token)
    if row is None or row['application'] != application or row['expires'] < time.monotonic():
        raise ValueError('That application target expired or belongs to another application. Query again.')
    if row.get('destination') != _destination.get():
        raise ValueError('The application data destination changed. Query the active application again.')
    return row


def _unchanged(row, current):
    if row['revision'] != _digest(current):
        raise ValueError('This record changed elsewhere. Query it again before changing it.')


def _text(value, name, limit=1000, required=False):
    if not isinstance(value, str) or len(value)>limit or '\0' in value or (required and not value.strip()):
        raise ValueError(f'Provide a valid {name}'+(' with text.' if required else '.'))
    return value


def _moment(value, zone):
    value = _text(value, 'ISO date and time', 80, True)
    moment = datetime.fromisoformat(value)
    if 'T' not in value:
        raise ValueError('Use a complete ISO date and time, with separate start and end fields.')
    if moment.tzinfo is not None:
        return moment.astimezone(zone)
    first, second = moment.replace(tzinfo=zone, fold=0), moment.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise ValueError('That time is ambiguous or skipped at a daylight-saving change. Include its UTC offset.')
    if first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != moment:
        raise ValueError('That local time does not exist. Choose another time.')
    return first


def _source(sources, selected='', *, default='is_default'):
    writable = [source for source in sources if source.writable]
    if selected:
        found = [source for source in writable if source.uid == selected]
    else:
        found = [source for source in writable if getattr(source, default, False)]
        if not found and len(writable)==1:
            found = writable
    if len(found)!=1:
        raise ValueError('Choose one writable calendar or task list using query_application resources; do not guess.')
    return found[0]


def _calendar_record(event):
    return {'id':event.uid, 'source':event.source_uid, 'title':event.summary,
            'start':event.start.isoformat(), 'end':event.end.isoformat(),
            'time_zone':event.tzid or event.zone_label, 'location':event.location,
            'description':event.description[:1000], 'recurring':event.recurring}


def _note_record(note):
    return {'id':note.id, 'title':note.title, 'body':note.body[:1000],
            'body_truncated':len(note.body)>1000,
            'body_sha256':hashlib.sha256(note.body.encode()).hexdigest(),
            'trashed':note.deleted_at is not None}


def _contact_record(contact):
    return {key:getattr(contact,key) for key in ('uid','name','phone','email','organization','role','book')}


def _place_record(place):
    return {key:getattr(place,key) for key in ('uid','name','region','country','latitude','longitude')}


def _task_record(task):
    return {'id':task.uid,'source':task.source_uid,'title':task.title,'completed':task.completed,
            'due':task.due.isoformat() if task.due else '', 'body':task.notes[:1000],
            'body_truncated':len(task.notes)>1000}


def _bounded(rows, budget):
    chosen,used=[],0
    for row in rows:
        size=len(json.dumps(row,ensure_ascii=False))
        if used+size>budget:
            break
        chosen.append(row)
        used+=size
    for row in rows[len(chosen):]:
        _targets.pop(row.get('target'),None)
    return chosen,len(chosen)<len(rows)


@contextmanager
def _store(module):
    destination = _destination.get()
    store = (module.NotesStore(Path(destination['data_home'])/'luma/notes/notes.sqlite3')
             if destination and destination['storage']=='flatpak' else module.NotesStore())
    try:
        yield store
    finally:
        store.close()


@_at_destination
def query_application(arguments):
    app = arguments['application']
    module = backend(app)
    query = _text(arguments.get('query',''), 'search', 200).casefold()
    resources, records = [], []
    if app == 'calendar':
        sources = module.list_sources()
        resources = [{'id':s.uid,'name':s.name,'writable':s.writable,'default':s.is_default} for s in sources]
        if arguments.get('start') and arguments.get('end'):
            zone = ZoneInfo(arguments.get('time_zone') or module.local_tzid())
            start,end = _moment(arguments['start'],zone),_moment(arguments['end'],zone)
            if not timedelta(0)<end-start<=timedelta(days=93):
                raise ValueError('Search a calendar interval of at most 93 days.')
            for event in module.list_events(start,end,sources,timeout=5):
                if query in event.summary.casefold() and len(records)<30:
                    row=_calendar_record(event)
                    row['target']=_target(app,event)
                    records.append(row)
    elif app == 'notes':
        with _store(module) as store:
            for note in store.list_notes(search=query,deleted=bool(arguments.get('removed',False)))[:30]:
                records.append({**_note_record(note),'target':_target(app,note)})
    elif app == 'contacts':
        for contact in module.load_contacts(query)[:30]:
            records.append({**_contact_record(contact),'target':_target(app,contact)})
    elif app == 'weather':
        saved=_place_store(module).list()
        resources=[_place_record(place) for place in saved]
        if query:
            search=module.LocationSearch()
            search.load()
            places=search.search(query,limit=20)
        else:
            places=saved
        for place in places[:30]:
            is_saved=next((p for p in saved if (p.latitude,p.longitude)==(place.latitude,place.longitude)),None)
            selected=is_saved or place
            records.append({**_place_record(selected),'saved':bool(is_saved),
                            'target':_target(app,selected,saved=bool(is_saved))})
    elif app == 'tasks':
        repo=module.TasksRepository()
        try:
            sources,tasks=repo.load()
            resources=[{'id':s.uid,'name':s.name,'writable':s.writable,'default':s.default} for s in sources]
            for task in tasks:
                if query in task.title.casefold() and len(records)<30:
                    records.append({**_task_record(task),'target':_target(app,task)})
        finally:
            repo.close()
    resources,resources_truncated=_bounded(resources,2200)
    records,truncated=_bounded(records,6200)
    return {'ok':True,'summary':f'Read {app} records and supported direct operations.',
            'data':{'application':app,'operations':OPERATIONS[app], 'resources':resources,
                    'records':records,'truncated':truncated,'resources_truncated':resources_truncated},'untrusted':True}


def _calendar(module, operation, fields, row, source, preview):
    from prairie_apps.calendar_data import EventDraft
    from prairie_apps.calendar_backup import ensure_backup
    sources=module.list_sources()
    if operation=='restore':
        if row is None or not row.get('removed'):
            raise ValueError('Choose a removed event returned by Ari.')
        summary='Restore '+row['record'].summary+' in Calendar'
        if preview:return summary,None
        ensure_backup(row['record'].source_uid,'event-restore',module.export_ics)
        module.restore_deletion(row['record'].source_uid,row['before'],row['after'])
        wanted=row['record']
    else:
        original=row['record'] if row else None
        if original is not None:
            if original.recurring or original.rid or original.all_day:
                raise ValueError('Recurring and all-day edits need their explicit scope in Calendar.')
            selected=_source(sources,original.source_uid)
            current=next((e for e in module.list_events(original.start-timedelta(days=1),
                original.end+timedelta(days=1),(selected,),timeout=5)
                if e.uid==original.uid and e.source_uid==original.source_uid),None)
            _unchanged(row,current)
        else:
            selected=_source(sources,source)
        if operation=='remove':
            if original is None:raise ValueError('Query the exact event to remove first.')
            summary='Remove '+original.summary+' from Calendar'
            if preview:return summary,None
            ensure_backup(selected.uid,'event-delete',module.export_ics)
            before=module.component_text(selected.uid,original.uid)
            module.delete_event(selected.uid,original.uid)
            after=module.component_text(selected.uid,original.uid,allow_missing=True)
            # list_events tolerates unavailable providers for Calendar's UI.
            # Its empty result alone is therefore not proof of deletion.
            # component_text is a strict owner read of this exact series.
            if 'BEGIN:VEVENT' in after.splitlines():
                raise RuntimeError('Calendar still contains the event; removal was not verified.')
            remaining=module.list_events(original.start-timedelta(days=1),original.end+timedelta(days=1),
                                         (selected,),timeout=5)
            if any(event.uid==original.uid for event in remaining):
                raise RuntimeError('Calendar did not confirm removal.')
            return summary,{'removed':True,'record':_calendar_record(original),
                'target':_target('calendar',original,removed=True,before=before,after=after)}
        zone_name=fields.get('time_zone') or (original.tzid if original else '') or module.local_tzid()
        zone=ZoneInfo(zone_name)
        title=_text(fields.get('title',original.summary if original else ''),'event title',500,True)
        start=_moment(fields['start'],zone) if 'start' in fields else original.start if original else None
        end=_moment(fields['end'],zone) if 'end' in fields else original.end if original else None
        if start is None or end is None or end<=start or end-start>timedelta(days=31):
            raise ValueError('Provide separate start and end times, with end after start (up to 31 days).')
        draft=EventDraft(source_uid=selected.uid,summary=title,start=start,end=end,tzid=zone_name,
            uid=original.uid if original else '',
            location=fields.get('location',original.location if original else ''),
            description=fields.get('description',original.description if original else ''))
        if original:
            # Preserve provider fields that the person did not request changing.
            draft.changed_fields=frozenset({'summary' if key=='title' else 'tzid' if key=='time_zone' else key for key in fields})
        summary=('Create ' if original is None else 'Update ')+title+' in Calendar'
        if preview:return summary+' · '+start.strftime('%b %-d, %-I:%M %p')+'–'+end.strftime('%-I:%M %p')+' '+zone_name,None
        ensure_backup(selected.uid,'event-save',module.export_ics)
        uid=module.save_event(draft)
        wanted=next((event for event in module.list_events(start-timedelta(days=1),end+timedelta(days=1),
            (selected,),timeout=5) if event.uid==uid),None)
        if wanted is None or wanted.summary!=title or wanted.start!=start or wanted.end!=end:
            raise RuntimeError('Calendar accepted the write but its saved title/time could not be verified. Do not create a duplicate; query the interval.')
    if operation=='restore':
        actual=next((event for event in module.list_events(wanted.start-timedelta(days=1),
            wanted.end+timedelta(days=1),sources,timeout=5)
            if event.uid==wanted.uid and event.source_uid==wanted.source_uid),None)
        if actual is None or actual.summary!=wanted.summary or actual.start!=wanted.start or actual.end!=wanted.end:
            raise RuntimeError('Calendar did not confirm the restored event.')
        wanted=actual
    return summary,{'record':_calendar_record(wanted),'target':_target('calendar',wanted)}


def _notes(module, operation, fields, row, _source_id, preview):
    with _store(module) as store:
        original=store.get_note(row['record'].id) if row else None
        if row:_unchanged(row,original)
        title=_text(fields.get('title',original.title if original else ''),'note title',500,True)
        body=_text(fields['body'],'note body',20000) if 'body' in fields else original.body if original else ''
        summary={'create':'Create','update':'Update','remove':'Move to Trash:','restore':'Restore'}[operation]+' '+title+' in Notes'
        if preview:return summary,None
        if operation=='create':
            note=store.create_note(title=title,body=body)
        elif original is None:
            raise ValueError('Query the exact note first.')
        elif operation=='update':
            note=store.update_note(original.id,title=title,body=body,
                runs=() if 'body' in fields else original.runs,expected=original)
        else:
            # Hold the owning store's write transaction across the revision
            # check and its public trash/restore operation.
            store.connection.execute('BEGIN IMMEDIATE')
            _unchanged(row,store.get_note(original.id))
            note=(store.soft_delete_note if operation=='remove' else store.restore_note)(original.id)
        actual=store.get_note(note.id)
        if operation in ('create','update') and (actual.title!=title or actual.body!=body):
            raise RuntimeError('Notes did not confirm the requested content.')
        if (operation=='remove') != bool(actual.deleted_at) and operation in ('remove','restore'):
            raise RuntimeError('Notes did not confirm its Trash state.')
        return summary,{'record':_note_record(actual),'target':_target('notes',actual)}


def _contacts(module, operation, fields, row, _source_id, preview):
    name=_text(fields.get('name',row['record'].name if row else ''),'contact name',500,True)
    if row:
        found=[contact for contact in module.load_contacts() if contact.uid==row['record'].uid]
        if len(found)!=1:raise ValueError('This contact is missing or ambiguous across address books.')
        _unchanged(row,found[0])
    summary=('Create ' if operation=='create' else 'Update ')+name+' in Contacts'
    changes=dict(fields)
    if 'organization' in changes or 'role' in changes:
        organization=changes.pop('organization',row['record'].organization if row else '')
        role=changes.pop('role',row['record'].role if row else '')
        if role and not organization:
            raise ValueError('Contacts needs an organization when setting a job role.')
        changes['work']=organization+(' · '+role if role else '')
    if preview:return summary,None
    uid=(module.create_contact(changes) if operation=='create' else module.update_contact(row['record'].uid,changes))
    found=[contact for contact in module.load_contacts() if contact.uid==uid]
    if len(found)!=1 or any(getattr(found[0],key)!=value for key,value in fields.items()):
        raise RuntimeError('The address book accepted the write but the saved fields could not be verified. Query before retrying.')
    return summary,{'record':_contact_record(found[0]),'target':_target('contacts',found[0])}


def _weather(module, operation, fields, row, _source_id, preview):
    if row is None:raise ValueError('Search for and choose the exact city first.')
    place=row['record']
    store=_place_store(module)
    summary={'add':'Add','remove':'Remove','restore':'Restore'}[operation]+' '+place.name+' in Weather'
    if operation=='remove':
        current=next((p for p in store.list() if p.uid==place.uid),None)
        _unchanged(row,current)
    if preview:return summary,None
    if operation=='remove':
        index,removed=store.remove(place.uid)
        if removed is None or any(p.uid==place.uid for p in store.list()):
            raise RuntimeError('Weather did not confirm removal.')
        return summary,{'removed':True,'record':_place_record(place),
            'target':_target('weather',place,removed=True,index=index)}
    if operation=='restore':
        if not row.get('removed'):raise ValueError('Choose a city removed by Ari to restore.')
        store.insert(row['index'],place)
    else:
        place=store.add(module.new_place(place))
    if not any(p.uid==place.uid for p in store.list()):
        raise RuntimeError('Weather did not confirm its saved city.')
    return summary,{'record':_place_record(place),'target':_target('weather',place,saved=True)}


def _tasks(module, operation, fields, row, source, preview):
    repo=module.TasksRepository()
    try:
        sources,tasks=repo.load()
        original=row['record'] if row else None
        if original:
            current=next((task for task in tasks if task.key==original.key),None)
            _unchanged(row,current)
            if original.recurring:
                raise ValueError('Recurring task changes need the occurrence controls in Tasks.')
        selected=_source(sources,original.source_uid if original else source,default='default')
        title=_text(fields.get('title',original.title if original else ''),'task title',500,True)
        changes={'notes' if key=='body' else key:value for key,value in fields.items()}
        if 'due' in changes:changes['due']=date.fromisoformat(changes['due']) if changes['due'] else None
        if operation=='update' and 'completed' in changes and len(changes)>1:
            raise ValueError('Complete a task in a separate operation from editing its fields.')
        summary=('Create ' if operation=='create' else 'Update ')+title+' in Tasks'
        if preview:return summary,None
        if operation=='create':uid=repo.add(selected.uid,changes)
        else:
            if 'completed' in changes:
                repo.complete(original,changes['completed'])
            else:
                repo.edit(original,changes)
            uid=original.uid
        _,tasks=repo.load()
        actual=next((task for task in tasks if task.uid==uid and task.source_uid==selected.uid),None)
        if actual is None or any(getattr(actual,key)!=value for key,value in changes.items()):
            raise RuntimeError('Tasks accepted the write but saved fields could not be verified. Query before retrying.')
        return summary,{'record':_task_record(actual),'target':_target('tasks',actual)}
    finally:
        repo.close()


@_at_destination
def change_application(arguments):
    app,operation=arguments['application'],arguments['operation']
    if app not in OPERATIONS or operation not in OPERATIONS[app]:
        raise ValueError('This application does not support that direct operation.')
    fields=arguments.get('fields',{})
    if not isinstance(fields,dict) or set(fields)-FIELDS[app]:
        raise ValueError('Use only the structured fields supported by this application.')
    if operation in ('remove','restore') and fields:
        raise ValueError('Removing or restoring uses the queried record, with no replacement fields.')
    for key,value in fields.items():
        if key=='completed':
            if type(value) is not bool:raise ValueError('Completed must be true or false.')
        else:_text(value,key,20000 if key=='body' else 4000)
    row=_existing(app,arguments['target']) if arguments.get('target') else None
    if operation in ('update','remove','restore') and row is None:
        raise ValueError('Query the exact existing record first; do not invent a target.')
    if operation=='create' and row is not None:
        raise ValueError('Creating a record does not take an existing target.')
    preview=bool(arguments.get('preview'))
    module=backend(app)
    handler={'calendar':_calendar,'notes':_notes,'contacts':_contacts,'weather':_weather,'tasks':_tasks}[app]
    summary,data=handler(module,operation,fields,row,arguments.get('source',''),preview)
    if preview:
        return {'ok':True,'preview':summary,'detail':json.dumps(fields,ensure_ascii=False)[:1500],
                'application_name':app.title(),'requested_name':app,'application_scope':'backend:'+app}
    if row:_targets.pop(arguments['target'],None)
    verb,separator,rest=summary.partition(' ')
    verb={'Create':'Created','Update':'Updated','Remove':'Removed','Restore':'Restored','Add':'Added','Move':'Moved'}.get(verb,verb)
    return {'ok':True,'summary':verb+separator+rest+'.','completed':True,'verified':True,'data':data,'untrusted':True}


def schema(properties, required=()):
    return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}


STRING={'type':'string'}
APP={'type':'string','enum':list(APPLICATIONS)}
TOOLS={
 'query_application':('Read structured Calendar, Notes, Contacts, Weather or Tasks records directly, without opening an app. Returns expiring targets and supported operations. Calendar search needs start/end ISO times; without them returns calendar resources. Weather query searches actual cities; empty query lists saved cities. Never guess targets or source IDs.',schema({'application':APP,'query':STRING,'start':STRING,'end':STRING,'time_zone':STRING,'removed':{'type':'boolean'}},['application']),query_application),
 'change_application':('Perform one direct app operation and verify the saved record, with no UI navigation. Prefer this over open_app/accessibility for supported tasks. Calendar create needs fields title, start, end as complete ISO times, optional time_zone (IANA, defaults to this machine), location/description; never put dates/times in the title. Notes create takes title/body. Contacts create takes name/phone/email/organization/role. Tasks create takes title/body/due (ISO date)/completed. Weather add/remove takes an exact queried city target. Updates/removals/restores need queried targets; only fields provided change. Sources default only when unambiguous. Query if a prerequisite is missing; do not switch to filling a title field.',schema({'application':APP,'operation':{'type':'string','enum':['create','update','remove','restore','add']},'target':STRING,'source':STRING,'fields':schema({key:({'type':'boolean'} if key=='completed' else STRING) for key in sorted(set.union(*FIELDS.values()))}),'preview':{'type':'boolean'}},['application','operation']),change_application),
}
if __name__=='__main__':
    serve('ari-productivity',TOOLS)
