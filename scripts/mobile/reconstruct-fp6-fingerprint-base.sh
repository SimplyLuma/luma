#!/bin/sh
# SPDX-License-Identifier: Apache-2.0

# Recreate the exact accepted FP6 microphone kernel state in the existing,
# isolated build tree.  The accepted image was intentionally produced in two
# phases: a complete microphone build without MODULE_ALLOW_BTF_MISMATCH,
# followed by an incremental relink with that explicit fallback enabled.  A
# clean build made directly from the final configuration is functionally
# equivalent but not byte-for-byte identical, so it is not an acceptable base
# for a fingerprint boot candidate.

set -eu
umask 022

build_root=${1:?usage: reconstruct-fp6-fingerprint-base.sh BUILD_ROOT}
rootfs=$build_root/lab/root
source_tree=$rootfs/work/linux
jobs=${LUMA_KERNEL_BUILD_JOBS:-4}
stage1_config_sha=67b55b06313dac5f69d6939459fb58791cef4f656cbf7564a881b17c2fdab4c0
stage1_image_sha=a955ed54f22de57f8d6060fa017a832413034037d08fe0715c40b6d18332e4c2
stage2_config_sha=a78af97c72e7d454348aa87dbe4bdabc81ab8328e9ae807610842059ef061d09
stage2_image_sha=760eb683f88ced7f869bc169ba123c52f0df6163c6cc2812b6676d812a3efb68
build_timestamp='2026-08-19 00:00:00 UTC'

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

hash() {
  sha256sum "$1" | awk '{print $1}'
}

case "$jobs" in
  ''|*[!0-9]*) die 'LUMA_KERNEL_BUILD_JOBS must be a positive integer' ;;
esac
[ "$jobs" -ge 1 ] || die 'LUMA_KERNEL_BUILD_JOBS must be at least 1'
[ "$(id -u)" -eq 0 ] || die 'run as root on the isolated builder'
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] ||
  die 'run only on an off-device Linux/aarch64 builder'
[ -x /mnt/lima-rosetta/rosetta ] || die 'Lima Rosetta is unavailable'
[ -d "$source_tree" ] || die 'prepared FP6 source tree is missing'
prepared_config_sha=$(hash "$source_tree/.config")
case "$prepared_config_sha" in
  "$stage1_config_sha"|"$stage2_config_sha") ;;
  *) die 'prepared configuration differs from both accepted build stages' ;;
esac
if [ -r /sys/firmware/devicetree/base/model ]; then
  model=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$model" in *Fairphone*) die 'refuse to compile on the FP6 target' ;; esac
fi

mounted_dev=false
mounted_proc=false
mounted_rosetta=false
mounted_sys=false
cleanup() {
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
  clang --version | head -n 1 | grep -Fx 'Alpine clang version 22.1.8'
  pahole --version | grep -Fx 'v1.31'
  cd /work/linux
  test \"\$(make ARCH=arm64 LLVM=1 LOCALVERSION= -s kernelrelease)\" = 7.1.2
  # A config toggle cannot rewind objects produced by the one-stage control
  # build into the historical two-stage state.  Keep the prepared/patched
  # sources and configuration, but discard every compiled object first.
  make ARCH=arm64 LLVM=1 clean
  scripts/config --disable MODULE_ALLOW_BTF_MISMATCH
  make ARCH=arm64 LLVM=1 LOCALVERSION= olddefconfig
  test \"\$(sha256sum .config | awk '{print \$1}')\" = '$stage1_config_sha'
  make -j'$jobs' ARCH=arm64 LLVM=1 KBUILD_BUILD_USER=pmos \\
    KBUILD_BUILD_HOST=build \\
    KBUILD_BUILD_TIMESTAMP='$build_timestamp' \\
    KBUILD_BUILD_VERSION=1-postmarketos-qcom-milos vmlinuz.efi
  test \"\$(sha256sum arch/arm64/boot/vmlinuz | awk '{print \$1}')\" = '$stage1_image_sha'
  scripts/config --enable MODULE_ALLOW_BTF_MISMATCH
  make ARCH=arm64 LLVM=1 LOCALVERSION= olddefconfig
  test \"\$(sha256sum .config | awk '{print \$1}')\" = '$stage2_config_sha'
  make -j'$jobs' ARCH=arm64 LLVM=1 KBUILD_BUILD_USER=pmos \\
    KBUILD_BUILD_HOST=build \\
    KBUILD_BUILD_TIMESTAMP='$build_timestamp' \\
    KBUILD_BUILD_VERSION=1-postmarketos-qcom-milos vmlinuz.efi
  test \"\$(sha256sum arch/arm64/boot/vmlinuz | awk '{print \$1}')\" = '$stage2_image_sha'
"

printf 'Exact accepted FP6 base restored: %s\n' "$source_tree"
