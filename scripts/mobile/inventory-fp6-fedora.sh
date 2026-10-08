#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Read-only hardware inventory for a Fairphone 6 already running the Fedora
# P5 diagnostic userspace. The caller owns SSH authentication; this collector
# retains no credential, radio identity, network name, or hardware address.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
ssh_bin=${LUMA_SSH:-ssh}
ssh_target=${LUMA_FP6_SSH_TARGET:-luma@172.16.42.1}
timestamp=${LUMA_FP6_FEDORA_TIMESTAMP:-$(date -u +%Y%m%dT%H%M%SZ)}
output_root=${LUMA_FP6_FEDORA_INVENTORY_DIR:-$repo_root/build/mobile/fp6-physical/fedora-inventory/$timestamp}
tmp_report=$(mktemp)
trap 'rm -f "$tmp_report"' EXIT

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

command -v "$ssh_bin" >/dev/null 2>&1 || die "SSH client is not installed: $ssh_bin"

"$ssh_bin" "$ssh_target" sh -s >"$tmp_report" <<'REMOTE'
set -u

count_pattern() {
  pattern=$1
  journalctl -b --no-pager 2>/dev/null | grep -Ec "$pattern" || true
}

service_state() {
  state=$(systemctl is-active "$1" 2>/dev/null || true)
  printf '%s' "${state:-unavailable}"
}

names_from_files() {
  found=false
  for file in "$@"; do
    [ -f "$file" ] || continue
    value=$(tr '\n' ' ' <"$file" 2>/dev/null | sed 's/[[:space:]][[:space:]]*/ /g; s/^ //; s/ $//' || true)
    [ -n "$value" ] || continue
    if [ "$found" = true ]; then printf ','; fi
    printf '%s' "$value"
    found=true
  done
  [ "$found" = true ] || printf unavailable
}

dt_model=$(tr -d '\000' </proc/device-tree/model 2>/dev/null || true)
os_id=$(sed -n 's/^ID=//p' /etc/os-release 2>/dev/null | tr -d '"' | tail -n 1)
os_version=$(sed -n 's/^VERSION_ID=//p' /etc/os-release 2>/dev/null | tr -d '"' | tail -n 1)
kernel_release=$(uname -r 2>/dev/null || true)
system_state=$(systemctl is-system-running 2>/dev/null || true)
system_failed=$(systemctl --failed --no-legend --no-pager 2>/dev/null | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')
graphical_state=$(systemctl is-active graphical.target 2>/dev/null || true)
phosh_count=$(pgrep -xc phosh 2>/dev/null || true)
phoc_count=$(pgrep -xc phoc 2>/dev/null || true)
plymouth_count=$(pgrep -xc plymouthd 2>/dev/null || true)
wayland_sessions=$(loginctl list-sessions --no-legend 2>/dev/null | awk '$4 == "seat0" && $6 == "user" {n++} END {print n+0}')
selinux_state=$(getenforce 2>/dev/null || printf unavailable)

display_status=$(cat /sys/class/drm/card0-DSI-1/status 2>/dev/null || true)
display_modes=$(tr '\n' ',' </sys/class/drm/card0-DSI-1/modes 2>/dev/null | sed 's/,$//' || true)
brightness=$(cat /sys/class/backlight/*/actual_brightness 2>/dev/null | head -n 1 || true)
max_brightness=$(cat /sys/class/backlight/*/max_brightness 2>/dev/null | head -n 1 || true)
sqe_present=$(test -f /usr/lib/firmware/postmarketos/gen80300_sqe.fw && printf true || printf false)
gmu_present=$(test -f /usr/lib/firmware/postmarketos/gen80300_gmu.bin && printf true || printf false)
zap_present=$(test -f /usr/lib/firmware/qcom/milos/fairphone/fp6/gen80300_zap.mbn && printf true || printf false)
touch_name=$(sed -n 's/^N: Name="\(.*Touchscreen.*\)"/\1/p' /proc/bus/input/devices 2>/dev/null | head -n 1)
haptic_name=$(sed -n 's/^N: Name="\(.*haptic.*\)"/\1/p' /proc/bus/input/devices 2>/dev/null | head -n 1)

usb_state=$(cat /sys/class/net/usb0/operstate 2>/dev/null || printf absent)
wifi_state=$(cat /sys/class/net/wlan0/operstate 2>/dev/null || printf absent)
bluetooth_state=$(rfkill list bluetooth 2>/dev/null | awk '/Soft blocked:/ {soft=$3} /Hard blocked:/ {hard=$3} END {if (soft == "no" && hard == "no") print "unblocked"; else if (soft || hard) print "blocked"; else print "unavailable"}')

alsa_cards=$(sed -n 's/^ [0-9][[:space:]].*/card/p' /proc/asound/cards 2>/dev/null | wc -l | tr -d ' ')
playback_devices=$(grep -c ': playback' /proc/asound/pcm 2>/dev/null || true)
capture_devices=$(grep -c ': capture' /proc/asound/pcm 2>/dev/null || true)
pipewire_status=$(wpctl status 2>/dev/null || true)
pipewire_devices=$(printf '%s\n' "$pipewire_status" | awk '/^Audio$/{audio=1; next} /^Video$/{audio=0; section=""} audio && /Devices:/{section="device"; next} audio && /Sinks:/{section="sink"; next} audio && /Sources:/{section="source"; next} audio && /Filters:/{section=""} section == "device" && /[0-9]+\./ {n++} END {print n+0}')
pipewire_sinks=$(printf '%s\n' "$pipewire_status" | awk '/^Audio$/{audio=1; next} /^Video$/{audio=0; section=""} audio && /Sinks:/{section="sink"; next} audio && /Sources:|Filters:/{section=""} section == "sink" && /[0-9]+\./ {n++} END {print n+0}')
pipewire_sources=$(printf '%s\n' "$pipewire_status" | awk '/^Audio$/{audio=1; next} /^Video$/{audio=0; section=""} audio && /Sources:/{section="source"; next} audio && /Filters:/{section=""} section == "source" && /[0-9]+\./ {n++} END {print n+0}')
video_nodes=$(find /dev -maxdepth 1 -name 'video[0-9]*' 2>/dev/null | wc -l | tr -d ' ')
media_nodes=$(find /dev -maxdepth 1 -name 'media[0-9]*' 2>/dev/null | wc -l | tr -d ' ')
video_names=$(names_from_files /sys/class/video4linux/video*/name)
iio_devices=$(find /sys/bus/iio/devices -maxdepth 1 -name 'iio:device*' 2>/dev/null | wc -l | tr -d ' ')
iio_names=$(names_from_files /sys/bus/iio/devices/iio:device*/name)
remoteproc_states=$(for directory in /sys/class/remoteproc/remoteproc*; do
  [ -f "$directory/name" ] || continue
  name=$(tr -d '\n' <"$directory/name" | sed 's/[[:space:]][[:space:]]*/ /g; s/^ //; s/ $//')
  state=$(tr -d '\n' <"$directory/state" 2>/dev/null | sed 's/[[:space:]][[:space:]]*/ /g; s/^ //; s/ $//' || printf unknown)
  printf '%s:%s,' "$name" "$state"
done | sed 's/,$//')

modem_count=$(mmcli -L 2>/dev/null | grep -c '/Modem/' || true)
modem_state=$(mmcli -m 0 -K 2>/dev/null | sed -n 's/^modem.generic.state[[:space:]]*:[[:space:]]*//p' | tail -n 1)
qrtr_version=$(rpm -q --qf '%{NAME}-%{EVR}.%{ARCH}' qrtr 2>/dev/null || printf unavailable)
qrtr_sockets=$(ss -a -f qrtr 2>/dev/null | sed '1d; /^[[:space:]]*$/d' | wc -l | tr -d ' ')
rmtfs_state=$(service_state rmtfs.service)
tqftp_state=$(service_state tqftpserv.service)
modemmanager_state=$(service_state ModemManager.service)
rmtfs_version=$(rpm -q --qf '%{NAME}-%{EVR}.%{ARCH}' rmtfs 2>/dev/null || printf unavailable)
study_partition=$(test -e /dev/disk/by-partlabel/study && printf true || printf false)
rmtfs_study_mapping=$(strings /usr/bin/rmtfs 2>/dev/null | grep -qx '/boot/modem_study' && printf true || printf false)

battery_dir=/sys/class/power_supply/qcom-battmgr-bat
battery_status=$(cat "$battery_dir/status" 2>/dev/null || true)
battery_health=$(cat "$battery_dir/health" 2>/dev/null || true)
battery_capacity=$(cat "$battery_dir/capacity" 2>/dev/null || true)
battery_temp=$(cat "$battery_dir/temp" 2>/dev/null || true)
thermal_range=$(for zone in /sys/class/thermal/thermal_zone*; do cat "$zone/temp" 2>/dev/null; done | awk 'NR == 1 {min=$1; max=$1} $1 < min {min=$1} $1 > max {max=$1} END {if (NR) printf "%s-%s", min, max; else printf "unavailable"}')

printf 'LUMA_FP6_FEDORA_INVENTORY_VERSION=1\n'
printf 'PHONE_ACCESSED=true\n'
printf 'MUTATING_OPERATIONS=none\n'
printf 'P5_INSTALL_AUTHORIZED=false\n'
printf 'DT_MODEL=%s\n' "${dt_model:-unavailable}"
printf 'OS_ID=%s\n' "${os_id:-unavailable}"
printf 'OS_VERSION_ID=%s\n' "${os_version:-unavailable}"
printf 'KERNEL_RELEASE=%s\n' "${kernel_release:-unavailable}"
printf 'SYSTEM_STATE=%s\n' "${system_state:-unavailable}"
printf 'SYSTEM_FAILED_UNITS=%s\n' "$system_failed"
printf 'GRAPHICAL_TARGET=%s\n' "${graphical_state:-unavailable}"
printf 'PHOSH_PROCESSES=%s\n' "$phosh_count"
printf 'PHOC_PROCESSES=%s\n' "$phoc_count"
printf 'PLYMOUTH_PROCESSES=%s\n' "$plymouth_count"
printf 'SEAT0_USER_SESSIONS=%s\n' "$wayland_sessions"
printf 'SELINUX_STATE=%s\n' "$selinux_state"
printf 'DISPLAY_STATUS=%s\n' "${display_status:-unavailable}"
printf 'DISPLAY_MODES=%s\n' "${display_modes:-unavailable}"
printf 'BRIGHTNESS=%s\n' "${brightness:-unavailable}"
printf 'MAX_BRIGHTNESS=%s\n' "${max_brightness:-unavailable}"
printf 'GPU_SQE_FIRMWARE_PRESENT=%s\n' "$sqe_present"
printf 'GPU_GMU_FIRMWARE_PRESENT=%s\n' "$gmu_present"
printf 'GPU_ZAP_FIRMWARE_PRESENT=%s\n' "$zap_present"
printf 'TOUCH_DEVICE=%s\n' "${touch_name:-unavailable}"
printf 'HAPTIC_DEVICE=%s\n' "${haptic_name:-unavailable}"
printf 'USB_NETWORK_STATE=%s\n' "$usb_state"
printf 'WIFI_STATE=%s\n' "$wifi_state"
printf 'BLUETOOTH_STATE=%s\n' "${bluetooth_state:-unavailable}"
printf 'ALSA_CARDS=%s\n' "$alsa_cards"
printf 'ALSA_PLAYBACK_DEVICES=%s\n' "$playback_devices"
printf 'ALSA_CAPTURE_DEVICES=%s\n' "$capture_devices"
printf 'PIPEWIRE_AUDIO_DEVICES=%s\n' "$pipewire_devices"
printf 'PIPEWIRE_SINKS=%s\n' "$pipewire_sinks"
printf 'PIPEWIRE_SOURCES=%s\n' "$pipewire_sources"
printf 'VIDEO_NODES=%s\n' "$video_nodes"
printf 'MEDIA_NODES=%s\n' "$media_nodes"
printf 'VIDEO_NODE_NAMES=%s\n' "${video_names:-unavailable}"
printf 'IIO_DEVICES=%s\n' "$iio_devices"
printf 'IIO_DEVICE_NAMES=%s\n' "${iio_names:-unavailable}"
printf 'REMOTEPROC_STATES=%s\n' "${remoteproc_states:-unavailable}"
printf 'MODEM_COUNT=%s\n' "$modem_count"
printf 'MODEM_STATE=%s\n' "${modem_state:-unavailable}"
printf 'QRTR_PACKAGE=%s\n' "$qrtr_version"
printf 'QRTR_SOCKET_COUNT=%s\n' "$qrtr_sockets"
printf 'RMTFS_SERVICE=%s\n' "$rmtfs_state"
printf 'RMTFS_PACKAGE=%s\n' "$rmtfs_version"
printf 'STUDY_PARTITION_PRESENT=%s\n' "$study_partition"
printf 'RMTFS_MODEM_STUDY_MAPPING=%s\n' "$rmtfs_study_mapping"
printf 'TQFTPSERV_SERVICE=%s\n' "$tqftp_state"
printf 'MODEMMANAGER_SERVICE=%s\n' "$modemmanager_state"
printf 'BATTERY_STATUS=%s\n' "${battery_status:-unavailable}"
printf 'BATTERY_HEALTH=%s\n' "${battery_health:-unavailable}"
printf 'BATTERY_CAPACITY=%s\n' "${battery_capacity:-unavailable}"
printf 'BATTERY_TEMP_TENTHS_C=%s\n' "${battery_temp:-unavailable}"
printf 'THERMAL_RANGE_MILLIC=%s\n' "$thermal_range"
printf 'ERROR_MODEM_RMTS_IOVEC=%s\n' "$(count_pattern 'rmts_read_iovec failed')"
printf 'MODEM_CRASH_COUNT=%s\n' "$(count_pattern 'handling crash.*in modem')"
printf 'ERROR_ESWIN_SPI_TIMEOUT=%s\n' "$(count_pattern 'eswin_eph861x.*Failed to read expected response')"
printf 'ERROR_GPU_FIRMWARE=%s\n' "$(count_pattern 'failed to load gen80300')"
printf 'ERROR_GPU_LOCKUP=%s\n' "$(count_pattern 'hangcheck detected gpu lockup')"
printf 'ERROR_GPU_RECOVERY=%s\n' "$(count_pattern 'a8xx_recover\|GMU OOB set')"
printf 'ERROR_DPU=%s\n' "$(count_pattern '\[dpu error\]\|no encoder found for crtc')"
REMOTE

grep -qx 'LUMA_FP6_FEDORA_INVENTORY_VERSION=1' "$tmp_report" || \
  die 'remote output is not a Luma Fedora inventory report'
grep -qx 'OS_ID=fedora' "$tmp_report" || die 'remote userspace is not Fedora'
grep -qx 'OS_VERSION_ID=44' "$tmp_report" || die 'remote Fedora release is not 44'
grep -Eqi '^DT_MODEL=.*Fairphone.*(Gen[.]? 6|6)' "$tmp_report" || \
  die 'remote device-tree identity is not Fairphone 6'
if grep -Eqi '(serial|imei|imsi|ssid|bssid|mac(_|-)address|wifi.*address|192[.]168[.])' "$tmp_report"; then
  die 'report contains a forbidden device or network identifier'
fi

mkdir -p "$output_root"
install -m 0600 "$tmp_report" "$output_root/fedora-hardware.env"
{
  printf 'LUMA_FP6_FEDORA_INVENTORY_SUMMARY_VERSION=1\n'
  printf 'IDENTITY=fp6-fedora44-confirmed\n'
  printf 'TRANSPORT=ssh\n'
  printf 'PHONE_ACCESSED=true\n'
  printf 'MUTATING_OPERATIONS=none\n'
  printf 'P5_INSTALL_AUTHORIZED=false\n'
} >"$output_root/summary.env"
chmod 0600 "$output_root/summary.env"

printf 'FP6 read-only Fedora hardware inventory complete: %s\n' "$output_root"
