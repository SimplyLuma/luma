#!/usr/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Luma health check (ADR-030 section 6): NetworkManager is running and answers
# on the system bus. Connectivity is deliberately not required: a machine that
# boots offline is healthy and must not roll back.
set -euo pipefail
# greenboot 0.16 restarts the computer whenever a required check fails, update
# or not, so this check enforces only on the trial boots of a newly finalized
# deployment (luma-update's helper, docs/os/luma-update.md) and reports without
# failing on every other boot.
. /usr/lib/luma-update/luma-greenboot-common.sh
timeout=${LUMA_HEALTH_NETWORK_TIMEOUT:-120}
deadline=$((SECONDS + timeout))
while [ "$SECONDS" -lt "$deadline" ]; do
  if systemctl is-active --quiet NetworkManager.service &&
     state=$(timeout 15 busctl --system --timeout=10 get-property \
       org.freedesktop.NetworkManager /org/freedesktop/NetworkManager \
       org.freedesktop.NetworkManager State 2>/dev/null); then
    printf 'Luma: NetworkManager is running (state %s)\n' "${state#u }"
    exit 0
  fi
  systemctl is-failed --quiet NetworkManager.service && break
  sleep 2
done
systemctl status NetworkManager.service --no-pager --lines=20 >&2 || true
luma_check_failed 'Luma: NetworkManager is not running or does not answer on D-Bus'
