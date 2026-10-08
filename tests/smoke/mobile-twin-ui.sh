#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

failures=0
check() {
  description=$1
  shift
  if "$@"; then
    printf 'PASS  %s\n' "$description"
  else
    printf 'FAIL  %s\n' "$description"
    failures=$((failures + 1))
  fi
}

check 'Phosh package' rpm -q phosh
check 'Phoc package' rpm -q phoc
check 'Stevia package' rpm -q stevia
check 'Phosh session descriptor' test -f /usr/share/wayland-sessions/phosh.desktop
check 'GDM virtual seat' systemctl is-active --quiet gdm.service
check 'Phosh shell process' pgrep -x phosh
check 'Phoc compositor process' pgrep -x phoc
check 'Twin display control' rpm -q wlr-randr
check 'Luma graphical session' bash -c \
  'loginctl list-sessions --no-legend | grep -Eq "[[:space:]]luma[[:space:]]"'
check 'Wayland session type' bash -c \
  'for session in $(loginctl list-sessions --no-legend | sed -n "/[[:space:]]luma[[:space:]]/s/^[[:space:]]*\([^[:space:]]*\).*/\1/p"); do [ "$(loginctl show-session "$session" -p Type --value)" = wayland ] && exit 0; done; exit 1'
check 'FP6 portrait DRM mode active' bash -c \
  'for socket in /run/user/1000/wayland-*; do [ -S "$socket" ] || continue; display=${socket##*/}; env XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY="$display" wlr-randr 2>/dev/null | grep -Eq "1116x2484.*current" && exit 0; done; exit 1'

if [ "$failures" -ne 0 ]; then
  printf '\nMobile twin UI smoke test: FAIL (%s checks)\n' "$failures"
  exit 1
fi

printf '\nMobile twin UI smoke test: PASS\n'
