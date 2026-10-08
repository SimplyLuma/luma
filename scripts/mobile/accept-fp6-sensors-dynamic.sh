#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Bounded physical acceptance collector for the FP6 motion/environment sensor
# stack.  It temporarily claims accelerometer and light through the already
# running iio-sensor-proxy, samples the remaining raw channels, then exits.
# It never changes configuration, modules, services, networking, boot state,
# or partitions.

set -euo pipefail
umask 077

duration=${1:-40}
capture_mode=${2:-proxy}
case $duration in
  ''|*[!0-9]*) printf 'error: duration must be an integer in seconds\n' >&2; exit 2 ;;
esac
[ "$duration" -ge 10 ] && [ "$duration" -le 120 ] || {
  printf 'error: duration must be between 10 and 120 seconds\n' >&2
  exit 2
}
case $capture_mode in
  proxy|raw) ;;
  *) printf 'error: capture mode must be proxy or raw\n' >&2; exit 2 ;;
esac

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
find_iio() {
  local expected=$1 device
  for device in /sys/bus/iio/devices/iio:device*; do
    [ -d "$device" ] || continue
    if [ "$(cat "$device/name")" = "$expected" ]; then
      printf '%s\n' "$device"
      return 0
    fi
  done
  return 1
}
update_min_max() {
  local value=$1 min_name=$2 max_name=$3
  if [ "$value" -lt "${!min_name}" ]; then printf -v "$min_name" '%s' "$value"; fi
  if [ "$value" -gt "${!max_name}" ]; then printf -v "$max_name" '%s' "$value"; fi
}

[ "$(id -u)" -eq 0 ] || die 'run as root on the FP6'
[ "$(uname -r)" = 7.1.2 ] || die 'kernel release differs'
[ "$(tr -d '\000' </proc/device-tree/model)" = 'The Fairphone (Gen. 6)' ] ||
  die 'device identity differs'
for tool in awk cat date grep head journalctl mktemp monitor-sensor paste rm \
  sed sleep sort stdbuf systemctl timeout tr uname wc; do
  command -v "$tool" >/dev/null 2>&1 || die "missing tool: $tool"
done
[ "$(systemctl is-active iio-sensor-proxy.service 2>/dev/null)" = active ] ||
  die 'iio-sensor-proxy is not already active'

als=$(find_iio stk36c61) || die 'STK36C61 is missing'
mag=$(find_iio qmc6308) || die 'QMC6308 is missing'
press=$(find_iio spl07-003) || die 'SPL07-003 is missing'
accel=$(find_iio icm42630-accel) || die 'ICM-42630 accelerometer is missing'
gyro=$(find_iio icm42630-gyro) || die 'ICM-42630 gyroscope is missing'

monitor_log=$(mktemp /tmp/luma-fp6-sensor-monitor.XXXXXX)
monitor_pid=
cleanup() {
  if [ -n "$monitor_pid" ]; then
    wait "$monitor_pid" 2>/dev/null || true
  fi
  rm -f -- "$monitor_log"
}
trap cleanup EXIT HUP INT TERM

start_time=$(date -Is)
printf 'LUMA_FP6_DYNAMIC_SENSOR_ACCEPTANCE_VERSION=1\n'
printf 'BOOT_ID=%s\n' "$(cat /proc/sys/kernel/random/boot_id)"
printf 'DURATION_SECONDS=%s\n' "$duration"
printf 'CAPTURE_MODE=%s\n' "$capture_mode"
printf 'OWNER_ACTION=rotate_portrait_landscape_and_cover_sensor\n'
printf 'COUNTDOWN_SECONDS=5\n'
sleep 5

# monitor-sensor performs bounded D-Bus Claim/Release calls. timeout ends the
# consumer even if no physical event arrives; no service is restarted.
if [ "$capture_mode" = proxy ]; then
  timeout --signal=TERM --kill-after=2 "$duration" \
    stdbuf -oL -eL monitor-sensor --accel --light >"$monitor_log" 2>&1 &
  monitor_pid=$!
fi

prox_min=2147483647; prox_max=-2147483648
light_min=2147483647; light_max=-2147483648
mag_x_min=2147483647; mag_x_max=-2147483648
mag_y_min=2147483647; mag_y_max=-2147483648
mag_z_min=2147483647; mag_z_max=-2147483648
accel_x_min=2147483647; accel_x_max=-2147483648
accel_y_min=2147483647; accel_y_max=-2147483648
accel_z_min=2147483647; accel_z_max=-2147483648
gyro_x_min=2147483647; gyro_x_max=-2147483648
gyro_y_min=2147483647; gyro_y_max=-2147483648
gyro_z_min=2147483647; gyro_z_max=-2147483648
pressure_min=999999999; pressure_max=-1
samples=0
sample_deadline=$(($(date +%s) + duration))
while [ "$(date +%s)" -lt "$sample_deadline" ]; do
  prox_value=$(cat "$als/in_proximity_raw")
  light_value=$(cat "$als/in_illuminance_raw")
  mag_x=$(cat "$mag/in_magn_x_raw")
  mag_y=$(cat "$mag/in_magn_y_raw")
  mag_z=$(cat "$mag/in_magn_z_raw")
  if [ "$capture_mode" = raw ]; then
    accel_x=$(cat "$accel/in_accel_x_raw")
    accel_y=$(cat "$accel/in_accel_y_raw")
    accel_z=$(cat "$accel/in_accel_z_raw")
    gyro_x=$(cat "$gyro/in_anglvel_x_raw")
    gyro_y=$(cat "$gyro/in_anglvel_y_raw")
    gyro_z=$(cat "$gyro/in_anglvel_z_raw")
  fi
  # Convert the decimal kPa value to integer micro-kPa for shell-safe ranges.
  pressure_value=$(awk '{ printf "%.0f\n", $1 * 1000000 }' "$press/in_pressure_input")
  update_min_max "$prox_value" prox_min prox_max
  update_min_max "$light_value" light_min light_max
  update_min_max "$mag_x" mag_x_min mag_x_max
  update_min_max "$mag_y" mag_y_min mag_y_max
  update_min_max "$mag_z" mag_z_min mag_z_max
  if [ "$capture_mode" = raw ]; then
    update_min_max "$accel_x" accel_x_min accel_x_max
    update_min_max "$accel_y" accel_y_min accel_y_max
    update_min_max "$accel_z" accel_z_min accel_z_max
    update_min_max "$gyro_x" gyro_x_min gyro_x_max
    update_min_max "$gyro_y" gyro_y_min gyro_y_max
    update_min_max "$gyro_z" gyro_z_min gyro_z_max
  fi
  update_min_max "$pressure_value" pressure_min pressure_max
  samples=$((samples + 1))
  # The QMC6308 direct-mode driver performs one conversion per axis read, so
  # the conversions above—not a fixed sample count—own most of this cadence.
  # Use a wall-clock deadline to keep the owner's physical test truly bounded.
  sleep 0.1
done
if [ -n "$monitor_pid" ]; then
  wait "$monitor_pid" 2>/dev/null || true
  monitor_pid=
fi

printf 'SAMPLES=%s\n' "$samples"
printf 'PROXIMITY_RAW_MIN=%s\nPROXIMITY_RAW_MAX=%s\n' "$prox_min" "$prox_max"
printf 'ILLUMINANCE_RAW_MIN=%s\nILLUMINANCE_RAW_MAX=%s\n' "$light_min" "$light_max"
printf 'MAGN_X_RAW_MIN=%s\nMAGN_X_RAW_MAX=%s\n' "$mag_x_min" "$mag_x_max"
printf 'MAGN_Y_RAW_MIN=%s\nMAGN_Y_RAW_MAX=%s\n' "$mag_y_min" "$mag_y_max"
printf 'MAGN_Z_RAW_MIN=%s\nMAGN_Z_RAW_MAX=%s\n' "$mag_z_min" "$mag_z_max"
if [ "$capture_mode" = raw ]; then
  printf 'ACCEL_X_RAW_MIN=%s\nACCEL_X_RAW_MAX=%s\n' "$accel_x_min" "$accel_x_max"
  printf 'ACCEL_Y_RAW_MIN=%s\nACCEL_Y_RAW_MAX=%s\n' "$accel_y_min" "$accel_y_max"
  printf 'ACCEL_Z_RAW_MIN=%s\nACCEL_Z_RAW_MAX=%s\n' "$accel_z_min" "$accel_z_max"
  printf 'GYRO_X_RAW_MIN=%s\nGYRO_X_RAW_MAX=%s\n' "$gyro_x_min" "$gyro_x_max"
  printf 'GYRO_Y_RAW_MIN=%s\nGYRO_Y_RAW_MAX=%s\n' "$gyro_y_min" "$gyro_y_max"
  printf 'GYRO_Z_RAW_MIN=%s\nGYRO_Z_RAW_MAX=%s\n' "$gyro_z_min" "$gyro_z_max"
else
  printf 'ACCEL_RAW_RANGES=claimed-by-iio-sensor-proxy\n'
  printf 'GYRO_RAW_RANGES=claimed-by-iio-sensor-proxy\n'
fi
printf 'PRESSURE_MICRO_KPA_MIN=%s\nPRESSURE_MICRO_KPA_MAX=%s\n' "$pressure_min" "$pressure_max"
printf 'MONITOR_SENSOR_BEGIN\n'
cat "$monitor_log"
printf 'MONITOR_SENSOR_END\n'
orientations=$(sed -n \
  -e 's/.*Accelerometer orientation changed: \([^,)]*\).*/\1/p' \
  -e 's/.*(orientation: \([^,)]*\),.*/\1/p' "$monitor_log" |
  sort -u | sed '/^[[:space:]]*$/d')
orientation_count=$(printf '%s\n' "$orientations" | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')
printf 'ORIENTATION_COUNT=%s\n' "$orientation_count"
printf 'ORIENTATIONS=%s\n' "$(printf '%s\n' "$orientations" | paste -sd, -)"
printf 'PROXIMITY_RANGE=%s\n' "$((prox_max - prox_min))"
printf 'ILLUMINANCE_RANGE=%s\n' "$((light_max - light_min))"
printf 'FAILED_UNITS=%s\n' "$(systemctl --failed --no-legend --no-pager | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')"
printf 'MAX_TEMP_MILLIC=%s\n' "$(for value in /sys/class/thermal/thermal_zone*/temp; do cat "$value" 2>/dev/null; done | sort -nr | head -1)"
printf 'NEW_SENSOR_FAULTS_BEGIN\n'
journalctl -k --since "$start_time" --no-pager | grep -Ei \
  'iio|icm426|qmc6308|stk33|stk36|dps310|spl07|BUG:|Oops|hangcheck|GMU.*timeout' || true
printf 'NEW_SENSOR_FAULTS_END\n'
printf 'PERSISTENT_MUTATIONS=none\n'
