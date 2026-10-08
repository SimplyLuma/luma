#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
output_dir=${LUMA_FP6_ROOTFS_OUTPUT:-/home/luma/build/fp6-rootfs}
report=${LUMA_FP6_ROOTFS_REPORT:-$output_dir/rootfs-smoke.txt}

[ -d "$output_dir/rootfs" ] || {
  printf 'error: missing composed rootfs: %s/rootfs\n' "$output_dir" >&2
  exit 1
}

sudo env \
  LUMA_FP6_ROOTFS="$output_dir/rootfs" \
  LUMA_FP6_ROOTFS_OUTPUT="$output_dir" \
  LUMA_FP6_PACKAGES_FILE="$repo_root/config/mobile/packages.txt" \
  LUMA_FP6_UI_PACKAGES_FILE="$repo_root/config/mobile/ui-packages.txt" \
  bash "$repo_root/tests/smoke/mobile-rootfs.sh" | sudo tee "$report"

grep -qx 'Fedora FP6 rootfs smoke test: PASS' "$report"
printf 'Fedora FP6 rootfs smoke evidence: %s\n' "$report"
