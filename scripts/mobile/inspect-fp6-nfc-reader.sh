#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Read-only enumeration check for the RAM-booted FP6 NFC reader candidate.
# Actual tag polling is a separate, user-observed physical acceptance step.

set -euo pipefail

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d ' ' -f 1; }
[ "$(id -u)" -eq 0 ] || die 'run as root on the FP6'
[ "$(uname -r)" = 7.1.2 ] || die 'kernel release differs'
[ "$(tr -d '\000' </proc/device-tree/model)" = 'The Fairphone (Gen. 6)' ] || die 'device identity differs'
for tool in cat cut find grep journalctl lsmod modinfo sha256sum sort systemctl tail tr uname wc; do
  command -v "$tool" >/dev/null 2>&1 || die "missing tool: $tool"
done

dt_compatible=/proc/device-tree/soc@0/geniqup@ac0000/i2c@a84000/nfc@27/compatible
[ -r "$dt_compatible" ] || die 'NFC device-tree node is absent'
[ "$(tr -d '\000' <"$dt_compatible")" = samsung,s3nrn4v ] || die 'NFC compatible differs'

modules=(nfc nci s3fwrn5 s3fwrn5_i2c)
hashes=(
  8beb3b1ee22a7af643cd763e768a21a0f53ee437c94244eceeb0ee33977e5ee0
  e97b561491ed1c4b9963a739cac0553e88b218346b42673e6ea85563b23b14e3
  ba972f65e232fca5a12a43a21ba9496b9e417af4156743aa2ea84af457d6551d
  df905d9ad4240b708a11c029af98344fa7d7ea23284ef0eb21f1655c6e67746a
)
printf 'LUMA_FP6_NFC_INSPECTION_VERSION=1\n'
printf 'BOOT_ID=%s\n' "$(cat /proc/sys/kernel/random/boot_id)"
printf 'MODEL=%s\n' "$(tr -d '\000' </proc/device-tree/model)"
printf 'KERNEL=%s\n' "$(uname -r)"
printf 'DT_COMPATIBLE=%s\n' "$(tr -d '\000' <"$dt_compatible")"
for index in "${!modules[@]}"; do
  path=$(modinfo -n "${modules[$index]}")
  [ -f "$path" ] || die "selected module is missing: ${modules[$index]}"
  [ "$(hash "$path")" = "${hashes[$index]}" ] || die "selected module differs: ${modules[$index]}"
  lsmod | grep -q "^${modules[$index]} " || die "module is not loaded: ${modules[$index]}"
  printf 'MODULE_%s=%s\n' "$(printf '%s' "${modules[$index]}" | tr '[:lower:]' '[:upper:]')" "$path"
done

class_count=$(find /sys/class/nfc -mindepth 1 -maxdepth 1 -type l 2>/dev/null | wc -l | tr -d ' ')
[ "$class_count" -ge 1 ] || die 'kernel NFC class has no controller'
printf 'NFC_CLASS_COUNT=%s\n' "$class_count"
find /sys/class/nfc -mindepth 1 -maxdepth 1 -type l -print | sort
printf 'FAILED_UNITS=%s\n' "$(systemctl --failed --no-legend --no-pager | grep -c . || true)"
printf '%s\n' NFC_KERNEL_LOG_BEGIN
journalctl -k -b --no-pager | grep -Ei 'nfc|nci|s3fwrn5|s3nrn4v' | tail -120 || true
printf '%s\n' NFC_KERNEL_LOG_END
printf 'TAG_POLLED=false\nMUTATING_OPERATIONS=none\n'
