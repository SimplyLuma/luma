#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Read-only native-Linux acceptance inventory for a Fairphone 6 already
# running the postmarketOS control image. The caller owns SSH authentication;
# this script never stores a password, key, host key, or network identifier.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
ssh_bin=${LUMA_SSH:-ssh}
ssh_target=${LUMA_FP6_SSH_TARGET:-user@172.16.42.1}
timestamp=${LUMA_FP6_CONTROL_TIMESTAMP:-$(date -u +%Y%m%dT%H%M%SZ)}
output_root=${LUMA_FP6_CONTROL_INVENTORY_DIR:-$repo_root/build/mobile/fp6-physical/control-inventory/$timestamp}
tmp_report=$(mktemp)
trap 'rm -f "$tmp_report"' EXIT

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

command -v "$ssh_bin" >/dev/null 2>&1 || die "SSH client is not installed: $ssh_bin"

"$ssh_bin" "$ssh_target" sh -s >"$tmp_report" <<'REMOTE'
set -u

value_or_unavailable() {
  if value=$($1 2>/dev/null); then
    printf '%s' "$value"
  else
    printf 'unavailable'
  fi
}

count_pattern() {
  pattern=$1
  count=$(journalctl -b --no-pager 2>/dev/null | grep -Ec "$pattern" || true)
  printf '%s' "$count"
}

dt_model=$(tr -d '\000' </proc/device-tree/model 2>/dev/null || true)
os_id=$(sed -n 's/^ID=//p' /etc/os-release 2>/dev/null | tr -d '"' | tail -n 1)
os_version=$(sed -n 's/^VERSION=//p' /etc/os-release 2>/dev/null | tr -d '"' | tail -n 1)
kernel_release=$(uname -r 2>/dev/null || true)
system_state=$(systemctl is-system-running 2>/dev/null || true)
system_failed=$(systemctl --failed --no-legend --no-pager 2>/dev/null | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')
user_failed=$(systemctl --user --failed --no-legend --no-pager 2>/dev/null | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')
graphical_state=$(systemctl is-active graphical.target 2>/dev/null || true)
phosh_count=$(pgrep -xc phosh 2>/dev/null || true)
phoc_count=$(pgrep -xc phoc 2>/dev/null || true)
boot_time=$(systemd-analyze 2>/dev/null | tr '\n' ' ' | sed 's/[[:space:]]*$//' || true)

display_status=$(cat /sys/class/drm/card0-DSI-1/status 2>/dev/null || true)
display_modes=$(tr '\n' ',' </sys/class/drm/card0-DSI-1/modes 2>/dev/null | sed 's/,$//' || true)
gpu_renderer=$(journalctl -b --no-pager 2>/dev/null | sed -n 's/.*GL renderer: //p' | tail -n 1)
sqe_loaded=$(journalctl -b --no-pager 2>/dev/null | grep -q 'loaded gen80300_sqe.fw from legacy location' && printf true || printf false)
gmu_loaded=$(journalctl -b --no-pager 2>/dev/null | grep -q 'loaded gen80300_gmu.bin from legacy location' && printf true || printf false)
touch_name=$(sed -n 's/^N: Name="\(.*Touchscreen.*\)"/\1/p' /proc/bus/input/devices 2>/dev/null | head -n 1)
touch_native=$(journalctl -b --no-pager 2>/dev/null | sed -n 's/.*Touchscreen resolution X\([0-9]*\)Y\([0-9]*\).*/\1x\2/p' | tail -n 1)

usb_state=$(cat /sys/class/net/usb0/operstate 2>/dev/null || printf absent)
wifi_state=$(cat /sys/class/net/wlan0/operstate 2>/dev/null || printf absent)
bluetooth_state=$(rfkill list bluetooth 2>/dev/null | awk '/Soft blocked:/ {soft=$3} /Hard blocked:/ {hard=$3} END {if (soft == "no" && hard == "no") print "unblocked"; else if (soft || hard) print "blocked"; else print "unavailable"}')

alsa_cards=$(sed -n 's/^ [0-9][[:space:]].*/card/p' /proc/asound/cards 2>/dev/null | wc -l | tr -d ' ')
playback_devices=$(aplay -l 2>/dev/null | grep -c '^card ' || true)
capture_devices=$(arecord -l 2>/dev/null | grep -c '^card ' || true)
pipewire_sinks=$(wpctl status 2>/dev/null | awk '/^[[:space:]]*├─ Sinks:/{section="sink"; next} /^[[:space:]]*├─ Sources:/{section="source"; next} /^[[:space:]]*[├└]─/{if ($0 !~ /Sinks:|Sources:/) section=""} section == "sink" && /[0-9]+\./ {n++} END {print n+0}')
pipewire_sources=$(wpctl status 2>/dev/null | awk '/^[[:space:]]*├─ Sources:/{section="source"; next} /^[[:space:]]*[├└]─/{if ($0 !~ /Sources:/) section=""} section == "source" && /[0-9]+\./ {n++} END {print n+0}')
video_nodes=$(find /dev -maxdepth 1 -name 'video[0-9]*' 2>/dev/null | wc -l | tr -d ' ')
media_nodes=$(find /dev -maxdepth 1 -name 'media[0-9]*' 2>/dev/null | wc -l | tr -d ' ')

modem_count=$(mmcli -L 2>/dev/null | grep -c '/Modem/' || true)
modem_state=$(mmcli -m 0 -K 2>/dev/null | sed -n 's/^modem.generic.state[[:space:]]*:[[:space:]]*//p' | tail -n 1)
modem_reason=$(mmcli -m 0 -K 2>/dev/null | sed -n 's/^modem.generic.state-failed-reason[[:space:]]*:[[:space:]]*//p' | tail -n 1)
gnss_state=$(systemctl show modem-gnss.service -p ActiveState -p SubState -p Result --value 2>/dev/null | tr '\n' ',' | sed 's/,$//' || true)
iio_devices=$(find /sys/bus/iio/devices -maxdepth 1 -name 'iio:device*' 2>/dev/null | wc -l | tr -d ' ')
haptic_name=$(sed -n 's/^N: Name="\(.*haptic.*\)"/\1/p' /proc/bus/input/devices 2>/dev/null | head -n 1)

battery_dir=/sys/class/power_supply/qcom-battmgr-bat
battery_status=$(cat "$battery_dir/status" 2>/dev/null || true)
battery_health=$(cat "$battery_dir/health" 2>/dev/null || true)
battery_capacity=$(cat "$battery_dir/capacity" 2>/dev/null || true)
battery_temp=$(cat "$battery_dir/temp" 2>/dev/null || true)
thermal_range=$(for zone in /sys/class/thermal/thermal_zone*; do cat "$zone/temp" 2>/dev/null; done | awk 'NR == 1 {min=$1; max=$1} $1 < min {min=$1} $1 > max {max=$1} END {if (NR) printf "%s-%s", min, max; else printf "unavailable"}')
brightness=$(cat /sys/class/backlight/*/actual_brightness 2>/dev/null | head -n 1 || true)
max_brightness=$(cat /sys/class/backlight/*/max_brightness 2>/dev/null | head -n 1 || true)

printf 'LUMA_FP6_CONTROL_ACCEPTANCE_VERSION=1\n'
printf 'PHONE_ACCESSED=true\n'
printf 'MUTATING_OPERATIONS=none\n'
printf 'P5_AUTHORIZED=false\n'
printf 'DT_MODEL=%s\n' "${dt_model:-unavailable}"
printf 'OS_ID=%s\n' "${os_id:-unavailable}"
printf 'OS_VERSION=%s\n' "${os_version:-unavailable}"
printf 'KERNEL_RELEASE=%s\n' "${kernel_release:-unavailable}"
printf 'SYSTEM_STATE=%s\n' "${system_state:-unavailable}"
printf 'SYSTEM_FAILED_UNITS=%s\n' "$system_failed"
printf 'USER_FAILED_UNITS=%s\n' "$user_failed"
printf 'GRAPHICAL_TARGET=%s\n' "${graphical_state:-unavailable}"
printf 'PHOSH_PROCESSES=%s\n' "$phosh_count"
printf 'PHOC_PROCESSES=%s\n' "$phoc_count"
printf 'BOOT_TIMING=%s\n' "${boot_time:-unavailable}"
printf 'DISPLAY_STATUS=%s\n' "${display_status:-unavailable}"
printf 'DISPLAY_MODES=%s\n' "${display_modes:-unavailable}"
printf 'GPU_RENDERER=%s\n' "${gpu_renderer:-unavailable}"
printf 'GPU_SQE_LOADED_FROM_ROOTFS=%s\n' "$sqe_loaded"
printf 'GPU_GMU_LOADED_FROM_ROOTFS=%s\n' "$gmu_loaded"
printf 'TOUCH_DEVICE=%s\n' "${touch_name:-unavailable}"
printf 'TOUCH_NATIVE_SIZE=%s\n' "${touch_native:-unavailable}"
printf 'USB_NETWORK_STATE=%s\n' "$usb_state"
printf 'WIFI_STATE=%s\n' "$wifi_state"
printf 'BLUETOOTH_STATE=%s\n' "${bluetooth_state:-unavailable}"
printf 'ALSA_CARDS=%s\n' "$alsa_cards"
printf 'ALSA_PLAYBACK_DEVICES=%s\n' "$playback_devices"
printf 'ALSA_CAPTURE_DEVICES=%s\n' "$capture_devices"
printf 'PIPEWIRE_SINKS=%s\n' "$pipewire_sinks"
printf 'PIPEWIRE_SOURCES=%s\n' "$pipewire_sources"
printf 'VIDEO_NODES=%s\n' "$video_nodes"
printf 'MEDIA_NODES=%s\n' "$media_nodes"
printf 'MODEM_COUNT=%s\n' "$modem_count"
printf 'MODEM_STATE=%s\n' "${modem_state:-unavailable}"
printf 'MODEM_FAILED_REASON=%s\n' "${modem_reason:-unavailable}"
printf 'GNSS_STATE=%s\n' "${gnss_state:-unavailable}"
printf 'IIO_DEVICES=%s\n' "$iio_devices"
printf 'HAPTIC_DEVICE=%s\n' "${haptic_name:-unavailable}"
printf 'BATTERY_STATUS=%s\n' "${battery_status:-unavailable}"
printf 'BATTERY_HEALTH=%s\n' "${battery_health:-unavailable}"
printf 'BATTERY_CAPACITY=%s\n' "${battery_capacity:-unavailable}"
printf 'BATTERY_TEMP_TENTHS_C=%s\n' "${battery_temp:-unavailable}"
printf 'THERMAL_RANGE_MILLIC=%s\n' "$thermal_range"
printf 'BRIGHTNESS=%s\n' "${brightness:-unavailable}"
printf 'MAX_BRIGHTNESS=%s\n' "${max_brightness:-unavailable}"
printf 'ERROR_REMOTEproc_HANDOVER=%s\n' "$(count_pattern 'Handover signaled, but it already happened')"
printf 'ERROR_GNSS_SETUP=%s\n' "$(count_pattern 'Failed to start Qualcomm GNSS Modem Setup')"
printf 'ERROR_BRIGHTNESS=%s\n' "$(count_pattern 'Failed to write brightness\|Setting backlight.*failed')"
printf 'ERROR_AUDIO_PROFILE=%s\n' "$(count_pattern 'Failed to find a working profile')"
printf 'ERROR_DPU=%s\n' "$(count_pattern '\[dpu error\]\|no encoder found for crtc')"
REMOTE

grep -qx 'LUMA_FP6_CONTROL_ACCEPTANCE_VERSION=1' "$tmp_report" || \
  die 'remote output is not a Luma control-acceptance report'
grep -qx 'OS_ID=postmarketos' "$tmp_report" || \
  die 'remote userspace is not the postmarketOS control image'
grep -Eqi '^DT_MODEL=.*Fairphone.*(Gen[.]? 6|6)' "$tmp_report" || \
  die 'remote device-tree identity is not Fairphone 6'
if grep -Eqi '(serial|imei|imsi|ssid|bssid|mac(_|-)address|wifi.*address|192[.]168[.])' "$tmp_report"; then
  die 'report contains a forbidden device or network identifier'
fi

mkdir -p "$output_root"
install -m 0600 "$tmp_report" "$output_root/control-acceptance.env"
{
  printf 'LUMA_FP6_CONTROL_INVENTORY_VERSION=1\n'
  printf 'IDENTITY=fp6-confirmed\n'
  printf 'TRANSPORT=ssh\n'
  printf 'PHONE_ACCESSED=true\n'
  printf 'MUTATING_OPERATIONS=none\n'
  printf 'P5_AUTHORIZED=false\n'
} >"$output_root/summary.env"
chmod 0600 "$output_root/summary.env"

printf 'FP6 read-only control acceptance inventory complete: %s\n' "$output_root"
