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
LUMA_FP6_PERIPHERAL_INVENTORY_VERSION=1
PHONE_ACCESSED=true
MUTATING_OPERATIONS=none
RADIO_STATE_CHANGED=false
GNSS_ENABLE_ATTEMPTED=false
NFC_POLL_ATTEMPTED=false
HAPTIC_ACTUATION_ATTEMPTED=false
BIOMETRIC_OPERATION_ATTEMPTED=false
DT_MODEL=The Fairphone (Gen. 6)
OS_ID=fedora
OS_VERSION_ID=44
KERNEL_RELEASE=7.1.2
BOOT_ID=fixture-boot
LIBQMI_PACKAGE=libqmi-1.36.0-3.fc44.aarch64
QMI_UTILS_PACKAGE=libqmi-utils-1.36.0-3.fc44.aarch64
MODEMMANAGER_PACKAGE=ModemManager-1.24.2-3.fc44.aarch64
GEOCLUE_PACKAGE=geoclue2-2.7.2-1.fc44.aarch64
MODEMMANAGER_SERVICE=active
GEOCLUE_SERVICE=inactive
MODEM_COUNT=1
LOCATION_CAPABILITIES=3gpp-lac-ci,gps-raw,gps-nmea
LOCATION_ENABLED=3gpp-lac-ci
LOCATION_SIGNALS=no
QRTR_SERVICE_COUNT=12
QRTR_DMS_PRESENT=true
MODEM_REMOTEPROC_STATE=running
NFC_CLASS_DEVICES=1
NFC_S3FWRN5_MODULE=loaded
NFC_S3FWRN5_I2C_MODULE=loaded
NFC_HWREG_FIRMWARE=present
NFC_SWREG_FIRMWARE=present
NEARD_SERVICE=inactive
BLUEZ_PACKAGE=bluez-5.83-1.fc44.aarch64
BLUETOOTH_SERVICE=active
BLUETOOTH_CONTROLLERS=1
BLUETOOTH_MANAGEMENT_CONTROLLERS=1
BLUETOOTH_RFKILL=unblocked
BLUETOOTH_POWERED=true
BLUETOOTH_PAIRABLE=false
BLUETOOTH_DISCOVERING=false
HAPTIC_INPUTS=1
HAPTIC_NAMES=aw86927-haptics
HALL_INPUTS=1
HALL_NAMES=gpio-keys:SW_LID
POWER_SUPPLIES=qcom-battmgr-bat:Battery:Discharging:na,usb:USB:na:1
BATTERY_CAPACITY=80
BATTERY_HEALTH=Good
BATTERY_TEMP_TENTHS_C=310
THERMAL_ZONES=22
THERMAL_RANGE_MILLIC=29000-38000
USB_ROLES=a600000.usb:device
REMOVABLE_BLOCK_DEVICES=none
MMC_HOSTS=1
MMC_MEDIA_DEVICES=0
LIBFPRINT_PACKAGE=unavailable
FPRINTD_PACKAGE=unavailable
FPRINTD_SERVICE=inactive
FINGERPRINT_INPUTS=0
FINGERPRINT_NAMES=unavailable
SYSTEM_FAILED_UNITS=0
ERROR_GPU_HANG=0
ERROR_NFC=0
ERROR_BLUETOOTH=0
ERROR_THERMAL=0
REPORT
MOCK_SSH
chmod +x "$test_root/bin/ssh"

LUMA_SSH="$test_root/bin/ssh" \
LUMA_FP6_SSH_TARGET=luma@fixture \
LUMA_FP6_PERIPHERAL_DIR="$test_root/output" \
LUMA_FP6_PERIPHERAL_TIMESTAMP=fixture \
  "$repo_root/scripts/mobile/inspect-fp6-peripherals.sh" >/dev/null

grep -qx 'IDENTITY=fp6-fedora44-confirmed' "$test_root/output/summary.env"
grep -qx 'MUTATING_OPERATIONS=none' "$test_root/output/summary.env"
grep -qx 'PHYSICAL_STIMULUS=not-performed' "$test_root/output/summary.env"
grep -qx 'GNSS_ENABLE_ATTEMPTED=false' "$test_root/output/peripherals.env"
grep -qx 'NFC_POLL_ATTEMPTED=false' "$test_root/output/peripherals.env"
grep -qx 'BIOMETRIC_OPERATION_ATTEMPTED=false' "$test_root/output/peripherals.env"
grep -qx 'NFC_CLASS_DEVICES=1' "$test_root/output/peripherals.env"
grep -qx 'BLUETOOTH_CONTROLLERS=1' "$test_root/output/peripherals.env"
grep -qx 'BLUETOOTH_MANAGEMENT_CONTROLLERS=1' "$test_root/output/peripherals.env"
grep -qx 'HAPTIC_INPUTS=1' "$test_root/output/peripherals.env"
grep -qx 'HALL_INPUTS=1' "$test_root/output/peripherals.env"
grep -qx 'HALL_NAMES=gpio-keys:SW_LID' "$test_root/output/peripherals.env"
grep -qx 'QRTR_DMS_PRESENT=true' "$test_root/output/peripherals.env"
grep -qx 'ERROR_GPU_HANG=0' "$test_root/output/peripherals.env"

[ "$(stat -f '%Lp' "$test_root/output/summary.env" 2>/dev/null || stat -c '%a' "$test_root/output/summary.env")" = 600 ]
[ "$(stat -f '%Lp' "$test_root/output/peripherals.env" 2>/dev/null || stat -c '%a' "$test_root/output/peripherals.env")" = 600 ]

if grep -Eq '(^|[[:space:]])(flash|erase|wipe|set[_-]active)([[:space:]]|$)' \
  "$repo_root/scripts/mobile/inspect-fp6-peripherals.sh"; then
  printf 'error: mutating command token found in peripheral inventory\n' >&2
  exit 1
fi

if grep -Eqi '(serial|imei|imsi|ssid|bssid|192[.]168[.])' \
  "$test_root/output/peripherals.env"; then
  printf 'error: peripheral inventory retained a forbidden identifier\n' >&2
  exit 1
fi

printf 'Mobile FP6 peripheral inventory fixture: PASS\n'
