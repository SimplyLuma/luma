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
report="$report_dir/mobile-twin-ui-smoke.txt"
deadline=$((SECONDS + ${LUMA_TWIN_UI_TIMEOUT:-300}))

ssh_options=(
  -i "$ssh_key"
  -p "$ssh_port"
  -o BatchMode=yes
  -o ConnectTimeout=5
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
)

mkdir -p "$report_dir"
while [ "$SECONDS" -lt "$deadline" ]; do
  if ssh "${ssh_options[@]}" luma@127.0.0.1 \
    'systemctl is-active --quiet gdm.service && pgrep -x phosh >/dev/null && pgrep -x phoc >/dev/null' \
    >/dev/null 2>&1; then
    break
  fi
  sleep 3
done

ssh "${ssh_options[@]}" luma@127.0.0.1 'bash -s' \
  <"$repo_root/tests/smoke/mobile-twin-ui.sh" | tee "$report"
grep -qx 'Mobile twin UI smoke test: PASS' "$report"
printf 'Mobile twin UI smoke evidence: %s\n' "$report"
