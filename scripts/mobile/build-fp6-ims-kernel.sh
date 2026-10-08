#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build the minimal FP6 IMS kernel capability delta and a complete matching
# module tree.  The accepted source tree is copied before it is changed; this
# script never contacts the phone or composes/flashes a boot partition.

set -euo pipefail
umask 022

source_tree=${1:?usage: build-fp6-ims-kernel.sh ACCEPTED_SOURCE_TREE OUTPUT_DIR}
output_dir=${2:?missing output directory}
kernel_release=7.1.2-luma-fp-ims1
localversion=-luma-fp-ims1
build_timestamp='2026-08-27 00:00:00 UTC'
jobs=${LUMA_KERNEL_BUILD_JOBS:-8}
in_place=${LUMA_IMS_KERNEL_IN_PLACE:-false}
accepted_config_sha=a403b3cb247d3f2ce358f8debcc3b93d3c1949813488da8e12ac81540cee519c
accepted_kernel_sha=101fafec6d1d661b1b159d69e43c14d1289ca03f0944af4d5593b10662524572

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

[[ $(uname -s) == Linux && $(uname -m) == aarch64 ]] || die 'requires Linux/aarch64'
[[ $(id -u) -eq 0 ]] || die 'run as root in the isolated builder'
[[ -d $source_tree && -f $source_tree/.config ]] || die 'accepted source tree absent'
[[ $(hash "$source_tree/.config") == "$accepted_config_sha" ]] || die 'accepted config differs'
[[ $(hash "$source_tree/arch/arm64/boot/vmlinuz") == "$accepted_kernel_sha" ]] || die 'accepted kernel differs'
[[ ! -e $output_dir ]] || die "refuse to overwrite output: $output_dir"
[[ $in_place == true || $in_place == false ]] || die 'invalid in-place setting'

work_tree=$output_dir/work/linux
install_tree=$output_dir/work/modules
bundle=$output_dir/bundle
mkdir -p "$output_dir/work" "$bundle"
if [[ $in_place == true ]]; then
  # Useful on a space-constrained isolated builder. The caller must retain a
  # separately verified byte-exact accepted source tree for rollback.
  work_tree=$source_tree
else
  cp -a "$source_tree" "$work_tree"
fi

(
  cd "$work_tree"
  # The accepted tree may contain x86_64 cross-build host utilities. Remove
  # generated products so Kbuild recreates every host tool natively on the
  # isolated AArch64 builder before changing the configuration.
  make ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" clean
  scripts/config --enable XFRM_USER
  scripts/config --enable INET_ESP
  scripts/config --enable INET6_ESP
  make ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" olddefconfig

  grep -qx 'CONFIG_XFRM=y' .config
  grep -qx 'CONFIG_XFRM_ALGO=y' .config
  grep -qx 'CONFIG_XFRM_USER=y' .config
  grep -qx 'CONFIG_INET_ESP=y' .config
  grep -qx 'CONFIG_INET6_ESP=y' .config
  grep -qx 'CONFIG_CRYPTO_AES=y' .config
  grep -qx 'CONFIG_CRYPTO_CBC=y' .config
  grep -qx 'CONFIG_CRYPTO_HMAC=y' .config
  grep -qx 'CONFIG_CRYPTO_SHA1=y' .config
  [[ $(make ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" -s kernelrelease) == "$kernel_release" ]]

  make -j"$jobs" ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" \
    KBUILD_BUILD_USER=pmos KBUILD_BUILD_HOST=build \
    KBUILD_BUILD_TIMESTAMP="$build_timestamp" \
    KBUILD_BUILD_VERSION=1-postmarketos vmlinuz.efi modules
  make ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" \
    INSTALL_MOD_PATH="$install_tree" modules_install
)

module_root=$install_tree/lib/modules/$kernel_release
[[ -d $module_root ]] || die 'matching module tree absent'
rm -f "$module_root/build" "$module_root/source"
module_count=$(find "$module_root" -type f -name '*.ko.zst' | wc -l | tr -d ' ')
[[ $module_count -gt 0 ]] || die 'matching module tree empty'

install -m 0644 "$work_tree/arch/arm64/boot/vmlinuz" "$bundle/Image.gz"
install -m 0644 "$work_tree/.config" "$bundle/config"
tar --sort=name --mtime="$build_timestamp" --owner=0 --group=0 --numeric-owner \
  -C "$install_tree" -cf - "lib/modules/$kernel_release" |
  zstd -q -19 -T0 -o "$bundle/modules-$kernel_release.tar.zst"

{
  printf 'LUMA_FP6_IMS_KERNEL_VERSION=1\n'
  printf 'KERNEL_RELEASE=%s\n' "$kernel_release"
  printf 'BASE_CONFIG_SHA256=%s\n' "$accepted_config_sha"
  printf 'BASE_KERNEL_SHA256=%s\n' "$accepted_kernel_sha"
  printf 'IMAGE_SHA256=%s\n' "$(hash "$bundle/Image.gz")"
  printf 'CONFIG_SHA256=%s\n' "$(hash "$bundle/config")"
  printf 'MODULE_TREE_SHA256=%s\n' "$(hash "$bundle/modules-$kernel_release.tar.zst")"
  printf 'MODULE_COUNT=%s\n' "$module_count"
  printf 'XFRM_USER_BUILTIN=true\n'
  printf 'IPV4_ESP_BUILTIN=true\n'
  printf 'IPV6_ESP_BUILTIN=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$bundle/manifest.env"

if [[ $in_place == false ]]; then
  rm -rf "$output_dir/work"
else
  rm -rf "$install_tree"
fi
printf 'FP6 IMS kernel bundle: %s\n' "$bundle"
