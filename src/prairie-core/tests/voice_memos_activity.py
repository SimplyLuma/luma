#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Native UI, real broker protocol and audio lifecycle on an isolated session.

The broker's identity adapter is explicitly simulated, as in the Calls
integration fixture. Packaged app-scope acceptance is a separate VM gate.
"""
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gio, GLib, Gtk

NAME = 'org.projectluma.SemanticBroker1'
PATH = '/org/projectluma/SemanticBroker1'
APP = 'org.projectluma.VoiceMemos'


def broker(reader):
    from luma_semantic_broker.core import BrokerCore
    from luma_semantic_broker.identity import IdentityError
    from luma_semantic_broker.model import Identity, IdentityStrength
    from luma_semantic_broker.service import SemanticBrokerService
    class Grants:
        def load(self): return ()
        def save(self, _): pass
    class Audit:
        def append(self, _): pass
        def tail(self, _): return ()
    class FixtureIdentity:
        def resolve(self, sender):
            return Identity('fixture:'+sender, APP, 'Voice Memos fixture', sender, os.getuid(), os.getpid(), IdentityStrength.MANAGED_NATIVE)
        def provider(self, sender, claimed):
            if claimed != APP: raise IdentityError('Wrong fixture app')
            return self.resolve(sender)
        def require_shell_host(self, sender):
            if sender != reader: raise IdentityError('Not the fixture reader')
            return self.resolve(sender)
    holder = []
    loop = GLib.MainLoop()
    def acquired(connection, _):
        holder.append(SemanticBrokerService(connection, identity=FixtureIdentity(),
            core=BrokerCore(grant_store=Grants(), audit_store=Audit(), locked=lambda: False)))
    Gio.bus_own_name(Gio.BusType.SESSION, NAME, Gio.BusNameOwnerFlags.NONE, acquired, None, lambda *_: loop.quit())
    loop.run()


def settle(condition=lambda: True, timeout=6):
    until = time.monotonic()+timeout
    while time.monotonic()<until:
        context=GLib.MainContext.default()
        while context.pending(): context.iteration(False)
        if condition():
            time.sleep(.02)
            return
        time.sleep(.01)
    raise AssertionError('Native recording/activity state did not settle')


def run():
    schema_name = os.environ.get('LUMA_CREATOR_SCHEMA_FILE')
    schema = Path(schema_name) if schema_name else Path(__file__).resolve().parents[3] / 'src/luma-shell-state/org.project_luma.shell-state.gschema.xml'
    assert schema.is_file(), 'The canonical appearance schema is required'
    schema_dir = tempfile.TemporaryDirectory(prefix='memos-appearance-')
    shutil.copyfile(schema, Path(schema_dir.name) / schema.name)
    subprocess.run(['glib-compile-schemas', '--strict', schema_dir.name], check=True)
    os.environ['GSETTINGS_SCHEMA_DIR'] = schema_dir.name
    os.environ['GSETTINGS_BACKEND'] = 'memory'
    flags=Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT|Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
    reader=Gio.DBusConnection.new_for_address_sync(os.environ['DBUS_SESSION_BUS_ADDRESS'],flags,None,None)
    process=subprocess.Popen([sys.executable,__file__,'--broker',reader.get_unique_name()])
    try:
        for _ in range(100):
            owner=reader.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','NameHasOwner',
                GLib.Variant('(s)',(NAME,)),GLib.VariantType.new('(b)'),Gio.DBusCallFlags.NONE,1000,None).unpack()[0]
            if owner: break
            time.sleep(.02)
        assert owner, 'Private real broker did not start'
        def activities():
            return reader.call_sync(NAME,PATH,NAME,'ListLiveExtensions',None,
                GLib.VariantType.new('(aa{sv})'),Gio.DBusCallFlags.NONE,2000,None).unpack()[0]
        from prairie_apps import voice_memos
        app=voice_memos.VoiceMemosApplication()
        assert app.register(None)
        with tempfile.TemporaryDirectory(prefix='memos-runtime-') as directory:
            with patch.dict(os.environ, {'XDG_MUSIC_DIR':directory}), \
                 patch.object(voice_memos.audio,'inspect_recording_capability',return_value=SimpleNamespace(available=True,source='Private synthetic test')):
                original_start=voice_memos.audio.start_recording
                def synthetic(root,**kwargs): return original_start(root,source='audiotestsrc',**kwargs)
                with patch.object(voice_memos.audio,'start_recording',side_effect=synthetic):
                    window=voice_memos.VoiceMemosWindow(app); window.present()
                    settle(lambda:not window._busy and window.content_stack.get_visible_child_name()=='empty')
                    assert not activities(), 'Idle window published recording'
                    window._record(); settle(lambda:window.recording and not window._busy)
                    live=activities(); assert len(live)==1, 'Active recording did not publish to the broker'
                    payload=live[0]['extension']; assert payload['category']=='recording' and payload['privacy']=='private'
                    assert payload['subtitle']=='Recording'
                    window._pause_recording(); settle(); assert activities()[0]['extension']['subtitle']=='Recording paused'
                    window._pause_recording(); settle(); assert activities()[0]['extension']['subtitle']=='Recording'
                    window._record(); assert not activities(), 'Stop request left an active recording publication'
                    settle(lambda:not window.recording and not window._busy and len(window.memos)==1)
                    # Desktop More is a bounded shared anchored menu, not a wide grown panel.
                    window.set_default_size(1024,520); settle()
                    button=window.action.bar_row.get_first_child()
                    while button and button.get_name()!='me-more': button=button.get_next_sibling()
                    assert button, 'More action missing'
                    assert button.bar_item.menu is not None and button.bar_item.panel is None
                    button.emit('clicked'); settle(); assert not window.action.grown
                    window._record(); settle(lambda:window.recording and not window._busy)
                    window.session._dispose()
                    window._record_error('Synthetic device disconnect'); settle(); assert not activities()
                    # Production invokes the error callback after source disposal;
                    # the fixture above disposes first as well.
                    sessions=[]
                    def captured(root,**kwargs):
                        session=synthetic(root,**kwargs); sessions.append(session); return session
                    with patch.object(voice_memos.audio,'start_recording',side_effect=captured):
                        window._record(); settle(lambda:window.recording and not window._busy)
                        window.close(); settle(lambda:window._closed and not window.get_mapped())
                        assert not activities(), 'Close/save retained recording'
                    app.quit()
                    print('PASS real broker protocol: idle, record, pause, resume, stop, error, close; shared anchored More')
    finally:
        process.terminate()
        try:process.wait(timeout=3)
        except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--broker': broker(sys.argv[2])
    else: run()
