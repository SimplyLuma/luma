# SPDX-License-Identifier: Apache-2.0
"""Real app models/EDS/MCP, with disposable bus and data; no GUI or account."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

assert os.environ.get('ARI_PRODUCTIVITY_ISOLATED')=='1'
if '--exercise' not in sys.argv:
    with tempfile.TemporaryDirectory(prefix='ari-productivity-') as directory:
        root=Path(directory)
        env=dict(os.environ)
        for variable,name in (('XDG_DATA_HOME','data'),('XDG_CONFIG_HOME','config'),
                ('XDG_STATE_HOME','state'),('XDG_CACHE_HOME','cache'),('XDG_RUNTIME_DIR','runtime')):
            (root/name).mkdir(mode=0o700)
            env[variable]=str(root/name)
        env.update(GSETTINGS_BACKEND='memory',GIO_USE_VFS='local',TZ='America/Chicago')
        env.pop('PRAIRIE_EDS_MODE',None)
        subprocess.run(['dbus-run-session','--',sys.executable,__file__,'--exercise'],env=env,check=True,timeout=180)
    sys.exit(0)

assert '/ari-productivity-' in os.environ['XDG_DATA_HOME']
from ari.mcp import Client
from ari.tools import productivity as direct
from prairie_apps import calendar_backend as calendar, notes_backend as notes, eds_backend as contacts
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
import gi
gi.require_version('Gio','2.0')
from gi.repository import Gio,GLib

processes=[]
client=None
try:
    bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
    for binary,name in (('evolution-source-registry','org.gnome.evolution.dataserver.Sources5'),
            ('evolution-calendar-factory','org.gnome.evolution.dataserver.Calendar8'),
            ('evolution-addressbook-factory','org.gnome.evolution.dataserver.AddressBook10')):
        processes.append(subprocess.Popen(['/usr/libexec/'+binary],stdout=sys.stderr,stderr=sys.stderr))
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            if bus.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus',
                    'NameHasOwner',GLib.Variant('(s)',(name,)),None,Gio.DBusCallFlags.NONE,1000,None).unpack()[0]:break
            time.sleep(.1)
        else:raise AssertionError('Private EDS owner did not start: '+name)
    client=Client('productivity',[sys.executable,str(Path(direct.__file__))])
    client.start()
    def invoke(name,args):
        result=client.call(name,args)
        assert result['ok'],result
        return result
    fields={'title':'Dog park','start':'2026-10-30T14:00:00','end':'2026-10-30T15:00:00',
            'time_zone':'America/Chicago'}
    preview=invoke('change_application',{'application':'calendar','operation':'create','fields':fields,'preview':True})
    assert '2:00 PM' in preview['preview'] and 'America/Chicago' in preview['preview']
    started=time.monotonic()
    created=invoke('change_application',{'application':'calendar','operation':'create','fields':fields})
    elapsed=time.monotonic()-started
    record=created['data']['record']
    assert record['title']=='Dog park',record
    assert record['start'].startswith('2026-10-30T14:00:00-05:00'),record
    assert record['end'].startswith('2026-10-30T15:00:00-05:00'),record
    zone=direct.ZoneInfo('America/Chicago')
    start,end=direct._moment(fields['start'],zone),direct._moment(fields['end'],zone)
    saved=next(event for event in calendar.list_events(start,end) if event.uid==record['id'])
    assert saved.summary=='Dog park' and saved.start==start and saved.end==end,saved
    print(f'Real EDS Calendar: correct title, date, 2–3 PM America/Chicago verified in {elapsed:.3f}s; zero UI steps PASS',flush=True)
    changed=invoke('change_application',{'application':'calendar','operation':'update',
        'target':created['data']['target'],'fields':{'title':'Dog park with Ada'}})
    assert changed['data']['record']['start']==record['start']
    removed=invoke('change_application',{'application':'calendar','operation':'remove','target':changed['data']['target']})
    assert not any(event.uid==record['id'] for event in calendar.list_events(start,end))
    restored=invoke('change_application',{'application':'calendar','operation':'restore','target':removed['data']['target']})
    assert restored['data']['record']['title']=='Dog park with Ada'
    backups=tuple((Path(os.environ['XDG_STATE_HOME'])/'luma/calendar-backups').glob('*.ics'))
    assert len(backups)>=3 and any('Dog park' in path.read_text() for path in backups)
    print('Calendar structured update, delete and owner restoration readback PASS',flush=True)

    note=invoke('change_application',{'application':'notes','operation':'create','fields':{'title':'Groceries','body':'Bread\nMilk'}})
    store=notes.NotesStore()
    assert store.get_note(note['data']['record']['id']).body=='Bread\nMilk'
    store.update_note(note['data']['record']['id'],title='User edit',body='Keep this')
    conflict=client.call('change_application',{'application':'notes','operation':'update','target':note['data']['target'],'fields':{'body':'overwrite'}})
    assert not conflict['ok'],conflict
    assert store.get_note(note['data']['record']['id']).body=='Keep this'
    current=invoke('query_application',{'application':'notes','query':'User edit'})['data']['records'][0]
    removed=invoke('change_application',{'application':'notes','operation':'remove','target':current['target']})
    assert store.get_note(current['id']).deleted_at
    invoke('change_application',{'application':'notes','operation':'restore','target':removed['data']['target']})
    # The desktop can delegate to a Flatpak while Ari itself is native. Both
    # libraries exist: a readback from the wrong one must never look successful.
    native_count=len(store.list_notes())
    destination={'application_id':'org.projectluma.Notes','storage':'flatpak',
                 'data_home':str(Path(os.environ['XDG_DATA_HOME'])/'flatpak-owner')}
    with patch.object(direct,'_application_destination',return_value=destination):
        owner_note=direct.change_application({'application':'notes','operation':'create',
            'fields':{'title':'Task List','body':'In the actual application library'}})
        assert owner_note['verified'] and owner_note['data']['verification']['storage']=='flatpak'
        owner_path=Path(destination['data_home'])/'luma/notes/notes.sqlite3'
        owner_store=notes.NotesStore(owner_path)
        assert owner_store.get_note(owner_note['data']['record']['id']).body=='In the actual application library'
        assert len(store.list_notes())==native_count
        owner_store.close()
    with patch.object(direct,'_application_destination',return_value={**destination,'storage':'native'}):
        try:
            direct.change_application({'application':'notes','operation':'remove','target':owner_note['data']['target']})
        except ValueError as error:assert 'destination changed' in str(error)
        else:raise AssertionError('A stale target changed a different application library')
    print('Actual separate Notes libraries: Flatpak destination, durable independent readback, native untouched, owner-change guard PASS',flush=True)
    assert store.get_note(current['id']).deleted_at is None
    store.close()
    print('Real Notes creation/readback, concurrent edit preservation, Trash and restore PASS',flush=True)

    person=invoke('change_application',{'application':'contacts','operation':'create',
        'fields':{'name':'Ari Native Test','phone':'+15550123456','email':'ari-native@example.test'}})
    saved=next(c for c in contacts.load_contacts() if c.uid==person['data']['record']['uid'])
    assert saved.name=='Ari Native Test' and saved.phone=='+15550123456'
    changed=invoke('change_application',{'application':'contacts','operation':'update','target':person['data']['target'],
        'fields':{'organization':'Native test'}})
    assert changed['data']['record']['phone']=='+15550123456'
    print('Real EDS Contacts create/update and preserved fields PASS',flush=True)

    task=invoke('change_application',{'application':'tasks','operation':'create',
        'fields':{'title':'Walk the dog','due':'2026-10-30','body':'Bring water'}})
    completed=invoke('change_application',{'application':'tasks','operation':'update','target':task['data']['target'],
        'fields':{'completed':True}})
    assert completed['data']['record']['completed'] is True
    print('Real EDS Tasks creation, completion and owner readback PASS',flush=True)

    city=invoke('query_application',{'application':'weather','query':'Chicago'})['data']['records'][0]
    added=invoke('change_application',{'application':'weather','operation':'add','target':city['target']})
    removed=invoke('change_application',{'application':'weather','operation':'remove','target':added['data']['target']})
    invoke('change_application',{'application':'weather','operation':'restore','target':removed['data']['target']})
    print('Real Weather model: direct city add/remove/restore without opening a window PASS',flush=True)

    from ari.agent import Agent,Turn
    from ari.policy import Policy
    from ari.store import Store
    store=Store(Path(os.environ['XDG_DATA_HOME'])/'agent-test.db')
    tools=SimpleNamespace(available=client.tools,by_name={tool.name:(client,tool) for tool in client.tools},call=client.call)
    for mode,expected in (('system',1),('never',0),('all',1)):
        calls=[]
        class Provider:
            model='scripted';hosted_search=False
            def chat(self,messages,schemas,**kwargs):
                calls.append(True)
                if len(calls)==1:
                    assert 'change_application' in {schema['function']['name'] for schema in schemas}
                    yield {'type':'tool_call','id':'create','name':'change_application',
                           'arguments':{'application':'calendar','operation':'create','fields':{**fields,'title':'Dog park '+mode}}}
                else:
                    assert 'Succeeded:' in messages[-1]['content']
                    assert 'T14:00:00-05:00' in messages[-1]['content']
                    yield {'type':'text','text':'Created Dog park for tomorrow from 2–3 PM.'}
        policy=Policy();policy.approval_mode=mode
        agent=Agent(store,tools,policy,model_provider=lambda:(Provider(),'Fixture','fixture','unknown','large'))
        events=[]
        def emit(event):
            events.append(event)
            if event['type']=='approval':agent.approve(event['approval'],True)
        with patch('ari.agent._current_settings',return_value=''),patch('ari.agent.audit.record'):
            agent.answer(Turn(store.new_conversation(),'Add a calendar event tomorrow at 2–3 PM: Dog park',emit))
        assert len(calls)==2 and events[-1]['type']=='done',events
        assert sum(event['type']=='approval' for event in events)==expected,events
        assert len([event for event in events if event['type']=='tool'])==1,events
        print(f'Real agent/{mode}: one typed operation, {expected} approvals, verified event; two scripted model calls, zero UI operations PASS',flush=True)
    store.db.close()
finally:
    if client:client.stop()
    for process in reversed(processes):process.terminate()
    for process in reversed(processes):
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait()
