#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Add the already-accepted FP6 camera, sensor, microphone-adjacent, and NFC
# module set to the complete matched-CMA module archive.  This operates only
# on a preserved native AArch64 build tree.  It never contacts the phone and
# never relinks the kernel or device tree.

set -Eeuo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
tree=${1:?usage: augment-fp6-fingerprint-matched-cma-modules.sh PRESERVED_TREE BASE_BUNDLE OUTPUT_DIR}
base_bundle=${2:?missing base bundle}
output_dir=${3:?missing output directory}
release=7.1.2-luma-fp-cma1
localversion=-luma-fp-cma1
runtime_image_sha=01d9f1070058ad0edb8832ffa3e76589b4b32b903d14f09abb3f10c13c02276c
runtime_config_sha=5bddfee4390b0f66de5709b99282e79ee3787a01749d5cc8d28ea82d6bbed7fe
base_archive_sha=23c83b4f0747761a50615b9b558cadae427cbf9b60a4077e834ec6aad20b366b
build_timestamp='2026-08-22 00:00:00 UTC'
jobs=${LUMA_KERNEL_BUILD_JOBS:-8}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d ' ' -f 1; }

[[ $(id -u) -eq 0 ]] || die 'run as root in the isolated builder'
[[ $(uname -s) == Linux && $(uname -m) == aarch64 ]] || die 'builder must be Linux/aarch64'
[[ -d $tree && -f $tree/Module.symvers && -f $tree/vmlinux ]] || die 'preserved complete build tree is absent'
[[ ! -e $output_dir ]] || die "refuse to overwrite output: $output_dir"
[[ $(hash "$tree/arch/arm64/boot/vmlinuz") == "$runtime_image_sha" ]] || die 'runtime kernel differs'
[[ $(hash "$tree/.config") == "$runtime_config_sha" ]] || die 'runtime build config differs'
[[ $(make -s -C "$tree" ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" kernelrelease) == "$release" ]] || die 'kernel release differs'

base_archive=$base_bundle/modules-$release.tar.zst
[[ -f $base_archive ]] || die 'base module archive is absent'
[[ $(hash "$base_archive") == "$base_archive_sha" ]] || die 'base module archive differs'

patches=(
  "$repo_root/patches/linux-milos/0039-iio-fp6-add-motion-environment-sensor-stack.patch"
  "$repo_root/patches/linux-milos/0040-iio-imu-inv-icm42600-add-icm42630.patch"
  "$repo_root/patches/linux-milos-nfc/0002-nfc-s3fwrn5-drop-of_match_ptr-and-maybe-unused.patch"
  "$repo_root/patches/linux-milos-nfc/0003-nfc-s3fwrn5-use-driver-name-string-literal.patch"
  "$repo_root/patches/linux-milos-nfc/0004-nfc-s3fwrn5-support-S3NRN4V-variant.patch"
  "$repo_root/patches/linux-milos-nfc/0006-nfc-s3fwrn5-refresh-S3NRN4V-v4.patch"
  "$repo_root/patches/linux-milos-nfc/0007-nfc-s3fwrn5-mirror-mainline-v5-exit-path.patch"
)
expected_patch_hashes=(
  07f14a9304a6b03fff49d5888d6bf99c2c472a51979f66169878c6f7acadb49d
  073794fa60058a07f500df2c25fef2355b3a669083657f0bde4182551c2963d3
  97df63716dab3284e9d94c3a1bdffd80fcb4084cda54f6ac5d151d839c328913
  c90a1065a2bd6521083a23baad2ab952d298e0148bc10136f35e5f394e604e8f
  91d21c551c2795c7f3a0a338c60e05b24c740b6c9e8a54113ac259b0ffa26ab9
  6b432bd7e50257cf5a169bf216df65cba36c16a4931c51fb910887907530c694
  cfa6a5edf0d4603f5772a0b8b9c7b4baa9ba49daa53b1f9d370d0076ef7fb79b
)
for i in "${!patches[@]}"; do
  [[ -f ${patches[$i]} && $(hash "${patches[$i]}") == "${expected_patch_hashes[$i]}" ]] ||
    die "source patch identity differs: ${patches[$i]}"
  if git -C "$tree" apply --check "${patches[$i]}"; then
    git -C "$tree" apply "${patches[$i]}"
  elif git -C "$tree" apply --reverse --check "${patches[$i]}"; then
    : # Safe resume after an interrupted module-only build.
  else
    die "source patch state is ambiguous: ${patches[$i]}"
  fi
done

# These are presentation-independent hardware modules. The accepted v13 DTB
# already contains the physically verified device nodes, so no DT is rebuilt.
for symbol in VIDEO_QCOM_CAMSS VIDEO_OV13B10 VIDEO_DW9714 VIDEO_DW9784 \
  VIDEO_S5KKD1SP VIDEO_IMX896 I2C_GPIO SPI_GPIO INV_ICM42600_SPI \
  STK3310 QMC6308 DPS310 NFC NFC_NCI NFC_S3FWRN5 NFC_S3FWRN5_I2C; do
  "$tree/scripts/config" --file "$tree/.config" --module "$symbol"
done

make_args=(ARCH=arm64 LLVM=1 LOCALVERSION="$localversion"
  KBUILD_BUILD_USER=pmos KBUILD_BUILD_HOST=build
  KBUILD_BUILD_TIMESTAMP="$build_timestamp"
  KBUILD_BUILD_VERSION=1-postmarketos)
make -C "$tree" "${make_args[@]}" olddefconfig
for setting in \
  CONFIG_VIDEO_QCOM_CAMSS=m CONFIG_VIDEO_OV13B10=m CONFIG_VIDEO_DW9714=m \
  CONFIG_VIDEO_DW9784=m CONFIG_VIDEO_S5KKD1SP=m CONFIG_VIDEO_IMX896=m \
  CONFIG_I2C_GPIO=m CONFIG_SPI_GPIO=m CONFIG_INV_ICM42600_SPI=m \
  CONFIG_IIO_KFIFO_BUF=m CONFIG_STK3310=m CONFIG_QMC6308=m CONFIG_DPS310=m \
  CONFIG_NFC=m CONFIG_NFC_NCI=m CONFIG_NFC_S3FWRN5=m CONFIG_NFC_S3FWRN5_I2C=m; do
  grep -qx "$setting" "$tree/.config" || die "module configuration missing: $setting"
done
complete_symvers_sha=$(hash "$tree/Module.symvers")
make -C "$tree" "${make_args[@]}" prepare modules_prepare
[[ $(hash "$tree/Module.symvers") == "$complete_symvers_sha" ]] ||
  die 'complete kernel Module.symvers changed during modules_prepare'
for setting in \
  CONFIG_VIDEO_OV13B10=m CONFIG_VIDEO_IMX896=m CONFIG_VIDEO_S5KKD1SP=m \
  CONFIG_I2C_GPIO=m CONFIG_SPI_GPIO=m CONFIG_INV_ICM42600_SPI=m \
  CONFIG_QMC6308=m CONFIG_NFC_S3FWRN5=m; do
  grep -qx "$setting" "$tree/include/config/auto.conf" ||
    die "generated module configuration missing: $setting"
done

module_paths=(
  drivers/media/v4l2-core/v4l2-cci.ko
  drivers/media/platform/qcom/camss/qcom-camss.ko
  drivers/media/i2c/ov13b10.ko
  drivers/media/i2c/dw9714.ko
  drivers/media/i2c/dw9784.ko
  drivers/media/i2c/s5kkd1sp.ko
  drivers/media/i2c/imx896.ko
  drivers/i2c/algos/i2c-algo-bit.ko
  drivers/i2c/busses/i2c-gpio.ko
  drivers/spi/spi-bitbang.ko
  drivers/spi/spi-gpio.ko
  drivers/iio/buffer/kfifo_buf.ko
  drivers/iio/common/inv_sensors/inv_sensors_timestamp.ko
  drivers/iio/imu/inv_icm42600/inv-icm42600.ko
  drivers/iio/imu/inv_icm42600/inv-icm42600-spi.ko
  drivers/iio/light/stk3310.ko
  drivers/iio/magnetometer/qmc6308.ko
  drivers/iio/pressure/dps310.ko
  net/nfc/nfc.ko
  net/nfc/nci/nci.ko
  drivers/nfc/s3fwrn5/s3fwrn5.ko
  drivers/nfc/s3fwrn5/s3fwrn5_i2c.ko
)
module_dirs=(
  drivers/media/v4l2-core
  drivers/media/platform/qcom/camss
  drivers/media/i2c
  drivers/i2c/algos
  drivers/i2c/busses
  drivers/spi
  drivers/iio/buffer
  drivers/iio/common/inv_sensors
  drivers/iio/imu/inv_icm42600
  drivers/iio/light
  drivers/iio/magnetometer
  drivers/iio/pressure
  net/nfc
  drivers/nfc/s3fwrn5
)

build_modules() {
  make -j"$jobs" -C "$tree" "${make_args[@]}" M=drivers/media/v4l2-core modules
  make -j"$jobs" -C "$tree" "${make_args[@]}" M=drivers/media/platform/qcom/camss modules
  make -j"$jobs" -C "$tree" "${make_args[@]}" M=drivers/media/i2c \
    KBUILD_EXTRA_SYMBOLS="$tree/drivers/media/v4l2-core/Module.symvers" modules
  make -j"$jobs" -C "$tree" "${make_args[@]}" M=drivers/i2c/algos modules
  make -j"$jobs" -C "$tree" "${make_args[@]}" M=drivers/i2c/busses \
    KBUILD_EXTRA_SYMBOLS="$tree/drivers/i2c/algos/Module.symvers" modules
  make -j"$jobs" -C "$tree" "${make_args[@]}" M=drivers/spi modules
  make -j"$jobs" -C "$tree" "${make_args[@]}" M=drivers/iio/buffer modules
  make -j"$jobs" -C "$tree" "${make_args[@]}" M=drivers/iio/common/inv_sensors modules
  make -j"$jobs" -C "$tree" "${make_args[@]}" M=drivers/iio/imu/inv_icm42600 \
    KBUILD_EXTRA_SYMBOLS="$tree/drivers/iio/common/inv_sensors/Module.symvers" modules
  make -j"$jobs" -C "$tree" "${make_args[@]}" M=drivers/iio/light modules
  make -j"$jobs" -C "$tree" "${make_args[@]}" M=drivers/iio/magnetometer modules
  make -j"$jobs" -C "$tree" "${make_args[@]}" M=drivers/iio/pressure modules
  make -j"$jobs" -C "$tree" "${make_args[@]}" M=net/nfc modules
  make -j"$jobs" -C "$tree" "${make_args[@]}" M=drivers/nfc/s3fwrn5 \
    KBUILD_EXTRA_SYMBOLS="$tree/net/nfc/Module.symvers" modules
}

mkdir -p "$output_dir"
first_hashes=$output_dir/repro-first.sha256
second_hashes=$output_dir/repro-second.sha256
# Stale per-directory modules.order files from the earlier full build can hide
# newly enabled modules. Start both compile passes from an explicit M= clean.
for dir in "${module_dirs[@]}"; do
  make -C "$tree" "${make_args[@]}" M="$dir" clean
done
build_modules
for module in "${module_paths[@]}"; do
  [[ -f $tree/$module ]] || die "hardware-continuity module missing: $module"
  [[ $(modinfo -F vermagic "$tree/$module" | cut -d ' ' -f 1) == "$release" ]] ||
    die "hardware-continuity module vermagic differs: $module"
done
(cd "$tree" && sha256sum "${module_paths[@]}") >"$first_hashes"

# Force a second compile of every added/replaced module. This is independent
# of the first object/link pass while retaining the exact completed vmlinux and
# Module.symvers against which both passes are built.
for dir in "${module_dirs[@]}"; do
  make -C "$tree" "${make_args[@]}" M="$dir" clean
done
build_modules
(cd "$tree" && sha256sum "${module_paths[@]}") >"$second_hashes"
cmp -s "$first_hashes" "$second_hashes" || die 'hardware-continuity modules are not byte reproducible'

install_tree=$output_dir/install
mkdir -p "$install_tree"
tar --zstd -xf "$base_archive" -C "$install_tree"
module_root=$install_tree/lib/modules/$release
[[ -d $module_root ]] || die 'base module archive layout differs'
for module in "${module_paths[@]}"; do
  destination=$module_root/kernel/$module.zst
  mkdir -p "$(dirname -- "$destination")"
  zstd -q -19 -T1 -f "$tree/$module" -o "$destination"
  order_path=kernel/$module
  grep -Fxq "$order_path" "$module_root/modules.order" || printf '%s\n' "$order_path" >>"$module_root/modules.order"
done
depmod -b "$install_tree" "$release"

bundle=$output_dir/bundle
mkdir -p "$bundle"
cp -p "$base_bundle/Image.gz" "$bundle/Image.gz"
cp -p "$base_bundle/config" "$bundle/config"
cp -p "$base_bundle/qseecomtee.ko" "$bundle/qseecomtee.ko"
cp -p "$base_bundle/focaltech_fp.ko" "$bundle/focaltech_fp.ko"
module_count=$(find "$module_root" -type f -name '*.ko.zst' | wc -l | tr -d ' ')
tar --sort=name --mtime='2026-08-22 00:00:00 UTC' --owner=0 --group=0 \
  --numeric-owner -C "$install_tree" -cf - lib/modules/$release |
  zstd -q -19 -T1 -o "$bundle/modules-$release.tar.zst"

[[ $(hash "$bundle/Image.gz") == "$runtime_image_sha" ]] || die 'runtime image changed'
[[ $(hash "$bundle/config") == "$runtime_config_sha" ]] || die 'runtime config changed'
for required in \
  kernel/drivers/media/i2c/imx896.ko.zst \
  kernel/drivers/media/i2c/s5kkd1sp.ko.zst \
  kernel/drivers/media/i2c/ov13b10.ko.zst \
  kernel/drivers/nfc/s3fwrn5/s3fwrn5_i2c.ko.zst \
  kernel/drivers/iio/imu/inv_icm42600/inv-icm42600-spi.ko.zst \
  kernel/drivers/iio/magnetometer/qmc6308.ko.zst \
  kernel/sound/soc/codecs/snd-soc-wcd9378-sdw.ko.zst \
  kernel/drivers/input/finger/focal_finger/focaltech_fp.ko.zst \
  kernel/drivers/tee/qseecom/qseecomtee.ko.zst; do
  [[ -f $module_root/$required ]] || die "required module absent from final tree: $required"
done

{
  printf 'LUMA_FP6_FINGERPRINT_MATCHED_CMA_CONTINUITY_BUILD_VERSION=1\n'
  printf 'KERNEL_RELEASE=%s\n' "$release"
  printf 'IMAGE_SHA256=%s\n' "$(hash "$bundle/Image.gz")"
  printf 'CONFIG_SHA256=%s\n' "$(hash "$bundle/config")"
  printf 'RUNTIME_CONFIG_SHA256=%s\n' "$(hash "$bundle/config")"
  printf 'MODULE_BUILD_CONFIG_SHA256=%s\n' "$(hash "$tree/.config")"
  printf 'QSEECOMTEE_KO_SHA256=%s\n' "$(hash "$bundle/qseecomtee.ko")"
  printf 'FOCALTECH_FP_KO_SHA256=%s\n' "$(hash "$bundle/focaltech_fp.ko")"
  printf 'BASE_MODULE_TREE_SHA256=%s\n' "$base_archive_sha"
  printf 'MODULE_TREE_SHA256=%s\n' "$(hash "$bundle/modules-$release.tar.zst")"
  printf 'MODULE_COUNT=%s\n' "$module_count"
  printf 'HARDWARE_CONTINUITY_MODULE_COUNT=%s\n' "${#module_paths[@]}"
  printf 'HARDWARE_CONTINUITY_MODULES_REPRODUCIBLE=true\n'
  printf 'CAMERA_MODULES_PRESERVED=true\n'
  printf 'MICROPHONE_MODULES_PRESERVED=true\n'
  printf 'NFC_MODULES_PRESERVED=true\n'
  printf 'SENSOR_MODULES_PRESERVED=true\n'
  printf 'FINGERPRINT_MODULES_PRESERVED=true\n'
  printf 'DMA_CMA_ENABLED=true\n'
  printf 'CMA_SIZE_MBYTES=32\n'
  printf 'TZMEM_MODE=shmbridge\n'
  printf 'DEDICATED_QSEECOM_HEAPS=true\n'
  printf 'QSEELOG_ENABLED=true\n'
  printf 'COMPLETE_MATCHING_MODULE_TREE=true\n'
  printf 'NATIVE_AARCH64_BUILD=true\n'
  printf 'CLANG_VERSION=21.1.8\n'
  printf 'PROPRIETARY_PAYLOADS_INCLUDED=false\n'
  printf 'RUNTIME_KERNEL_RELINKED=false\n'
  printf 'RUNTIME_DTB_REBUILT=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
  while read -r module_hash module; do
    key=$(basename "$module" .ko | tr '[:lower:].-' '[:upper:]__')
    printf 'MODULE_%s_SHA256=%s\n' "$key" "$module_hash"
  done <"$second_hashes"
} >"$bundle/manifest.env"
chmod 0644 "$bundle/manifest.env"

# The install tree is derivable from the pinned archive and clutters the
# release output; retain both compile-hash ledgers and the final bundle only.
find "$install_tree" -depth -delete
printf 'FP6 matched-CMA hardware-continuity bundle: %s\n' "$bundle"
