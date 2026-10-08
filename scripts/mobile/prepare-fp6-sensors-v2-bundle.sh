#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Merge the exact ICM-42630 identity/autoload fix into the dependency-complete
# sensor v1 bundle.  This is an offline artifact operation: it cannot contact
# a phone, load a module, reboot, or write a partition.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-sensors.env"

base=${1:?usage: prepare-fp6-sensors-v2-bundle.sh V1_BUNDLE ICM42630_FIX OUTPUT_DIR}
fix=${2:?usage: prepare-fp6-sensors-v2-bundle.sh V1_BUNDLE ICM42630_FIX OUTPUT_DIR}
output=${3:?usage: prepare-fp6-sensors-v2-bundle.sh V1_BUNDLE ICM42630_FIX OUTPUT_DIR}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d ' ' -f 1; }

[ -d "$base/modules" ] && [ -f "$base/manifest.env" ] || die 'v1 bundle is incomplete'
[ -d "$fix/modules" ] && [ -f "$fix/manifest.env" ] || die 'ICM-42630 fix is incomplete'
[ ! -e "$output" ] || die "refuse to overwrite output: $output"

for manifest in "$base/manifest.env" "$fix/manifest.env"; do
  if grep -Evq '^[A-Z0-9_]+=[A-Za-z0-9._,-]+$' "$manifest"; then
    die "manifest contains unexpected syntax: $manifest"
  fi
done

# shellcheck disable=SC1090
. "$base/manifest.env"
[ "${LUMA_FP6_SENSORS_BUILD_VERSION:-}" = 1 ] || die 'base bundle version differs'
[ "${UPSTREAM_COMMIT:-}" = "$FP6_SENSORS_UPSTREAM_COMMIT" ] || die 'base upstream commit differs'
[ "${SENSOR_PATCH_SHA256:-}" = "$FP6_SENSORS_PATCH_SHA256" ] || die 'base sensor patch differs'
[ "${KERNEL_UNCHANGED:-}" = true ] && [ "${RAM_BOOT_ONLY:-}" = true ] || die 'base provenance differs'
[ "${PHONE_ACCESSED:-}" = false ] && [ "${PARTITION_WRITTEN:-}" = false ] || die 'base provenance differs'

base_files=(i2c-algo-bit i2c-gpio spi-bitbang spi-gpio kfifo_buf inv_sensors_timestamp inv-icm42600 inv-icm42600-spi stk3310 qmc6308 dps310)
base_hashes=(
  "$FP6_SENSORS_I2C_ALGO_BIT_KO_SHA256" "$FP6_SENSORS_I2C_GPIO_KO_SHA256"
  "$FP6_SENSORS_SPI_BITBANG_KO_SHA256" "$FP6_SENSORS_SPI_GPIO_KO_SHA256"
  "$FP6_SENSORS_KFIFO_BUF_KO_SHA256" "$FP6_SENSORS_INV_SENSORS_TIMESTAMP_KO_SHA256"
  c0f7fbd6ed972064513eb692edf0052258af040d5d38d1f3f8c1a20b3b47079a
  1352aa0b8570c614db2dd1d3bbdb37ecd469954e3713b193fd12a5fdbe9cd526
  "$FP6_SENSORS_STK3310_KO_SHA256" "$FP6_SENSORS_QMC6308_KO_SHA256"
  "$FP6_SENSORS_DPS310_KO_SHA256"
)
for index in "${!base_files[@]}"; do
  module=$base/modules/${base_files[$index]}.ko
  [ "$(hash "$module")" = "${base_hashes[$index]}" ] || die "base module differs: ${base_files[$index]}"
done
[ "$(hash "$base/milos-fairphone-fp6.dtb")" = "$FP6_SENSORS_DTB_SHA256" ] || die 'base DTB differs'

unset LUMA_FP6_SENSORS_BUILD_VERSION UPSTREAM_COMMIT SENSOR_PATCH_SHA256 KERNEL_UNCHANGED RAM_BOOT_ONLY PHONE_ACCESSED PARTITION_WRITTEN
# shellcheck disable=SC1090
. "$fix/manifest.env"
[ "${LUMA_FP6_ICM42630_FIX_VERSION:-}" = 1 ] || die 'ICM-42630 fix version differs'
[ "${BASE_KERNEL_SHA256:-}" = "$FP6_SENSORS_BASE_KERNEL_SHA256" ] || die 'fix kernel differs'
[ "${ICM42630_PATCH_SHA256:-}" = "$FP6_SENSORS_ICM42630_PATCH_SHA256" ] || die 'ICM-42630 patch differs'
[ "${CONFIG_SHA256:-}" = "$FP6_SENSORS_CONFIG_SHA256" ] || die 'fix config differs'
[ "${KERNEL_UNCHANGED:-}" = true ] && [ "${DTB_UNCHANGED:-}" = true ] || die 'fix provenance differs'
[ "${PHONE_ACCESSED:-}" = false ] && [ "${PARTITION_WRITTEN:-}" = false ] || die 'fix provenance differs'
[ "$(hash "$fix/modules/inv-icm42600.ko")" = "$INV_ICM42600_KO_SHA256" ] || die 'fixed core differs'
[ "$(hash "$fix/modules/inv-icm42600-spi.ko")" = "$INV_ICM42600_SPI_KO_SHA256" ] || die 'fixed SPI module differs'

mkdir -p "$output/modules"
install -m 0644 "$base/milos-fairphone-fp6.dtb" "$output/milos-fairphone-fp6.dtb"
for file in "${base_files[@]}"; do
  install -m 0644 "$base/modules/$file.ko" "$output/modules/$file.ko"
done
install -m 0644 "$fix/modules/inv-icm42600.ko" "$output/modules/inv-icm42600.ko"
install -m 0644 "$fix/modules/inv-icm42600-spi.ko" "$output/modules/inv-icm42600-spi.ko"

{
  printf 'LUMA_FP6_SENSORS_BUILD_VERSION=2\n'
  printf 'UPSTREAM_COMMIT=%s\n' "$FP6_SENSORS_UPSTREAM_COMMIT"
  printf 'KERNEL_RELEASE=7.1.2\n'
  printf 'BASE_KERNEL_SHA256=%s\n' "$FP6_SENSORS_BASE_KERNEL_SHA256"
  printf 'SENSOR_PATCH_SHA256=%s\n' "$FP6_SENSORS_PATCH_SHA256"
  printf 'ICM42630_PATCH_SHA256=%s\n' "$FP6_SENSORS_ICM42630_PATCH_SHA256"
  printf 'CONFIG_SHA256=%s\n' "$FP6_SENSORS_CONFIG_SHA256"
  printf 'DTB_SHA256=%s\n' "$FP6_SENSORS_DTB_SHA256"
  for file in "${base_files[@]}"; do
    name=$(printf '%s' "$file.ko" | tr '[:lower:].-' '[:upper:]__')
    printf '%s_SHA256=%s\n' "$name" "$(hash "$output/modules/$file.ko")"
  done
  printf 'KERNEL_UNCHANGED=true\n'
  printf 'DTB_UNCHANGED=true\n'
  printf 'RAM_BOOT_ONLY=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output/manifest.env"
chmod 0644 "$output/manifest.env"

printf 'FP6 sensor v2 bundle: %s\n' "$output"
