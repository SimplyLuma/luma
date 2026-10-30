# SPDX-License-Identifier: Apache-2.0
"""Real Tide controls and MCP/agent recovery, private server fixture only.

The library/player are in-memory fixtures: no account, keyring or audio. Model
decisions are scripted Responses. This proves neither hosted decisions nor
real playback, but exercises the production Tide window and Ari control path.
"""
import io,json,os,subprocess,sys,time
from pathlib import Path

root=Path(__file__).resolve().parents[2]
out=Path(os.environ['DEV_ARTIFACT_DIR'])
assert os.environ.get('ARI_TIDE_ISOLATED')=='1'

if '--app' in sys.argv:
    import gi
    gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
    gi.require_foreign('cairo')
    from gi.repository import Adw,Gio,GLib
    from luma_appkit import install_appkit,install_lumaui,add_style_sheet
    from luma_tide.fixture import FixtureLibrary
    from luma_tide.ui import TideWindow
    source=FixtureLibrary(out/'library.json')
    def state(player):
        album,song=source.library.song(player.song_id)
        (out/'player.json').write_text(json.dumps({'title':song.title,'artist':album.artist,'playing':player.playing}))
    source.subscribe(state)
    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id='org.projectluma.Tide')
            self.window=None
        def do_startup(self):
            Adw.Application.do_startup(self)
            install_appkit();install_lumaui()
            add_style_sheet(str(root/'src/luma-tide/data/tide.css'))
        def do_activate(self):
            if self.window is None:
                self.window=TideWindow(self,source)
                def view_state():
                    entry=self.window.search_item.entry
                    children=[]
                    child=self.window.search.get_first_accessible_child()
                    while child is not None and len(children)<10:
                        children.append({'type':type(child).__name__,'role':child.get_accessible_role().value_nick})
                        child=child.get_next_accessible_sibling()
                    (out/'view.json').write_text(json.dumps({'search':self.window.search.get_visible(),
                        'entry_visible':entry.get_visible(),'entry_mapped':entry.get_mapped(),
                        'entry_child_visible':entry.get_child_visible(),'search_mapped':self.window.search.get_mapped(),
                        'entry_role':entry.get_accessible_role().value_nick,'search_children':children}))
                    return True
                GLib.timeout_add(100,view_state)
            self.window.present()
    sys.exit(Application().run([]))

import gi
gi.require_version('Gio','2.0')
from gi.repository import Gio,GLib
from types import SimpleNamespace
from unittest.mock import patch
from ari.agent import Agent,Turn
from ari.chatgpt_provider import ChatGPTProvider
from ari.mcp import Client
from ari.policy import Policy
from ari.store import Store
from ari.tools import accessibility,desktop

library=json.loads((root/'tests/fixtures/tide-v70.json').read_text())
library['albums'].append({'id':'pink','t':'Wish You Were Here','a':'Pink Floyd','y':1975,
    'h':100,'h2':120,'h3':140,'tr':[['Have a Cigar','5:08']]})
(out/'library.json').write_text(json.dumps(library))
applications=Path(os.environ['XDG_DATA_HOME'])/'applications'
applications.mkdir(parents=True)
(applications/'org.projectluma.Tide.desktop').write_text('[Desktop Entry]\nType=Application\nName=Tide\nExec=false\nDBusActivatable=true\n')
bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
bus.call_sync('org.a11y.Bus','/org/a11y/bus','org.freedesktop.DBus.Properties','Set',
    GLib.Variant('(ssv)',('org.a11y.Status','IsEnabled',GLib.Variant('b',True))),None,Gio.DBusCallFlags.NONE,5000,None)
client=Client('accessibility',[sys.executable,str(Path(accessibility.__file__))])
process=None
log=(out/'tide.log').open('w')
try:
    process=subprocess.Popen([sys.executable,__file__,'--app'],stdout=log,stderr=log)
    client.start()
    def wait(function):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            value=function()
            if value:return value
            if process.poll() is not None:raise AssertionError('Tide fixture exited; inspect tide.log')
            time.sleep(.1)
        raise AssertionError('Timed out waiting for Tide fixture')
    wait(lambda:client.call('desktop_applications',{'name':'Tide'})['data'])
    info=Gio.DesktopAppInfo.new('org.projectluma.Tide.desktop')
    assert desktop._running_app(info) is True
    launched=desktop.open_app({'name':'Tide'})
    assert launched['ok'] and launched['unchanged'] and not launched['completed'],launched
    assert 'already running' in launched['summary'] and 'Opened' not in launched['summary']
    print('Real already-running GApplication launch acknowledged without false Opened receipt PASS',flush=True)
    progress={'stage':0,'retry':False,'calls':0}
    def opened(_url,*,body,**kwargs):
        progress['calls']+=1
        stage=progress['stage']
        data=None
        if stage:
            output=body['input'][-1]['output']
            assert 'Succeeded:' in output,output
            data=json.loads(output.partition('Details: ')[2])
            (out/f'stage-{stage}.json').write_text(json.dumps(data,indent=2))
        if stage==0:
            name,args='desktop_applications',{'name':'Tide'}
        elif stage==1:
            progress['application']=data[0]['application']
            command=next(row for row in data[0]['snapshot']['commands'] if row['name']=='tide-search')
            name,args='act_in_application',{'application':progress['application'],'target':command['target'],'operation':'command'}
        elif stage==2:
            entry=next((row for row in data['controls'] if row['editable']),None)
            if entry:
                name,args='act_in_application',{'application':progress['application'],'target':entry['target'],'operation':'set_text','text':'Have a Cigar'}
            else:
                if not (out/'search-tree.json').exists():
                    nodes=[]
                    def dump(node,depth=0):
                        if node is None or depth>48 or len(nodes)>1500:return
                        try:
                            nodes.append({'role':node.get_role().value_nick,'name':node.get_name(),
                                'states':[state.value_nick for state in node.get_state_set().get_states()],
                                'interfaces':node.get_interfaces()})
                            for index in range(min(node.get_child_count(),1000)):dump(node.get_child_at_index(index),depth+1)
                        except Exception:pass
                    dump(accessibility.api().get_desktop(0))
                    (out/'search-tree.json').write_text(json.dumps(nodes,indent=2))
                if progress['calls']>5:raise AssertionError('Search editor is missing; inspect view.json and search-tree.json')
                progress['stage']-=1
                name,args='inspect_application',{'application':progress['application']}
        elif stage==3:
            song=next((row for row in data['controls'] if 'have a cigar' in row['name'].casefold()
                and any(action in ('click','activate') for action in row['actions'])),None)
            if song:
                name,args='act_in_application',{'application':progress['application'],'target':song['target'],'operation':'action','action':song['actions'][0]}
            else:
                progress['stage']-=1
                name,args='inspect_application',{'application':progress['application'],'query':'Have a Cigar'}
        else:
            state=json.loads((out/'player.json').read_text())
            assert state=={'title':'Have a Cigar','artist':'Pink Floyd','playing':True},state
            if not progress['retry']:
                progress['retry']=True
                return io.BytesIO(b'data: {"type":"error","code":"server_error"}\n\n')
            item={'type':'message','content':[{'type':'output_text','text':'Selected Have a Cigar by Pink Floyd in Tide.'}]}
            return io.BytesIO(('data: '+json.dumps({'type':'response.completed','response':{'status':'completed','output':[item]}})+'\n\n').encode())
        progress['stage']+=1
        item={'type':'function_call','name':name,'namespace':'ari','call_id':str(progress['stage']),'arguments':json.dumps(args)}
        return io.BytesIO(('data: '+json.dumps({'type':'response.completed','response':{'status':'completed','output':[item]}})+'\n\n').encode())
    provider=ChatGPTProvider(SimpleNamespace(access=lambda _: 'fixture',transport=SimpleNamespace(open=opened)),
        'fixture','fixture',allow_search=False)
    tools=SimpleNamespace(available=client.tools,by_name={tool.name:(client,tool) for tool in client.tools},call=client.call)
    store=Store(out/'ari.db')
    policy=Policy();policy.approval_mode='never'
    events=[]
    agent=Agent(store,tools,policy,model_provider=lambda:(provider,'Fixture','private test','2026','large'))
    with patch('ari.agent._current_settings',return_value=''),patch('ari.agent.audit.record'):
        agent.answer(Turn(store.new_conversation(),'Play Have a Cigar by Pink Floyd on Tide',events.append))
    assert 'Selected Have a Cigar' in events[-1].get('text',''),events[-1]
    assert progress['retry']
    mutations=[event for event in events if event['type']=='step']
    assert len(mutations)==3,mutations
    assert any(event.get('label')=='Retrying ChatGPT (1/2)' for event in events)
    assert not any(event['type']=='approval' for event in events)
    store.db.close()
    print('Real Tide UI: one search command, text entry, exact track selection, fixture player/artist verified; transient continuation recovered, no repeated actions, zero approvals PASS',flush=True)
finally:
    client.stop()
    if process:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait()
    log.close()
