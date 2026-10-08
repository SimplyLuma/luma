#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Read-only Fairphone 6 inventory. This script intentionally contains no
# reboot, unlock, lock, erase, wipe, boot, or flash operation.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-physical/inputs.env"

adb_bin=${LUMA_ADB:-adb}
fastboot_bin=${LUMA_FASTBOOT:-fastboot}
requested_transport=${LUMA_FP6_TRANSPORT:-auto}
timestamp=${LUMA_FP6_INVENTORY_TIMESTAMP:-$(date -u +%Y%m%dT%H%M%SZ)}
output_root=${LUMA_FP6_INVENTORY_DIR:-$repo_root/build/mobile/fp6-physical/inventory/$timestamp}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

redact() {
  sed -E \
    -e 's/((serialno|serial-number|ro\.serialno|androidboot\.serialno)[=:][[:space:]]*)[^[:space:]]+/\1REDACTED/Ig' \
    -e 's/^([[:xdigit:]]{8,})[[:space:]]+(device|fastboot)$/REDACTED \2/'
}

select_single_serial() {
  mode=$1
  listing=$2
  serials=$(printf '%s\n' "$listing" | awk -v expected="$mode" '$2 == expected {print $1}')
  count=$(printf '%s\n' "$serials" | sed '/^$/d' | wc -l | tr -d ' ')
  [ "$count" -eq 1 ] || die "expected exactly one authorized $mode device; found $count"
  printf '%s\n' "$serials"
}

write_summary() {
  transport=$1
  manufacturer=$2
  model=$3
  device=$4
  {
    printf 'LUMA_FP6_INVENTORY_VERSION=1\n'
    printf 'TRANSPORT=%s\n' "$transport"
    printf 'MANUFACTURER=%s\n' "$manufacturer"
    printf 'MODEL=%s\n' "$model"
    printf 'DEVICE=%s\n' "$device"
    printf 'IDENTITY=fp6-confirmed\n'
    printf 'MUTATING_OPERATIONS=none\n'
  } >"$output_root/summary.env"
}

inventory_adb() {
  command -v "$adb_bin" >/dev/null 2>&1 || die "adb is not installed"
  listing=$("$adb_bin" devices 2>/dev/null | sed '1d' | sed '/^$/d')
  serial=$(select_single_serial device "$listing")

  adb_shell() {
    "$adb_bin" -s "$serial" shell "$@"
  }

  manufacturer=$(adb_shell getprop ro.product.manufacturer | tr -d '\r')
  model=$(adb_shell getprop ro.product.model | tr -d '\r')
  device=$(adb_shell getprop ro.product.device | tr -d '\r')

  printf '%s\n' "$manufacturer" | grep -Fqi "$FP6_EXPECTED_MANUFACTURER" || \
    die "connected ADB device is not manufactured by Fairphone"
  printf '%s\n' "$model" | grep -Eq "$FP6_EXPECTED_MODEL_REGEX" || \
    die "connected ADB model is not an accepted FP6 identity: $model"
  printf '%s\n' "$device" | grep -Eqi "$FP6_EXPECTED_DEVICE_REGEX" || \
    die "connected ADB product is not an accepted FP6 identity: $device"

  mkdir -p "$output_root"
  write_summary adb "$manufacturer" "$model" "$device"

  {
    for property in \
      ro.product.manufacturer ro.product.model ro.product.device \
      ro.product.board ro.board.platform ro.hardware ro.soc.manufacturer \
      ro.soc.model ro.build.fingerprint ro.build.id ro.build.version.release \
      ro.build.version.security_patch ro.build.version.incremental \
      ro.boot.slot_suffix ro.boot.slot ro.boot.verifiedbootstate \
      ro.boot.flash.locked ro.boot.vbmeta.device_state ro.boot.bootloader \
      ro.boot.baseband; do
      value=$(adb_shell getprop "$property" | tr -d '\r')
      printf '%s=%s\n' "$property" "$value"
    done
  } | redact >"$output_root/android-properties.txt"

  adb_shell uname -a | redact >"$output_root/uname.txt"
  if partitions=$(adb_shell cat /proc/partitions 2>&1); then
    printf '%s\n' "$partitions" | redact >"$output_root/partitions.txt"
  else
    printf 'UNAVAILABLE: stock Android denied read access to /proc/partitions\n' \
      >"$output_root/partitions.txt"
  fi
  adb_shell dumpsys battery | redact >"$output_root/battery.txt"
  adb_shell wm size | redact >"$output_root/display-size.txt"
  adb_shell wm density | redact >"$output_root/display-density.txt"

  printf 'FP6 read-only ADB inventory complete: %s\n' "$output_root"
}

fastboot_getvar() {
  serial=$1
  variable=$2
  output=$("$fastboot_bin" -s "$serial" getvar "$variable" 2>&1 || true)
  printf '%s\n' "$output" | sed -nE "s/^${variable}:[[:space:]]*//p" | tail -n 1
}

inventory_fastboot() {
  command -v "$fastboot_bin" >/dev/null 2>&1 || die "fastboot is not installed"
  listing=$("$fastboot_bin" devices 2>/dev/null || true)
  serial=$(select_single_serial fastboot "$listing")
  product=$(fastboot_getvar "$serial" product)
  printf '%s\n' "$product" | grep -Eqi "$FP6_EXPECTED_DEVICE_REGEX" || \
    die "connected fastboot product is not an accepted FP6 identity: $product"

  mkdir -p "$output_root"
  write_summary fastboot "$FP6_EXPECTED_MANUFACTURER" FP6 "$product"
  {
    for variable in product current-slot unlocked secure version-bootloader \
      version-baseband is-userspace max-download-size; do
      printf '%s=%s\n' "$variable" "$(fastboot_getvar "$serial" "$variable")"
    done
  } | redact >"$output_root/fastboot-variables.txt"

  device_info=$("$fastboot_bin" -s "$serial" oem device-info 2>&1 || true)
  printf '%s\n' "$device_info" | redact >"$output_root/fastboot-device-info.txt"

  printf 'FP6 read-only fastboot inventory complete: %s\n' "$output_root"
}

case "$requested_transport" in
  adb)
    inventory_adb
    ;;
  fastboot)
    inventory_fastboot
    ;;
  auto)
    if command -v "$adb_bin" >/dev/null 2>&1 && \
      "$adb_bin" devices 2>/dev/null | awk 'NR > 1 && $2 == "device" {found=1} END {exit !found}'; then
      inventory_adb
    elif command -v "$fastboot_bin" >/dev/null 2>&1 && \
      "$fastboot_bin" devices 2>/dev/null | awk '$2 == "fastboot" {found=1} END {exit !found}'; then
      inventory_fastboot
    else
      die "no single authorized ADB or fastboot device is visible"
    fi
    ;;
  *)
    die "LUMA_FP6_TRANSPORT must be auto, adb, or fastboot"
    ;;
esac
