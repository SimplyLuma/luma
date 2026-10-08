#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Privacy-preserving physical GNSS acceptance.  Exact coordinates are read
# only inside this process to determine whether both latitude and longitude
# exist; their values are never printed or persisted.  GPS is disabled and the
# original refresh restored on every exit path.

set -euo pipefail

duration=${1:-300}
case $duration in
  ''|*[!0-9]*) printf 'error: duration must be an integer in seconds\n' >&2; exit 2 ;;
esac
[ "$duration" -ge 60 ] && [ "$duration" -le 900 ] || {
  printf 'error: duration must be between 60 and 900 seconds\n' >&2
  exit 2
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die 'run as root on the FP6'
[ "$(tr -d '\000' </proc/device-tree/model)" = 'The Fairphone (Gen. 6)' ] ||
  die 'device identity differs'
[ "$(uname -r)" = 7.1.2 ] || die 'kernel release differs'
for command in awk cat date env grep head journalctl mmcli rpm sed seq sleep \
  sort systemctl systemd-inhibit timeout tr uname wc; do
  command -v "$command" >/dev/null 2>&1 || die "missing tool: $command"
done

[ "$(rpm -q --qf '%{EVR}' libqmi)" = 1.39.1-0.1.luma1.fc44 ] ||
  die 'installed libqmi differs'
[ "$(rpm -q --qf '%{EVR}' libqmi-utils)" = 1.39.1-0.1.luma1.fc44 ] ||
  die 'installed libqmi-utils differs'
[ "$(rpm -q --qf '%{EVR}' ModemManager)" = 1.25.95-0.1.luma1.fc44 ] ||
  die 'installed ModemManager differs'
[ "$(rpm -q --qf '%{EVR}' ModemManager-glib)" = 1.25.95-0.1.luma1.fc44 ] ||
  die 'installed ModemManager-glib differs'
[ "$(systemctl is-active ModemManager.service)" = active ] ||
  die 'ModemManager is not active'

# Keep the display free to blank while preventing automatic system suspend
# from removing the modem halfway through the bounded acquisition window.
if [ "${LUMA_FP6_GNSS_SLEEP_INHIBITED:-false}" != true ]; then
  exec systemd-inhibit --what=sleep --mode=block \
    --why='Luma bounded FP6 GNSS fix acceptance' \
    env LUMA_FP6_GNSS_SLEEP_INHIBITED=true "$0" "$duration"
fi

modem_list=$(timeout 10 mmcli -L 2>/dev/null || true)
modem_count=$(printf '%s\n' "$modem_list" | grep -c '/Modem/' || true)
[ "$modem_count" -eq 1 ] || die "expected exactly one modem, found $modem_count"
modem_index=$(printf '%s\n' "$modem_list" |
  sed -n 's|.*/Modem/\([0-9][0-9]*\).*|\1|p')
[ -n "$modem_index" ] || die 'could not determine modem index'

start_epoch=$(date +%s)
start_time=$(date -Is)
cleanup() {
  if mmcli -L 2>/dev/null | grep -q '/Modem/'; then
    mmcli -m "$modem_index" --location-disable-gps-raw \
      --location-disable-gps-nmea >/dev/null 2>&1 || true
    mmcli -m "$modem_index" --location-set-gps-refresh-rate=30 \
      >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT HUP INT TERM

mmcli -m "$modem_index" --location-enable-gps-raw \
  --location-enable-gps-nmea >/dev/null
mmcli -m "$modem_index" --location-set-gps-refresh-rate=5 >/dev/null

printf 'LUMA_FP6_GNSS_FIX_TEST_VERSION=1\n'
printf 'SLEEP_INHIBITED=true\n'
printf 'COORDINATES_PRINTED=false\n'
printf 'COORDINATES_PERSISTED=false\n'
printf 'DURATION_LIMIT_SECONDS=%s\n' "$duration"

fix=false
nmea_seen=false
modem_present=true
iterations=$((duration / 10))
for iteration in $(seq 1 "$iterations"); do
  # Deliberately keep the location payload in memory only.  The awk process
  # reports presence of both coordinate fields, never their values.
  location=$(timeout 8 mmcli -m "$modem_index" --location-get -K 2>/dev/null || true)
  raw_fix=$(printf '%s\n' "$location" | awk -F: '
    /^modem[.]location[.]gps[.]raw[.](latitude|longitude)[[:space:]]*:/ {
      value=$2
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      if (value != "" && value != "--" && value != "unknown") found++
    }
    END { print found >= 2 ? "true" : "false" }
  ')
  if printf '%s\n' "$location" | grep -q '^modem[.]location[.]gps[.]nmea'; then
    nmea_seen=true
  fi
  if ! mmcli -L 2>/dev/null | grep -q '/Modem/'; then
    modem_present=false
  fi
  if [ "$raw_fix" = true ]; then
    fix=true
    break
  fi
  [ "$modem_present" = true ] || break
  sleep 10
done

cleanup
trap - EXIT HUP INT TERM
elapsed=$(($(date +%s) - start_epoch))
printf 'NMEA_STREAM_SEEN=%s\n' "$nmea_seen"
printf 'MODEM_PRESENT_AFTER_POLL=%s\n' "$modem_present"
printf 'FIX_ACCEPTED=%s\n' "$fix"
printf 'TIME_TO_FIX_SECONDS=%s\n' "$elapsed"
printf 'MAX_TEMP_MILLIC='; for value in /sys/class/thermal/thermal_zone*/temp; do
  cat "$value" 2>/dev/null
done | sort -nr | head -1
printf 'FAILED_UNITS='; systemctl --failed --no-legend --no-pager |
  sed '/^[[:space:]]*$/d' | wc -l
printf 'NEW_GNSS_OR_SYSTEM_FAULTS='; journalctl -k --since "$start_time" --no-pager |
  grep -Eic 'qrtr.*(fail|error)|qmi.*(fail|error)|gnss.*(fail|error)|gps.*(fail|error)|BUG:|Oops|hangcheck|GMU.*timeout|thermal.*(critical|shutdown)' || true
printf 'PERSISTENT_MUTATIONS=none\n'

[ "$fix" = true ] || exit 3
