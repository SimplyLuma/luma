#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
vm_name=${LUMA_VM_NAME:-luma-design}
guest_user=${LUMA_GUEST_USER:-luma}
guest_key=${LUMA_GUEST_KEY:-"$repo_root/build/provisioning/luma-m0-guest"}
timeout_seconds=${LUMA_GUEST_TIMEOUT:-300}

if [ ! -f "$guest_key" ]; then
  printf 'error: guest key is missing; run scripts/prepare-test-provisioning.sh\n' >&2
  exit 1
fi

deadline=$((SECONDS + timeout_seconds))
guest_ip=
while [ "$SECONDS" -lt "$deadline" ]; do
  guest_ip=$(sudo virsh domifaddr "$vm_name" --source lease 2>/dev/null |
    awk '/ipv4/ {sub("/.*", "", $4); print $4; exit}')
  if [ -n "$guest_ip" ] && ssh \
    -n \
    -i "$guest_key" \
    -o BatchMode=yes \
    -o ConnectTimeout=5 \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    "$guest_user@$guest_ip" true 2>/dev/null; then
    break
  fi
  sleep 5
done

if [ -z "$guest_ip" ] || [ "$SECONDS" -ge "$deadline" ]; then
  printf 'error: guest SSH did not become ready within %s seconds\n' "$timeout_seconds" >&2
  exit 1
fi

printf 'guest=%s ip=%s\n' "$vm_name" "$guest_ip" >&2
exec ssh \
  -i "$guest_key" \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  "$guest_user@$guest_ip" "$@"
