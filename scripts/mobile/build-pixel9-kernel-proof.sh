#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Compile the exact unmodified Tokay kernel and device tree as an off-device
# source/toolchain proof. This does not assemble an Android boot image, include
# a root filesystem or firmware, contact a phone, or authorize a boot/flash.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

source_root=${1:?usage: build-pixel9-kernel-proof.sh SOURCE_ROOT OUTPUT_DIR}
output_dir=${2:?usage: build-pixel9-kernel-proof.sh SOURCE_ROOT OUTPUT_DIR}
build_dir=$output_dir/build
artifact_dir=$output_dir/artifacts
jobs=${LUMA_PIXEL9_KERNEL_BUILD_JOBS:-4}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

case "$jobs" in
  ''|*[!0-9]*) die 'LUMA_PIXEL9_KERNEL_BUILD_JOBS must be a positive integer' ;;
esac
[ "$jobs" -ge 1 ] || die 'LUMA_PIXEL9_KERNEL_BUILD_JOBS must be at least 1'
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] || \
  die 'the Pixel 9 kernel proof must run off-device on Linux/aarch64'

if [ -r /sys/firmware/devicetree/base/model ]; then
  build_device=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$build_device" in
    *Pixel*|*tokay*|*Tokay*) die 'refusing to compile on a Pixel target' ;;
  esac
fi

for tool in awk clang git install make sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || die "missing kernel build tool: $tool"
done

linux=$source_root/linux-zumapro
source_manifest=$source_root/luma-source-manifest.env
[ -d "$linux/.git" ] || die "missing full Linux checkout: $linux"
[ -f "$source_manifest" ] || die "missing full-source manifest: $source_manifest"
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

[ "$(git -C "$linux" rev-parse HEAD)" = "$PIXEL9_LINUX_COMMIT" ] || \
  die 'Linux checkout differs from the pinned commit'
[ -z "$(git -C "$linux" status --porcelain --untracked-files=no)" ] || \
  die 'Linux checkout contains tracked modifications'
[ "$(sha256sum "$linux/arch/arm64/configs/$PIXEL9_LINUX_DEFCONFIG" | awk '{print $1}')" = \
  "$PIXEL9_LINUX_DEFCONFIG_SHA256" ] || die 'Tokay defconfig checksum differs'
grep -Fqx "LINUX_COMMIT=$PIXEL9_LINUX_COMMIT" "$source_manifest" || \
  die 'full-source manifest does not match the Linux pin'
grep -Fqx 'PHONE_ACCESSED=false' "$source_manifest" || \
  die 'full-source manifest lacks the no-phone boundary'
grep -Fqx 'BOOT_AUTHORIZED=false' "$source_manifest" || \
  die 'full-source manifest leaves the boot gate ambiguous'
grep -Fqx 'FLASH_AUTHORIZED=false' "$source_manifest" || \
  die 'full-source manifest leaves the flash gate ambiguous'

mkdir -p "$build_dir" "$artifact_dir"
source_date_epoch=$(git -C "$linux" show -s --format=%ct "$PIXEL9_LINUX_COMMIT")
export SOURCE_DATE_EPOCH="$source_date_epoch"
export KBUILD_BUILD_TIMESTAMP="@$source_date_epoch"
export KBUILD_BUILD_USER=luma
export KBUILD_BUILD_HOST=pixel9-builder
export KBUILD_BUILD_VERSION=1

make -C "$linux" O="$build_dir" ARCH=arm64 LLVM=1 \
  "$PIXEL9_LINUX_DEFCONFIG"
make -C "$linux" O="$build_dir" ARCH=arm64 LLVM=1 -j"$jobs" Image dtbs

image=$build_dir/arch/arm64/boot/Image
tokay_dtb=$build_dir/arch/arm64/boot/dts/exynos/google/zumapro-tokay.dtb
for artifact in "$image" "$tokay_dtb" "$build_dir/.config"; do
  [ -s "$artifact" ] || die "kernel proof artifact is missing: $artifact"
done

install -m 0644 "$image" "$artifact_dir/Image"
install -m 0644 "$tokay_dtb" "$artifact_dir/zumapro-tokay.dtb"
install -m 0644 "$build_dir/.config" "$artifact_dir/config"

kernel_release=$(make -s -C "$linux" O="$build_dir" ARCH=arm64 LLVM=1 kernelrelease)
compiler_line=$(clang --version | sed -n '1p' | tr '\n' ' ')
{
  printf 'LUMA_PIXEL9_KERNEL_PROOF_VERSION=1\n'
  printf 'SCOPE=compile-only-unmodified-upstream\n'
  printf 'LINUX_COMMIT=%s\n' "$PIXEL9_LINUX_COMMIT"
  printf 'DEFCONFIG=%s\n' "$PIXEL9_LINUX_DEFCONFIG"
  printf 'DEFCONFIG_SHA256=%s\n' "$PIXEL9_LINUX_DEFCONFIG_SHA256"
  printf 'SOURCE_DATE_EPOCH=%s\n' "$source_date_epoch"
  printf 'KERNEL_RELEASE=%s\n' "$kernel_release"
  printf 'COMPILER=%s\n' "$compiler_line"
  printf 'IMAGE_SHA256=%s\n' "$(sha256sum "$artifact_dir/Image" | awk '{print $1}')"
  printf 'TOKAY_DTB_SHA256=%s\n' "$(sha256sum "$artifact_dir/zumapro-tokay.dtb" | awk '{print $1}')"
  printf 'CONFIG_SHA256=%s\n' "$(sha256sum "$artifact_dir/config" | awk '{print $1}')"
  printf 'BUILD_HOST=Linux/aarch64\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'MODULES_BUILT=false\n'
  printf 'ROOTFS_INCLUDED=false\n'
  printf 'FIRMWARE_INCLUDED=false\n'
  printf 'ANDROID_BOOT_IMAGE_ASSEMBLED=false\n'
  printf 'AVB_SIGNED=false\n'
  printf 'BOOT_AUTHORIZED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

printf 'Pixel 9 compile-only kernel proof: %s\n' "$output_dir"
