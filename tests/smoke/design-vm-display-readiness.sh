#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
helper="$repo_root/ops/design-vm/luma-wait-for-virtual-display"
unit="$repo_root/ops/design-vm/luma-virtual-display-ready.service"
installer="$repo_root/scripts/vm/configure-design-display-readiness.sh"
fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT

test -x "$helper"
test -x "$installer"
grep -Fq 'Before=gdm.service display-manager.service' "$unit"
grep -Fq 'WantedBy=graphical.target' "$unit"
grep -Fq 'gdm.service.wants/luma-virtual-display-ready.service' "$installer"

connector="$fixture/card1-Virtual-1"
mkdir -p "$connector"
printf 'connected\n' >"$connector/status"
printf '1920x1080\n' >"$connector/modes"
LUMA_DRM_ROOT="$fixture" \
LUMA_DISPLAY_READY_TIMEOUT=1 \
LUMA_DISPLAY_READY_STABLE_CHECKS=2 \
LUMA_DISPLAY_READY_INTERVAL=0.01 \
  "$helper"

printf 'disconnected\n' >"$connector/status"
LUMA_DRM_ROOT="$fixture" \
LUMA_DISPLAY_READY_TIMEOUT=1 \
LUMA_DISPLAY_READY_STABLE_CHECKS=2 \
LUMA_DISPLAY_READY_INTERVAL=0.01 \
  "$helper" 2>"$fixture/timeout.log"
grep -Fq 'continuing headless' "$fixture/timeout.log"

if LUMA_DRM_ROOT="$fixture" LUMA_DISPLAY_READY_TIMEOUT=invalid "$helper"; then
  printf 'error: invalid display timeout was accepted\n' >&2
  exit 1
fi

printf 'Design VM display readiness contract: PASS\n'
