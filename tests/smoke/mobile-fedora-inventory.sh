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
LUMA_FP6_FEDORA_INVENTORY_VERSION=1
PHONE_ACCESSED=true
MUTATING_OPERATIONS=none
P5_INSTALL_AUTHORIZED=false
DT_MODEL=Fairphone (Gen. 6)
OS_ID=fedora
OS_VERSION_ID=44
KERNEL_RELEASE=7.1.2
SYSTEM_STATE=running
SYSTEM_FAILED_UNITS=0
GRAPHICAL_TARGET=active
PHOSH_PROCESSES=1
PHOC_PROCESSES=1
PLYMOUTH_PROCESSES=0
SEAT0_USER_SESSIONS=1
SELINUX_STATE=Disabled
DISPLAY_STATUS=connected
DISPLAY_MODES=1116x2484
BRIGHTNESS=4095
MAX_BRIGHTNESS=4095
GPU_SQE_FIRMWARE_PRESENT=true
GPU_GMU_FIRMWARE_PRESENT=true
GPU_ZAP_FIRMWARE_PRESENT=true
TOUCH_DEVICE=ESWIN EPH86XX Touchscreen
HAPTIC_DEVICE=aw86927-haptics
USB_NETWORK_STATE=up
WIFI_STATE=up
BLUETOOTH_STATE=unblocked
ALSA_CARDS=0
ALSA_PLAYBACK_DEVICES=0
ALSA_CAPTURE_DEVICES=0
PIPEWIRE_AUDIO_DEVICES=0
PIPEWIRE_SINKS=0
PIPEWIRE_SOURCES=0
VIDEO_NODES=0
MEDIA_NODES=0
VIDEO_NODE_NAMES=unavailable
IIO_DEVICES=0
IIO_DEVICE_NAMES=unavailable
REMOTEPROC_STATES=modem:offline
MODEM_COUNT=0
MODEM_STATE=unavailable
QRTR_PACKAGE=qrtr-1.2-4.fc44.aarch64
QRTR_SOCKET_COUNT=0
RMTFS_SERVICE=active
RMTFS_PACKAGE=rmtfs-1.1.1-2.fc44.aarch64
STUDY_PARTITION_PRESENT=true
RMTFS_MODEM_STUDY_MAPPING=false
TQFTPSERV_SERVICE=active
MODEMMANAGER_SERVICE=active
BATTERY_STATUS=Discharging
BATTERY_HEALTH=Good
BATTERY_CAPACITY=91
BATTERY_TEMP_TENTHS_C=290
THERMAL_RANGE_MILLIC=28000-39000
ERROR_MODEM_RMTS_IOVEC=10
MODEM_CRASH_COUNT=10
ERROR_ESWIN_SPI_TIMEOUT=3
ERROR_GPU_FIRMWARE=0
ERROR_GPU_LOCKUP=0
ERROR_GPU_RECOVERY=0
ERROR_DPU=0
REPORT
MOCK_SSH
chmod +x "$test_root/bin/ssh"

LUMA_SSH="$test_root/bin/ssh" \
LUMA_FP6_SSH_TARGET=luma@fixture \
LUMA_FP6_FEDORA_INVENTORY_DIR="$test_root/output" \
LUMA_FP6_FEDORA_TIMESTAMP=fixture \
  "$repo_root/scripts/mobile/inventory-fp6-fedora.sh" >/dev/null

grep -qx 'IDENTITY=fp6-fedora44-confirmed' "$test_root/output/summary.env"
grep -qx 'MUTATING_OPERATIONS=none' "$test_root/output/summary.env"
grep -qx 'P5_INSTALL_AUTHORIZED=false' "$test_root/output/summary.env"
grep -qx 'SYSTEM_STATE=running' "$test_root/output/fedora-hardware.env"
grep -qx 'DISPLAY_MODES=1116x2484' "$test_root/output/fedora-hardware.env"
grep -qx 'PLYMOUTH_PROCESSES=0' "$test_root/output/fedora-hardware.env"
grep -qx 'PIPEWIRE_AUDIO_DEVICES=0' "$test_root/output/fedora-hardware.env"
grep -qx 'ERROR_MODEM_RMTS_IOVEC=10' "$test_root/output/fedora-hardware.env"
grep -qx 'STUDY_PARTITION_PRESENT=true' "$test_root/output/fedora-hardware.env"
grep -qx 'RMTFS_MODEM_STUDY_MAPPING=false' "$test_root/output/fedora-hardware.env"
grep -qx 'ERROR_GPU_LOCKUP=0' "$test_root/output/fedora-hardware.env"
grep -qx 'ERROR_GPU_RECOVERY=0' "$test_root/output/fedora-hardware.env"
[ "$(stat -f '%Lp' "$test_root/output/summary.env" 2>/dev/null || stat -c '%a' "$test_root/output/summary.env")" = 600 ]
[ "$(stat -f '%Lp' "$test_root/output/fedora-hardware.env" 2>/dev/null || stat -c '%a' "$test_root/output/fedora-hardware.env")" = 600 ]

if grep -Eq '(^|[[:space:]])(flash|erase|wipe|set[_-]active)([[:space:]]|$)' \
  "$repo_root/scripts/mobile/inventory-fp6-fedora.sh"; then
  printf 'error: mutating command token found in Fedora inventory\n' >&2
  exit 1
fi

if grep -Eqi '(serial|imei|imsi|ssid|bssid|192[.]168[.])' \
  "$test_root/output/fedora-hardware.env"; then
  printf 'error: Fedora inventory retained a forbidden identifier\n' >&2
  exit 1
fi

printf 'Mobile Fedora physical inventory fixture: PASS\n'
