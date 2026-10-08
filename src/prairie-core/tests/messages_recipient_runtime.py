#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Native recipient preparation supports owned self conversations after device lookup.

Directory/helper replies are controlled; this does not claim live E2EE delivery.
"""
import json, os, shutil, subprocess, tempfile, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Adw,Gio,GLib


def settle():
    end=time.monotonic()+.1
    while time.monotonic()<end:
        while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
        time.sleep(.005)


def run():
    schema_name=os.environ.get('LUMA_CREATOR_SCHEMA_FILE')
    schema=Path(schema_name) if schema_name else Path(__file__).resolve().parents[3]/'src/luma-shell-state/org.project_luma.shell-state.gschema.xml'
    assert schema.is_file()
    with tempfile.TemporaryDirectory(prefix='messages-recipient-') as directory:
        root=Path(directory);shutil.copyfile(schema,root/schema.name)
        subprocess.run(['glib-compile-schemas','--strict',str(root)],check=True)
        os.environ['GSETTINGS_SCHEMA_DIR']=str(root);os.environ['GSETTINGS_BACKEND']='memory'
        from luma_appkit import install_appkit,install_lumaui,add_style_sheet
        from prairie_apps import messages,messages_luma as luma
        from prairie_apps.messages_app_port import LumaUIMessagesWindow
        app=Adw.Application(application_id='org.projectluma.RecipientReview',flags=Gio.ApplicationFlags.NON_UNIQUE)
        assert app.register(None);install_appkit();install_lumaui()
        add_style_sheet(os.environ.get('LUMA_MESSAGES_STYLE_PATH',str(Path(messages.__file__).resolve().parents[1]/'style/messages.css')))
        fixture=root/'messages.json';fixture.write_text(json.dumps({'people':{},'conversations':[]}))
        calls,opened,names,done=[],[],[],[]
        store=SimpleNamespace(threads=lambda:[],set_display_name=lambda address,name:names.append((address,name)))
        service=SimpleNamespace(store=store)
        def request(_service,command,args,callback):calls.append((command,args,callback))
        with patch.dict(os.environ,{'LUMA_MESSAGES_FIXTURE':str(fixture)}):
            window=LumaUIMessagesWindow(app);window.present();settle()
            assert window.surface is not None, 'Test must use the shipped LumaUI Messages surface'
            with patch.object(window,'luma_call',side_effect=request),patch.object(window,'_open_thread',side_effect=lambda record,**_:opened.append(record)):
                def start(handle):
                    window.start_luma_conversation(service,handle,done.append)
                    assert calls[-1][0]=='luma.identity','Own account must be freshly verified'
                    return calls[-1][2]
                window._luma_identity={'account':'stale-cached-account'}
                start('myself')({'account':'own-account'},None)
                assert calls[-1][0]=='luma.people'
                calls[-1][2]({'account':'own-account','display_name':'hello@example.invalid'},None)
                assert calls[-1][0]=='luma.devices'
                calls[-1][2]({'devices':[{'id':'own-enrolled-device'}]},None)
                assert done[-1] is None and opened[-1].display_name=='@myself'
                assert names[-1]==('@myself','@myself')
                start('friend')({},None);assert done[-1].code=='identity_unavailable'
                failure=SimpleNamespace(code='not_connected',message='Reconnect first')
                start('friend')(None,failure);assert done[-1] is failure
                old=start('old');new=start('new');count=len(calls)
                old({'account':'own-account'},None);assert len(calls)==count,'Stale intent fetched a recipient'
                new({'account':'own-account'},None)
                calls[-1][2]({'account':'friend','display_name':'Private email@example.invalid'},None)
                assert calls[-1][0]=='luma.devices'
                calls[-1][2]({'devices':[{'id':'friend-device'}]},None)
                assert done[-1] is None and names[-1]==('@new','@new') and opened[-1].display_name=='@new'
                assert luma.public_person_name({'display_name':'  Alice  Example  '},'alice')=='Alice Example'
                assert luma.public_person_name({'display_name':''},'alice')=='@alice'
                assert luma.public_person_name({'display_name':'hello@example.invalid'},'alice')=='@alice'
                assert luma.public_person_name({'display_name':{}},'alice')=='@alice'
                callback=start('later');window.closed=True;count=len(calls)
                callback({'account':'own-account'},None);assert len(calls)==count
                window.closed=False
            window.close();settle();app.quit()
        print('PASS native self preparation after actual availability lookup; fresh identity, stale/closed callbacks, real-name/public-handle fallback')


if __name__=='__main__':run()
