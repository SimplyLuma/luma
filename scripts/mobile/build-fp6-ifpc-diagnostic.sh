#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build the one-variable A810 IFPC diagnostic kernel for the Fairphone 6.
# This script creates artifacts only. It never contacts, reboots, or flashes a
# phone.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
source_dir=${1:?usage: build-fp6-ifpc-diagnostic.sh KERNEL_SOURCE [OUTPUT_DIR]}
output_dir=${2:-$repo_root/build/mobile/fp6-physical/kernel-ifpc-test1}
running_config=${LUMA_FP6_RUNNING_CONFIG:-/proc/config.gz}
build_jobs=${LUMA_KERNEL_BUILD_JOBS:-4}

source_commit=dfe0125e73541d1984e4d2d37bd069a036bf0451
kernel_release=7.1.2
patch_path=$repo_root/patches/linux-milos/0001-drm-msm-a810-disable-ifpc-diagnostic.patch

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

for tool in bc bison clang flex git gzip ld.lld make mktemp od pahole sha256sum \
  stat tar tr truncate; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required kernel build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != aarch64 ]; then
  printf 'error: build the FP6 diagnostic kernel on native Linux/aarch64\n' >&2
  exit 1
fi

[ -d "$source_dir/.git" ] || {
  printf 'error: kernel source must be a Git checkout: %s\n' "$source_dir" >&2
  exit 1
}
[ "$(git -C "$source_dir" rev-parse HEAD)" = "$source_commit" ] || {
  printf 'error: kernel source is not pinned to %s\n' "$source_commit" >&2
  exit 1
}
[ -r "$running_config" ] || {
  printf 'error: running kernel configuration is unreadable: %s\n' "$running_config" >&2
  exit 1
}

mkdir -p "$output_dir"
work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
source_tree=$work_dir/linux
mkdir -p "$source_tree"

# Archive the exact commit so CONFIG_LOCALVERSION_AUTO cannot append a dirty
# Git suffix to the required 7.1.2 module ABI.
git -C "$source_dir" archive "$source_commit" | tar -x -C "$source_tree"
(
  cd "$source_tree"
  git apply "$patch_path"
  case "$running_config" in
    *.gz) gzip -dc "$running_config" >.config ;;
    *) install -m 0644 "$running_config" .config ;;
  esac
  make LLVM=1 olddefconfig

  [ "$(make LLVM=1 LOCALVERSION= -s kernelrelease)" = "$kernel_release" ]
  grep -qx 'CONFIG_CC_IS_CLANG=y' .config
  grep -qx 'CONFIG_CLANG_VERSION=220108' .config
  grep -qx 'CONFIG_LD_IS_LLD=y' .config
  grep -qx 'CONFIG_LTO_CLANG_THIN=y' .config
  grep -qx 'CONFIG_CFI=y' .config
  grep -qx 'CONFIG_SHADOW_CALL_STACK=y' .config
  grep -qx 'CONFIG_DRM_MSM=y' .config

  make -j"$build_jobs" LLVM=1 LOCALVERSION= Image

  # The FP6 control Image is zero-padded through the ARM64 header's declared
  # image_size. A raw objcopy output stops at the last file-backed byte; using
  # that shorter payload made the otherwise valid diagnostic kernel fail before
  # the root filesystem or persistent journal came up.
  image=arch/arm64/boot/Image
  declared_size=$(od -An -t u8 -j 16 -N 8 "$image" | tr -d '[:space:]')
  actual_size=$(stat -c %s "$image")
  case "$declared_size" in
    ''|*[!0-9]*)
      printf 'error: ARM64 Image header has an invalid image_size\n' >&2
      exit 1
      ;;
  esac
  [ "$actual_size" -le "$declared_size" ] || {
    printf 'error: raw Image exceeds its declared ARM64 image_size\n' >&2
    exit 1
  }
  truncate -s "$declared_size" "$image"
  gzip -9 -n -c "$image" >arch/arm64/boot/Image.gz
)

image=$source_tree/arch/arm64/boot/Image.gz
[ -s "$image" ] || {
  printf 'error: the diagnostic kernel image was not produced\n' >&2
  exit 1
}

install -m 0644 "$image" "$output_dir/Image.gz"
install -m 0644 "$source_tree/.config" "$output_dir/config"

image_sha=$(sha256sum "$output_dir/Image.gz" | awk '{print $1}')
config_sha=$(sha256sum "$output_dir/config" | awk '{print $1}')
patch_sha=$(sha256sum "$patch_path" | awk '{print $1}')
{
  printf 'LUMA_FP6_KERNEL_DIAGNOSTIC_VERSION=1\n'
  printf 'SOURCE_COMMIT=%s\n' "$source_commit"
  printf 'KERNEL_RELEASE=%s\n' "$kernel_release"
  printf 'DIAGNOSTIC=A810_IFPC_DISABLED\n'
  printf 'IMAGE_SHA256=%s\n' "$image_sha"
  printf 'CONFIG_SHA256=%s\n' "$config_sha"
  printf 'PATCH_SHA256=%s\n' "$patch_sha"
  printf 'PHONE_ACCESSED=false\n'
  printf 'BOOT_IMAGE_REPACKED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

printf 'FP6 IFPC diagnostic kernel: %s\n' "$output_dir/Image.gz"
