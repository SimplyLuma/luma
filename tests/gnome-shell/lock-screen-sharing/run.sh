#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# One headless Luma session with the real portal stack, then probe.js.
#
#   run.sh OUTDIR MODE        MODE: lock | lifecycle | screens | pill | ring
#
# LOCK_MONITORS: virtual monitors, e.g. "1920x1200 3440x1440 2560x1600".
#
# Inside a Fedora container with the Shell and Mutter under test, PipeWire,
# WirePlumber, xdg-desktop-portal, luma-portal (LOCK_PORTAL: the directory
# holding luma_portal, defaults to the installed one), pipewire-gstreamer and
# python3-gobject, with the luma-portal D-Bus service pointing at
# portal-launch.sh. LOCK_OVERLAY loads LOCK_OVERLAY/js/ui and
# LOCK_OVERLAY/data/theme over the installed Shell. LOCK_KEYFILE, LOCK_W,
# LOCK_H as in tests/gnome-shell/screen-sharing.
set -u
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
out=$1
mode=${2:-lock}
mkdir -p "$out"
rm -f "$out"/*.png "$out"/*.json "$out"/*.txt "$out"/*.log "$out/stop"
export LOCK_OUT=$out LOCK_HERE=$here LOCK_MODE=$mode
export SHARE_APPS=$here/../screen-sharing
export XDG_RUNTIME_DIR=/tmp/lock-run; rm -rf $XDG_RUNTIME_DIR; mkdir -m 700 -p $XDG_RUNTIME_DIR
export HOME=/tmp/lock-home; rm -rf $HOME
mkdir -p $HOME/.config/glib-2.0/settings $HOME/.local/share/applications
cp ${LOCK_KEYFILE:-$SHARE_APPS/keyfile.light} $HOME/.config/glib-2.0/settings/keyfile
cp $SHARE_APPS/apps/*.desktop $HOME/.local/share/applications/
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1
export XDG_CURRENT_DESKTOP=GNOME XDG_SESSION_TYPE=wayland GDK_BACKEND=wayland
export GTK_A11Y=none NO_AT_BRIDGE=1
if [ -n "${LOCK_OVERLAY:-}" ]; then
  export G_RESOURCE_OVERLAYS="/org/gnome/shell/ui=$LOCK_OVERLAY/js/ui:/org/gnome/shell/theme=$LOCK_OVERLAY/data/theme"
fi
# One session at a time: a PipeWire or portal left by an earlier run would
# serve this one's clients.
cleanup() {
  pkill -x pipewire 2>/dev/null; pkill -x wireplumber 2>/dev/null
  pkill -f xdg-desktop-portal 2>/dev/null; pkill -f luma_portal 2>/dev/null
  pkill -f "$here/client.py" 2>/dev/null; pkill -f "screen-sharing/window.py" 2>/dev/null
  pkill -f fake-gdm.py 2>/dev/null
  true
}
cleanup
mkdir -p /run/dbus
dbus-send --system --dest=org.freedesktop.DBus --type=method_call --print-reply / \
    org.freedesktop.DBus.GetId >/dev/null 2>&1 ||
  { rm -f /run/dbus/system_bus_socket /run/dbus/pid /run/dbus/messagebus.pid; dbus-daemon --system --fork 2>/dev/null; }
rm -rf /run/systemd/seats
if [ "$mode" != ring ]; then
  # The Shell makes its screen shield only where it can lock (fake-gdm.py).
  # Container only: no logind to activate, a seats directory, a GDM that
  # answers its version.
  rm -f /usr/share/dbus-1/system-services/org.freedesktop.login1.service
  cat >/etc/dbus-1/system.d/luma-lock-test.conf <<'CONF'
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig><policy user="root"><allow own="org.gnome.DisplayManager"/>
<allow send_destination="org.gnome.DisplayManager"/></policy></busconfig>
CONF
  dbus-send --system --type=method_call --dest=org.freedesktop.DBus / org.freedesktop.DBus.ReloadConfig 2>/dev/null
  pkill -f fake-gdm.py 2>/dev/null
  python3 "$here/fake-gdm.py" >"$out/fake-gdm.log" 2>&1 </dev/null &
  sleep 1
  mkdir -p /run/systemd/seats
fi
rpm -q mutter gnome-shell xdg-desktop-portal >"$out/packages.txt" 2>&1
echo "LOCK_PORTAL=${LOCK_PORTAL:-installed}" >>"$out/packages.txt"
timeout ${LOCK_TIMEOUT:-420} dbus-run-session -- bash -c '
  pipewire > "$LOCK_OUT/pipewire.log" 2>&1 &
  sleep 1
  wireplumber > "$LOCK_OUT/wireplumber.log" 2>&1 &
  sleep 1
  monitors=""
  for m in ${LOCK_MONITORS:-${LOCK_W:-1920}x${LOCK_H:-1200}}; do monitors="$monitors --virtual-monitor $m"; done
  gnome-shell --headless --no-x11 $monitors \
    --automation-script=$LOCK_HERE/probe.js' > "$out/shell.log" 2>&1
status=$?
touch "$out/stop"
sleep 1
cleanup
python3 - "$out/checks.json" "$status" <<'PY'
import json, sys
try:
    checks = json.load(open(sys.argv[1]))
except FileNotFoundError:
    print(f"no result: gnome-shell exited with {sys.argv[2]}")
    sys.exit(1)
failed = [c for c in checks if not c["ok"]]
for c in checks:
    print(("PASS " if c["ok"] else "FAIL ") + c["name"] + ("" if c["ok"] else f"  -- {c.get('detail')}"))
print(f"{len(checks) - len(failed)} passed, {len(failed)} failed")
sys.exit(min(len(failed), 100))
PY
