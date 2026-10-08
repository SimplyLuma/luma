"""Messages uses the daemon's scoped exchange, never its cloud credentials."""
import json
from . import transport


class DaemonRelayExchange:
    def __init__(self, _directory, peer, _epoch, _address, _port, *, timeout=10, authorized):
        self.peer,self.authorized=peer,authorized
        self.timeout=timeout

    def __call__(self, request):
        from gi.repository import Gio,GLib
        from .daemon import BUS,PATH
        if not self.authorized(): raise PermissionError('account unavailable')
        encoded=transport.encode(request).decode('utf-8')
        connection=Gio.bus_get_sync(Gio.BusType.SESSION,None)
        result=connection.call_sync(BUS,PATH,BUS,'ExchangeMessage',GLib.Variant('(ss)',(self.peer,encoded)),
            GLib.VariantType.new('(s)'),Gio.DBusCallFlags.NONE,45000,None)
        if not self.authorized(): raise PermissionError('account authorization lost')
        raw=result.unpack()[0]
        if len(raw.encode('utf-8'))>transport.MAX_FRAME: raise ValueError('receipt bound')
        value=json.loads(raw)
        if (not isinstance(value,dict) or set(value)!={'state','result'}
                or value['state'] not in {'complete','unknown','denied','invalid'}
                or value['result'] is not None and not isinstance(value['result'],dict)):
            raise ValueError('invalid relay receipt')
        return value
