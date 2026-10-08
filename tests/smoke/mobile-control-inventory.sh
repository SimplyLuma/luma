#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
mkdir -p "$test_root/bin" "$test_root/output"

cat >"$test_root/bin/ssh" <<'MOCK_SSH'
#!/usr/bin/env bash
set -eu
cat >/dev/null
cat <<'REPORT'
LUMA_FP6_CONTROL_ACCEPTANCE_VERSION=1
PHONE_ACCESSED=true
MUTATING_OPERATIONS=none
P5_AUTHORIZED=false
DT_MODEL=Fairphone (Gen. 6)
OS_ID=postmarketos
OS_VERSION=edge
KERNEL_RELEASE=7.1.2-postmarketos-qcom-milos
SYSTEM_STATE=running
SYSTEM_FAILED_UNITS=0
USER_FAILED_UNITS=0
GRAPHICAL_TARGET=active
PHOSH_PROCESSES=1
PHOC_PROCESSES=1
BOOT_TIMING=Startup finished in fixture
DISPLAY_STATUS=connected
DISPLAY_MODES=1116x2484
GPU_RENDERER=Adreno (TM) 810
GPU_SQE_LOADED_FROM_ROOTFS=true
GPU_GMU_LOADED_FROM_ROOTFS=true
TOUCH_DEVICE=ESWIN EPH86XX Touchscreen
TOUCH_NATIVE_SIZE=1116x2484
USB_NETWORK_STATE=up
WIFI_STATE=up
BLUETOOTH_STATE=unblocked
ALSA_CARDS=1
ALSA_PLAYBACK_DEVICES=0
ALSA_CAPTURE_DEVICES=0
PIPEWIRE_SINKS=0
PIPEWIRE_SOURCES=0
VIDEO_NODES=2
MEDIA_NODES=0
MODEM_COUNT=1
MODEM_STATE=failed
MODEM_FAILED_REASON=sim-missing
GNSS_STATE=activating,auto-restart,exit-code
IIO_DEVICES=0
HAPTIC_DEVICE=aw86927-haptics
BATTERY_STATUS=Full
BATTERY_HEALTH=Good
BATTERY_CAPACITY=100
BATTERY_TEMP_TENTHS_C=300
THERMAL_RANGE_MILLIC=31400-37000
BRIGHTNESS=1257
MAX_BRIGHTNESS=4095
ERROR_REMOTEproc_HANDOVER=100
ERROR_GNSS_SETUP=10
ERROR_BRIGHTNESS=2
ERROR_AUDIO_PROFILE=2
ERROR_DPU=4
REPORT
MOCK_SSH
chmod +x "$test_root/bin/ssh"

LUMA_SSH="$test_root/bin/ssh" \
LUMA_FP6_SSH_TARGET=user@fixture \
LUMA_FP6_CONTROL_INVENTORY_DIR="$test_root/output" \
LUMA_FP6_CONTROL_TIMESTAMP=fixture \
  "$repo_root/scripts/mobile/inventory-fp6-control.sh" >/dev/null

grep -qx 'IDENTITY=fp6-confirmed' "$test_root/output/summary.env"
grep -qx 'MUTATING_OPERATIONS=none' "$test_root/output/summary.env"
grep -qx 'P5_AUTHORIZED=false' "$test_root/output/summary.env"
grep -qx 'GPU_RENDERER=Adreno (TM) 810' \
  "$test_root/output/control-acceptance.env"
grep -qx 'ALSA_PLAYBACK_DEVICES=0' \
  "$test_root/output/control-acceptance.env"
grep -qx 'GNSS_STATE=activating,auto-restart,exit-code' \
  "$test_root/output/control-acceptance.env"
[ "$(stat -f '%Lp' "$test_root/output/summary.env" 2>/dev/null || stat -c '%a' "$test_root/output/summary.env")" = 600 ]
[ "$(stat -f '%Lp' "$test_root/output/control-acceptance.env" 2>/dev/null || stat -c '%a' "$test_root/output/control-acceptance.env")" = 600 ]

if grep -Eq '(flash|erase|wipe|reboot|set[_-]active|flashing (un)?lock)' \
  "$repo_root/scripts/mobile/inventory-fp6-control.sh"; then
  printf 'error: mutating command token found in control inventory\n' >&2
  exit 1
fi

if grep -Eqi '(serial|imei|imsi|ssid|bssid|192[.]168[.])' \
  "$test_root/output/control-acceptance.env"; then
  printf 'error: control inventory retained a forbidden identifier\n' >&2
  exit 1
fi

printf 'Mobile native-Linux control inventory fixture: PASS\n'
