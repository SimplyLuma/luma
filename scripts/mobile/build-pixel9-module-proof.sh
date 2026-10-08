#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Compile and stage every module selected by the exact Tokay defconfig. This is
# an off-device dependency proof only: it does not create an initramfs, Android
# boot container, root filesystem, or phone-authorized payload.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

source_root=${1:?usage: build-pixel9-module-proof.sh SOURCE_ROOT OUTPUT_DIR}
output_dir=${2:?usage: build-pixel9-module-proof.sh SOURCE_ROOT OUTPUT_DIR}
build_dir=$output_dir/build
stage_dir=$output_dir/stage
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
  die 'the Pixel 9 module proof must run off-device on Linux/aarch64'

if [ -r /sys/firmware/devicetree/base/model ]; then
  build_device=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$build_device" in
    *Pixel*|*tokay*|*Tokay*) die 'refusing to compile on a Pixel target' ;;
  esac
fi

for tool in awk clang find git install make sha256sum sort tar; do
  command -v "$tool" >/dev/null 2>&1 || die "missing module build tool: $tool"
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

mkdir -p "$build_dir" "$stage_dir" "$artifact_dir"
source_date_epoch=$(git -C "$linux" show -s --format=%ct "$PIXEL9_LINUX_COMMIT")
export SOURCE_DATE_EPOCH="$source_date_epoch"
export KBUILD_BUILD_TIMESTAMP="@$source_date_epoch"
export KBUILD_BUILD_USER=luma
export KBUILD_BUILD_HOST=pixel9-builder
export KBUILD_BUILD_VERSION=1

make -C "$linux" O="$build_dir" ARCH=arm64 LLVM=1 \
  "$PIXEL9_LINUX_DEFCONFIG"
make -C "$linux" O="$build_dir" ARCH=arm64 LLVM=1 -j"$jobs" \
  Image dtbs modules
make -C "$linux" O="$build_dir" ARCH=arm64 LLVM=1 \
  INSTALL_MOD_PATH="$stage_dir" DEPMOD=true modules_install

kernel_release=$(make -s -C "$linux" O="$build_dir" ARCH=arm64 LLVM=1 kernelrelease)
module_root=$stage_dir/lib/modules/$kernel_release
[ -d "$module_root" ] || die 'modules_install did not create the expected release directory'

find "$module_root" -type l -delete
find "$module_root" -type f -name '*.ko' -printf '%P\n' | LC_ALL=C sort \
  >"$artifact_dir/modules.files.txt"
sed -e 's#^#kernel/#' -e 's#[.]o$#.ko#' "$build_dir/modules.order" | LC_ALL=C sort \
  >"$artifact_dir/modules.expected.txt"
configured_module_symbols=$(grep -c '=m$' "$build_dir/.config" || true)
expected_module_files=$(wc -l <"$artifact_dir/modules.expected.txt" | tr -d ' ')
installed_module_files=$(wc -l <"$artifact_dir/modules.files.txt" | tr -d ' ')
[ "$configured_module_symbols" -gt 0 ] || die 'Tokay defconfig unexpectedly selects no modules'
cmp -s "$artifact_dir/modules.expected.txt" "$artifact_dir/modules.files.txt" || \
  die 'modules_install output differs from the kernel modules.order contract'

find "$module_root" -type f -exec touch -d "@$source_date_epoch" {} +
tar --sort=name --mtime="@$source_date_epoch" --owner=0 --group=0 --numeric-owner \
  -C "$stage_dir" -cf "$artifact_dir/modules.tar" lib/modules
install -m 0644 "$build_dir/Module.symvers" "$artifact_dir/Module.symvers"
install -m 0644 "$build_dir/System.map" "$artifact_dir/System.map"
install -m 0644 "$build_dir/.config" "$artifact_dir/config"
install -m 0644 "$build_dir/arch/arm64/boot/Image" "$artifact_dir/Image"
install -m 0644 \
  "$build_dir/arch/arm64/boot/dts/exynos/google/zumapro-tokay.dtb" \
  "$artifact_dir/zumapro-tokay.dtb"

compiler_line=$(clang --version | sed -n '1p' | tr '\n' ' ')
{
  printf 'LUMA_PIXEL9_MODULE_PROOF_VERSION=1\n'
  printf 'SCOPE=compile-and-stage-unmodified-upstream-modules\n'
  printf 'LINUX_COMMIT=%s\n' "$PIXEL9_LINUX_COMMIT"
  printf 'DEFCONFIG=%s\n' "$PIXEL9_LINUX_DEFCONFIG"
  printf 'DEFCONFIG_SHA256=%s\n' "$PIXEL9_LINUX_DEFCONFIG_SHA256"
  printf 'SOURCE_DATE_EPOCH=%s\n' "$source_date_epoch"
  printf 'KERNEL_RELEASE=%s\n' "$kernel_release"
  printf 'COMPILER=%s\n' "$compiler_line"
  printf 'CONFIGURED_MODULE_SYMBOLS=%s\n' "$configured_module_symbols"
  printf 'EXPECTED_MODULE_FILES=%s\n' "$expected_module_files"
  printf 'INSTALLED_MODULE_FILES=%s\n' "$installed_module_files"
  printf 'MODULES_TAR_SHA256=%s\n' "$(sha256sum "$artifact_dir/modules.tar" | awk '{print $1}')"
  printf 'MODULES_FILE_LIST_SHA256=%s\n' "$(sha256sum "$artifact_dir/modules.files.txt" | awk '{print $1}')"
  printf 'MODULES_EXPECTED_LIST_SHA256=%s\n' "$(sha256sum "$artifact_dir/modules.expected.txt" | awk '{print $1}')"
  printf 'MODULE_SYMVERS_SHA256=%s\n' "$(sha256sum "$artifact_dir/Module.symvers" | awk '{print $1}')"
  printf 'SYSTEM_MAP_SHA256=%s\n' "$(sha256sum "$artifact_dir/System.map" | awk '{print $1}')"
  printf 'CONFIG_SHA256=%s\n' "$(sha256sum "$artifact_dir/config" | awk '{print $1}')"
  printf 'IMAGE_SHA256=%s\n' "$(sha256sum "$artifact_dir/Image" | awk '{print $1}')"
  printf 'TOKAY_DTB_SHA256=%s\n' "$(sha256sum "$artifact_dir/zumapro-tokay.dtb" | awk '{print $1}')"
  printf 'BUILD_HOST=Linux/aarch64\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'MODULES_BUILT=true\n'
  printf 'MODULES_STAGED=true\n'
  printf 'INITRAMFS_ASSEMBLED=false\n'
  printf 'ROOTFS_INCLUDED=false\n'
  printf 'FIRMWARE_INCLUDED=false\n'
  printf 'ANDROID_BOOT_IMAGE_ASSEMBLED=false\n'
  printf 'AVB_SIGNED=false\n'
  printf 'BOOT_AUTHORIZED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

printf 'Pixel 9 module compile/stage proof: %s\n' "$output_dir"
