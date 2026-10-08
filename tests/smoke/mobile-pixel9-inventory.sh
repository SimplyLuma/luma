#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf -- "$test_root"' EXIT
mkdir -p "$test_root/bin" "$test_root/adb-output" "$test_root/fastboot-output"

cat >"$test_root/bin/adb" <<'MOCK_ADB'
#!/usr/bin/env bash
set -eu
if [ "${1:-}" = devices ]; then
  printf 'List of devices attached\nTOKAYFIXTURE1234\tdevice product:tokay model:Pixel_9 device:tokay transport_id:1\n'
  exit 0
fi
[ "${1:-}" = -s ]
[ "${2:-}" = TOKAYFIXTURE1234 ]
shift 2
[ "${1:-}" = shell ]
shift
case "$*" in
  'getprop ro.product.manufacturer') printf 'Google\n' ;;
  'getprop ro.product.model') printf 'Pixel 9\n' ;;
  'getprop ro.product.device') printf 'tokay\n' ;;
  'getprop ro.build.id') printf 'FIXTURE.260831.001\n' ;;
  'getprop ro.build.version.security_patch') printf '2026-08-05\n' ;;
  'getprop ro.boot.slot_suffix') printf '_a\n' ;;
  'getprop ro.boot.flash.locked') printf '1\n' ;;
  'getprop ro.boot.vbmeta.device_state') printf 'locked\n' ;;
  'getprop '*) printf 'fixture-value\n' ;;
  'uname -a') printf 'Linux localhost 6.1-android14 fixture\n' ;;
  'dumpsys battery') printf 'level: 91\nstatus: 2\n' ;;
  'wm size') printf 'Physical size: 1080x2424\n' ;;
  'wm density') printf 'Physical density: 422\n' ;;
  *) exit 2 ;;
esac
MOCK_ADB

cat >"$test_root/bin/fastboot" <<'MOCK_FASTBOOT'
#!/usr/bin/env bash
set -eu
if [ "${1:-}" = devices ]; then
  printf 'TOKAYFIXTURE1234\tfastboot\n'
  exit 0
fi
[ "${1:-}" = -s ]
[ "${2:-}" = TOKAYFIXTURE1234 ]
shift 2
[ "${1:-}" = getvar ]
case "${2:-}" in
  product) printf 'product: tokay\n' >&2 ;;
  current-slot) printf 'current-slot: a\n' >&2 ;;
  unlocked) printf 'unlocked: no\n' >&2 ;;
  secure) printf 'secure: yes\n' >&2 ;;
  slot-successful:a) printf '(bootloader) slot-successful:a: yes\n' >&2 ;;
  slot-successful:b) printf '(bootloader) slot-successful:b: yes\n' >&2 ;;
  *) printf '%s: fixture-value\n' "${2:-unknown}" >&2 ;;
esac
MOCK_FASTBOOT
chmod +x "$test_root/bin/adb" "$test_root/bin/fastboot"

LUMA_ADB="$test_root/bin/adb" \
LUMA_FASTBOOT="$test_root/bin/fastboot" \
LUMA_PIXEL9_TRANSPORT=adb \
LUMA_PIXEL9_INVENTORY_DIR="$test_root/adb-output" \
LUMA_PIXEL9_INVENTORY_TIMESTAMP=fixture \
  "$repo_root/scripts/mobile/inventory-pixel9.sh" >/dev/null

grep -qx 'TRANSPORT=adb' "$test_root/adb-output/summary.env"
grep -qx 'DEVICE=tokay' "$test_root/adb-output/summary.env"
grep -qx 'IDENTITY=tokay-confirmed' "$test_root/adb-output/summary.env"
grep -qx 'MUTATING_OPERATIONS=none' "$test_root/adb-output/summary.env"
grep -qx 'UNLOCK_AUTHORIZED=false' "$test_root/adb-output/summary.env"
grep -qx 'FLASH_AUTHORIZED=false' "$test_root/adb-output/summary.env"
grep -qx 'ro.build.id=FIXTURE.260831.001' "$test_root/adb-output/android-properties.txt"
grep -qx 'Physical size: 1080x2424' "$test_root/adb-output/display-size.txt"

if rg -Fq 'TOKAYFIXTURE1234' "$test_root/adb-output"; then
  printf 'error: ADB inventory retained the transport serial\n' >&2
  exit 1
fi

LUMA_ADB="$test_root/bin/adb" \
LUMA_FASTBOOT="$test_root/bin/fastboot" \
LUMA_PIXEL9_TRANSPORT=fastboot \
LUMA_PIXEL9_INVENTORY_DIR="$test_root/fastboot-output" \
LUMA_PIXEL9_INVENTORY_TIMESTAMP=fixture \
  "$repo_root/scripts/mobile/inventory-pixel9.sh" >/dev/null

grep -qx 'TRANSPORT=fastboot' "$test_root/fastboot-output/summary.env"
grep -qx 'product=tokay' "$test_root/fastboot-output/fastboot-variables.txt"
grep -qx 'unlocked=no' "$test_root/fastboot-output/fastboot-variables.txt"
grep -qx 'slot-successful:a=yes' "$test_root/fastboot-output/fastboot-variables.txt"
grep -qx 'slot-successful:b=yes' "$test_root/fastboot-output/fastboot-variables.txt"

if rg -Fq 'TOKAYFIXTURE1234' "$test_root/fastboot-output"; then
  printf 'error: fastboot inventory retained the transport serial\n' >&2
  exit 1
fi

printf 'Pixel 9 read-only inventory fixtures: PASS\n'

