#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Release gate checks: package files (RPM, DEB, AppImage, Flatpak bundles and
# references, Snap, APK, Luma's Android package set, Windows programs) open in
# Valet without the "choose an application" prompt, from a sandboxed app
# (through the OpenURI portal) as well as from the Shell and gio. Run as root
# inside a disposable VM installed from the candidate image or from a nightly
# medium. Needs no display and no network. Prints one JSON line per check
# ({"check", "result": "pass"|"fail"|"skip", "detail"}) and exits 1 if any
# check failed. The throwaway account it creates is removed again.
#
#   package-files-open-in-valet.sh
#
# Checks run in the environment a GNOME session would have:
# XDG_CURRENT_DESKTOP=GNOME plus what systemd's environment.d generators give
# a user session (the launcher policy's XDG_DATA_DIRS).
set -uo pipefail
valet=io.luma.Valet.desktop
policy=/usr/share/luma/open-directly.list
package_types="application/x-rpm application/vnd.debian.binary-package application/vnd.appimage
  application/vnd.flatpak application/vnd.flatpak.ref application/vnd.flatpak.repo application/vnd.snap
  application/vnd.android.package-archive application/vnd.projectluma.android-package-set
  application/x-ms-dos-executable application/x-msdownload application/x-msi
  application/vnd.microsoft.portable-executable"
user=luma-gate-valet
failed=0
work=$(mktemp -d /var/tmp/luma-gate-valet.XXXXXX)
chmod 0755 "$work"

emit() {
  python3 -c 'import json, sys; print(json.dumps({"check": sys.argv[1], "result": sys.argv[2], "detail": sys.argv[3][-600:]}))' "$1" "$2" "$3"
  [ "$2" = fail ] && failed=1
  return 0
}
cleanup() {
  pkill -KILL -u "$user" >/dev/null 2>&1 || true
  sleep 1
  userdel -r "$user" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

session_env() {
  local home runtime line gen
  home=$(getent passwd "$user" | cut -d: -f6)
  runtime="$work/run"
  install -d -m 0700 -o "$user" -g "$user" "$runtime"
  env_lines=("HOME=$home" "USER=$user" "LOGNAME=$user" "PATH=/usr/local/bin:/usr/bin:/bin"
             "XDG_CURRENT_DESKTOP=GNOME" "XDG_SESSION_DESKTOP=gnome" "XDG_SESSION_TYPE=wayland"
             "XDG_RUNTIME_DIR=$runtime")
  for gen in /usr/lib/systemd/user-environment-generators/*; do
    [ -x "$gen" ] || continue
    while IFS= read -r line; do
      case $line in [A-Za-z_]*=*) env_lines+=("$line") ;; esac
    done < <(cd /tmp && runuser -u "$user" -- env -i "${env_lines[@]}" "$gen" 2>/dev/null)
  done
}
as_session() {
  session_env
  (cd /tmp && runuser -u "$user" -- env -i "${env_lines[@]}" "$@")
}

# 1. The portal is Luma's build, with the package-file policy compiled in.
portal=$(rpm -q xdg-desktop-portal 2>&1)
case $portal in
  *.luma.*) emit portal-is-luma-build pass "$portal" ;;
  *) emit portal-is-luma-build fail "not a Luma build: $portal" ;;
esac
if grep -qaF "$policy" /usr/libexec/xdg-desktop-portal; then
  emit portal-has-policy pass "/usr/libexec/xdg-desktop-portal reads $policy"
else
  emit portal-has-policy fail "/usr/libexec/xdg-desktop-portal does not read $policy"
fi

# 2. The policy names Valet for every package type, and Valet is a system app.
missing=
for type in $package_types; do
  grep -Fxq "$type=$valet" "$policy" 2>/dev/null || missing="$missing $type"
done
if [ -f "$policy" ] && [ -z "$missing" ]; then emit policy-lists-package-types pass "$policy names $valet for every package type"
else emit policy-lists-package-types fail "$policy does not name $valet for:${missing:- (file missing)}"; fi
if [ -f "/usr/share/applications/$valet" ] && [ ! -L "/usr/share/applications/$valet" ]; then
  emit valet-is-system-app pass "/usr/share/applications/$valet"
else
  emit valet-is-system-app fail "/usr/share/applications/$valet is missing or a link"
fi

# 3. A fresh account's default for every package type is Valet (gio, the Shell).
useradd -m "$user" || { emit fresh-account fail "useradd $user failed"; exit 1; }
bad=
for type in $package_types; do
  got=$(as_session gio mime "$type" 2>&1 | sed -n 's/^Default application for .*: //p' | head -n 1)
  [ "$got" = "$valet" ] || bad="$bad $type=${got:-none}"
done
if [ -z "$bad" ]; then emit gio-mime-package-types pass "every package type defaults to $valet"
else emit gio-mime-package-types fail "not $valet:$bad"; fi

# 4. OpenFile on an RPM skips the chooser; with ask=true it does not. The
#    portal runs on a private session bus with its debug log; the installer it
#    launches has no display here and exits. Needs dbus-run-session, which the
#    image does not ship; the package build runs the same cases as %check.
if ! command -v dbus-run-session >/dev/null 2>&1; then
  emit portal-opens-rpm-directly skip 'dbus-run-session is not installed; covered by the xdg-desktop-portal %check'
  emit portal-ask-still-asks skip 'dbus-run-session is not installed; covered by the xdg-desktop-portal %check'
  exit "$failed"
fi
home=$(getent passwd "$user" | cut -d: -f6)
printf '\355\253\356\333\003\000\000\000' >"$home/package.rpm"
head -c 88 /dev/zero >>"$home/package.rpm"
chown "$user:$user" "$home/package.rpm"
cat >"$work/open.py" <<'PY'
import os, sys, gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib
bus = Gio.bus_get_sync(Gio.BusType.SESSION)
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_CLOEXEC)
fds = Gio.UnixFDList.new_from_array([fd])
options = {"ask": GLib.Variant("b", sys.argv[2] == "ask")}
bus.call_with_unix_fd_list_sync(
    "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
    "org.freedesktop.portal.OpenURI", "OpenFile",
    GLib.Variant("(sha{sv})", ("", 0, options)), None, Gio.DBusCallFlags.NONE, 20000, fds, None)
PY
cat >"$work/probe.sh" <<'SH'
#!/bin/bash
# probe.sh FILE ask|direct LOG: OpenFile through a private portal, then the log.
/usr/libexec/xdg-desktop-portal --verbose --replace >"$3" 2>&1 &
portal=$!
# The bus name is taken before every portal interface is exported.
for _ in $(seq 150); do
  gdbus introspect --session --dest org.freedesktop.portal.Desktop \
    --object-path /org/freedesktop/portal/desktop 2>/dev/null |
    grep -q 'interface org.freedesktop.portal.OpenURI ' && break
  sleep 0.2
done
python3 -B "$(dirname "$0")/open.py" "$1" "$2" >>"$3" 2>&1
for _ in $(seq 50); do
  grep -q 'app chooser' "$3" && break
  sleep 0.2
done
kill "$portal" 2>/dev/null
SH
chmod 0755 "$work/probe.sh" "$work/open.py"
for mode in direct ask; do
  as_session dbus-run-session -- timeout 60 "$work/probe.sh" "$home/package.rpm" "$mode" "$work/run/$mode.log" \
    >/dev/null 2>&1
done
reason=$(grep -o 'Allowing to skip app chooser: system installer' "$work/run/direct.log" | head -n 1)
if [ -n "$reason" ]; then emit portal-opens-rpm-directly pass "$reason"
else emit portal-opens-rpm-directly fail "$(grep -i 'chooser\|content type\|error' "$work/run/direct.log" | tail -n 5)"; fi
reason=$(grep -o 'Refusing to skip app chooser: app request' "$work/run/ask.log" | head -n 1)
if [ -n "$reason" ]; then emit portal-ask-still-asks pass "$reason"
else emit portal-ask-still-asks fail "$(grep -i 'chooser\|error' "$work/run/ask.log" | tail -n 5)"; fi

exit "$failed"
