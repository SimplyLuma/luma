#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Read-only acceptance inventory for FP6 peripherals that do not need physical
# stimulus. This deliberately does not enable radios, GNSS, NFC polling,
# haptics, charging modes, or fingerprint enrollment.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
ssh_bin=${LUMA_SSH:-ssh}
ssh_target=${LUMA_FP6_SSH_TARGET:-luma@172.16.42.1}
ssh_identity_file=${LUMA_FP6_SSH_IDENTITY_FILE:-}
timestamp=${LUMA_FP6_PERIPHERAL_TIMESTAMP:-$(date -u +%Y%m%dT%H%M%SZ)}
output_root=${LUMA_FP6_PERIPHERAL_DIR:-$repo_root/build/mobile/fp6-physical/peripheral-inventory/$timestamp}
tmp_report=$(mktemp)
trap 'rm -f "$tmp_report"' EXIT

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

command -v "$ssh_bin" >/dev/null 2>&1 || die "SSH client is not installed: $ssh_bin"
ssh_args=(-o BatchMode=yes -o ConnectTimeout=10)
if [ -n "$ssh_identity_file" ]; then
  [ -r "$ssh_identity_file" ] || die "SSH identity is not readable: $ssh_identity_file"
  ssh_args+=(-i "$ssh_identity_file")
fi

"$ssh_bin" "${ssh_args[@]}" "$ssh_target" sh -s >"$tmp_report" <<'REMOTE'
set -u

service_state() {
  state=$(systemctl is-active "$1" 2>/dev/null || true)
  printf '%s' "${state:-unavailable}"
}

package_nevra() {
  if rpm -q "$1" >/dev/null 2>&1; then
    rpm -q --qf '%{NAME}-%{EVR}.%{ARCH}' "$1" 2>/dev/null
  else
    printf unavailable
  fi
}

count_pattern() {
  journalctl -b -k --no-pager 2>/dev/null | grep -Eic "$1" || true
}

join_input_names() {
  pattern=$1
  sed -n 's/^N: Name="\(.*\)"/\1/p' /proc/bus/input/devices 2>/dev/null |
    grep -Ei "$pattern" | paste -sd, - || true
}

join_lid_switch_names() {
  for name_path in /sys/class/input/event*/device/name; do
    [ -r "$name_path" ] || continue
    input_dir=${name_path%/name}
    [ -r "$input_dir/capabilities/sw" ] || continue
    low_word=$(awk '{print $NF}' "$input_dir/capabilities/sw")
    [ -n "$low_word" ] || continue
    low_nibble=${low_word#${low_word%?}}
    case "$low_nibble" in
      1|3|5|7|9|b|B|d|D|f|F)
        printf '%s:SW_LID\n' "$(cat "$name_path")"
        ;;
    esac
  done | paste -sd, -
}

dt_model=$(tr -d '\000' </proc/device-tree/model 2>/dev/null || true)
os_id=$(sed -n 's/^ID=//p' /etc/os-release 2>/dev/null | tr -d '"' | tail -n 1)
os_version=$(sed -n 's/^VERSION_ID=//p' /etc/os-release 2>/dev/null | tr -d '"' | tail -n 1)
kernel_release=$(uname -r 2>/dev/null || true)
boot_id=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)

libqmi_package=$(package_nevra libqmi)
qmi_utils_package=$(package_nevra libqmi-utils)
modemmanager_package=$(package_nevra ModemManager)
geoclue_package=$(package_nevra geoclue2)
bluez_package=$(package_nevra bluez)
libfprint_package=$(package_nevra libfprint)
fprintd_package=$(package_nevra fprintd)

modem_count=$(timeout 8 mmcli -L 2>/dev/null | grep -c '/Modem/' || true)
location_status=$(timeout 10 mmcli -m 0 --location-status -K 2>/dev/null || true)
location_capabilities=$(printf '%s\n' "$location_status" | sed -n 's/^modem.location.capabilities[[:space:]]*:[[:space:]]*//p' | tail -n 1)
location_enabled=$(printf '%s\n' "$location_status" | sed -n 's/^modem.location.enabled[[:space:]]*:[[:space:]]*//p' | tail -n 1)
location_signals=$(printf '%s\n' "$location_status" | sed -n 's/^modem.location.signals-location[[:space:]]*:[[:space:]]*//p' | tail -n 1)
qrtr_services=$(timeout 5 qrtr-lookup 2>/dev/null || true)
qrtr_service_count=$(printf '%s\n' "$qrtr_services" | awk 'NR > 1 && NF {count++} END {print count + 0}')
qrtr_dms_present=$(printf '%s\n' "$qrtr_services" | awk '$1 == 2 {found=1} END {print found ? "true" : "false"}')
modem_remoteproc_state=unavailable
for remote in /sys/class/remoteproc/remoteproc*; do
  [ -r "$remote/name" ] || continue
  [ "$(cat "$remote/name")" = modem ] || continue
  modem_remoteproc_state=$(cat "$remote/state" 2>/dev/null || printf unavailable)
done

nfc_class_count=$(find /sys/class/nfc -maxdepth 1 -name 'nfc[0-9]*' 2>/dev/null | wc -l | tr -d ' ')
nfc_s3fwrn5_module=$(test -d /sys/module/s3fwrn5 && printf loaded || printf absent)
nfc_i2c_module=$(test -d /sys/module/s3fwrn5_i2c && printf loaded || printf absent)
nfc_hwreg=$(test -f /usr/lib/firmware/samsung/s3nrn4v/hwreg.bin -o -f /lib/firmware/samsung/s3nrn4v/hwreg.bin && printf present || printf absent)
nfc_swreg=$(test -f /usr/lib/firmware/samsung/s3nrn4v/swreg.bin -o -f /lib/firmware/samsung/s3nrn4v/swreg.bin && printf present || printf absent)

bluetooth_controller_count=$(find /sys/class/bluetooth -maxdepth 1 -name 'hci[0-9]*' 2>/dev/null | wc -l | tr -d ' ')
bluetooth_management_count=$(timeout 5 btmgmt info 2>/dev/null |
  grep -Ec '^hci[0-9]+:[[:space:]]+Primary controller$' || true)
bluetooth_rfkill=$(rfkill list bluetooth 2>/dev/null | awk '/Soft blocked:/ {soft=$3} /Hard blocked:/ {hard=$3} END {if (soft == "no" && hard == "no") print "unblocked"; else if (soft || hard) print "blocked"; else print "unavailable"}')
bluetooth_powered=$(busctl get-property org.bluez /org/bluez/hci0 org.bluez.Adapter1 Powered 2>/dev/null | awk '{print $2}' || true)
bluetooth_pairable=$(busctl get-property org.bluez /org/bluez/hci0 org.bluez.Adapter1 Pairable 2>/dev/null | awk '{print $2}' || true)
bluetooth_discovering=$(busctl get-property org.bluez /org/bluez/hci0 org.bluez.Adapter1 Discovering 2>/dev/null | awk '{print $2}' || true)

haptic_names=$(join_input_names 'haptic|vibrator')
hall_names=$(join_lid_switch_names)
haptic_count=$(printf '%s\n' "$haptic_names" | awk -F, 'NF && $1 != "" {print NF; next} {print 0}')
hall_count=$(printf '%s\n' "$hall_names" | awk -F, 'NF && $1 != "" {print NF; next} {print 0}')

power_supply_summary=$(for directory in /sys/class/power_supply/*; do
  [ -d "$directory" ] || continue
  name=${directory##*/}
  type=$(cat "$directory/type" 2>/dev/null || printf unknown)
  status=$(cat "$directory/status" 2>/dev/null || printf na)
  online=$(cat "$directory/online" 2>/dev/null || printf na)
  printf '%s:%s:%s:%s,' "$name" "$type" "$status" "$online"
done | sed 's/,$//')
battery_capacity=$(cat /sys/class/power_supply/qcom-battmgr-bat/capacity 2>/dev/null || true)
battery_health=$(cat /sys/class/power_supply/qcom-battmgr-bat/health 2>/dev/null || true)
battery_temp=$(cat /sys/class/power_supply/qcom-battmgr-bat/temp 2>/dev/null || true)
thermal_count=$(find /sys/class/thermal -maxdepth 1 -name 'thermal_zone*' 2>/dev/null | wc -l | tr -d ' ')
thermal_range=$(for zone in /sys/class/thermal/thermal_zone*; do cat "$zone/temp" 2>/dev/null; done | awk 'NR == 1 {min=$1; max=$1} $1 < min {min=$1} $1 > max {max=$1} END {if (NR) printf "%s-%s", min, max; else printf "unavailable"}')

usb_role_summary=$(for role in /sys/class/usb_role/*/role; do
  [ -f "$role" ] || continue
  controller=${role%/role}; controller=${controller##*/}
  printf '%s:%s,' "$controller" "$(cat "$role" 2>/dev/null || printf unknown)"
done | sed 's/,$//')
removable_block_summary=$(for directory in /sys/block/*; do
  [ -f "$directory/removable" ] || continue
  removable=$(cat "$directory/removable" 2>/dev/null || printf unknown)
  [ "$removable" = 1 ] || continue
  name=${directory##*/}
  size=$(cat "$directory/size" 2>/dev/null || printf unknown)
  printf '%s:%s,' "$name" "$size"
done | sed 's/,$//')
mmc_host_count=$(find /sys/class/mmc_host -maxdepth 1 -name 'mmc[0-9]*' 2>/dev/null | wc -l | tr -d ' ')
mmc_media_count=$(find /sys/bus/mmc/devices -maxdepth 1 -mindepth 1 2>/dev/null | wc -l | tr -d ' ')

fingerprint_names=$(join_input_names 'fingerprint')
fingerprint_input_count=$(printf '%s\n' "$fingerprint_names" | awk -F, 'NF && $1 != "" {print NF; next} {print 0}')

printf 'LUMA_FP6_PERIPHERAL_INVENTORY_VERSION=1\n'
printf 'PHONE_ACCESSED=true\n'
printf 'MUTATING_OPERATIONS=none\n'
printf 'RADIO_STATE_CHANGED=false\n'
printf 'GNSS_ENABLE_ATTEMPTED=false\n'
printf 'NFC_POLL_ATTEMPTED=false\n'
printf 'HAPTIC_ACTUATION_ATTEMPTED=false\n'
printf 'BIOMETRIC_OPERATION_ATTEMPTED=false\n'
printf 'DT_MODEL=%s\n' "${dt_model:-unavailable}"
printf 'OS_ID=%s\n' "${os_id:-unavailable}"
printf 'OS_VERSION_ID=%s\n' "${os_version:-unavailable}"
printf 'KERNEL_RELEASE=%s\n' "${kernel_release:-unavailable}"
printf 'BOOT_ID=%s\n' "${boot_id:-unavailable}"
printf 'LIBQMI_PACKAGE=%s\n' "$libqmi_package"
printf 'QMI_UTILS_PACKAGE=%s\n' "$qmi_utils_package"
printf 'MODEMMANAGER_PACKAGE=%s\n' "$modemmanager_package"
printf 'GEOCLUE_PACKAGE=%s\n' "$geoclue_package"
printf 'MODEMMANAGER_SERVICE=%s\n' "$(service_state ModemManager.service)"
printf 'GEOCLUE_SERVICE=%s\n' "$(service_state geoclue.service)"
printf 'MODEM_COUNT=%s\n' "$modem_count"
printf 'LOCATION_CAPABILITIES=%s\n' "${location_capabilities:-unavailable}"
printf 'LOCATION_ENABLED=%s\n' "${location_enabled:-unavailable}"
printf 'LOCATION_SIGNALS=%s\n' "${location_signals:-unavailable}"
printf 'QRTR_SERVICE_COUNT=%s\n' "$qrtr_service_count"
printf 'QRTR_DMS_PRESENT=%s\n' "$qrtr_dms_present"
printf 'MODEM_REMOTEPROC_STATE=%s\n' "$modem_remoteproc_state"
printf 'NFC_CLASS_DEVICES=%s\n' "$nfc_class_count"
printf 'NFC_S3FWRN5_MODULE=%s\n' "$nfc_s3fwrn5_module"
printf 'NFC_S3FWRN5_I2C_MODULE=%s\n' "$nfc_i2c_module"
printf 'NFC_HWREG_FIRMWARE=%s\n' "$nfc_hwreg"
printf 'NFC_SWREG_FIRMWARE=%s\n' "$nfc_swreg"
printf 'NEARD_SERVICE=%s\n' "$(service_state neard.service)"
printf 'BLUEZ_PACKAGE=%s\n' "$bluez_package"
printf 'BLUETOOTH_SERVICE=%s\n' "$(service_state bluetooth.service)"
printf 'BLUETOOTH_CONTROLLERS=%s\n' "$bluetooth_controller_count"
printf 'BLUETOOTH_MANAGEMENT_CONTROLLERS=%s\n' "$bluetooth_management_count"
printf 'BLUETOOTH_RFKILL=%s\n' "${bluetooth_rfkill:-unavailable}"
printf 'BLUETOOTH_POWERED=%s\n' "${bluetooth_powered:-unavailable}"
printf 'BLUETOOTH_PAIRABLE=%s\n' "${bluetooth_pairable:-unavailable}"
printf 'BLUETOOTH_DISCOVERING=%s\n' "${bluetooth_discovering:-unavailable}"
printf 'HAPTIC_INPUTS=%s\n' "$haptic_count"
printf 'HAPTIC_NAMES=%s\n' "${haptic_names:-unavailable}"
printf 'HALL_INPUTS=%s\n' "$hall_count"
printf 'HALL_NAMES=%s\n' "${hall_names:-unavailable}"
printf 'POWER_SUPPLIES=%s\n' "${power_supply_summary:-unavailable}"
printf 'BATTERY_CAPACITY=%s\n' "${battery_capacity:-unavailable}"
printf 'BATTERY_HEALTH=%s\n' "${battery_health:-unavailable}"
printf 'BATTERY_TEMP_TENTHS_C=%s\n' "${battery_temp:-unavailable}"
printf 'THERMAL_ZONES=%s\n' "$thermal_count"
printf 'THERMAL_RANGE_MILLIC=%s\n' "$thermal_range"
printf 'USB_ROLES=%s\n' "${usb_role_summary:-unavailable}"
printf 'REMOVABLE_BLOCK_DEVICES=%s\n' "${removable_block_summary:-none}"
printf 'MMC_HOSTS=%s\n' "$mmc_host_count"
printf 'MMC_MEDIA_DEVICES=%s\n' "$mmc_media_count"
printf 'LIBFPRINT_PACKAGE=%s\n' "$libfprint_package"
printf 'FPRINTD_PACKAGE=%s\n' "$fprintd_package"
printf 'FPRINTD_SERVICE=%s\n' "$(service_state fprintd.service)"
printf 'FINGERPRINT_INPUTS=%s\n' "$fingerprint_input_count"
printf 'FINGERPRINT_NAMES=%s\n' "${fingerprint_names:-unavailable}"
printf 'SYSTEM_FAILED_UNITS=%s\n' "$(systemctl --failed --no-legend --no-pager 2>/dev/null | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')"
printf 'ERROR_GPU_HANG=%s\n' "$(count_pattern 'hangcheck|GMU.*timeout')"
printf 'ERROR_NFC=%s\n' "$(count_pattern 's3fwrn5.*(fail|error)|s3nrn4v.*(fail|error)')"
printf 'ERROR_BLUETOOTH=%s\n' "$(count_pattern 'Bluetooth.*(fail|error)|hci[0-9].*(fail|error)')"
printf 'ERROR_THERMAL=%s\n' "$(count_pattern 'thermal.*(critical|shutdown)')"
REMOTE

grep -qx 'LUMA_FP6_PERIPHERAL_INVENTORY_VERSION=1' "$tmp_report" || \
  die 'remote output is not a Luma FP6 peripheral report'
grep -qx 'OS_ID=fedora' "$tmp_report" || die 'remote userspace is not Fedora'
grep -qx 'OS_VERSION_ID=44' "$tmp_report" || die 'remote Fedora release is not 44'
grep -Eqi '^DT_MODEL=.*Fairphone.*(Gen[.]? 6|6)' "$tmp_report" || \
  die 'remote device-tree identity is not Fairphone 6'
if grep -Eqi '(serial|imei|imsi|ssid|bssid|mac(_|-)address|wifi.*address|192[.]168[.])' "$tmp_report"; then
  die 'report contains a forbidden device or network identifier'
fi

mkdir -p "$output_root"
install -m 0600 "$tmp_report" "$output_root/peripherals.env"
{
  printf 'LUMA_FP6_PERIPHERAL_SUMMARY_VERSION=1\n'
  printf 'IDENTITY=fp6-fedora44-confirmed\n'
  printf 'TRANSPORT=ssh\n'
  printf 'PHONE_ACCESSED=true\n'
  printf 'MUTATING_OPERATIONS=none\n'
  printf 'PHYSICAL_STIMULUS=not-performed\n'
} >"$output_root/summary.env"
chmod 0600 "$output_root/summary.env"

printf 'FP6 read-only peripheral inventory complete: %s\n' "$output_root"
