#!/bin/sh
# SPDX-License-Identifier: Apache-2.0

# Bound the first physical Luma Shell trial. If the exact luma-owned
# gnome-shell process never appears or exits during the stability window,
# re-arm only greetd's already-proven Phosh initial session. This script never
# reboots, flashes, changes a slot, accesses a partition, or edits config.

set -eu

startup_timeout=${LUMA_SHELL_STARTUP_TIMEOUT:-120}
stability_window=${LUMA_SHELL_STABILITY_WINDOW:-600}
poll_interval=${LUMA_SHELL_POLL_INTERVAL:-2}
greetd_config=/etc/greetd/config.toml
run_marker=/run/greetd.run

if [ "$(id -u)" -ne 0 ]; then
  printf 'luma-shell-watchdog: must run as root\n' >&2
  exit 1
fi

case "$startup_timeout:$stability_window:$poll_interval" in
  *[!0-9:]*|'')
    printf 'luma-shell-watchdog: time values must be positive integers\n' >&2
    exit 1
    ;;
esac

for value in "$startup_timeout" "$stability_window" "$poll_interval"; do
  if [ "$value" -le 0 ]; then
    printf 'luma-shell-watchdog: time values must be positive integers\n' >&2
    exit 1
  fi
done

if ! grep -Fq 'command = "phosh-session"' "$greetd_config" ||
   ! grep -Fq 'user = "luma"' "$greetd_config"; then
  printf 'luma-shell-watchdog: proven Phosh initial session is not configured\n' >&2
  exit 1
fi

find_luma_shell() {
  for pid in $(pgrep -u luma -x gnome-shell 2>/dev/null || true); do
    if [ "$(readlink "/proc/$pid/exe" 2>/dev/null || true)" = \
         /usr/bin/gnome-shell ]; then
      printf '%s\n' "$pid"
      return 0
    fi
  done
  return 1
}

recover_phosh() {
  printf 'luma-shell-watchdog: restoring proven Phosh initial session\n' >&2
  systemctl stop greetd.service

  if [ -e "$run_marker" ]; then
    if [ -L "$run_marker" ] || [ ! -f "$run_marker" ] ||
       [ "$(stat -c %U "$run_marker")" != root ]; then
      printf 'luma-shell-watchdog: refusing unexpected %s identity\n' \
        "$run_marker" >&2
      systemctl start greetd.service
      exit 1
    fi
    unlink "$run_marker"
  fi

  systemctl start greetd.service
}

start_deadline=$(( $(date +%s) + startup_timeout ))
shell_pid=
while [ "$(date +%s)" -lt "$start_deadline" ]; do
  shell_pid=$(find_luma_shell || true)
  [ -n "$shell_pid" ] && break
  sleep "$poll_interval"
done

if [ -z "$shell_pid" ]; then
  printf 'luma-shell-watchdog: Luma Shell did not start in %ss\n' \
    "$startup_timeout" >&2
  recover_phosh
  exit 1
fi

printf 'luma-shell-watchdog: observing gnome-shell PID %s for %ss\n' \
  "$shell_pid" "$stability_window"
stable_deadline=$(( $(date +%s) + stability_window ))
while [ "$(date +%s)" -lt "$stable_deadline" ]; do
  current_pid=$(find_luma_shell || true)
  if [ "$current_pid" != "$shell_pid" ]; then
    printf 'luma-shell-watchdog: Luma Shell exited during trial\n' >&2
    recover_phosh
    exit 1
  fi
  sleep "$poll_interval"
done

printf 'luma-shell-watchdog: stability window passed\n'
