#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Incrementally relink the already verified isolated FP6 microphone build tree
# with Linux's explicit split-BTF mismatch fallback. This is needed because the
# built-in Qualcomm SoundWire paging fix legitimately changes vmlinux BTF while
# Fedora's retained modules carry split BTF from the proven kernel. Binary kABI,
# symbol CRCs, kernel release, and module contents remain unchanged.

set -euo pipefail
umask 022

build_root=${1:?usage: relink-fp6-microphone-kernel-btf-fallback.sh BUILD_ROOT OUTPUT_DIR}
output_dir=${2:?usage: relink-fp6-microphone-kernel-btf-fallback.sh BUILD_ROOT OUTPUT_DIR}
rootfs=$build_root/lab/root
source_tree=$rootfs/work/linux
old_config_sha=67b55b06313dac5f69d6939459fb58791cef4f656cbf7564a881b17c2fdab4c0
old_image_sha=a955ed54f22de57f8d6060fa017a832413034037d08fe0715c40b6d18332e4c2
dtb_sha=42427369df56f961ca1aa60f02edb6b8f63cb0a60b7aed4e4f820cb5fda6951b
qcom_sdw_sha=ba08049a11a62b4aeaebc87a72e0fa2dbeac2567c1f39c36712476a3d3eadd32
wcd9378_sha=22ed89e18882599cb012dd988f863aba77d003c346a96a4b478ca99802657c29
build_timestamp='2026-08-19 00:00:00 UTC'

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "$(id -u)" -eq 0 ] || die 'run as root on the isolated builder'
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] ||
  die 'run only on the Linux/aarch64 builder'
[ -x /mnt/lima-rosetta/rosetta ] || die 'Lima Rosetta is unavailable'
if [ -r /sys/firmware/devicetree/base/model ]; then
  model=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$model" in *Fairphone*) die 'refuse to compile on the FP6 target' ;; esac
fi
[ -d "$source_tree" ] || die 'verified microphone source tree is missing'
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"

hash() { sha256sum "$1" | cut -d ' ' -f 1; }
[ "$(hash "$source_tree/.config")" = "$old_config_sha" ] ||
  die 'input build configuration differs'
[ "$(hash "$source_tree/arch/arm64/boot/vmlinuz")" = "$old_image_sha" ] ||
  die 'input rebuilt kernel differs'
[ "$(hash "$source_tree/arch/arm64/boot/dts/qcom/milos-fairphone-fp6.dtb")" = "$dtb_sha" ] ||
  die 'input microphone DTB differs'
[ "$(hash "$source_tree/drivers/soundwire/qcom.c")" = "$qcom_sdw_sha" ] ||
  die 'Qualcomm SoundWire source differs'
[ "$(hash "$source_tree/sound/soc/codecs/wcd9378.c")" = "$wcd9378_sha" ] ||
  die 'WCD9378 source differs'

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
  cd /work/linux
  scripts/config --enable MODULE_ALLOW_BTF_MISMATCH
  make ARCH=arm64 LLVM=1 LOCALVERSION= olddefconfig
  grep -qx 'CONFIG_MODULE_ALLOW_BTF_MISMATCH=y' .config
  grep -qx 'CONFIG_SOUNDWIRE_QCOM=y' .config
  test \"\$(make ARCH=arm64 LLVM=1 LOCALVERSION= -s kernelrelease)\" = 7.1.2
  make -j4 ARCH=arm64 LLVM=1 KBUILD_BUILD_USER=pmos \\
    KBUILD_BUILD_HOST=build \\
    KBUILD_BUILD_TIMESTAMP='$build_timestamp' \\
    KBUILD_BUILD_VERSION=1-postmarketos-qcom-milos vmlinuz.efi
"

new_image=$source_tree/arch/arm64/boot/vmlinuz
new_image_sha=$(hash "$new_image")
[ "$new_image_sha" != "$old_image_sha" ] || die 'kernel did not relink'
gzip -t "$new_image"

mkdir -p "$output_dir"
install -m 0644 "$new_image" "$output_dir/Image.gz"
install -m 0644 "$source_tree/.config" "$output_dir/config"
install -m 0644 \
  "$source_tree/arch/arm64/boot/dts/qcom/milos-fairphone-fp6.dtb" \
  "$output_dir/milos-fairphone-fp6.dtb"
{
  printf 'LUMA_FP6_MICROPHONE_RELINK_VERSION=1\n'
  printf 'KERNEL_RELEASE=7.1.2\n'
  printf 'INPUT_IMAGE_SHA256=%s\n' "$old_image_sha"
  printf 'IMAGE_SHA256=%s\n' "$new_image_sha"
  printf 'CONFIG_SHA256=%s\n' "$(hash "$output_dir/config")"
  printf 'DTB_SHA256=%s\n' "$dtb_sha"
  printf 'SOUNDWIRE_QCOM_SHA256=%s\n' "$qcom_sdw_sha"
  printf 'MODULE_BTF_MISMATCH_FALLBACK=true\n'
  printf 'MODULE_BINARY_KABI_CHANGED=false\n'
  printf 'RAM_BOOT_ONLY=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

printf 'FP6 microphone live-capture kernel: %s\n' "$output_dir/Image.gz"
