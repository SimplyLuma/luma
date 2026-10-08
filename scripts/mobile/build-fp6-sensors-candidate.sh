#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build only the FP6 sensor modules and DTB in the existing isolated
# Linux/aarch64 builder. The accepted v5 kernel is verified and never relinked;
# this script cannot access a phone or write a partition.

set -euo pipefail
umask 022

build_root=${1:?usage: build-fp6-sensors-candidate.sh BUILD_ROOT DAPM_PATCH SENSOR_PATCH OUTPUT_DIR}
dapm_patch=${2:?usage: build-fp6-sensors-candidate.sh BUILD_ROOT DAPM_PATCH SENSOR_PATCH OUTPUT_DIR}
sensor_patch=${3:?usage: build-fp6-sensors-candidate.sh BUILD_ROOT DAPM_PATCH SENSOR_PATCH OUTPUT_DIR}
output_dir=${4:?usage: build-fp6-sensors-candidate.sh BUILD_ROOT DAPM_PATCH SENSOR_PATCH OUTPUT_DIR}
resume=${LUMA_FP6_SENSORS_RESUME:-false}
rootfs=$build_root/lab/root
source_tree=$rootfs/work/linux
config_sha=a78af97c72e7d454348aa87dbe4bdabc81ab8328e9ae807610842059ef061d09
image_sha=760eb683f88ced7f869bc169ba123c52f0df6163c6cc2812b6676d812a3efb68
input_dtb_sha=28bdea6b91939f9ae55c33e04cea3b39058d4d49494f594195dbade4396b5e78
input_dts_sha=6aab82b20c0d2c1ccf7284b5e93afa47ac20409122a4d3c79cbab458a5cd485c
dapm_patch_sha=53fcddb1c91a7fcb4fdf33b9c0db7da8892bbf4597809ca3be6cd8e6d14bddd9
sensor_patch_sha=07f14a9304a6b03fff49d5888d6bf99c2c472a51979f66169878c6f7acadb49d
build_timestamp='2026-08-19 00:00:00 UTC'

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d ' ' -f 1; }

[ "$(id -u)" -eq 0 ] || die 'run as root on the isolated builder'
[ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ] ||
  die 'invoke with sudo from the unprivileged builder user'
builder_user=$SUDO_USER
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] ||
  die 'run only on the Linux/aarch64 builder'
[ -x /mnt/lima-rosetta/rosetta ] || die 'Lima Rosetta is unavailable'
if [ -r /sys/firmware/devicetree/base/model ]; then
  model=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$model" in *Fairphone*) die 'refuse to compile on the FP6 target' ;; esac
fi
[ -d "$source_tree" ] || die 'verified v4 source tree is missing'
[ -f "$dapm_patch" ] || die 'accepted v5 DAPM patch is missing'
[ -f "$sensor_patch" ] || die 'sensor patch is missing'
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"
[ "$resume" = false ] || [ "$resume" = true ] ||
  die 'LUMA_FP6_SENSORS_RESUME must be true or false'
[ "$(hash "$dapm_patch")" = "$dapm_patch_sha" ] || die 'DAPM patch differs'
[ "$(hash "$sensor_patch")" = "$sensor_patch_sha" ] || die 'sensor patch differs'
[ "$(hash "$source_tree/arch/arm64/boot/vmlinuz")" = "$image_sha" ] || die 'accepted kernel differs'
if [ "$resume" = false ]; then
  [ "$(hash "$source_tree/.config")" = "$config_sha" ] || die 'input config differs'
  [ "$(hash "$source_tree/arch/arm64/boot/dts/qcom/milos-fairphone-fp6.dtb")" = "$input_dtb_sha" ] || die 'v4 input DTB differs'
  [ "$(hash "$source_tree/arch/arm64/boot/dts/qcom/milos-fairphone-fp6.dts")" = "$input_dts_sha" ] || die 'v4 input DTS differs'
  patch -d "$source_tree" --dry-run -p1 <"$dapm_patch"
  patch -d "$source_tree" -p1 <"$dapm_patch"
  git -C "$source_tree" apply --check "$sensor_patch"
  git -C "$source_tree" apply "$sensor_patch"
else
  patch -d "$source_tree" --dry-run -R -p1 <"$dapm_patch" >/dev/null
  git -C "$source_tree" apply --reverse --check "$sensor_patch"
fi

mounted_dev=false
mounted_proc=false
mounted_rosetta=false
mounted_sys=false
manifest_tmp=
cleanup() {
  if [ -n "$manifest_tmp" ]; then rm -f -- "$manifest_tmp"; fi
  if [ "$mounted_rosetta" = true ]; then umount -l -R "$rootfs/mnt/lima-rosetta" 2>/dev/null || true; fi
  if [ "$mounted_sys" = true ]; then umount -l -R "$rootfs/sys" 2>/dev/null || true; fi
  if [ "$mounted_proc" = true ]; then umount -l "$rootfs/proc" 2>/dev/null || true; fi
  if [ "$mounted_dev" = true ]; then umount -l -R "$rootfs/dev" 2>/dev/null || true; fi
}
trap cleanup EXIT HUP INT TERM

mount --rbind /dev "$rootfs/dev"; mounted_dev=true
mount -t proc proc "$rootfs/proc"; mounted_proc=true
mount --rbind /sys "$rootfs/sys"; mounted_sys=true
mount --rbind /mnt/lima-rosetta "$rootfs/mnt/lima-rosetta"; mounted_rosetta=true

chroot "$rootfs" /bin/sh -eu -c "
  cd /work/linux
  scripts/config --module I2C_GPIO
  scripts/config --module SPI_GPIO
  scripts/config --module INV_ICM42600_SPI
  scripts/config --module STK3310
  scripts/config --module QMC6308
  scripts/config --module DPS310
  make ARCH=arm64 LLVM=1 LOCALVERSION= olddefconfig
  grep -qx 'CONFIG_I2C_GPIO=m' .config
  grep -qx 'CONFIG_SPI_GPIO=m' .config
  grep -qx 'CONFIG_INV_ICM42600_SPI=m' .config
  grep -qx 'CONFIG_IIO_KFIFO_BUF=m' .config
  grep -qx 'CONFIG_STK3310=m' .config
  grep -qx 'CONFIG_QMC6308=m' .config
  grep -qx 'CONFIG_DPS310=m' .config
  test \"\$(make ARCH=arm64 LLVM=1 LOCALVERSION= -s kernelrelease)\" = 7.1.2
  make -j4 ARCH=arm64 LLVM=1 KBUILD_BUILD_USER=pmos \\
    KBUILD_BUILD_HOST=build KBUILD_BUILD_TIMESTAMP='$build_timestamp' \\
    KBUILD_BUILD_VERSION=1-postmarketos-qcom-milos \\
    qcom/milos-fairphone-fp6.dtb
  make -j4 ARCH=arm64 LLVM=1 M=drivers/i2c/algos modules
  make -j4 ARCH=arm64 LLVM=1 M=drivers/i2c/busses \\
    KBUILD_EXTRA_SYMBOLS=/work/linux/drivers/i2c/algos/Module.symvers modules
  make -j4 ARCH=arm64 LLVM=1 M=drivers/spi modules
  make -j4 ARCH=arm64 LLVM=1 M=drivers/iio/buffer modules
  make -j4 ARCH=arm64 LLVM=1 M=drivers/iio/common/inv_sensors modules
  make -j4 ARCH=arm64 LLVM=1 M=drivers/iio/imu/inv_icm42600 \\
    KBUILD_EXTRA_SYMBOLS=/work/linux/drivers/iio/common/inv_sensors/Module.symvers modules
  make -j4 ARCH=arm64 LLVM=1 M=drivers/iio/light modules
  make -j4 ARCH=arm64 LLVM=1 M=drivers/iio/magnetometer modules
  make -j4 ARCH=arm64 LLVM=1 M=drivers/iio/pressure modules
"

dtb=$source_tree/arch/arm64/boot/dts/qcom/milos-fairphone-fp6.dtb
modules="
$source_tree/drivers/i2c/algos/i2c-algo-bit.ko
$source_tree/drivers/i2c/busses/i2c-gpio.ko
$source_tree/drivers/spi/spi-bitbang.ko
$source_tree/drivers/spi/spi-gpio.ko
$source_tree/drivers/iio/buffer/kfifo_buf.ko
$source_tree/drivers/iio/common/inv_sensors/inv_sensors_timestamp.ko
$source_tree/drivers/iio/imu/inv_icm42600/inv-icm42600.ko
$source_tree/drivers/iio/imu/inv_icm42600/inv-icm42600-spi.ko
$source_tree/drivers/iio/light/stk3310.ko
$source_tree/drivers/iio/magnetometer/qmc6308.ko
$source_tree/drivers/iio/pressure/dps310.ko"
[ -f "$dtb" ] || die 'rebuilt FP6 DTB is missing'
[ "$(hash "$dtb")" != "$input_dtb_sha" ] || die 'DTB did not change'
for module in $modules; do
  [ -f "$module" ] || die "sensor module is missing: $module"
  [ "$(modinfo -F vermagic "$module")" = '7.1.2 SMP preempt mod_unload aarch64' ] ||
    die "module vermagic differs: $module"
done
[ "$(hash "$source_tree/arch/arm64/boot/vmlinuz")" = "$image_sha" ] ||
  die 'kernel changed during module-only build'

runuser -u "$builder_user" -- mkdir -p "$output_dir/modules"
runuser -u "$builder_user" -- install -m 0644 "$dtb" "$output_dir/milos-fairphone-fp6.dtb"
for module in $modules; do
  runuser -u "$builder_user" -- install -m 0644 "$module" "$output_dir/modules/$(basename "$module")"
done
manifest_tmp=$(mktemp)
{
  printf 'LUMA_FP6_SENSORS_BUILD_VERSION=1\n'
  printf 'UPSTREAM_COMMIT=af49850e65bdf5dc6f4a14fb9e8e5c5814522d33\n'
  printf 'KERNEL_RELEASE=7.1.2\n'
  printf 'BASE_KERNEL_SHA256=%s\n' "$image_sha"
  printf 'BASE_DTB_SHA256=%s\n' "$input_dtb_sha"
  printf 'DAPM_PATCH_SHA256=%s\n' "$dapm_patch_sha"
  printf 'SENSOR_PATCH_SHA256=%s\n' "$sensor_patch_sha"
  printf 'CONFIG_SHA256=%s\n' "$(hash "$source_tree/.config")"
  printf 'DTB_SHA256=%s\n' "$(hash "$output_dir/milos-fairphone-fp6.dtb")"
  for module in $modules; do
    name=$(basename "$module" | tr '[:lower:].-' '[:upper:]__')
    printf '%s_SHA256=%s\n' "$name" "$(hash "$output_dir/modules/$(basename "$module")")"
  done
  printf 'KERNEL_UNCHANGED=true\n'
  printf 'RAM_BOOT_ONLY=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$manifest_tmp"
chmod 0644 "$manifest_tmp"
runuser -u "$builder_user" -- install -m 0644 "$manifest_tmp" "$output_dir/manifest.env"

printf 'FP6 sensor candidate bundle: %s\n' "$output_dir"
