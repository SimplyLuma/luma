#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# ADR-030 section 6 health check: luma-updated starts and answers, so this
# deployment can still receive the fix for whatever is wrong with it.
set -u
. /usr/lib/luma-update/luma-greenboot-common.sh
# Up to ten attempts: activation normally answers within seconds, and a broken
# agent fails at once, so a failed trial boot is not held for minutes.
for _ in $(seq 1 10); do
  if timeout 20 busctl --system --timeout=15 get-property org.projectluma.Update1 /org/projectluma/Update1 \
       org.projectluma.Update1 State >/dev/null 2>&1; then
    exit 0
  fi
  systemctl reset-failed luma-updated.service 2>/dev/null || :
  sleep 3
done
systemctl status --no-pager luma-updated.service >&2 || :
luma_check_failed "luma-updated did not answer on the system bus"
