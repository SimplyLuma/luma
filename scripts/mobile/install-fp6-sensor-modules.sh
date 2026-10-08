#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Install the exact FP6 sensor module set as a recoverable depmod overlay. This
# script never loads a module, restarts a service, reboots, touches a partition,
# or accesses the bootloader. The matching DTB is exercised with fastboot boot.

set -euo pipefail
umask 022

expected_kernel=7.1.2
install_root=/usr/lib/modules/$expected_kernel/updates/luma-fp6-sensors
state_root=/var/lib/luma/fp6-sensors

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d ' ' -f 1; }

[ "$(id -u)" -eq 0 ] || die 'run as root on the FP6'
[ "$(uname -r)" = "$expected_kernel" ] || die 'unexpected running kernel'
[ "$(tr -d '\000' </proc/device-tree/model)" = 'The Fairphone (Gen. 6)' ] ||
  die 'device is not the exact Fairphone 6 target'
for tool in cmp cut depmod grep install mkdir modinfo mv readelf readlink sha256sum tr; do
  command -v "$tool" >/dev/null 2>&1 || die "missing tool: $tool"
done

bundle=${1:?usage: install-fp6-sensor-modules.sh BUNDLE_DIR}
manifest=$bundle/manifest.env
[ -f "$manifest" ] || die 'bundle manifest is missing'
if grep -Evq '^[A-Z0-9_]+=[A-Za-z0-9._,-]+$' "$manifest"; then
  die 'bundle manifest contains unexpected syntax'
fi
# shellcheck disable=SC1090
. "$manifest"
[ "${LUMA_FP6_SENSORS_BUILD_VERSION:-}" = 2 ] || die 'bundle version differs'
[ "${UPSTREAM_COMMIT:-}" = af49850e65bdf5dc6f4a14fb9e8e5c5814522d33 ] || die 'upstream commit differs'
[ "${KERNEL_RELEASE:-}" = "$expected_kernel" ] || die 'bundle kernel differs'
[ "${BASE_KERNEL_SHA256:-}" = 760eb683f88ced7f869bc169ba123c52f0df6163c6cc2812b6676d812a3efb68 ] || die 'base kernel differs'
[ "${SENSOR_PATCH_SHA256:-}" = 07f14a9304a6b03fff49d5888d6bf99c2c472a51979f66169878c6f7acadb49d ] || die 'sensor patch differs'
[ "${ICM42630_PATCH_SHA256:-}" = 073794fa60058a07f500df2c25fef2355b3a669083657f0bde4182551c2963d3 ] || die 'ICM-42630 patch differs'
[ "${KERNEL_UNCHANGED:-}" = true ] || die 'kernel provenance differs'
[ "${DTB_UNCHANGED:-}" = true ] || die 'DTB provenance differs'
[ "${RAM_BOOT_ONLY:-}" = true ] || die 'bundle is not RAM-boot-only'
[ "${PHONE_ACCESSED:-}" = false ] || die 'bundle provenance differs'
[ "${PARTITION_WRITTEN:-}" = false ] || die 'bundle provenance differs'

files=(
  i2c-algo-bit i2c-gpio spi-bitbang spi-gpio kfifo_buf inv_sensors_timestamp
  inv-icm42600 inv-icm42600-spi stk3310 qmc6308 dps310
)
module_ids=(
  i2c_algo_bit i2c_gpio spi_bitbang spi_gpio kfifo_buf inv_sensors_timestamp
  inv_icm42600 inv_icm42600_spi stk3310 qmc6308 dps310
)
hashes=(
  "$I2C_ALGO_BIT_KO_SHA256" "$I2C_GPIO_KO_SHA256"
  "$SPI_BITBANG_KO_SHA256" "$SPI_GPIO_KO_SHA256"
  "$KFIFO_BUF_KO_SHA256" "$INV_SENSORS_TIMESTAMP_KO_SHA256" "$INV_ICM42600_KO_SHA256"
  "$INV_ICM42600_SPI_KO_SHA256" "$STK3310_KO_SHA256"
  "$QMC6308_KO_SHA256" "$DPS310_KO_SHA256"
)

for index in "${!files[@]}"; do
  module=$bundle/modules/${files[$index]}.ko
  [ -f "$module" ] || die "missing module: ${files[$index]}"
  [ "$(hash "$module")" = "${hashes[$index]}" ] ||
    die "module checksum differs: ${files[$index]}"
  [ "$(modinfo -F vermagic "$module" | awk '{print $1}')" = "$expected_kernel" ] ||
    die "module vermagic differs: ${files[$index]}"
  readelf -h "$module" | grep -Fq 'Machine:                           AArch64' ||
    die "module architecture differs: ${files[$index]}"
done

if [ -f "$install_root/manifest.env" ] && cmp -s "$manifest" "$install_root/manifest.env"; then
  printf 'The verified FP6 sensor module bundle is already installed.\n'
  exit 0
fi

bundle_id=${QMC6308_KO_SHA256:0:16}
mkdir -p "$state_root/backups" "$state_root/failed"
backup_root=$state_root/backups/module-overlay-before-$bundle_id
failed_root=$state_root/failed/module-overlay-$bundle_id
[ ! -e "$backup_root" ] || die "backup already exists: $backup_root"
[ ! -e "$failed_root" ] || die "failed-install record already exists: $failed_root"

had_previous=false
if [ -e "$install_root" ]; then
  mv "$install_root" "$backup_root"
  had_previous=true
fi
rollback_install=true
cleanup_install() {
  if [ "$rollback_install" = true ]; then
    if [ -e "$install_root" ]; then mv "$install_root" "$failed_root" 2>/dev/null || true; fi
    if [ "$had_previous" = true ] && [ -e "$backup_root" ]; then mv "$backup_root" "$install_root" 2>/dev/null || true; fi
    depmod "$expected_kernel" 2>/dev/null || true
  fi
}
trap cleanup_install EXIT

mkdir -p "$install_root/modules"
for file in "${files[@]}"; do
  install -m 0644 "$bundle/modules/$file.ko" "$install_root/modules/$file.ko"
done
install -m 0644 "$manifest" "$install_root/manifest.env"
depmod "$expected_kernel"
for index in "${!files[@]}"; do
  selected=$(modinfo -n "${module_ids[$index]}")
  [ "$(readlink -f "$selected")" = "$(readlink -f "$install_root/modules/${files[$index]}.ko")" ] ||
    die "modprobe did not select the Luma overlay for ${files[$index]}: $selected"
done

rollback_install=false
trap - EXIT
printf 'Installed the verified FP6 sensor modules without loading them.\n'
if [ "$had_previous" = true ]; then printf 'Previous overlay backup: %s\n' "$backup_root"; fi
printf 'modules_loaded=false\nservices_restarted=false\nrebooted=false\npartitions_written=false\n'
