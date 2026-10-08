#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build only the ICM-42630 core/SPI module pair in the isolated Linux/aarch64
# builder.  The kernel and already booted DTB are never relinked, and this
# script cannot access a phone or partition.

set -euo pipefail
umask 022

build_root=${1:?usage: build-fp6-icm42630-fix.sh BUILD_ROOT SENSOR_PATCH ICM42630_PATCH OUTPUT_DIR}
sensor_patch=${2:?usage: build-fp6-icm42630-fix.sh BUILD_ROOT SENSOR_PATCH ICM42630_PATCH OUTPUT_DIR}
imu_patch=${3:?usage: build-fp6-icm42630-fix.sh BUILD_ROOT SENSOR_PATCH ICM42630_PATCH OUTPUT_DIR}
output_dir=${4:?usage: build-fp6-icm42630-fix.sh BUILD_ROOT SENSOR_PATCH ICM42630_PATCH OUTPUT_DIR}
rootfs=$build_root/lab/root
source_tree=$rootfs/work/linux
kernel_sha=760eb683f88ced7f869bc169ba123c52f0df6163c6cc2812b6676d812a3efb68
config_sha=d0337fd4bb3103a445a2aa7bf67840ae5d001bebe9b612a8841d267d8caf4077
sensor_patch_sha=07f14a9304a6b03fff49d5888d6bf99c2c472a51979f66169878c6f7acadb49d
imu_patch_sha=073794fa60058a07f500df2c25fef2355b3a669083657f0bde4182551c2963d3
old_core_sha=c0f7fbd6ed972064513eb692edf0052258af040d5d38d1f3f8c1a20b3b47079a
old_spi_sha=1352aa0b8570c614db2dd1d3bbdb37ecd469954e3713b193fd12a5fdbe9cd526
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
[ -d "$source_tree" ] || die 'verified sensor source tree is missing'
[ -f "$sensor_patch" ] && [ -f "$imu_patch" ] || die 'required patch is missing'
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"
[ "$(hash "$sensor_patch")" = "$sensor_patch_sha" ] || die 'sensor patch differs'
[ "$(hash "$imu_patch")" = "$imu_patch_sha" ] || die 'ICM-42630 patch differs'
[ "$(hash "$source_tree/arch/arm64/boot/vmlinuz")" = "$kernel_sha" ] || die 'accepted kernel differs'
[ "$(hash "$source_tree/.config")" = "$config_sha" ] || die 'sensor config differs'
git -C "$source_tree" apply --reverse --check "$sensor_patch"
git -C "$source_tree" apply --check "$imu_patch"

core=$source_tree/drivers/iio/imu/inv_icm42600/inv-icm42600.ko
spi=$source_tree/drivers/iio/imu/inv_icm42600/inv-icm42600-spi.ko
[ "$(hash "$core")" = "$old_core_sha" ] || die 'input IMU core module differs'
[ "$(hash "$spi")" = "$old_spi_sha" ] || die 'input IMU SPI module differs'
git -C "$source_tree" apply "$imu_patch"

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
  test \"\$(make ARCH=arm64 LLVM=1 LOCALVERSION= -s kernelrelease)\" = 7.1.2
  make -j4 ARCH=arm64 LLVM=1 M=drivers/iio/imu/inv_icm42600 \\
    KBUILD_BUILD_USER=pmos KBUILD_BUILD_HOST=build \\
    KBUILD_BUILD_TIMESTAMP='$build_timestamp' \\
    KBUILD_BUILD_VERSION=1-postmarketos-qcom-milos \\
    KBUILD_EXTRA_SYMBOLS=/work/linux/drivers/iio/common/inv_sensors/Module.symvers modules
"

for module in "$core" "$spi"; do
  [ -f "$module" ] || die "rebuilt module is missing: $module"
  [ "$(modinfo -F vermagic "$module")" = '7.1.2 SMP preempt mod_unload aarch64' ] ||
    die "module vermagic differs: $module"
done
modinfo "$spi" | grep -Fxq 'alias:          spi:icm42630' || die 'SPI autoload alias is missing'
modinfo "$spi" | grep -Fq 'of:N*T*Cinvensense,icm42630' || die 'OF alias is missing'
[ "$(hash "$source_tree/arch/arm64/boot/vmlinuz")" = "$kernel_sha" ] || die 'kernel changed'

runuser -u "$builder_user" -- mkdir -p "$output_dir/modules"
runuser -u "$builder_user" -- install -m 0644 "$core" "$output_dir/modules/inv-icm42600.ko"
runuser -u "$builder_user" -- install -m 0644 "$spi" "$output_dir/modules/inv-icm42600-spi.ko"
manifest_tmp=$(mktemp)
{
  printf 'LUMA_FP6_ICM42630_FIX_VERSION=1\n'
  printf 'KERNEL_RELEASE=7.1.2\n'
  printf 'BASE_KERNEL_SHA256=%s\n' "$kernel_sha"
  printf 'SENSOR_PATCH_SHA256=%s\n' "$sensor_patch_sha"
  printf 'ICM42630_PATCH_SHA256=%s\n' "$imu_patch_sha"
  printf 'CONFIG_SHA256=%s\n' "$config_sha"
  printf 'INV_ICM42600_KO_SHA256=%s\n' "$(hash "$output_dir/modules/inv-icm42600.ko")"
  printf 'INV_ICM42600_SPI_KO_SHA256=%s\n' "$(hash "$output_dir/modules/inv-icm42600-spi.ko")"
  printf 'KERNEL_UNCHANGED=true\n'
  printf 'DTB_UNCHANGED=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$manifest_tmp"
chmod 0644 "$manifest_tmp"
runuser -u "$builder_user" -- install -m 0644 "$manifest_tmp" "$output_dir/manifest.env"

printf 'FP6 ICM-42630 fix bundle: %s\n' "$output_dir"
