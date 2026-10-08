#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-twin/inputs.env"

build_dir=${LUMA_TWIN_BUILD_DIR:-$repo_root/build/mobile/fp6-twin}
ssh_key="$build_dir/luma-fp6-twin-ed25519"
ssh_port=${LUMA_TWIN_SSH_PORT_OVERRIDE:-$LUMA_TWIN_SSH_PORT}
report_dir="$build_dir/tests/$(date -u +%Y%m%dT%H%M%SZ)"
report="$report_dir/mobile-twin-smoke.txt"
timeout_seconds=${LUMA_TWIN_BOOT_TIMEOUT:-900}
deadline=$((SECONDS + timeout_seconds))

[ -f "$ssh_key" ] || {
  printf 'error: missing twin SSH key; run prepare-fp6-twin.sh first\n' >&2
  exit 1
}
mkdir -p "$report_dir"

ssh_options=(
  -i "$ssh_key"
  -p "$ssh_port"
  -o BatchMode=yes
  -o ConnectTimeout=5
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
)

printf 'Waiting up to %s seconds for the clean twin to finish first boot...\n' "$timeout_seconds"
while [ "$SECONDS" -lt "$deadline" ]; do
  if ssh "${ssh_options[@]}" luma@127.0.0.1 \
    'test -f /var/lib/cloud/instance/boot-finished' >/dev/null 2>&1; then
    break
  fi
  sleep 3
done

if ! ssh "${ssh_options[@]}" luma@127.0.0.1 \
  'test -f /var/lib/cloud/instance/boot-finished' >/dev/null 2>&1; then
  printf 'error: twin did not finish first boot within %s seconds\n' "$timeout_seconds" >&2
  exit 1
fi

ssh "${ssh_options[@]}" luma@127.0.0.1 'bash -s' \
  <"$repo_root/tests/smoke/mobile-twin.sh" | tee "$report"
grep -qx 'Mobile twin smoke test: PASS' "$report"

printf 'Mobile twin smoke evidence: %s\n' "$report"
