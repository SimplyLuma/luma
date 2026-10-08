"""Actual private session-bus daemon control; synthetic empty account only."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from gi.repository import Gio,GLib
from luma_continuity.account import AccountModel
from luma_continuity.daemon import Daemon,BUS,PATH


with tempfile.TemporaryDirectory() as root:
    os.environ['HOME']=root
    loop=GLib.MainLoop()
    connection=Gio.bus_get_sync(Gio.BusType.SESSION,None)
    daemon=Daemon(connection,AccountModel(),observe_environment=False)
    name=connection.get_unique_name()
    errors=[]
    def work():
        try:
            def call(method,params=None):
                return connection.call_sync(name,PATH,BUS,method,params,None,Gio.DBusCallFlags.NONE,5000,None)
            def state():return json.loads(call('GetState').unpack()[0])
            for enabled in (False,True,False):
                deadline=time.monotonic()+5
                while state().get('busy'):
                    assert time.monotonic()<deadline
                    time.sleep(.01)
                call('SetEnabled',GLib.Variant('(b)',(enabled,)))
                while state().get('busy'):
                    assert time.monotonic()<deadline
                    time.sleep(.01)
                result=json.loads(call('GetQuickState').unpack()[0])
                assert result['enabled']==enabled and result['connected_peers']==0,result
                assert result['status']==('unconfigured' if enabled else 'disabled'),result
                assert not state().get('error'),state().get('error')
                assert json.loads((Path(root)/'.local/share/luma-connect/enabled.json').read_text())==enabled
            assert not (Path(root)/'.local/share/luma-connect/device').exists()
        except BaseException as error:errors.append(error)
        finally:GLib.idle_add(loop.quit)
    thread=threading.Thread(target=work)
    thread.start();loop.run();thread.join();daemon.close()
    if errors:raise errors[0]
    restarted=Daemon(connection,AccountModel(),observe_environment=False)
    assert restarted.enabled is False
    restarted.close()
    print('PASS actual D-Bus enable/disable persistence and restart; no identity creation or grants')
