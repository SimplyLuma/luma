#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build the first experimental FP6 camera lane from exact source. This script
# creates a DTB and kernel modules only. It never contacts or changes a phone.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-camera.env"

kernel_archive=${1:?usage: build-fp6-camera-kernel.sh KERNEL_ARCHIVE PATCH_ARCHIVE [OUTPUT_DIR]}
patch_archive=${2:?usage: build-fp6-camera-kernel.sh KERNEL_ARCHIVE PATCH_ARCHIVE [OUTPUT_DIR]}
output_dir=${3:-$repo_root/build/mobile/fp6-physical/fp6-camera-kernel-wide1}
running_config=${LUMA_FP6_RUNNING_CONFIG:-/proc/config.gz}
build_jobs=${LUMA_KERNEL_BUILD_JOBS:-2}

case "$build_jobs" in
  ''|*[!0-9]*)
    printf 'error: LUMA_KERNEL_BUILD_JOBS must be a positive integer\n' >&2
    exit 1
    ;;
esac
[ "$build_jobs" -ge 1 ] || {
  printf 'error: LUMA_KERNEL_BUILD_JOBS must be at least 1\n' >&2
  exit 1
}

for tool in awk bc bison clang flex git gzip install ld.lld llvm-objcopy \
  make mktemp od pahole readelf sha256sum stat tar tr truncate; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: missing camera-kernel build tool: %s\n' "$tool" >&2
    exit 1
  }
done

[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] || {
  printf 'error: build the FP6 camera candidate on Linux/aarch64\n' >&2
  exit 1
}
[ -f "$kernel_archive" ] && [ -f "$patch_archive" ] || {
  printf 'error: both pinned source archives must exist\n' >&2
  exit 1
}
[ "$(sha256sum "$kernel_archive" | awk '{print $1}')" = \
  "$FP6_CAMERA_KERNEL_ARCHIVE_SHA256" ] || {
  printf 'error: Milos source archive checksum differs\n' >&2
  exit 1
}
[ "$(sha256sum "$patch_archive" | awk '{print $1}')" = \
  "$FP6_CAMERA_PATCH_ARCHIVE_SHA256" ] || {
  printf 'error: FP6 camera patch archive checksum differs\n' >&2
  exit 1
}
[ -r "$running_config" ] || {
  printf 'error: running kernel configuration is unreadable: %s\n' \
    "$running_config" >&2
  exit 1
}

patches=(
  0001-media-qcom-camss-add-TFE665-VFE-support-for-milos.patch
  0002-media-qcom-camss-add-CSIPHY-v2.2.1-milos-lane-config.patch
  0003-media-qcom-camss-add-SM7635-resources-and-compatible.patch
  0004-media-ov13b10-add-OF-match-selection-API-and-supplies.patch
  0005-arm64-dts-qcom-milos-add-CAMSS-and-FP6-ultra-wide-ca.patch
)
expected_hashes=(
  "$FP6_CAMERA_PATCH_1_SHA256"
  "$FP6_CAMERA_PATCH_2_SHA256"
  "$FP6_CAMERA_PATCH_3_SHA256"
  "$FP6_CAMERA_PATCH_4_SHA256"
  "$FP6_CAMERA_PATCH_5_SHA256"
)
mkdir -p "$output_dir"
output_dir=$(CDPATH= cd -- "$output_dir" && pwd)
work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
tar -xzf "$kernel_archive" -C "$work_dir"
tar -xzf "$patch_archive" -C "$work_dir"
source_tree=$work_dir/linux
patch_source=$work_dir/patches

for index in "${!patches[@]}"; do
  patch=$patch_source/kernel/${patches[$index]}
  [ -f "$patch" ] || {
    printf 'error: missing pinned camera patch: %s\n' "$patch" >&2
    exit 1
  }
  [ "$(sha256sum "$patch" | awk '{print $1}')" = \
    "${expected_hashes[$index]}" ] || {
    printf 'error: camera patch checksum differs: %s\n' "$patch" >&2
    exit 1
  }
done

for patch in "${patches[@]}"; do
  git -C "$source_tree" apply "$patch_source/kernel/$patch"
done

(
  cd "$source_tree"
  case "$running_config" in
    *.gz) gzip -dc "$running_config" >.config ;;
    *) install -m 0644 "$running_config" .config ;;
  esac
  scripts/config --module VIDEO_QCOM_CAMSS
  scripts/config --module VIDEO_OV13B10
  scripts/config --module VIDEO_DW9714
  make LLVM=1 LOCALVERSION= olddefconfig
  [ "$(make LLVM=1 LOCALVERSION= -s kernelrelease)" = 7.1.2 ]
  grep -qx 'CONFIG_VIDEO_QCOM_CAMSS=m' .config
  grep -qx 'CONFIG_VIDEO_OV13B10=m' .config
  grep -qx 'CONFIG_VIDEO_DW9714=m' .config

  # vmlinux.symvers produces the exact built-in symbol table but stops before
  # pahole's multi-gigabyte full-kernel BTF pass, which can exhaust the FP6's
  # memory. Keep BTF enabled in the config: its fields are part of struct module
  # and must match the running kernel. Build the complete configured module set
  # so Kbuild validates exports supplied by other modular media dependencies.
  make -j"$build_jobs" LLVM=1 LOCALVERSION= vmlinux.symvers dtbs
  make -j"$build_jobs" LLVM=1 LOCALVERSION= modules
)

dtb=$source_tree/arch/arm64/boot/dts/qcom/milos-fairphone-fp6.dtb
camss=$source_tree/drivers/media/platform/qcom/camss/qcom-camss.ko
sensor=$source_tree/drivers/media/i2c/ov13b10.ko
lens=$source_tree/drivers/media/i2c/dw9714.ko
for artifact in "$dtb" "$camss" "$sensor" "$lens"; do
  [ -s "$artifact" ] || {
    printf 'error: camera build artifact is missing: %s\n' "$artifact" >&2
    exit 1
  }
done

install -m 0644 "$dtb" "$output_dir/milos-fairphone-fp6-camera-wide1.dtb"
install -m 0644 "$camss" "$output_dir/qcom-camss.ko"
install -m 0644 "$sensor" "$output_dir/ov13b10.ko"
install -m 0644 "$lens" "$output_dir/dw9714.ko"
install -m 0644 "$source_tree/.config" "$output_dir/config"

# The running postmarketOS kernel and this exact-source rebuild can order their
# BTF base types differently when built with different compiler/pahole stacks.
# Split module BTF then fails validation even though code, vermagic, symbol
# versions, and struct module match. Runtime modules do not require BTF, so
# remove only that debug/introspection section from the distributed copies.
for module in qcom-camss ov13b10 dw9714; do
  readelf -SW "$output_dir/$module.ko" | grep -q '[.]BTF'
  llvm-objcopy --remove-section=.BTF "$output_dir/$module.ko"
  ! readelf -SW "$output_dir/$module.ko" | grep -q '[.]BTF'
done

{
  printf 'LUMA_FP6_CAMERA_KERNEL_BUILD_VERSION=1\n'
  printf 'SCOPE=experimental-ov13b10-ultrawide\n'
  printf 'KERNEL_SOURCE_COMMIT=%s\n' "$FP6_CAMERA_KERNEL_COMMIT"
  printf 'CAMERA_PATCH_COMMIT=%s\n' "$FP6_CAMERA_PATCH_COMMIT"
  printf 'DTB_SHA256=%s\n' "$(sha256sum "$output_dir/milos-fairphone-fp6-camera-wide1.dtb" | awk '{print $1}')"
  printf 'CAMSS_SHA256=%s\n' "$(sha256sum "$output_dir/qcom-camss.ko" | awk '{print $1}')"
  printf 'OV13B10_SHA256=%s\n' "$(sha256sum "$output_dir/ov13b10.ko" | awk '{print $1}')"
  printf 'DW9714_SHA256=%s\n' "$(sha256sum "$output_dir/dw9714.ko" | awk '{print $1}')"
  printf 'CONFIG_SHA256=%s\n' "$(sha256sum "$output_dir/config" | awk '{print $1}')"
  printf 'MODULE_BTF_STRIPPED=true\n'
  printf 'RUNTIME_SYMBOL_TEST_REQUIRED=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'BOOT_IMAGE_REPACKED=false\n'
  printf 'MODULES_INSTALLED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

printf 'FP6 experimental camera kernel bundle: %s\n' "$output_dir"
