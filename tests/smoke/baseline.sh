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

check 'x86_64 guest architecture' test "$(uname -m)" = x86_64
check 'Fedora guest identity' grep -q '^ID=fedora$' /etc/os-release
check 'Fedora 44 guest version' grep -q '^VERSION_ID="\?44"\?$' /etc/os-release
check 'graphical target is the default' test "$(systemctl get-default)" = graphical.target
check 'display manager is active' systemctl is-active --quiet display-manager.service
check 'NetworkManager is active' systemctl is-active --quiet NetworkManager.service

failed_units=$(systemctl --failed --no-legend --plain 2>/dev/null || true)
if [ -z "$failed_units" ]; then
  printf 'PASS  no failed system units\n'
else
  printf 'FAIL  failed system units detected\n%s\n' "$failed_units"
  failures=$((failures + 1))
fi

if command -v rpm-ostree >/dev/null 2>&1; then
  printf 'INFO  rpm-ostree status follows\n'
  rpm-ostree status || failures=$((failures + 1))
elif command -v bootc >/dev/null 2>&1; then
  printf 'INFO  bootc status follows\n'
  bootc status || failures=$((failures + 1))
else
  printf 'FAIL  neither bootc nor rpm-ostree is available\n'
  failures=$((failures + 1))
fi

if [ "$failures" -ne 0 ]; then
  printf '\nBaseline smoke test: FAIL (%s checks)\n' "$failures"
  exit 1
fi

printf '\nBaseline smoke test: PASS\n'
