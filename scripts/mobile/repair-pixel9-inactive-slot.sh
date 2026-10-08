#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Repair the inactive stock slot with the exact signed full OTA selected for
# the currently booted Pixel 9 build. This is intentionally a narrow operation:
# no bootloader unlock, fastboot flash/update, wipe, or manual slot change.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

adb_bin=${LUMA_ADB:-adb}
authorized=${LUMA_PIXEL9_INACTIVE_SLOT_REPAIR_AUTHORIZED:-0}
ota_path=${LUMA_PIXEL9_OTA_PATH:-$repo_root/build/cache/recovery/pixel9/$PIXEL9_OBSERVED_BUILD_ID/$PIXEL9_FULL_OTA_FILENAME}
timestamp=${LUMA_PIXEL9_REPAIR_TIMESTAMP:-$(date -u +%Y%m%dT%H%M%SZ)}
evidence_root=${LUMA_PIXEL9_REPAIR_EVIDENCE_DIR:-$repo_root/build/mobile/pixel9-physical/inactive-slot-repair/$timestamp}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

verify_sha256() {
  expected=$1
  path=$2
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s  %s\n' "$expected" "$path" | sha256sum -c -
  elif command -v shasum >/dev/null 2>&1; then
    printf '%s  %s\n' "$expected" "$path" | shasum -a 256 -c -
  else
    die 'sha256sum or shasum is required'
  fi
}

[ "$authorized" = 1 ] || \
  die 'inactive-slot repair is not authorized; set LUMA_PIXEL9_INACTIVE_SLOT_REPAIR_AUTHORIZED=1 for the explicitly approved invocation only'
[ "$PIXEL9_INACTIVE_SLOT_REPAIR_COMPLETED" = false ] || \
  die 'inactive-slot repair is already complete; refusing to consume another authorization'
command -v "$adb_bin" >/dev/null 2>&1 || die 'adb is not installed'
[ -f "$ota_path" ] || die "pinned full OTA is missing: $ota_path"
[ "$(wc -c <"$ota_path" | tr -d ' ')" -eq "$PIXEL9_FULL_OTA_BYTES" ] || \
  die 'full OTA byte size does not match the pin'
verify_sha256 "$PIXEL9_FULL_OTA_SHA256" "$ota_path"

listing=$($adb_bin devices 2>/dev/null | sed '1d' | sed '/^$/d')
serials=$(printf '%s\n' "$listing" | awk '$2 == "device" {print $1}')
count=$(printf '%s\n' "$serials" | sed '/^$/d' | wc -l | tr -d ' ')
[ "$count" -eq 1 ] || die "expected exactly one authorized Android device; found $count"
serial=$serials

adb_shell() {
  "$adb_bin" -s "$serial" shell "$@"
}

[ "$(adb_shell getprop ro.product.device | tr -d '\r')" = tokay ] || \
  die 'connected device is not tokay'
[ "$(adb_shell getprop ro.build.id | tr -d '\r')" = "$PIXEL9_OBSERVED_BUILD_ID" ] || \
  die 'connected build does not match the pinned OTA'
[ "$(adb_shell getprop ro.boot.slot_suffix | tr -d '\r')" = _b ] || \
  die 'repair precondition requires known-good slot B'
[ "$(adb_shell getprop ro.boot.verifiedbootstate | tr -d '\r')" = green ] || \
  die 'verified boot is not green'
[ "$(adb_shell getprop ro.boot.flash.locked | tr -d '\r')" = 1 ] || \
  die 'bootloader is not locked'
[ "$(adb_shell getprop ro.boot.bootloader | tr -d '\r')" = "$PIXEL9_OBSERVED_BOOTLOADER" ] || \
  die 'bootloader does not match the pinned recovery boundary'

battery=$(adb_shell dumpsys battery | tr -d '\r')
present=$(printf '%s\n' "$battery" | awk -F': ' '/^  present:/ {print $2; exit}')
level=$(printf '%s\n' "$battery" | awk -F': ' '/^  level:/ {print $2; exit}')
[ "$present" = true ] || die 'battery is not reported present'
[ -n "$level" ] && [ "$level" -ge 80 ] || die 'battery must be at least 80 percent'

mkdir -p "$evidence_root"
control="$evidence_root/control.env"
{
  printf 'LUMA_PIXEL9_INACTIVE_SLOT_REPAIR_VERSION=1\n'
  printf 'DEVICE=tokay\n'
  printf 'PRE_REPAIR_BUILD=%s\n' "$PIXEL9_OBSERVED_BUILD_ID"
  printf 'PRE_REPAIR_SLOT=_b\n'
  printf 'PRE_REPAIR_VERIFIED_BOOT=green\n'
  printf 'PRE_REPAIR_BOOTLOADER_LOCKED=true\n'
  printf 'PRE_REPAIR_BATTERY_LEVEL=%s\n' "$level"
  printf 'OTA_FILENAME=%s\n' "$PIXEL9_FULL_OTA_FILENAME"
  printf 'OTA_BYTES=%s\n' "$PIXEL9_FULL_OTA_BYTES"
  printf 'OTA_SHA256=%s\n' "$PIXEL9_FULL_OTA_SHA256"
  printf 'OWNER_AUTHORIZED=true\n'
  printf 'AUTHORIZED_OPERATION=same-build-signed-full-ota-sideload\n'
  printf 'UNLOCK_AUTHORIZED=false\n'
  printf 'FASTBOOT_FLASH_AUTHORIZED=false\n'
  printf 'WIPE_AUTHORIZED=false\n'
  printf 'MANUAL_SLOT_CHANGE_AUTHORIZED=false\n'
  printf 'STATUS=authorized-not-started\n'
} >"$control"

printf 'STATUS=entering-stock-recovery\n' >>"$control"
"$adb_bin" -s "$serial" reboot sideload-auto-reboot

sideload_visible=false
attempt=0
while [ "$attempt" -lt 120 ]; do
  attempt=$((attempt + 1))
  state=$("$adb_bin" -s "$serial" get-state 2>/dev/null || true)
  if [ "$state" = sideload ]; then
    sideload_visible=true
    break
  fi
  sleep 1
done
[ "$sideload_visible" = true ] || die 'stock recovery did not enter ADB sideload mode within 120 seconds'

printf 'STATUS=sideload-started\n' >>"$control"
"$adb_bin" -s "$serial" sideload "$ota_path" 2>&1 | tee "$evidence_root/adb-sideload.txt"
printf 'STATUS=sideload-command-complete\n' >>"$control"

android_visible=false
attempt=0
while [ "$attempt" -lt 240 ]; do
  attempt=$((attempt + 1))
  state=$("$adb_bin" -s "$serial" get-state 2>/dev/null || true)
  if [ "$state" = device ]; then
    boot_complete=$(adb_shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)
    if [ "$boot_complete" = 1 ]; then
      android_visible=true
      break
    fi
  fi
  sleep 1
done
[ "$android_visible" = true ] || die 'Android did not complete boot within 240 seconds after sideload'

post_build=$(adb_shell getprop ro.build.id | tr -d '\r')
post_slot=$(adb_shell getprop ro.boot.slot_suffix | tr -d '\r')
post_verified=$(adb_shell getprop ro.boot.verifiedbootstate | tr -d '\r')
post_locked=$(adb_shell getprop ro.boot.flash.locked | tr -d '\r')
[ "$post_build" = "$PIXEL9_OBSERVED_BUILD_ID" ] || die 'post-repair build changed unexpectedly'
[ "$post_slot" = _a ] || die "stock OTA did not boot repaired slot A; observed $post_slot"
[ "$post_verified" = green ] || die 'post-repair verified boot is not green'
[ "$post_locked" = 1 ] || die 'post-repair bootloader is not locked'

{
  printf 'POST_REPAIR_BUILD=%s\n' "$post_build"
  printf 'POST_REPAIR_SLOT=%s\n' "$post_slot"
  printf 'POST_REPAIR_VERIFIED_BOOT=%s\n' "$post_verified"
  printf 'POST_REPAIR_BOOTLOADER_LOCKED=true\n'
  printf 'STATUS=android-boot-verified-fastboot-audit-required\n'
} >>"$control"

printf 'Pixel 9 signed full-OTA repair completed; read-only fastboot audit remains: %s\n' "$evidence_root"
