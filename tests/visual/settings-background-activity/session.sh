#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
# A long-lived X session with Settings open on Apps, driven with xdotool.
export HOME=/tmp/settings-home XDG_RUNTIME_DIR=/tmp/settings-run
rm -rf $HOME $XDG_RUNTIME_DIR; mkdir -p $HOME $XDG_RUNTIME_DIR; chmod 700 $XDG_RUNTIME_DIR
export XDG_CURRENT_DESKTOP=GNOME GDK_BACKEND=x11 GSK_RENDERER=cairo LIBGL_ALWAYS_SOFTWARE=1 NO_AT_BRIDGE=1 GTK_A11Y=none
mkdir -p /run/dbus && dbus-daemon --system --fork
Xvfb :77 -screen 0 1280x860x24 >/dev/null 2>&1 &
export DISPLAY=:77
sleep 2
exec dbus-run-session -- bash -c '
  python3 /work/fake_agents.py > /work/out/fake.log 2>&1 &
  sleep 1
  gnome-control-center applications > /work/out/settings.log 2>&1 &
  sleep infinity'
