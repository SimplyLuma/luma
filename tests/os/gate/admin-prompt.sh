#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Release gate checks for the administrator prompt (GNOME Shell's polkit
# agent, Seal since Shell patch 0146). A broken prompt locks every person out
# of administrator actions, so the gate authenticates through the real prompt
# in a real logged-in GNOME session: real polkitd, the real agent helper and
# Fedora's PAM stack. The password is typed by the gate host as key presses
# into the VM (virsh send-key); nothing here answers polkit itself.
#
# Run as root in a disposable gate VM, one phase per call, driven by
# scripts/os/gate.sh (gate_admin_prompt):
#
#   admin-prompt.sh setup            (password on stdin) a wheel member with
#                                    that password, logged in by GDM, with
#                                    GNOME Shell running that session
#   admin-prompt.sh start CASE       starts CASE as that person in their own
#                                    user manager; prints "prompt" once the
#                                    prompt has started a PAM conversation,
#                                    "done" if CASE ended without one
#   admin-prompt.sh pending CASE     after a wrong password: CASE is still
#                                    waiting, PAM refused the password and
#                                    the prompt asks again
#   admin-prompt.sh finish CASE      waits for CASE to end; it must succeed
#
# CASE is pkexec (`pkexec true`), flatpak-system (Depot's system Flatpak
# install through flatpak-system-helper) or udisks-open (included UDisks2
# opening only the owned LUMAGATEUSB fixture for writing, without writing data).
# setup, pending and finish print one JSON line per check
# ({"check", "result": "pass"|"fail", "detail"}).
set -uo pipefail
user=luma-gate-admin
state=/var/tmp/luma-gate-admin-prompt
phase=${1:-}
case_name=${2:-}

emit() {
  python3 -c 'import json, sys; print(json.dumps({"check": sys.argv[1], "result": sys.argv[2], "detail": sys.argv[3][-900:]}))' "$1" "$2" "$3"
}
helpers() { pgrep -f 'polkit-agent-helper-1' 2>/dev/null | wc -l; }
unit() { printf 'luma-gate-prompt-%s' "$1"; }
as_user_systemctl() { systemctl --user -M "$user@" "$@"; }

case "$phase" in
setup)
  install -d -m 0700 "$state"
  IFS= read -r password
  id "$user" >/dev/null 2>&1 || useradd -m -G wheel -c 'Gate Admin' "$user"
  printf '%s:%s\n' "$user" "$password" | chpasswd
  # Past first-boot setup, straight into the person's session.
  install -d -o "$user" -g "$user" "/home/$user/.config"
  printf 'yes' >"/home/$user/.config/gnome-initial-setup-done"
  chown "$user:$user" "/home/$user/.config/gnome-initial-setup-done"
  rm -f /var/lib/gdm/run-initial-setup
  python3 - <<'PY'
import configparser
path = "/etc/gdm/custom.conf"
c = configparser.ConfigParser(strict=False, interpolation=None)
c.optionxform = str
c.read(path)
if not c.has_section("daemon"):
    c.add_section("daemon")
c.set("daemon", "AutomaticLoginEnable", "True")
c.set("daemon", "AutomaticLogin", "luma-gate-admin")
c.set("daemon", "InitialSetupEnable", "False")
with open(path, "w") as f:
    c.write(f)
PY
  # A clean slate: GDM logs in automatically once per start, so any earlier
  # session of this person is ended first.
  loginctl disable-linger "$user" >/dev/null 2>&1 || true
  loginctl terminate-user "$user" >/dev/null 2>&1 || true
  sleep 3
  date +%s >"$state/since"
  systemctl restart gdm
  deadline=$((SECONDS + 240)); session= ; shell=
  while [ "$SECONDS" -lt "$deadline" ]; do
    session=$(loginctl list-sessions --no-legend 2>/dev/null | awk -v u="$user" '$3 == u { print $1 }' | while read -r s; do
      [ "$(loginctl show-session "$s" -p Type --value)" = wayland ] && [ "$(loginctl show-session "$s" -p State --value)" = active ] && echo "$s"; done | head -n 1)
    if [ -n "$session" ]; then
      # polkit 127 logs no agent registration, so the session's own Shell is
      # the evidence that an agent can exist; the prompt cases below prove it
      # answers (without one, polkit refuses with no dialog and they fail).
      # polkit answers a request from outside a session by looking up the
      # person's display session, so that must be this one.
      [ "$(loginctl show-user "$user" -p Display --value 2>/dev/null)" = "$session" ] || { sleep 3; continue; }
      shell=$(as_user_systemctl is-active org.gnome.Shell@wayland.service 2>/dev/null)
      [ "$shell" = active ] || shell=$(pgrep -a -f 'gnome-shell --mode=user' | head -n 1)
      [ -n "$shell" ] && break
    fi
    sleep 3
  done
  if [ -n "$session" ]; then
    emit admin-session pass "GDM logged $user (wheel) into active Wayland session $session, the session polkit asks about"
  else
    emit admin-session fail "no active Wayland session for $user: $(loginctl list-sessions --no-legend 2>&1 | tr '\n' ';')"
    exit 1
  fi
  if [ -n "$shell" ]; then
    emit admin-shell pass "GNOME Shell runs the session that must show the prompt: $shell"
  else
    emit admin-shell fail "no GNOME Shell in session $session: $(as_user_systemctl list-units --state=failed --no-legend --plain 2>&1 | tr '\n' ';')"
    exit 1
  fi
  # A settled session: the prompt must not race the session's own start-up.
  sleep 15
  cat >"$state/udisks.py" <<'PY'
import fcntl
import os
import stat
import time

BUS = 'org.freedesktop.UDisks2'
ROOT = '/org/freedesktop/UDisks2'
SERIAL = 'LUMAGATEUSB'
SIZE = 64 * 1024 * 1024


def select_gate_block(objects):
    """Reject every disk except the uniquely identified disposable gate USB."""
    matches = []
    for path, interfaces in objects.items():
        block = interfaces.get(BUS + '.Block')
        if not block or BUS + '.Partition' in interfaces:
            continue
        drive = objects.get(block.get('Drive'), {}).get(BUS + '.Drive', {})
        if drive.get('Serial') != SERIAL:
            continue
        if (drive.get('ConnectionBus') != 'usb' or block.get('Size') != SIZE or
                block.get('HintIgnore', False) or block.get('ReadOnly', False)):
            raise ValueError('gate USB identity or write eligibility changed')
        if interfaces.get(BUS + '.Filesystem', {}).get('MountPoints'):
            raise ValueError('gate USB unexpectedly mounted')
        device = bytes(block.get('PreferredDevice', [])).rstrip(b'\0').decode()
        if not device.startswith('/dev/'):
            raise ValueError('gate USB has no block device identity')
        matches.append((path, device))
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError('gate USB identity is ambiguous')
    return matches[0]


def main():
    from gi.repository import Gio, GLib
    connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    def snapshot():
        return connection.call_sync(
            BUS, ROOT, 'org.freedesktop.DBus.ObjectManager', 'GetManagedObjects',
            None, None, Gio.DBusCallFlags.NONE, 10_000, None).unpack()[0]

    deadline = time.monotonic() + 60
    target = None
    while target is None and time.monotonic() < deadline:
        target = select_gate_block(snapshot())
        if target is None:
            time.sleep(0.5)
    if target is None:
        raise RuntimeError('UDisks sees no owned LUMAGATEUSB drive')
    if select_gate_block(snapshot()) != target:
        raise RuntimeError('gate USB changed before the authorization request')
    path, device = target
    block = Gio.DBusProxy.new_sync(connection, Gio.DBusProxyFlags.NONE, None,
                                  BUS, path, BUS + '.Block', None)
    result, fds = block.call_with_unix_fd_list_sync(
        'OpenForRestore', GLib.Variant('(a{sv})', ({},)),
        Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, 120_000, None, None)
    fd = fds.get(result.unpack()[0])
    try:
        mode = os.fstat(fd).st_mode
        flags = fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE
        if not stat.S_ISBLK(mode) or flags not in (os.O_WRONLY, os.O_RDWR):
            raise RuntimeError('UDisks did not return a writable block-device FD')
        print(f'opened owned {SERIAL} {device} for writing through UDisks (no data written)')
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
PY
  chmod 0644 "$state/udisks.py"
  chmod 0755 "$state"
  ;;
start)
  since=$(date +%s)
  printf '%s\n' "$since" >"$state/$case_name.since"
  as_user_systemctl reset-failed "$(unit "$case_name").service" >/dev/null 2>&1 || true
  case "$case_name" in
    pkexec)
      cmd=(bash -c 'pkexec true; echo "pkexec exit=$?"') ;;
    flatpak-system)
      ref=$(cat "$state/flatpak-ref" 2>/dev/null)
      cmd=(flatpak install --system -y flathub "$ref") ;;
    udisks-open)
      cmd=(python3 "$state/udisks.py") ;;
    *) echo "unknown case $case_name" >&2; exit 2 ;;
  esac
  # Transient service in the person's user manager: polkit finds their
  # graphical session for it, as for an app the Shell started.
  systemd-run --user -M "$user@" --quiet --collect --unit="$(unit "$case_name")" \
    -p StandardOutput=journal -p StandardError=journal "${cmd[@]}" || { echo start-failed; exit 1; }
  wait=90; [ "$case_name" = flatpak-system ] && wait=300
  deadline=$((SECONDS + ${3:-$wait}))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if [ "$(helpers)" -gt 0 ]; then echo prompt; exit 0; fi
    if ! as_user_systemctl is-active --quiet "$(unit "$case_name").service"; then echo done; exit 0; fi
    sleep 1
  done
  echo none
  ;;
pending)
  since=$(cat "$state/$case_name.since")
  sleep 2
  active=no; as_user_systemctl is-active --quiet "$(unit "$case_name").service" && active=yes
  refused=$(journalctl -q --no-pager -o cat --since "@$since" 2>/dev/null | grep -E 'pam_unix\(polkit-1:auth\): authentication failure' | tail -n 1)
  deadline=$((SECONDS + 20))
  while [ "$(helpers)" -eq 0 ] && [ "$SECONDS" -lt "$deadline" ]; do sleep 1; done
  again=$(helpers)
  if [ "$active" = yes ] && [ -n "$refused" ] && [ "$again" -gt 0 ]; then
    emit "admin-$case_name-wrong-password" pass "PAM refused the wrong password ($refused); the request kept waiting and the prompt asked again"
  else
    emit "admin-$case_name-wrong-password" fail "still waiting: $active; PAM refusal logged: ${refused:-none}; prompt asking again: $again"
  fi
  ;;
finish)
  since=$(cat "$state/$case_name.since")
  deadline=$((SECONDS + ${3:-600}))
  while as_user_systemctl is-active --quiet "$(unit "$case_name").service" && [ "$SECONDS" -lt "$deadline" ]; do sleep 2; done
  result=$(as_user_systemctl show -p Result --value "$(unit "$case_name").service" 2>/dev/null)
  status=$(as_user_systemctl show -p ExecMainStatus --value "$(unit "$case_name").service" 2>/dev/null)
  out=$(journalctl -q --no-pager -o cat --since "@$since" --user-unit "$(unit "$case_name").service" _UID="$(id -u "$user")" 2>/dev/null | tail -n 6 | tr '\n' ' ')
  [ -n "$out" ] || out=$(journalctl -q --no-pager -o cat --since "@$since" _SYSTEMD_USER_UNIT="$(unit "$case_name").service" 2>/dev/null | tail -n 6 | tr '\n' ' ')
  denied=$(journalctl -q --no-pager -o cat --since "@$since" _COMM=polkitd 2>/dev/null | grep -Ei 'not authorized|dismissed|Operator of unix-session' | tail -n 2 | tr '\n' ' ')
  ok=0
  case "$case_name" in
    pkexec) grep -q 'pkexec exit=0' <<<"$out" && ok=1 ;;
    flatpak-system) [ "$result" = success ] && [ "$status" = 0 ] && flatpak info --system "$(cat "$state/flatpak-ref")" >/dev/null 2>&1 && ok=1 ;;
    udisks-open) [ "$result" = success ] && [ "$status" = 0 ] && grep -q 'for writing through UDisks' <<<"$out" && ok=1 ;;
  esac
  if [ "$ok" -eq 1 ]; then
    emit "admin-$case_name" pass "allowed: $out ${denied}"
  else
    emit "admin-$case_name" fail "result=$result status=$status output: ${out:-none} polkitd: ${denied:-none}"
  fi
  as_user_systemctl stop "$(unit "$case_name").service" >/dev/null 2>&1 || true
  if [ "$case_name" = flatpak-system ] && [ -s "$state/flatpak-ref" ]; then
    flatpak uninstall --system -y --noninteractive "$(cat "$state/flatpak-ref")" >/dev/null 2>&1 || true
  fi
  ;;
flatpak-ref)
  # A tiny standalone Flathub runtime with no dependencies and no extra data
  # (the Adwaita-dark GTK 3 theme, a few kilobytes): the same
  # flatpak-system-helper deploy and polkit check as Depot's app installs.
  ref=$(timeout 180 flatpak remote-ls --system flathub --runtime --columns=ref 2>/dev/null | grep -E '^runtime/org\.gtk\.Gtk3theme\.Adwaita-dark/x86_64/' | sort -V | tail -n 1)
  [ -n "$ref" ] || { echo none; exit 1; }
  printf '%s\n' "$ref" >"$state/flatpak-ref"
  echo "$ref"
  ;;
*)
  echo "usage: admin-prompt.sh setup|start CASE|pending CASE|finish CASE|flatpak-ref" >&2
  exit 2
  ;;
esac
