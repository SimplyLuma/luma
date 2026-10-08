# SPDX-License-Identifier: Apache-2.0
"""On-demand per-user owner; only installed signed Mods is admitted."""
import threading
from concurrent.futures import ThreadPoolExecutor
from gi.repository import Gio, GLib
from .profile_host import BUS, OBJECT, MAX_INPUT, Profiles, decode, encode
from .runtime import UserRuntime

XML = '<node><interface name="'+BUS+'"><method name="Call"><arg name="request" type="s" direction="in"/><arg name="result" type="s" direction="out"/></method></interface></node>'

class Service:
    def __init__(self):
        self.loop = GLib.MainLoop()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='mod-profiles')
        self.slots = threading.BoundedSemaphore(2)
        self.active = 0
        self.profiles = Profiles(UserRuntime.current())
        self.owner = Gio.bus_own_name(Gio.BusType.SESSION, BUS, Gio.BusNameOwnerFlags.NONE,
            self.acquired, None, lambda *_: self.loop.quit())
        GLib.timeout_add_seconds(60, self.idle)
    def idle(self):
        if self.active: return GLib.SOURCE_CONTINUE
        self.loop.quit(); return GLib.SOURCE_REMOVE
    def acquired(self, connection, _name):
        connection.register_object(OBJECT, Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], self.call, None, None)
    def call(self, connection, sender, _path, _interface, method, args, invocation):
        if method != 'Call' or args.get_size() > MAX_INPUT + 64:
            invocation.return_dbus_error(BUS+'.InvalidInput', 'Invalid Mods request.'); return
        if not self.slots.acquire(blocking=False):
            invocation.return_dbus_error(BUS+'.Busy', 'A Mods operation is already running.'); return
        self.active += 1
        def finish(value=None, error=None):
            try:
                if error: invocation.return_dbus_error(BUS+'.Refused', 'The operation was refused. Refresh the Mod review and current state.')
                else: invocation.return_value(GLib.Variant('(s)', (value,)))
            finally: self.active -= 1; self.slots.release()
            return GLib.SOURCE_REMOVE
        def work():
            try:
                from luma_installer.app_data_broker import authenticate
                if authenticate(connection, sender) != 'org.projectluma.Mods': raise PermissionError('This is not Mods.')
                value = encode(self.profiles.dispatch(decode(args.unpack()[0])))
            except Exception as error: GLib.idle_add(finish, None, error)
            else: GLib.idle_add(finish, value, None)
        try: self.pool.submit(work)
        except Exception as error: finish(None, error)
    def run(self):
        try: self.loop.run()
        finally:
            self.pool.shutdown(wait=True)
            Gio.bus_unown_name(self.owner)
def main(): Service().run()
if __name__ == '__main__': main()
