# SPDX-License-Identifier: Apache-2.0
"""Record the Shell's own Sysprof marks (Mutter's profiler, on the session
bus) into OUT for SECONDS: profile-shell.py OUT SECONDS."""
import os
import sys
import time

from gi.repository import Gio, GLib

out, seconds = sys.argv[1], float(sys.argv[2])
bus = Gio.bus_get_sync(Gio.BusType.SESSION)
fd = os.open(out, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o644)
fds = Gio.UnixFDList.new()
index = fds.append(fd)
bus.call_with_unix_fd_list_sync('org.gnome.Shell', '/org/gnome/Sysprof3/Profiler',
                                'org.gnome.Sysprof3.Profiler', 'Start',
                                GLib.Variant('(a{sv}h)', ({}, index)), None, 0, -1, fds, None)
print('started', flush=True)
time.sleep(seconds)
bus.call_sync('org.gnome.Shell', '/org/gnome/Sysprof3/Profiler', 'org.gnome.Sysprof3.Profiler',
              'Stop', None, None, 0, -1, None)
print('stopped', os.path.getsize(out), flush=True)
