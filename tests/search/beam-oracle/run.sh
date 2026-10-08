#!/bin/bash
# Beam search oracle in a Fedora container with the built gnome-shell,
# luma-search and localsearch installed. run.sh THEME(light|dark) OUT [SCALE]
# The container needs the oracle's bwrap stand-in at /usr/bin/bwrap
# (containers cannot nest bubblewrap; glycin encodes the screenshots).
set -u
theme=${1:-light}; out=${2:-/work/out-$theme}
export ORACLE_SCALE=${3:-1}
here=$(cd "$(dirname "$0")" && pwd)
base=/var/lib/luma-search-oracle-$theme; rm -rf "$base"; mkdir -p "$base"
export HOME=$base/home XDG_RUNTIME_DIR=$base/run
rm -rf "$out"; mkdir -p "$out" "$HOME" ; mkdir -m 700 -p "$XDG_RUNTIME_DIR"
export XDG_CONFIG_HOME=$HOME/.config XDG_DATA_HOME=$HOME/.local/share XDG_CACHE_HOME=$HOME/.cache
mkdir -p "$XDG_CONFIG_HOME/glib-2.0/settings" "$XDG_DATA_HOME/fonts" "$XDG_DATA_HOME/applications"
cp /work/fonts/*.ttf "$XDG_DATA_HOME/fonts/" 2>/dev/null
cd "$HOME"
mkdir -p Desktop Documents Downloads Music Pictures Videos depot depot-1840284-backup-x2 depot-old \
  Projects/depot/build/depot Documents/Taxes
for f in "Documents/report.pdf" "Documents/report (copy).pdf" "Documents/report (1).pdf" "Documents/Taxes/invoice-march.pdf" "Projects/depot/README.md"; do printf x >"$f"; done
cat >"$XDG_CONFIG_HOME/user-dirs.dirs" <<DIRS
XDG_DESKTOP_DIR="\$HOME/Desktop"
XDG_DOCUMENTS_DIR="\$HOME/Documents"
XDG_DOWNLOAD_DIR="\$HOME/Downloads"
XDG_MUSIC_DIR="\$HOME/Music"
XDG_PICTURES_DIR="\$HOME/Pictures"
XDG_VIDEOS_DIR="\$HOME/Videos"
DIRS
# Settings panels the Luma pages point at (the container has no Settings app).
for panel in applications background display power notifications privacy sound multitasking keyboard mouse datetime region users about search universal-access dash; do
  printf '[Desktop Entry]\nType=Application\nName=%s\nExec=true\nIcon=preferences-system-symbolic\nNoDisplay=true\n' "$panel" >"$XDG_DATA_HOME/applications/gnome-$panel-panel.desktop"
done
# A mail app named like Charlie: nothing in its id says mail.
printf '[Desktop Entry]\nType=Application\nName=Post\nExec=true\nIcon=mail-send-symbolic\nCategories=Network;Email;\n' >"$XDG_DATA_HOME/applications/org.oracle.Post.desktop"
mkdir -p "$XDG_DATA_HOME/gnome-shell/search-providers"
printf '[Shell Search Provider]\nDesktopId=org.oracle.Post.desktop\nBusName=org.oracle.Post\nObjectPath=/org/oracle/Post/SearchProvider\nVersion=2\n' >"$XDG_DATA_HOME/gnome-shell/search-providers/org.oracle.Post.search-provider.ini"
scheme=prefer-light; [ "$theme" = dark ] && scheme=prefer-dark
cat >"$XDG_CONFIG_HOME/glib-2.0/settings/keyfile" <<KEY
[org/project-luma/shell-state]
surface-treatment='$theme'
reduce-transparency=false

[org/gnome/desktop/interface]
color-scheme='$scheme'
enable-animations=false

[org/gnome/mutter]
experimental-features=['scale-monitor-framebuffer']

[org/gnome/shell]
welcome-dialog-last-shown-version='999'

[org/freedesktop/tracker/miner/files]
index-recursive-directories=['\$HOME']
index-single-directories=@as []
KEY
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1 ORACLE_OUT=$out LANG=C.UTF-8
mkdir -p /run/dbus
if ! dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.GetId >/dev/null 2>&1; then
  rm -f /run/dbus/system_bus_socket /run/dbus/pid; dbus-daemon --system --fork 2>/dev/null
fi
# Without a real logind, the Shell must not take the systemd login path.
rm -rf /run/systemd/seats
timeout ${ORACLE_TIMEOUT:-240} dbus-run-session -- bash -c '
  # One indexer: start it and let it own its bus name before anything asks.
  /usr/libexec/localsearch-3 >"$ORACLE_OUT/localsearch.log" 2>&1 &
  for i in $(seq 1 50); do dbus-send --session --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.NameHasOwner string:org.freedesktop.LocalSearch3 2>/dev/null | grep -q true && break; sleep 0.2; done
  python3 '"$here"'/filemanager1.py "$ORACLE_OUT/filemanager1.log" &
  python3 '"$here"'/mailprovider.py &
  for i in $(seq 1 60); do localsearch status 2>/dev/null | grep -q "Currently indexed: [1-9]" && localsearch status | grep -q idle && break; sleep 1; done
  localsearch status >"$ORACLE_OUT/index.txt" 2>&1
  gnome-shell --headless --no-x11 --virtual-monitor 1920x1200 --automation-script='"$here"'/probe.js
' >"$out/shell.log" 2>&1
echo "exit $?"
grep -c "JS ERROR" "$out/shell.log"
