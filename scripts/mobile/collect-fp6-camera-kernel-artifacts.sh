#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Collect an already-completed exact FP6 camera kernel build. This is useful
# after a resource-constrained build is resumed manually; it performs the same
# artifact and manifest checks as build-fp6-camera-kernel.sh without compiling.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-camera.env"

source_tree=${1:?usage: collect-fp6-camera-kernel-artifacts.sh SOURCE_TREE OUTPUT_DIR}
output_dir=${2:?usage: collect-fp6-camera-kernel-artifacts.sh SOURCE_TREE OUTPUT_DIR}

for tool in awk grep install llvm-objcopy make modinfo readelf sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: missing camera artifact tool: %s\n' "$tool" >&2
    exit 1
  }
done
[ -d "$source_tree" ] || {
  printf 'error: source tree does not exist: %s\n' "$source_tree" >&2
  exit 1
}

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

(
  cd "$source_tree"
  [ "$(make LLVM=1 LOCALVERSION= -s kernelrelease)" = 7.1.2 ]
  grep -qx 'CONFIG_DEBUG_INFO_BTF=y' .config
  grep -qx 'CONFIG_DEBUG_INFO_BTF_MODULES=y' .config
  grep -qx 'CONFIG_VIDEO_QCOM_CAMSS=m' .config
  grep -qx 'CONFIG_VIDEO_OV13B10=m' .config
  grep -qx 'CONFIG_VIDEO_DW9714=m' .config
)
for module in "$camss" "$sensor" "$lens"; do
  [ "$(modinfo -F vermagic "$module" | awk '{print $1}')" = 7.1.2 ] || {
    printf 'error: module vermagic differs: %s\n' "$module" >&2
    exit 1
  }
done

mkdir -p "$output_dir"
output_dir=$(CDPATH= cd -- "$output_dir" && pwd)
install -m 0644 "$dtb" "$output_dir/milos-fairphone-fp6-camera-wide1.dtb"
install -m 0644 "$camss" "$output_dir/qcom-camss.ko"
install -m 0644 "$sensor" "$output_dir/ov13b10.ko"
install -m 0644 "$lens" "$output_dir/dw9714.ko"
install -m 0644 "$source_tree/.config" "$output_dir/config"

# Keep full BTF in the source-tree artifacts, but remove it from runtime copies.
# Different compiler/pahole stacks can order the rebuilt base BTF differently
# from the running postmarketOS kernel. Module BTF is optional at runtime.
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

printf 'Collected FP6 experimental camera kernel bundle: %s\n' "$output_dir"
