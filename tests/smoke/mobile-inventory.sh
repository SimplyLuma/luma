#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
mkdir -p "$test_root/bin" "$test_root/adb-output" "$test_root/fastboot-output"

cat >"$test_root/bin/adb" <<'MOCK_ADB'
#!/usr/bin/env bash
set -eu
if [ "${1:-}" = devices ]; then
  printf 'List of devices attached\n0123456789ABCDEF\tdevice\n'
  exit 0
fi
[ "${1:-}" = -s ]
shift 2
[ "${1:-}" = shell ]
shift
case "$*" in
  'getprop ro.product.manufacturer') printf 'Fairphone\n' ;;
  'getprop ro.product.model') printf 'The Fairphone (Gen. 6)\n' ;;
  'getprop ro.product.device') printf 'FP6\n' ;;
  'getprop ro.build.version.security_patch') printf '2026-07-05\n' ;;
  'getprop ro.boot.flash.locked') printf '0\n' ;;
  'getprop ro.boot.vbmeta.device_state') printf 'unlocked\n' ;;
  'getprop '*) printf 'fixture-value\n' ;;
  'uname -a') printf 'Linux milos 6.18 fixture\n' ;;
  'cat /proc/partitions')
    if [ "${LUMA_MOCK_PARTITIONS_DENIED:-false}" = true ]; then
      printf 'cat: /proc/partitions: Permission denied\n' >&2
      exit 1
    fi
    printf 'major minor blocks name\n 259 0 1024 sda\n'
    ;;
  'dumpsys battery') printf 'level: 80\nstatus: 2\n' ;;
  'wm size') printf 'Physical size: 1116x2484\n' ;;
  'wm density') printf 'Physical density: 431\n' ;;
  *) exit 2 ;;
esac
MOCK_ADB

cat >"$test_root/bin/fastboot" <<'MOCK_FASTBOOT'
#!/usr/bin/env bash
set -eu
if [ "${1:-}" = devices ]; then
  printf '0123456789ABCDEF\tfastboot\n'
  exit 0
fi
[ "${1:-}" = -s ]
shift 2
case "${1:-}" in
  getvar)
    case "${2:-}" in
      product) printf 'product: FP6\n' >&2 ;;
      current-slot) printf 'current-slot: a\n' >&2 ;;
      unlocked) printf 'unlocked: yes\n' >&2 ;;
      secure) printf 'secure: yes\n' >&2 ;;
      *) printf '%s: fixture-value\n' "${2:-unknown}" >&2 ;;
    esac
    ;;
  oem)
    [ "${2:-}" = device-info ]
    printf 'Device unlocked: true\nDevice critical unlocked: true\n' >&2
    ;;
  *) exit 2 ;;
esac
MOCK_FASTBOOT
chmod +x "$test_root/bin/adb" "$test_root/bin/fastboot"

LUMA_ADB="$test_root/bin/adb" \
LUMA_FASTBOOT="$test_root/bin/fastboot" \
LUMA_FP6_TRANSPORT=adb \
LUMA_FP6_INVENTORY_DIR="$test_root/adb-output" \
LUMA_FP6_INVENTORY_TIMESTAMP=fixture \
  "$repo_root/scripts/mobile/inventory-fp6.sh" >/dev/null

grep -qx 'TRANSPORT=adb' "$test_root/adb-output/summary.env"
grep -qx 'IDENTITY=fp6-confirmed' "$test_root/adb-output/summary.env"
grep -qx 'MUTATING_OPERATIONS=none' "$test_root/adb-output/summary.env"
grep -qx 'Physical size: 1116x2484' "$test_root/adb-output/display-size.txt"

# Real stock Android can deny /proc/partitions, and callers may supply tools
# from a path containing spaces. Neither condition may abort P0 inventory.
mkdir -p "$test_root/platform tools" "$test_root/adb-denied-output"
cp "$test_root/bin/adb" "$test_root/platform tools/adb"
cp "$test_root/bin/fastboot" "$test_root/platform tools/fastboot"
LUMA_ADB="$test_root/platform tools/adb" \
LUMA_FASTBOOT="$test_root/platform tools/fastboot" \
LUMA_MOCK_PARTITIONS_DENIED=true \
LUMA_FP6_TRANSPORT=adb \
LUMA_FP6_INVENTORY_DIR="$test_root/adb-denied-output" \
LUMA_FP6_INVENTORY_TIMESTAMP=fixture \
  "$repo_root/scripts/mobile/inventory-fp6.sh" >/dev/null
grep -qx 'IDENTITY=fp6-confirmed' "$test_root/adb-denied-output/summary.env"
grep -qx 'UNAVAILABLE: stock Android denied read access to /proc/partitions' \
  "$test_root/adb-denied-output/partitions.txt"

LUMA_ADB="$test_root/bin/adb" \
LUMA_FASTBOOT="$test_root/bin/fastboot" \
LUMA_FP6_TRANSPORT=fastboot \
LUMA_FP6_INVENTORY_DIR="$test_root/fastboot-output" \
LUMA_FP6_INVENTORY_TIMESTAMP=fixture \
  "$repo_root/scripts/mobile/inventory-fp6.sh" >/dev/null

grep -qx 'TRANSPORT=fastboot' "$test_root/fastboot-output/summary.env"
grep -qx 'product=FP6' "$test_root/fastboot-output/fastboot-variables.txt"
grep -qx 'unlocked=yes' "$test_root/fastboot-output/fastboot-variables.txt"
grep -qx 'Device unlocked: true' "$test_root/fastboot-output/fastboot-device-info.txt"
grep -qx 'Device critical unlocked: true' "$test_root/fastboot-output/fastboot-device-info.txt"

mkdir -p "$test_root/readiness"
cp -R "$test_root/adb-output" "$test_root/readiness/inventory"
cat >"$test_root/readiness/recovery-manifest.txt" <<'RECOVERY'
LUMA_FP6_RECOVERY_MANIFEST_VERSION=1
SECURITY_PATCH=2026-07-05
CHECKSUM_VERIFIED=true
FLASH_AUTHORIZED=false
RECOVERY
cat >"$test_root/readiness/control-manifest.txt" <<'CONTROL'
LUMA_FP6_CONTROL_MANIFEST_VERSION=1
CHECKSUMS_VERIFIED=true
INSTALL_MUTATES_PHONE=true
INSTALL_AUTHORIZED=false
CONTROL

LUMA_FP6_PHYSICAL_BUILD_DIR="$test_root/readiness" \
  "$repo_root/scripts/mobile/check-fp6-readiness.sh" \
  "$test_root/readiness/inventory" >"$test_root/readiness/pass.txt"
grep -qx 'UNLOCK_AUTHORIZED=false' "$test_root/readiness/pass.txt"
grep -qx 'INSTALL_AUTHORIZED=false' "$test_root/readiness/pass.txt"
grep -qx 'OBSERVED_BOOTLOADER_STATE=unlocked' "$test_root/readiness/pass.txt"
grep -qx 'DEVICE_UPDATE_REQUIRED=false' "$test_root/readiness/pass.txt"

sed 's/SECURITY_PATCH=2026-07-05/SECURITY_PATCH=2026-06-05/' \
  "$test_root/readiness/recovery-manifest.txt" >"$test_root/readiness/recovery-old.txt"
mv "$test_root/readiness/recovery-old.txt" "$test_root/readiness/recovery-manifest.txt"
if LUMA_FP6_PHYSICAL_BUILD_DIR="$test_root/readiness" \
  "$repo_root/scripts/mobile/check-fp6-readiness.sh" \
  "$test_root/readiness/inventory" >/dev/null 2>&1; then
  printf 'error: readiness accepted recovery older than the device patch\n' >&2
  exit 1
fi

if grep -Eq '(\$adb_bin|\$fastboot_bin).*[[:space:]](flash|flashing|erase|wipe|reboot|boot|format)([[:space:]";]|$)' \
  "$repo_root/scripts/mobile/inventory-fp6.sh"; then
  printf 'error: mutating command token found in inventory implementation\n' >&2
  exit 1
fi

printf 'Mobile physical inventory and readiness fixtures: PASS\n'
