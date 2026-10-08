#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Evaluate evidence already collected by other non-mutating tools. This script
# never communicates with a phone and cannot authorize unlocking or install.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
build_dir=${LUMA_FP6_PHYSICAL_BUILD_DIR:-$repo_root/build/mobile/fp6-physical}
inventory_dir=${1:-}

if [ -z "$inventory_dir" ]; then
  inventory_dir=$(find "$build_dir/inventory" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort | tail -n 1)
fi
[ -n "$inventory_dir" ] || {
  printf 'NOT READY: no FP6 inventory report exists\n' >&2
  exit 1
}

summary="$inventory_dir/summary.env"
properties="$inventory_dir/android-properties.txt"
recovery="$build_dir/recovery-manifest.txt"
control="$build_dir/control-manifest.txt"

for required in "$summary" "$properties" "$recovery" "$control"; do
  [ -f "$required" ] || {
    printf 'NOT READY: required evidence is missing: %s\n' "$required" >&2
    exit 1
  }
done

read_value() {
  key=$1
  file=$2
  sed -n "s/^${key}=//p" "$file" | tail -n 1
}

[ "$(read_value IDENTITY "$summary")" = fp6-confirmed ] || {
  printf 'NOT READY: inventory identity is not confirmed as FP6\n' >&2
  exit 1
}
[ "$(read_value TRANSPORT "$summary")" = adb ] || {
  printf 'NOT READY: stock-Android ADB inventory is required before destructive work\n' >&2
  exit 1
}
[ "$(read_value CHECKSUM_VERIFIED "$recovery")" = true ] || {
  printf 'NOT READY: stock recovery checksum is not verified\n' >&2
  exit 1
}
[ "$(read_value CHECKSUMS_VERIFIED "$control")" = true ] || {
  printf 'NOT READY: control artifact checksums are not verified\n' >&2
  exit 1
}
[ "$(read_value INSTALL_AUTHORIZED "$control")" = false ] || {
  printf 'NOT READY: source control manifest unexpectedly authorizes installation\n' >&2
  exit 1
}

device_patch=$(read_value ro.build.version.security_patch "$properties")
recovery_patch=$(read_value SECURITY_PATCH "$recovery")
[ -n "$device_patch" ] && [ -n "$recovery_patch" ] || {
  printf 'NOT READY: security-patch evidence is incomplete\n' >&2
  exit 1
}

if [[ "$recovery_patch" < "$device_patch" ]]; then
  printf 'NOT READY: recovery patch %s is older than device patch %s\n' \
    "$recovery_patch" "$device_patch" >&2
  printf 'Do not unlock or relock; obtain non-rollback recovery material first.\n' >&2
  exit 1
fi

if [[ "$recovery_patch" > "$device_patch" ]]; then
  device_update_required=true
else
  device_update_required=false
fi

flash_locked=$(read_value ro.boot.flash.locked "$properties")
vbmeta_state=$(read_value ro.boot.vbmeta.device_state "$properties")
if [ "$flash_locked" = 0 ] && [ "$vbmeta_state" = unlocked ]; then
  observed_bootloader_state=unlocked
elif [ "$flash_locked" = 1 ] && [ "$vbmeta_state" = locked ]; then
  observed_bootloader_state=locked
else
  observed_bootloader_state=ambiguous
fi

battery_level=$(sed -nE 's/^[[:space:]]*level:[[:space:]]*([0-9]+).*$/\1/p' "$inventory_dir/battery.txt" | tail -n 1)
[ -n "$battery_level" ] && [ "$battery_level" -ge 80 ] || {
  printf 'NOT READY: battery must be at least 80%%; observed %s%%\n' "${battery_level:-unknown}" >&2
  exit 1
}

printf 'FP6 AUTOMATED EVIDENCE READY FOR P3 REVIEW\n'
printf '  device security patch:   %s\n' "$device_patch"
printf '  recovery security patch: %s\n' "$recovery_patch"
printf '  battery:                  %s%%\n' "$battery_level"
printf '  recovery:                 checksum verified\n'
printf '  postmarketOS control:     checksums verified\n'
printf 'OBSERVED_BOOTLOADER_STATE=%s\n' "$observed_bootloader_state"
printf 'DEVICE_UPDATE_REQUIRED=%s\n' "$device_update_required"
if [ "$device_update_required" = true ]; then
  printf 'Update stock Android and repeat P0 before Linux testing.\n'
fi
printf 'UNLOCK_AUTHORIZED=false\n'
printf 'INSTALL_AUTHORIZED=false\n'
printf 'A separate owner decision, complete backup, and destructive runbook review remain required.\n'
