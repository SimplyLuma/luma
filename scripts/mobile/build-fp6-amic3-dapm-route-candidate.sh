#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build only the FP6 DTB needed to connect the v4 ADC2 master-port mapping to
# LPASS TX SWR_INPUT4. Run solely in the existing isolated Linux/aarch64
# builder; this script cannot access the phone.

set -euo pipefail
umask 022

build_root=${1:?usage: build-fp6-amic3-dapm-route-candidate.sh BUILD_ROOT PATCH OUTPUT_DIR}
route_patch=${2:?usage: build-fp6-amic3-dapm-route-candidate.sh BUILD_ROOT PATCH OUTPUT_DIR}
output_dir=${3:?usage: build-fp6-amic3-dapm-route-candidate.sh BUILD_ROOT PATCH OUTPUT_DIR}
rootfs=$build_root/lab/root
source_tree=$rootfs/work/linux
config_sha=a78af97c72e7d454348aa87dbe4bdabc81ab8328e9ae807610842059ef061d09
image_sha=760eb683f88ced7f869bc169ba123c52f0df6163c6cc2812b6676d812a3efb68
input_dtb_sha=28bdea6b91939f9ae55c33e04cea3b39058d4d49494f594195dbade4396b5e78
input_dts_sha=6aab82b20c0d2c1ccf7284b5e93afa47ac20409122a4d3c79cbab458a5cd485c
module_sha=10b7aaea18a2df37c1ac6ad195db47e9f89f7c7249080e7ef30a8e1221d761d9
patch_sha=53fcddb1c91a7fcb4fdf33b9c0db7da8892bbf4597809ca3be6cd8e6d14bddd9
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
[ -d "$source_tree" ] || die 'verified v4 source tree is missing'
[ -f "$route_patch" ] || die 'ADC2 DAPM route patch is missing'
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"
[ "$(hash "$route_patch")" = "$patch_sha" ] || die 'route patch differs'
[ "$(hash "$source_tree/.config")" = "$config_sha" ] || die 'v3 config differs'
[ "$(hash "$source_tree/arch/arm64/boot/vmlinuz")" = "$image_sha" ] || die 'v3 kernel differs'
[ "$(hash "$source_tree/arch/arm64/boot/dts/qcom/milos-fairphone-fp6.dtb")" = "$input_dtb_sha" ] || die 'v4 input DTB differs'
[ "$(hash "$source_tree/arch/arm64/boot/dts/qcom/milos-fairphone-fp6.dts")" = "$input_dts_sha" ] || die 'v4 input DTS differs'
[ "$(hash "$source_tree/sound/soc/codecs/snd-soc-wcd9378-sdw.ko")" = "$module_sha" ] || die 'v4 module differs'
patch -d "$source_tree" --dry-run -p1 <"$route_patch"
patch -d "$source_tree" -p1 <"$route_patch"

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
  grep -qx 'CONFIG_MODULE_ALLOW_BTF_MISMATCH=y' .config
  test \"\$(make ARCH=arm64 LLVM=1 LOCALVERSION= -s kernelrelease)\" = 7.1.2
  make -j4 ARCH=arm64 LLVM=1 KBUILD_BUILD_USER=pmos \\
    KBUILD_BUILD_HOST=build KBUILD_BUILD_TIMESTAMP='$build_timestamp' \\
    KBUILD_BUILD_VERSION=1-postmarketos-qcom-milos \\
    qcom/milos-fairphone-fp6.dtb
"

dtb=$source_tree/arch/arm64/boot/dts/qcom/milos-fairphone-fp6.dtb
[ -f "$dtb" ] || die 'rebuilt FP6 DTB is missing'
[ "$(hash "$dtb")" != "$input_dtb_sha" ] || die 'DTB did not change'
[ "$(hash "$source_tree/sound/soc/codecs/snd-soc-wcd9378-sdw.ko")" = "$module_sha" ] || die 'module changed during DT-only build'

runuser -u "$builder_user" -- mkdir -p "$output_dir"
runuser -u "$builder_user" -- install -m 0644 "$dtb" "$output_dir/milos-fairphone-fp6.dtb"
manifest_tmp=$(mktemp)
{
  printf 'LUMA_FP6_AMIC3_DAPM_BUILD_VERSION=1\n'
  printf 'KERNEL_RELEASE=7.1.2\n'
  printf 'BASE_KERNEL_SHA256=%s\n' "$image_sha"
  printf 'BASE_DTB_SHA256=%s\n' "$input_dtb_sha"
  printf 'PATCH_SHA256=%s\n' "$patch_sha"
  printf 'WCD9378_SDW_KO_SHA256=%s\n' "$module_sha"
  printf 'DTB_SHA256=%s\n' "$(hash "$output_dir/milos-fairphone-fp6.dtb")"
  printf 'MODULE_UNCHANGED=true\n'
  printf 'RAM_BOOT_ONLY=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$manifest_tmp"
runuser -u "$builder_user" -- install -m 0644 "$manifest_tmp" "$output_dir/manifest.env"

printf 'FP6 AMIC3 DAPM route bundle: %s\n' "$output_dir"
