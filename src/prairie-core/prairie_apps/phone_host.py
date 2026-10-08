# SPDX-License-Identifier: Apache-2.0
"""Fixed, token-free Phone-only host metadata calls."""
import json
from gi.repository import Gio, GLib
BUS='org.projectluma.Connect1'
PATH='/org/projectluma/Connect'
METHODS=frozenset({'GetPhoneContext','GetPhoneHistory','GetPhoneTogether','PhoneCompanionRequest','ClosePhoneCompanion'})

def call(method, value=None):
    if method not in METHODS:
        raise ValueError('Unsupported Phone metadata request.')
    parameters=GLib.Variant('(s)',(value,)) if value is not None else None
    try:
        reply=Gio.bus_get_sync(Gio.BusType.SESSION,None).call_sync(BUS,PATH,BUS,method,
            parameters,GLib.VariantType.new('(s)'),Gio.DBusCallFlags.NONE,12000,None)
        text=reply.unpack()[0]
        if len(text.encode())>256*1024:raise ValueError('Phone reply exceeds its bound.')
        result=json.loads(text)
        if not isinstance(result,dict):raise ValueError('Invalid Phone response.')
        return result
    except (GLib.Error,ValueError):
        raise OSError('Phone connection information is unavailable. Open Luma Connect and try again.') from None
