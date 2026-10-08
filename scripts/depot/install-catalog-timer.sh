#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Install and start luma-depot-catalog.timer on the build host (root).
#
#   install-catalog-timer.sh [--remove]
set -euo pipefail
. "$(dirname -- "$0")/lib.sh"
[ "$(id -u)" = 0 ] || depot_die "run as root"

if [ "${1:-}" = --remove ]; then
  systemctl disable --now luma-depot-catalog.timer 2>/dev/null || true
  rm -f /etc/systemd/system/luma-depot-catalog.{service,timer}
  systemctl daemon-reload
  exit 0
fi

install -d -m 0755 /etc/luma-depot
real_checkout=$(realpath "$depot_repo_root")
{
  printf 'DEPOT_CHECKOUT=%s\n' "$real_checkout"
  printf 'DEPOT_ROOT=%s\n' "$DEPOT_ROOT"
  printf 'DEPOT_KEYS=%s\n' "$DEPOT_KEYS"
  [ -n "${CONTAINERS_STORAGE_CONF:-}" ] && printf 'CONTAINERS_STORAGE_CONF=%s\n' "$CONTAINERS_STORAGE_CONF"
} >/etc/luma-depot/catalog.env
install -m 0644 "$depot_repo_root/scripts/depot/systemd/luma-depot-catalog.service" \
  "$depot_repo_root/scripts/depot/systemd/luma-depot-catalog.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now luma-depot-catalog.timer
systemctl list-timers luma-depot-catalog.timer --no-pager
