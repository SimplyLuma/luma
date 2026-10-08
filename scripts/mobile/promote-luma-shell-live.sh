#!/bin/sh
# SPDX-License-Identifier: Apache-2.0

# Promote an already-installed, validated Luma Shell launcher to greetd's live
# initial session. Intended for the physical FP6 bring-up image. If GNOME Shell
# does not take the display promptly, restore the exact preceding config and
# proven rescue session without rebooting.

set -eu

candidate=${1:-/tmp/luma-greetd-config.toml}
config=/etc/greetd/config.toml
backup=/run/luma-greetd-rescue-config.toml
marker=/run/greetd.run

if [ "$(id -u)" -ne 0 ]; then
  printf 'promotion: must run as root\n' >&2
  exit 1
fi

test -f "$candidate"
test ! -L "$candidate"
test -x /usr/local/bin/luma-shell-session
grep -Fqx 'command = "/usr/local/bin/luma-shell-session"' "$candidate"
grep -Fqx 'command = "agreety --cmd /usr/local/bin/luma-shell-session"' "$candidate"
grep -Fqx 'command = "phosh-session"' "$config"

cp -a "$config" "$backup"
install -o root -g root -m 0644 "$candidate" "$config"

rearm() {
  if [ -e "$marker" ]; then
    test ! -L "$marker"
    test -f "$marker"
    test "$(stat -c %U "$marker")" = root
    unlink "$marker"
  fi
}

rollback() {
  printf 'promotion: Luma Shell did not acquire the display; restoring rescue session\n' >&2
  systemctl stop greetd.service
  install -o root -g root -m 0644 "$backup" "$config"
  rearm
  systemctl start greetd.service
  exit 1
}

systemctl stop greetd.service
rearm
systemctl start greetd.service

deadline=$(( $(date +%s) + 60 ))
shell_pid=
while [ "$(date +%s)" -lt "$deadline" ]; do
  for pid in $(pgrep -u luma -x gnome-shell 2>/dev/null || true); do
    if [ "$(readlink "/proc/$pid/exe" 2>/dev/null || true)" = /usr/bin/gnome-shell ]; then
      shell_pid=$pid
      break
    fi
  done
  [ -n "$shell_pid" ] && break
  sleep 2
done

[ -n "$shell_pid" ] || rollback
if pgrep -x phoc >/dev/null 2>&1 || pgrep -x phosh >/dev/null 2>&1; then
  rollback
fi

printf 'LUMA_SHELL_PID=%s\n' "$shell_pid"
printf 'LUMA_SESSION_PROMOTION=accepted\n'
