#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Read-only acceptance collector for the FP6 sensor candidate. It does not load
# modules, claim D-Bus sensors, restart services, alter sysfs, or change power.

set -euo pipefail

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d ' ' -f 1; }

[ "$(id -u)" -eq 0 ] || die 'run as root on the FP6'
[ "$(uname -r)" = 7.1.2 ] || die 'kernel release differs'
[ "$(tr -d '\000' </proc/device-tree/model)" = 'The Fairphone (Gen. 6)' ] ||
  die 'device identity differs'
for tool in basename busctl cat cut dirname find grep head journalctl lsmod \
  modinfo sed sha256sum sort systemctl tr uname wc; do
  command -v "$tool" >/dev/null 2>&1 || die "missing tool: $tool"
done

files=(i2c-algo-bit i2c-gpio spi-bitbang spi-gpio kfifo_buf inv_sensors_timestamp inv-icm42600 inv-icm42600-spi stk3310 qmc6308 dps310)
module_ids=(i2c_algo_bit i2c_gpio spi_bitbang spi_gpio kfifo_buf inv_sensors_timestamp inv_icm42600 inv_icm42600_spi stk3310 qmc6308 dps310)
hashes=(
  1b2507245744c4e9a2ebfbce5d4dbf3e890aec912e04cfd9b1dce0f3b694efcb
  ed0e71db2e38e4d48cc208767fa9b5b245d0fa1bfb4b3180a4ea13652d994c23
  cf4849c3b9339c4b4700c9a00054dcb0ec62dac4fecef70b6b50440c6a7d8259
  c7d972e0fbd52714a4eb6634407f55885f59a6db90f722f118acda86cff98434
  4a745b5a4e77eb92ea5d12063cb3815937c8a0019095cdbcfdf5f4af235eff0c
  1d03eeb67b32c442aba70e1f14a7d3073a744ce09b12ad2ef118f4372a3dcafd
  2747fc085cf90d1eaa7836a66fb4461083df570bcc070f600e089fc3fe058da4
  aa498e91897e357082826383dafc9c385d0b878a5d882cd505e3a6a2ca8deb65
  b4df1073fe0505a28203b146024e7fe7024bc3e43f94ef6dabe6c331cb1bf47d
  d11e0e93ba119073afa7c9fe1eeab679dfdcfa533b7c2b49ac6ca68adb76f21f
  59f897297bb4f9033800e6a14429e8163b50bd40bbb6bcd960e18bdc9cd63487
)

printf 'LUMA_FP6_SENSOR_INSPECTION_VERSION=1\n'
printf 'BOOT_ID=%s\n' "$(cat /proc/sys/kernel/random/boot_id)"
printf 'MODEL=%s\n' "$(tr -d '\000' </proc/device-tree/model)"
printf 'KERNEL=%s\n' "$(uname -r)"

for index in "${!module_ids[@]}"; do
  path=$(modinfo -n "${module_ids[$index]}")
  [ -f "$path" ] || die "selected module is missing: ${module_ids[$index]}"
  [ "$(hash "$path")" = "${hashes[$index]}" ] || die "selected module differs: ${module_ids[$index]}"
  lsmod | grep -q "^${module_ids[$index]} " || die "module is not loaded: ${module_ids[$index]}"
  printf 'MODULE_%s=%s\n' "$(printf '%s' "${module_ids[$index]}" | tr '[:lower:]-' '[:upper:]_')" "$path"
done

for node in \
  /proc/device-tree/i2c-sensors/magnetometer@2c/compatible \
  /proc/device-tree/i2c-sensors/light-sensor@48/compatible \
  /proc/device-tree/i2c-sensors/pressure-sensor@76/compatible \
  /proc/device-tree/spi-imu/imu@0/compatible; do
  [ -r "$node" ] || die "device-tree node is missing: $node"
  printf 'DT_%s=' "$(basename "$(dirname "$node")" | tr '[:lower:]@-' '[:upper:]__')"
  tr '\000' ',' <"$node" | sed 's/,$//'
  printf '\n'
done

iio_count=0
iio_names=
for device in /sys/bus/iio/devices/iio:device*; do
  [ -d "$device" ] || continue
  name=$(cat "$device/name")
  iio_count=$((iio_count + 1))
  iio_names=${iio_names:+$iio_names,}$name
  printf 'IIO_DEVICE=%s NAME=%s\n' "${device##*/}" "$name"
  for value in "$device"/in_*_raw "$device"/in_*_input "$device"/in_*_scale; do
    [ -r "$value" ] || continue
    printf '  %s=' "${value##*/}"
    if reading=$(cat "$value" 2>&1); then
      printf '%s\n' "$reading"
    else
      printf 'unavailable (%s)\n' "$reading"
    fi
  done
  if [ -r "$device/buffer/enable" ]; then
    printf '  buffer_enable='
    cat "$device/buffer/enable"
  fi
done
[ "$iio_count" -ge 5 ] || die "expected at least five IIO devices, found $iio_count"
for expected in qmc6308 stk36c61 spl07-003 icm42630-accel icm42630-gyro; do
  printf '%s\n' "$iio_names" | tr ',' '\n' | grep -Fxq "$expected" || die "IIO device is missing: $expected"
done
printf 'IIO_COUNT=%s\nIIO_NAMES=%s\n' "$iio_count" "$iio_names"

proxy_state=$(systemctl is-active iio-sensor-proxy.service 2>/dev/null || true)
printf 'SENSOR_PROXY_STATE=%s\n' "$proxy_state"
if [ "$proxy_state" = active ]; then
  busctl --system introspect net.hadess.SensorProxy /net/hadess/SensorProxy 2>/dev/null || true
  for property in HasAccelerometer AccelerometerOrientation HasAmbientLight \
    LightLevel HasProximity ProximityNear; do
    printf 'SENSOR_PROXY_%s=' "$(printf '%s' "$property" | tr '[:lower:]' '[:upper:]')"
    busctl --system get-property net.hadess.SensorProxy /net/hadess/SensorProxy \
      net.hadess.SensorProxy "$property" 2>&1 || true
  done
fi
printf 'FAILED_UNITS=%s\n' "$(systemctl --failed --no-legend --no-pager | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')"
printf 'MAX_TEMP_MILLIC=%s\n' "$(for value in /sys/class/thermal/thermal_zone*/temp; do cat "$value" 2>/dev/null; done | sort -nr | head -1)"
printf '%s\n' 'KERNEL_SENSOR_FAULTS_BEGIN'
journalctl -k -b --no-pager | grep -Ei 'iio|icm426|qmc6308|stk33|stk36|dps310|spl07|i2c-gpio|spi-gpio' | tail -200 || true
printf '%s\n' 'KERNEL_SENSOR_FAULTS_END'
printf 'MUTATING_OPERATIONS=none\n'
