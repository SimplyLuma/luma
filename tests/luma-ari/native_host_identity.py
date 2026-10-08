# SPDX-License-Identifier: Apache-2.0
"""Real bus/process custody; root must be denied, ordinary host identity admitted."""
import os
from gi.repository import Gio
from ari.host_identity import authenticate, Refused
bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
if os.getuid()==0:
    try: authenticate(bus,bus.get_unique_name())
    except Refused: print("ACTUAL ROOT BUS CALLER REFUSED")
    else: raise AssertionError("Root acquired ordinary-user authority")
else:
    assert authenticate(bus,bus.get_unique_name())=="native"
    print("ACTUAL NATIVE UID/PID/START/HOSTROOT/BROKER NAMESPACE PASS")
