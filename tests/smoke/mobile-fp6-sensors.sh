#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
env_file=$repo_root/config/mobile/fp6-sensors.env
patch=$repo_root/patches/linux-milos/0039-iio-fp6-add-motion-environment-sensor-stack.patch
imu_patch=$repo_root/patches/linux-milos/0040-iio-imu-inv-icm42600-add-icm42630.patch
builder=$repo_root/scripts/mobile/build-fp6-sensors-candidate.sh
imu_builder=$repo_root/scripts/mobile/build-fp6-icm42630-fix.sh
v2_packager=$repo_root/scripts/mobile/prepare-fp6-sensors-v2-bundle.sh
installer=$repo_root/scripts/mobile/install-fp6-sensor-modules.sh
repacker=$repo_root/scripts/mobile/prepare-fp6-sensors-boot-candidate.sh
inspector=$repo_root/scripts/mobile/inspect-fp6-sensors-candidate.sh
dynamic_acceptor=$repo_root/scripts/mobile/accept-fp6-sensors-dynamic.sh
bundle=$repo_root/build/mobile/fp6-physical/fp6-sensors-v2-final
boot_bundle=$repo_root/build/mobile/fp6-physical/fp6-sensors-boot-v1

bash -n "$builder" "$imu_builder" "$v2_packager" "$installer" "$repacker" "$inspector" "$dynamic_acceptor"
# shellcheck disable=SC1090
. "$env_file"

test "$FP6_SENSORS_UPSTREAM_COMMIT" = af49850e65bdf5dc6f4a14fb9e8e5c5814522d33
test "$(sha256sum "$patch" | cut -d ' ' -f 1)" = "$FP6_SENSORS_PATCH_SHA256"
test "$(sha256sum "$imu_patch" | cut -d ' ' -f 1)" = "$FP6_SENSORS_ICM42630_PATCH_SHA256"
test "$FP6_SENSORS_BASE_BOOT_SHA256" = 77819b90457e55809708aac1bc01d4d5d063f1bc274c1a4b0f9de716bfe94e01
test "$FP6_SENSORS_BASE_KERNEL_SHA256" = 760eb683f88ced7f869bc169ba123c52f0df6163c6cc2812b6676d812a3efb68
test "$FP6_SENSORS_BASE_DTB_SHA256" = cd942c35f96d13c8ea22bcb9bab611abb8cf2a34fec9c95d2e17a56baa617c02
test "$FP6_SENSORS_DTB_SHA256" = 4b5c8c5b96788d186cd918e3cfc27c6c2cf264e4a7842450b399944f42e1812f
test "$FP6_SENSORS_V1_BOOT_SHA256" = 4b25c7745393faa64f98cbfc76dbcf08d3a846012e59bd4293311f79c503f61a

grep -Fq 'QMC6308 magnetometer' "$patch"
grep -Fq 'compatible = "invensense,icm42630", "invensense,icm42631"' "$patch"
grep -Fq 'compatible = "sensortek,stk36c61", "sensortek,stk3310"' "$patch"
grep -Fq 'compatible = "goertek,spl07-003"' "$patch"
grep -Fq '{ "icm42630", INV_CHIP_ICM42630 }' "$imu_patch"
grep -Fq 'INV_ICM42600_WHOAMI_ICM42630' "$imu_patch"
grep -Fq 'scripts/config --module I2C_GPIO' "$builder"
grep -Fq 'scripts/config --module SPI_GPIO' "$builder"
grep -Fq 'scripts/config --module INV_ICM42600_SPI' "$builder"
grep -Fq 'KBUILD_EXTRA_SYMBOLS=/work/linux/drivers/i2c/algos/Module.symvers' "$builder"
grep -Fq 'KBUILD_EXTRA_SYMBOLS=/work/linux/drivers/iio/common/inv_sensors/Module.symvers' "$builder"
grep -Fq 'KERNEL_UNCHANGED=true' "$builder"
grep -Fq 'PHONE_ACCESSED=false' "$builder"
grep -Fq 'PARTITION_WRITTEN=false' "$builder"
if grep -Eiq '^[[:space:]]*(adb|fastboot|scp|ssh)([[:space:]]|$)' "$builder"; then
  printf 'FAIL: isolated sensor builder must not contact a device\n' >&2
  exit 1
fi
grep -Fq 'alias is missing' "$imu_builder"
grep -Fq 'KERNEL_UNCHANGED=true' "$imu_builder"
grep -Fq 'PHONE_ACCESSED=false' "$imu_builder"
if grep -Eiq '^[[:space:]]*(adb|fastboot|scp|ssh)([[:space:]]|$)' "$imu_builder" "$v2_packager"; then
  printf 'FAIL: offline ICM-42630 tools must not contact a device\n' >&2
  exit 1
fi

grep -Fq 'cmp "$base_dir/kernel" "$candidate_dir/kernel"' "$repacker"
grep -Fq 'cmp "$base_dir/ramdisk" "$candidate_dir/ramdisk"' "$repacker"
grep -Fq 'HEADER_ROUNDTRIP_EXACT=true' "$repacker"
grep -Fq 'RAM_BOOT_ONLY=true' "$repacker"
grep -Fq 'PARTITION_WRITTEN=false' "$repacker"
if grep -Eiq '(^|[[:space:]])(adb|fastboot|scp|ssh)([[:space:]]|$)' "$repacker"; then
  printf 'FAIL: offline sensor repacker must not contact a device\n' >&2
  exit 1
fi

grep -Fq "The Fairphone (Gen. 6)" "$installer"
grep -Fq 'modules_loaded=false' "$installer"
grep -Fq 'services_restarted=false' "$installer"
grep -Fq 'partitions_written=false' "$installer"
if grep -Eiq '^[[:space:]]*(insmod|modprobe|rmmod|systemctl|reboot|fastboot)([[:space:]]|$)' "$installer"; then
  printf 'FAIL: sensor installer must not load modules, restart, or boot\n' >&2
  exit 1
fi

grep -Fq 'expected at least five IIO devices' "$inspector"
grep -Fq 'qmc6308 stk36c61 spl07-003 icm42630-accel icm42630-gyro' "$inspector"
grep -Fq 'MUTATING_OPERATIONS=none' "$inspector"
if grep -Eiq '^[[:space:]]*(amixer.*cset|insmod|modprobe|rmmod|systemctl[[:space:]]+(start|stop|restart)|reboot|fastboot)([[:space:]]|$)' "$inspector"; then
  printf 'FAIL: sensor inspector must remain read-only\n' >&2
  exit 1
fi
grep -Fq 'monitor-sensor --accel --light' "$dynamic_acceptor"
grep -Fq 'sample_deadline=$(($(date +%s) + duration))' "$dynamic_acceptor"
grep -Fq 'The QMC6308 direct-mode driver performs one conversion per axis read' \
  "$dynamic_acceptor"
grep -Fq 'PERSISTENT_MUTATIONS=none' "$dynamic_acceptor"
if grep -Eiq '^[[:space:]]*(insmod|modprobe|rmmod|systemctl[[:space:]]+(start|stop|restart)|reboot|fastboot)([[:space:]]|$)' "$dynamic_acceptor"; then
  printf 'FAIL: dynamic sensor acceptor must not alter modules, services, or boot state\n' >&2
  exit 1
fi

test "$(sha256sum "$bundle/milos-fairphone-fp6.dtb" | cut -d ' ' -f 1)" = "$FP6_SENSORS_DTB_SHA256"
grep -Fxq 'LUMA_FP6_SENSORS_BUILD_VERSION=2' "$bundle/manifest.env"
grep -Fxq "ICM42630_PATCH_SHA256=$FP6_SENSORS_ICM42630_PATCH_SHA256" "$bundle/manifest.env"
files=(i2c-algo-bit i2c-gpio spi-bitbang spi-gpio kfifo_buf inv_sensors_timestamp inv-icm42600 inv-icm42600-spi stk3310 qmc6308 dps310)
hashes=(
  "$FP6_SENSORS_I2C_ALGO_BIT_KO_SHA256" "$FP6_SENSORS_I2C_GPIO_KO_SHA256"
  "$FP6_SENSORS_SPI_BITBANG_KO_SHA256" "$FP6_SENSORS_SPI_GPIO_KO_SHA256"
  "$FP6_SENSORS_KFIFO_BUF_KO_SHA256" "$FP6_SENSORS_INV_SENSORS_TIMESTAMP_KO_SHA256" "$FP6_SENSORS_ICM42600_KO_SHA256"
  "$FP6_SENSORS_ICM42600_SPI_KO_SHA256" "$FP6_SENSORS_STK3310_KO_SHA256"
  "$FP6_SENSORS_QMC6308_KO_SHA256" "$FP6_SENSORS_DPS310_KO_SHA256"
)
for index in "${!files[@]}"; do
  test "$(sha256sum "$bundle/modules/${files[$index]}.ko" | cut -d ' ' -f 1)" = "${hashes[$index]}"
done
test "$(sha256sum "$boot_bundle/boot-fp6-luma-sensors-v1.img" | cut -d ' ' -f 1)" = "$FP6_SENSORS_V1_BOOT_SHA256"
grep -Fxq 'CANDIDATE_HASH_PINNED=true' "$boot_bundle/boot-manifest.env"
grep -Fxq 'KERNEL_UNCHANGED=true' "$boot_bundle/boot-manifest.env"
grep -Fxq 'RAMDISK_UNCHANGED=true' "$boot_bundle/boot-manifest.env"
grep -Fxq 'PARTITION_WRITTEN=false' "$boot_bundle/boot-manifest.env"

printf 'PASS: FP6 sensor candidate is exact-hash, dependency-complete, and RAM-boot-only\n'
